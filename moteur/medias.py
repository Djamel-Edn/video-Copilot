"""Analyse des médias (ffprobe) et proxys légers pour l'aperçu."""
from __future__ import annotations

import json
import subprocess
from fractions import Fraction
from pathlib import Path

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"}
BROWSER_AUDIO = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".opus"}
BROWSER_VIDEO_CODECS = {"h264", "vp8", "vp9", "av1"}
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def probe(path: Path) -> dict:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
        capture_output=True, text=True, check=True, creationflags=NO_WINDOW)
    info = json.loads(out.stdout)
    streams = info.get("streams", [])
    v = next((s for s in streams if s.get("codec_type") == "video"
              and not s.get("disposition", {}).get("attached_pic")), None)
    a = next((s for s in streams if s.get("codec_type") == "audio"), None)
    ext = path.suffix.lower()
    if v is not None and (ext in IMAGE_EXT or info["format"].get("format_name", "").endswith("_pipe")):
        kind = "image"
    elif v is not None:
        kind = "video"
    elif a is not None:
        kind = "audio"
    else:
        raise ValueError(f"Aucun flux audio ni vidéo dans {path.name}.")
    m = {"name": path.name, "path": str(path.resolve()), "kind": kind,
         "duration": float(info["format"].get("duration") or 0.0) if kind != "image" else 0.0,
         "has_audio": a is not None, "proxy": None}
    if v is not None:
        m["width"], m["height"] = int(v["width"]), int(v["height"])
        try:
            m["fps"] = float(Fraction(v.get("avg_frame_rate") or v.get("r_frame_rate") or "30/1"))
        except (ZeroDivisionError, ValueError):
            m["fps"] = 30.0
        m["codec"] = v.get("codec_name")
    m["browser_ok"] = (
        kind == "image"
        or (kind == "audio" and ext in BROWSER_AUDIO)
        or (kind == "video" and m.get("codec") in BROWSER_VIDEO_CODECS and ext in {".mp4", ".webm", ".mov", ".m4v"})
    )
    return m


def proxy_command(src: Path, dst: Path, kind: str) -> list[str]:
    if kind == "audio":
        return ["ffmpeg", "-y", "-v", "error", "-i", str(src), "-vn", "-c:a", "aac", "-b:a", "160k", str(dst)]
    # 540p, une image clé par image : le défilement et les sauts sont instantanés.
    return ["ffmpeg", "-y", "-v", "error", "-i", str(src),
            "-vf", "scale=-2:'min(540,ih)'", "-c:v", "libx264", "-preset", "veryfast", "-crf", "24",
            "-g", "1", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k",
            "-movflags", "+faststart", str(dst)]


def make_proxy(src: Path, dst: Path, kind: str) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(dst.stem + ".part" + dst.suffix)
    subprocess.run(proxy_command(src, tmp, kind), check=True, capture_output=True, creationflags=NO_WINDOW)
    tmp.replace(dst)


PEAKS_PER_SEC = 100


def vignette(src: Path, dst: Path, duration: float) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    t = min(1.0, duration / 2)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{t:.3f}", "-i", str(src), "-frames:v", "1",
                    "-vf", "scale=320:-2", "-q:v", "4", str(dst)], check=True, capture_output=True,
                   creationflags=NO_WINDOW)


def forme_onde(src: Path, dst: Path) -> None:
    """Pics d'amplitude (0-1), PEAKS_PER_SEC valeurs par seconde, pour dessiner l'onde."""
    import array
    rate = 8000
    out = subprocess.run(["ffmpeg", "-v", "error", "-i", str(src), "-vn", "-ac", "1", "-ar", str(rate),
                          "-f", "s16le", "-"], capture_output=True, check=True, creationflags=NO_WINDOW).stdout
    a = array.array("h")
    a.frombytes(out[: len(out) // 2 * 2])
    step = rate // PEAKS_PER_SEC
    peaks = [round(max((abs(x) for x in a[i:i + step]), default=0) / 32768, 3) for i in range(0, len(a), step)]
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(json.dumps({"per_sec": PEAKS_PER_SEC, "peaks": peaks}), "utf-8")


_STYLES = {"bold", "italic", "light", "semibold", "semilight", "black", "medium", "thin", "extralight",
           "extrabold", "ultralight", "ultrabold", "regular", "oblique", "condensed", "heavy", "demibold",
           "book", "narrow", "semicondensed", "variable", "roman", "gras", "italique", "léger", "demigras",
           "noir", "moyen", "fin", "étroit", "condensé", "normal", "oblique", "extragras", "semigras"}


def polices_systeme() -> list[str]:
    """Familles de polices installées sous Windows (registre), sans les variantes de graisse."""
    import re
    try:
        import winreg
    except ImportError:
        return ["Arial", "Georgia", "Verdana"]
    names = set()
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        try:
            k = winreg.OpenKey(hive, r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts")
        except OSError:
            continue
        i = 0
        while True:
            try:
                name = winreg.EnumValue(k, i)[0]
            except OSError:
                break
            i += 1
            name = re.sub(r"\s*\((TrueType|OpenType|All res|VGA res)\)\s*$", "", name)
            for part in name.split(" & "):
                words = [w for w in part.split() if w.lower().replace("-", "") not in _STYLES]
                fam = " ".join(words).strip()
                if fam and not fam[0].isdigit():
                    names.add(fam)
    return sorted(names, key=str.lower)
