"""Export : plan image par image, décodeurs FFmpeg exacts, mixage audio, encodeur.

Le rendu de chaque image est fait par l'interface (le même moteur WebGL que
l'aperçu). Le serveur lui fournit, pour chaque image, les pixels exacts des
vidéos actives — décodées en continu par FFmpeg, donc sans erreur d'image —
puis envoie l'image rendue à l'encodeur. Le son est mixé directement par
FFmpeg depuis les fichiers originaux, images clés de volume comprises.
"""
from __future__ import annotations

import math
import subprocess
from functools import lru_cache
from pathlib import Path

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
QUALITY = {"haute": (18, 19), "moyenne": (23, 25), "legere": (28, 30)}   # (crf x264, cq nvenc)


# ---------------------------------------------------------------- plan

def frame_count(t0: float, t1: float, fps: float) -> int:
    return max(1, math.ceil((t1 - t0) * fps - 1e-6))


def frame_time(t0: float, i: int, fps: float) -> float:
    return t0 + i / fps


def track_of(doc, tid):
    return next((t for t in doc["tracks"] if t["id"] == tid), None)


def video_clips_at(doc, t, sp=None):
    """Clips vidéo (média vidéo) visibles à l'instant t, y compris pendant une transition
    (le clip est alors prolongé dans la marge de sa source : sp = projet.spans(doc))."""
    from .projet import spans
    sp = sp or spans(doc)
    out = []
    for c in doc["clips"].values():
        a, b = sp[c["id"]]
        if c["type"] != "media" or not (a <= t < b):
            continue
        m = doc["media"].get(c["media"])
        tr = track_of(doc, c["track"])
        if m and m["kind"] == "video" and tr and tr["kind"] == "video" and not tr.get("hidden"):
            out.append(c)
    return out


def decode_size(doc, clip, out_scale: float) -> tuple[int, int]:
    """Taille de décodage : l'ajustement au cadre, agrandi si le clip est zoomé
    (jusqu'à ×2), sans jamais dépasser la résolution native de la source."""
    W, H = doc["settings"]["width"], doc["settings"]["height"]
    m = doc["media"][clip["media"]]
    sw, sh = m.get("width") or W, m.get("height") or H
    sa, fa = sw / sh, W / H
    dw, dh = (W, W / sa) if sa > fa else (H * sa, H)
    scales = [clip["props"]["scale"]] + [k["v"] for k in clip["keyframes"].get("scale", [])]
    k = min(2.0, max(1.0, max(scales))) * out_scale
    w, h = min(sw, dw * k), min(sh, dh * k)
    return max(2, int(round(w / 2)) * 2), max(2, int(round(h / 2)) * 2)


# ---------------------------------------------------------------- décodeur vidéo

