"""Images d'illustration générées en local : modèles, formats, styles, placement.

Le calcul se fait dans un processus à part (outils/image_worker.py, environnement
avec torch + diffusers). Ce module reste léger : il décrit les modèles, prépare
les requêtes et les opérations de timeline, et sait si les poids sont déjà là.
"""
from __future__ import annotations

import math
import os
from pathlib import Path

from .gpu import Choix

# Modèles branchés. Seul le niveau « léger » est implémenté et vérifié (GTX 1650 4 Go) ;
# il tourne sur toute carte NVIDIA ≥ 3,5 Go, et sur processeur (lent).
MODELES = {
    "leger": {
        "label": "DreamShaper 8 + LCM (4 étapes)",
        "base": "Lykon/dreamshaper-8",
        "variant": "fp16",
        "fichiers": ["model_index.json", "scheduler/*", "tokenizer/*", "feature_extractor/*",
                     "text_encoder/config.json", "text_encoder/model.fp16.safetensors",
                     "unet/config.json", "unet/diffusion_pytorch_model.fp16.safetensors",
                     "vae/config.json", "vae/diffusion_pytorch_model.fp16.safetensors"],
        "lora": ["latent-consistency/lcm-lora-sdv1-5", "pytorch_lora_weights.safetensors"],
        "vae_rapide": "madebyollin/taesd",   # décodeur compact (MIT) : 2 s au lieu de 33 s sur processeur
        "poids_go": 2.3,
        "licence": "CreativeML OpenRAIL-M",
        "aire": 912 * 512,          # pixels par image (SD 1.5 : ~0,47 Mpx, au-delà les sujets se dédoublent)
    },
}

# Suffixes de prompt : ils fixent le rendu sans que l'utilisateur ait à connaître les formules.
STYLES = {
    "illustration": ("Illustration", "digital illustration, clean lines, vibrant colors, highly detailed"),
    "photo": ("Photo", "professional photograph, natural light, sharp focus, 35mm, realistic"),
    "cinema": ("Cinéma", "cinematic film still, dramatic lighting, shallow depth of field, film grain"),
    "3d": ("3D", "3d render, soft studio lighting, octane render, highly detailed"),
    "plat": ("Plat / vectoriel", "flat vector illustration, minimal, simple shapes, solid colors"),
    "aquarelle": ("Aquarelle", "watercolor painting, soft washes, paper texture"),
    "aucun": ("Aucun (prompt brut)", ""),
}
NEGATIF = "lowres, blurry, worst quality, jpeg artifacts, watermark, signature, text, deformed, disfigured"

FORMATS = {"sequence": None, "16:9": 16 / 9, "9:16": 9 / 16, "1:1": 1.0, "4:5": 4 / 5}
QUALITES = {"rapide": 4, "fine": 6}   # étapes ; « fine » décode aussi avec le VAE complet


def choisir_calcul(pref: str, gpus: list, perf: dict) -> str:
    """Carte graphique ou processeur. `perf` : secondes par étape et par mégapixel mesurées
    sur CETTE machine ({"cuda": 229, "cpu": 31}). Une carte qui chauffe et se bride peut être
    plus lente que le processeur : en mode auto, la mesure décide."""
    cuda_ok = any(g.vendor == "nvidia" and g.vram_mo >= 3500 for g in gpus)
    if pref == "cpu" or not cuda_ok:
        return "cpu"
    if pref == "cuda":
        return "cuda"
    c, p = (perf.get("cuda") or {}).get("v"), (perf.get("cpu") or {}).get("v")
    if c and p:
        return "cuda" if c <= p else "cpu"
    return "cuda"


def estimation(perf: dict, calcul: str, w: int, h: int, qualite: str = "rapide", count: int = 1) -> float | None:
    """Durée prévue en secondes (calcul seul), si une mesure existe pour ce calcul."""
    v = (perf.get(calcul) or {}).get("v")
    if not v:
        return None
    decode = 0 if qualite == "rapide" else (33 if calcul == "cpu" else 5) * (w * h) / (912 * 512)
    return count * (v * QUALITES.get(qualite, 4) * w * h / 1e6 + decode + 2)


