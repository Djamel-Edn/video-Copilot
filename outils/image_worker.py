"""Générateur d'images local, processus persistant piloté par le serveur de l'éditeur.

    <python avec torch + diffusers> outils/image_worker.py --modele leger --accel cuda|cpu

Protocole : une requête JSON par ligne sur l'entrée standard, des événements JSON
par ligne sur la sortie standard (journaux et erreurs détaillées sur stderr) :

    → {"id": "j1", "prompt": "...", "negative": "...", "width": 912, "height": 512,
       "quality": "rapide", "seed": 123, "count": 1, "out_dir": "...", "stem": "ia_..."}
    ← {"event": "download", "progress": 0.42, "done_mo": 900, "total_mo": 2140}
    ← {"event": "ready", "device": "cpu", "dtype": "float32", "load_s": 12.4, ...}
    ← {"event": "progress", "id": "j1", "image": 0, "count": 1, "step": 2, "steps": 4}
    ← {"event": "done", "id": "j1", "files": [...], "seeds": [...], "seconds": 61.2, "s_par_etape_mpx": 33.1}
    ← {"event": "error", "id": "j1", "error": "..."}

Le modèle est chargé une fois ; chaque image ne coûte ensuite que le calcul.
Pièges vérifiés sur GTX 1650 : le décodeur d'image (VAE) donne des NaN (image
noire) en demi-précision → VAE complet en fp32 ; le décodeur compact TAESD
(« rapide ») n'a pas ce défaut et décode en 2 s au lieu de 33 s sur processeur.
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import sys
import threading
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from moteur.images import MODELES, QUALITES  # noqa: E402  (léger : pas de torch)

_out_lock = threading.Lock()


def emit(**ev):
    with _out_lock:
        sys.stdout.write(json.dumps(ev, ensure_ascii=False) + "\n")
        sys.stdout.flush()


def log(*a):
    print(*a, file=sys.stderr, flush=True)


def _taille(d: Path) -> int:
    return sum(f.stat().st_size for f in d.iterdir() if f.is_file()) if d.exists() else 0


def telecharger(spec) -> None:
    """Télécharge ce qui manque (rien si tout est en cache), en signalant la progression."""
    from huggingface_hub import HfApi, snapshot_download, try_to_load_from_cache
    from huggingface_hub.constants import HF_HUB_CACHE

    depots = [(spec["base"], spec["fichiers"]), (spec["lora"][0], [spec["lora"][1], "*.json"]),
              (spec["vae_rapide"], ["config.json", "diffusion_pytorch_model.safetensors"])]
    api, plan = HfApi(), []
    try:
        for repo, motifs in depots:
            info = api.model_info(repo, files_metadata=True)
            manque = [s for s in info.siblings if any(fnmatch.fnmatch(s.rfilename, p) for p in motifs)
                      and not isinstance(try_to_load_from_cache(repo, s.rfilename), str)]
            if manque:
                plan.append((repo, motifs, sum(s.size or 0 for s in manque)))
    except Exception as e:  # hors ligne : on tente avec le cache tel quel
        log("inventaire impossible :", e)
        return
    total = sum(p[2] for p in plan)
    if not plan:
        return
    blobs = [Path(HF_HUB_CACHE) / ("models--" + r.replace("/", "--")) / "blobs" for r, _, _ in plan]
    depart = {b: _taille(b) for b in blobs}
    fini = threading.Event()

    def suivre():
        while not fini.wait(0.7):
            fait = sum(max(0, _taille(b) - depart[b]) for b in blobs)
            emit(event="download", progress=round(min(fait / max(total, 1), 0.99), 3),
                 done_mo=round(fait / 1e6), total_mo=round(total / 1e6))

    threading.Thread(target=suivre, daemon=True).start()
    try:
        for repo, motifs, _ in plan:
            snapshot_download(repo, allow_patterns=motifs)
    finally:
        fini.set()
    emit(event="download", progress=1.0, done_mo=round(total / 1e6), total_mo=round(total / 1e6))


class Generateur:
    def __init__(self, spec, accel):
        import torch
        from diffusers import AutoencoderTiny, LCMScheduler, StableDiffusionPipeline

        t0 = time.time()
        self.torch = torch
        self.device = "cuda" if accel == "cuda" and torch.cuda.is_available() else "cpu"
        self.dtype = torch.float16 if self.device == "cuda" else torch.float32
        pipe = StableDiffusionPipeline.from_pretrained(
            # Poids fp16 (convertis en fp32 sur processeur). Pas de filtre de contenu : tout reste local.
            spec["base"], variant=spec.get("variant"), torch_dtype=self.dtype,
            safety_checker=None, requires_safety_checker=False, feature_extractor=None)
        pipe.scheduler = LCMScheduler.from_config(pipe.scheduler.config)
        repo, fn = spec["lora"]
        pipe.load_lora_weights(repo, weight_name=fn, adapter_name="lcm")
        pipe.fuse_lora()
        pipe.unload_lora_weights()
        pipe.to(self.device)
        if self.device == "cuda":
            pipe.enable_attention_slicing()          # 4 Go : la mémoire d'attention reste petite
            pipe.vae.to(torch.float32)               # VAE en fp16 → NaN (image noire) sur GTX 16xx
            pipe.vae.enable_tiling()
        pipe.set_progress_bar_config(disable=True)
        self.pipe = pipe
        self.tiny = AutoencoderTiny.from_pretrained(spec["vae_rapide"], torch_dtype=self.dtype).to(self.device)
        self.load_s = time.time() - t0

    def decoder(self, lat, rapide: bool):
        torch = self.torch
        with torch.no_grad():
            if rapide:
                img = self.tiny.decode(lat.to(self.dtype) / self.tiny.config.scaling_factor).sample
            else:
                vae = self.pipe.vae
                img = vae.decode(lat.to(vae.dtype) / vae.config.scaling_factor).sample
        img = img.float()
        if torch.isnan(img).any():
            raise RuntimeError("Décodage instable (NaN) : réessaie en qualité « rapide ».")
        return self.pipe.image_processor.postprocess(img, output_type="pil")[0]

    def generer(self, req):
        torch = self.torch
        t0 = time.time()
        count = max(1, min(int(req.get("count", 1)), 4))
        qualite = req.get("quality", "rapide")
        steps = QUALITES.get(qualite, 4)
        base_seed = int(req["seed"]) if req.get("seed") is not None else int(torch.randint(0, 2**31 - 1, (1,)).item())
        out_dir = Path(req["out_dir"])
        out_dir.mkdir(parents=True, exist_ok=True)
        w, h = int(req["width"]), int(req["height"])
        files, seeds, t_unet = [], [], 0.0
        for i in range(count):
            seed = base_seed + i
            gen = torch.Generator(device=self.device).manual_seed(seed)

            def cb(_pipe, step, _t, kw, i=i):
                emit(event="progress", id=req["id"], image=i, count=count, step=step + 1, steps=steps)
                return kw

            tu = time.time()
            lat = self.pipe(prompt=req["prompt"], negative_prompt=req.get("negative") or None,
                            width=w, height=h, num_inference_steps=steps,
                            guidance_scale=float(req.get("guidance", 1.0)), generator=gen,
                            output_type="latent", callback_on_step_end=cb).images
            t_unet += time.time() - tu
            img = self.decoder(lat, rapide=qualite == "rapide")
            if img.convert("L").getextrema()[1] < 8:
                raise RuntimeError("Image noire : calcul instable sur cette carte.")
            f = out_dir / f"{req.get('stem', 'ia')}_{seed}.png"
            img.save(f)
            files.append(str(f))
            seeds.append(seed)
        mpx = w * h / 1e6
        return {"files": files, "seeds": seeds, "seconds": round(time.time() - t0, 2),
                "s_par_etape_mpx": round(t_unet / (count * steps) / mpx, 2)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--modele", default="leger", choices=sorted(MODELES))
    ap.add_argument("--accel", default="cuda", choices=["cuda", "cpu"])
    a = ap.parse_args()
    spec = MODELES[a.modele]
    try:
        telecharger(spec)
        g = Generateur(spec, a.accel)
    except Exception as e:
        log(traceback.format_exc())
        emit(event="fatal", error=f"{type(e).__name__}: {e}"[-800:])
        return 1
    emit(event="ready", device=g.device, dtype=str(g.dtype).replace("torch.", ""), load_s=round(g.load_s, 1),
         modele=spec["base"])
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            continue
        if req.get("cmd") == "quit":
            break
        try:
            emit(event="done", id=req.get("id"), **g.generer(req))
        except Exception as e:
            log(traceback.format_exc())
            emit(event="error", id=req.get("id"), error=f"{type(e).__name__}: {e}"[-800:])
            if g.device == "cuda":
                g.torch.cuda.empty_cache()
    return 0


if __name__ == "__main__":
    sys.exit(main())