class Decodeur:
    """Flux continu d'images à la cadence du projet pour un clip, à partir d'un instant source.
    fmt "yuv420p" (BT.709, plage TV) : 1,5 octet par pixel, converti en RVB par la carte graphique."""

    def __init__(self, path: str, ss: float, speed: float, fps: float, w: int, h: int, max_dur: float,
                 fmt: str = "yuv420p"):
        self.fmt = fmt
        self.size = w * h * 3 // 2 if fmt == "yuv420p" else w * h * 4
        self.w, self.h = w, h
        scale = f"scale={w}:{h}:flags=bicubic" + (":out_color_matrix=bt709:out_range=tv" if fmt == "yuv420p" else "")
        # Marge manquante avant le début de la source (transition) : on répète la
        # première image le temps qu'il faut, pour que la suite tombe pile au bon instant.
        pad = f"tpad=start_duration={-ss / speed:.6f}:start_mode=clone," if ss < 0 else ""
        vf = f"setpts=(PTS-STARTPTS)/{speed:.6f},{pad}fps={fps:.6f},{scale},format={fmt}"
        self.proc = subprocess.Popen(
            ["ffmpeg", "-v", "error", "-ss", f"{max(0.0, ss):.6f}", "-t", f"{max_dur * speed + 1:.3f}", "-i", path,
             "-an", "-vf", vf, "-f", "rawvideo", "-"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, creationflags=NO_WINDOW)
        self.last = bytes(self.size)

    def read(self) -> bytes:
        buf = bytearray()
        while len(buf) < self.size:
            chunk = self.proc.stdout.read(self.size - len(buf))
            if not chunk:
                return self.last  # fin de la source : on garde la dernière image
            buf += chunk
        self.last = bytes(buf)
        return self.last

    def close(self):
        try:
            self.proc.kill()
        except OSError:
            pass


# ---------------------------------------------------------------- audio

def _ease(u: str, ease: str) -> str:
    if ease == "ease_in":
        return f"({u}*{u})"
    if ease == "ease_out":
        return f"(1-(1-{u})*(1-{u}))"
    if ease == "ease_in_out":
        return f"if(lt({u},0.5),2*{u}*{u},1-pow(-2*{u}+2,2)/2)"
    return u


def volume_expr(keys, base: float, offset: float) -> str:
    """Expression FFmpeg du volume en fonction de t (temps du filtre), avec t + offset = temps du clip."""
    if not keys:
        return f"{base:.6f}"
    T = f"(t+{offset:.6f})"
    expr = f"{keys[-1]['v']:.6f}"
    for a, b in reversed(list(zip(keys, keys[1:]))):
        if a["ease"] == "hold" or b["t"] - a["t"] < 1e-9:
            seg = f"{a['v']:.6f}"
        else:
            u = f"(({T}-{a['t']:.6f})/{b['t'] - a['t']:.6f})"
            seg = f"({a['v']:.6f}+({b['v'] - a['v']:.6f})*{_ease(u, a['ease'])})"
        expr = f"if(lt({T},{b['t']:.6f}),{seg},{expr})"
    return f"if(lt({T},{keys[0]['t']:.6f}),{keys[0]['v']:.6f},{expr})"


def _atempo(speed: float) -> list[str]:
    out, s = [], speed
    while s > 2.0:
        out.append("atempo=2.0"); s /= 2.0
    while s < 0.5:
        out.append("atempo=0.5"); s /= 0.5
    if abs(s - 1.0) > 1e-6:
        out.append(f"atempo={s:.6f}")
    return out


def audio_graph(doc, t0: float, t1: float, first_input: int = 1):
    """Renvoie (fichiers d'entrée, filtergraph, étiquette de sortie) ou (liste vide, None, None) sans son."""
    inputs: list[str] = []
    parts: list[str] = []
    labels: list[str] = []
    total = t1 - t0
    for c in sorted(doc["clips"].values(), key=lambda c: c["start"]):
        if c["type"] != "media":
            continue
        m = doc["media"].get(c["media"])
        tr = track_of(doc, c["track"])
        if not m or not tr or tr.get("muted") or m["kind"] == "image":
            continue
        if not (m["kind"] == "audio" or m.get("has_audio")) or c.get("audio_muted"):
            continue
        a, b = max(c["start"], t0), min(c["start"] + c["duration"], t1)
        if b - a < 1e-4:
            continue
        if m["path"] not in inputs:
            inputs.append(m["path"])
        k = first_input + inputs.index(m["path"])
        src_in = c["in"] + (a - c["start"]) * c["speed"]
        offset = a - c["start"]
        # Mono : copié tel quel sur les deux canaux (la conversion par défaut le baisse de 3 dB,
        # alors que l'aperçu le joue à plein niveau).
        mono = ["aresample=48000", "pan=stereo|c0=c0|c1=c0"] if m.get("channels") == 1 else []
        chain = [f"atrim=start={src_in:.6f}:duration={(b - a) * c['speed']:.6f}", "asetpts=PTS-STARTPTS",
                 *mono, "aformat=sample_rates=48000:channel_layouts=stereo", *_atempo(c["speed"]),
                 f"volume='{volume_expr(c['keyframes'].get('volume', []), c['props']['volume'], offset)}':eval=frame",
                 f"adelay=delays={int(round((a - t0) * 1000))}:all=1"]
        lab = f"a{len(labels)}"
        parts.append(f"[{k}:a]" + ",".join(chain) + f"[{lab}]")
        labels.append(lab)
    if not labels:
        return [], None, None
    mix = "".join(f"[{l}]" for l in labels)
    parts.append(f"{mix}amix=inputs={len(labels)}:normalize=0:duration=longest,"
                 f"apad=whole_dur={total:.6f},atrim=duration={total:.6f}[aout]")
    return inputs, ";\n".join(parts), "aout"


# ---------------------------------------------------------------- encodeur

@lru_cache(maxsize=1)
def encodeurs_disponibles() -> list[str]:
    """x264 toujours ; NVENC seulement s'il démarre vraiment (pilote récent requis)."""
    out = ["x264"]
    try:
        r = subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=s=256x256:d=0.1",
                            "-c:v", "h264_nvenc", "-f", "null", "-"], capture_output=True, timeout=20,
                           creationflags=NO_WINDOW)
        if r.returncode == 0:
            out.append("nvenc")
    except (OSError, subprocess.TimeoutExpired):
        pass
    return out


