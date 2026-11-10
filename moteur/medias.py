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
         "has_audio": a is not None, "channels": int(a.get("channels") or 2) if a else 0, "proxy": None}
    if v is not None:
        m["width"], m["height"] = int(v["width"]), int(v["height"])
        # Vidéos de téléphone : l'image est stockée couchée avec une consigne « tourner de 90° ».
        # FFmpeg et le navigateur la redressent au décodage ; on enregistre donc les
        # dimensions telles qu'elles s'affichent.
        m["rotation"] = rotation(v)
        if m["rotation"] in (90, 270):
            m["width"], m["height"] = m["height"], m["width"]
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


def rotation(stream: dict) -> int:
    """Rotation d'affichage en degrés (0, 90, 180, 270), depuis la matrice d'affichage ou l'ancien tag."""
    rot = 0.0
    for sd in stream.get("side_data_list") or []:
        if "rotation" in sd:
            rot = float(sd["rotation"])
            break
    else:
        try:
            rot = float((stream.get("tags") or {}).get("rotate", 0))
        except ValueError:
            rot = 0.0
    return int(round(rot / 90.0)) * 90 % 360


def proxy_command(src: Path, dst: Path, kind: str) -> list[str]:
    if kind == "audio":
        return ["ffmpeg", "-y", "-v", "error", "-i", str(src), "-vn", "-c:a", "aac", "-b:a", "160k", str(dst)]
    # Petit côté à 540 px (paysage comme portrait), une image clé par image : le
    # défilement et les sauts sont instantanés. FFmpeg redresse les vidéos tournées.
    return ["ffmpeg", "-y", "-v", "error", "-i", str(src),
            "-vf", "scale='if(gt(iw,ih),-2,min(540,iw))':'if(gt(iw,ih),min(540,ih),-2)'", "-c:v", "libx264", "-preset", "veryfast", "-crf", "24",
            "-g", "1", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k",
            "-movflags", "+faststart", str(dst)]


def chemin_proxy(dossier: Path, mid: str, kind: str) -> Path:
    return dossier / "proxies" / (f"{mid}.m4a" if kind == "audio" else f"{mid}.mp4")


def besoin_proxy(m: dict) -> bool:
    """Même règle qu'à l'import : toute vidéo, et le son que le navigateur ne lit pas."""
    return m["kind"] == "video" or (m["kind"] == "audio" and not m.get("browser_ok"))


def proxys_a_reprendre(doc: dict, dossier: Path) -> tuple[dict[str, str], list[str]]:
    """Proxys perdus par un arrêt du serveur, à reprendre au démarrage.

    Renvoie ({média: chemin relatif d'un proxy déjà fini à relier}, [médias à refaire]).
    Un proxy s'écrit dans « .part » puis est renommé : un fichier final présent est donc
    complet, il manquait seulement l'enregistrement du projet (sauvegarde regroupée de 0,3 s).
    Un proxy noté en échec (proxy_error) n'est pas relancé : il échouerait à chaque démarrage.
    """
    a_relier, a_refaire = {}, []
    for mid, m in doc["media"].items():
        if not besoin_proxy(m) or m.get("proxy_error") or not Path(m["path"]).exists():
            continue
        if m.get("proxy") and (dossier / m["proxy"]).exists():
            continue
        fini = chemin_proxy(dossier, mid, m["kind"])
        if not m.get("proxy") and fini.exists():
            a_relier[mid] = str(fini.relative_to(dossier))
        else:
            a_refaire.append(mid)
    return a_relier, a_refaire


def nettoyer_partiels(dossier: Path) -> int:
    """Supprime les proxys à moitié écrits (« m3.part.mp4 ») laissés par un arrêt brutal."""
    n = 0
    for f in (dossier / "proxies").glob("*.part.*"):
        try:
            f.unlink()
            n += 1
        except OSError:
            pass
    return n


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