def modele_pour(choix: Choix | None) -> str:
    """Le choix du GPU désigne un niveau ; seul « leger » est branché pour l'instant."""
    return "leger"


def taille(doc: dict, aspect: str = "sequence", modele: str = "leger") -> tuple[int, int]:
    """Largeur × hauteur (multiples de 8) d'aire constante, au rapport demandé."""
    if aspect not in FORMATS:
        raise ValueError(f"Format inconnu : {aspect!r}. Possibles : {list(FORMATS)}.")
    r = FORMATS[aspect] or doc["settings"]["width"] / doc["settings"]["height"]
    aire = MODELES[modele]["aire"]
    w = math.sqrt(aire * r)
    return max(256, round(w / 8) * 8), max(256, round(w / r / 8) * 8)


def prompt_complet(prompt: str, style: str = "illustration") -> str:
    prompt = (prompt or "").strip()
    if not prompt:
        raise ValueError("Décris l'image à générer.")
    if style not in STYLES:
        raise ValueError(f"Style inconnu : {style!r}. Possibles : {list(STYLES)}.")
    suffixe = STYLES[style][1]
    return f"{prompt}, {suffixe}" if suffixe else prompt


def cache_hf() -> Path:
    if os.environ.get("HF_HUB_CACHE"):
        return Path(os.environ["HF_HUB_CACHE"])
    home = os.environ.get("HF_HOME") or str(Path.home() / ".cache" / "huggingface")
    return Path(home) / "hub"


def poids_presents(modele: str = "leger") -> bool:
    spec = MODELES[modele]
    def present(repo, rel):
        snaps = cache_hf() / ("models--" + repo.replace("/", "--")) / "snapshots"
        return snaps.exists() and any((s / rel).is_file() for s in snaps.iterdir())
    return (present(spec["base"], "unet/diffusion_pytorch_model.fp16.safetensors")
            and present(spec["base"], "text_encoder/model.fp16.safetensors")
            and present(*spec["lora"])
            and present(spec["vae_rapide"], "diffusion_pytorch_model.safetensors"))


def piste_libre(doc: dict, a: float, b: float) -> str | None:
    """Piste vidéo déverrouillée, au-dessus de la première, libre entre a et b (comme l'interface)."""
    vids = [t for t in doc["tracks"] if t["kind"] == "video" and not t.get("locked") and t.get("role") != "subtitles"]
    for t in vids[1:]:
        if not any(c["track"] == t["id"] and c["start"] < b - 1e-6 and c["start"] + c["duration"] > a + 1e-6
                   for c in doc["clips"].values()):
            return t["id"]
    return None


def ops_placer(doc: dict, mids: list[str], at: float, duree: float = 4.0, piste: str | None = None,
               zoom: bool = True) -> list[dict]:
    """Pose les images l'une après l'autre à partir de `at`, avec un zoom lent si demandé."""
    ops: list[dict] = []
    total = duree * len(mids)
    track = piste or piste_libre(doc, at, at + total)
    if track is None:
        op = {"op": "add_track", "kind": "video", "name": "Illustrations"}
        subs = next((t for t in doc["tracks"] if t.get("role") == "subtitles"), None)
        if subs:
            op["before"] = subs["id"]      # les sous-titres restent par-dessus les illustrations
        ops.append(op)
        track = "$0"
    for i, mid in enumerate(mids):
        op = {"op": "add_clip", "media": mid, "track": track, "start": round(at + i * duree, 4), "duration": duree}
        if zoom:
            # Zoom lent (Ken Burns) : l'image vit, et 1,02 couvre l'éventuel liseré d'arrondi.
            op["props"] = {"scale": 1.02}
        ops.append(op)
        if zoom:
            ops.append({"op": "set_keyframes", "clip": f"${len(ops) - 1}", "prop": "scale",
                        "keys": [{"t": 0, "v": 1.02, "ease": "linear"}, {"t": duree, "v": 1.1}]})
    return ops