def encoder_command(out: Path, w: int, h: int, fps: float, duration: float, quality: str, encoder: str,
                    audio_inputs: list[str], graph_file: Path | None, audio_label: str | None,
                    in_fmt: str = "rgba") -> list[str]:
    crf, cq = QUALITY.get(quality, QUALITY["haute"])
    cmd = ["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", in_fmt, "-s", f"{w}x{h}",
           "-r", f"{fps:.6f}"]
    if in_fmt == "yuv420p":
        # Étiqueter l'entrée, sinon FFmpeg la suppose BT.601 et « corrige » les couleurs.
        cmd += ["-colorspace", "bt709", "-color_range", "tv", "-color_primaries", "bt709", "-color_trc", "bt709"]
    cmd += ["-i", "pipe:0"]
    for p in audio_inputs:
        cmd += ["-i", p]
    if graph_file is not None:
        cmd += ["-/filter_complex", str(graph_file)]
    # readPixels lit de bas en haut : on retourne l'image.
    if in_fmt == "yuv420p":
        # L'interface a déjà converti en YUV BT.709, rangées dans l'ordre : rien à refaire.
        cmd += ["-map", "0:v"]
    else:
        cmd += ["-vf", "vflip,scale=out_color_matrix=bt709:out_range=tv,format=yuv420p", "-map", "0:v"]
    if audio_label:
        cmd += ["-map", f"[{audio_label}]", "-c:a", "aac", "-b:a", "192k"]
    if encoder == "nvenc":
        cmd += ["-c:v", "h264_nvenc", "-preset", "p5", "-rc", "vbr", "-cq", str(cq), "-b:v", "0"]
    else:
        cmd += ["-c:v", "libx264", "-preset", "medium", "-crf", str(crf)]
    cmd += ["-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
            "-t", f"{duration:.6f}", "-movflags", "+faststart", str(out)]
    return cmd


# ---------------------------------------------------------------- normalisation du volume (LUFS)

LOUDNESS = {"youtube": -14.0, "podcast": -16.0, "broadcast": -23.0}


def loudnorm_filtre(cible: float, mesure: dict | None = None) -> str:
    base = f"loudnorm=I={cible:.1f}:TP=-1.5:LRA=11"
    if mesure is None:
        return base + ":print_format=json"
    return (base + f":measured_I={mesure['input_i']}:measured_TP={mesure['input_tp']}"
            f":measured_LRA={mesure['input_lra']}:measured_thresh={mesure['input_thresh']}"
            f":offset={mesure['target_offset']}:linear=true:print_format=summary,aresample=48000")


def mesurer_loudness(doc, t0: float, t1: float, cible: float, graph_file: Path) -> dict | None:
    """Première passe : mesure le volume intégré du mixage. None si silence ou pas de son."""
    import json as _json
    inputs, graph, label = audio_graph(doc, t0, t1, first_input=0)
    if not graph:
        return None
    graph_file.write_text(graph + f";\n[{label}]{loudnorm_filtre(cible)}[ln]", "utf-8")
    cmd = ["ffmpeg", "-hide_banner", "-nostats"]
    for p in inputs:
        cmd += ["-i", p]
    cmd += ["-/filter_complex", str(graph_file), "-map", "[ln]", "-f", "null", "-"]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", creationflags=NO_WINDOW)
    txt = r.stderr
    i = txt.rfind("{")
    if r.returncode != 0 or i < 0:
        return None
    try:
        m = _json.loads(txt[i:txt.index("}", i) + 1])
        if float(m["input_i"]) < -70:   # silence : rien à normaliser
            return None
        return m
    except (ValueError, KeyError):
        return None
