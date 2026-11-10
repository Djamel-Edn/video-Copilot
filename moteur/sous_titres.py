"""Sous-titres automatiques : les mots de la séquence regroupés en sous-titres,
posés comme clips texte ordinaires sur une piste « Sous-titres » (donc
modifiables, animables et exportés par le même moteur), plus l'export SRT.
"""
from __future__ import annotations

STYLES = {
    "classique": {
        "label": "Classique (bas de l'écran, fond)", "y": 0.37, "max_chars": 42, "max_lines": 2, "max_words": None,
        "text": {"size": 50, "weight": 700, "color": "#ffffff", "background": "#000000", "background_opacity": 0.6,
                 "padding": 14, "radius": 6, "line_height": 1.25},
    },
    "shorts": {
        "label": "Shorts (gros, 1 à 3 mots)", "y": 0.16, "max_chars": 16, "max_lines": 1, "max_words": 3,
        "text": {"size": 92, "weight": 900, "uppercase": True, "color": "#ffffff", "stroke_color": "#000000",
                 "stroke_width": 7, "shadow_opacity": 0.5, "shadow_blur": 16, "shadow_y": 6},
        "keys": {"scale": [{"t": 0.0, "v": 1.18, "ease": "ease_out"}, {"t": 0.09, "v": 1.0}]},
    },
    "minimal": {
        "label": "Minimal (blanc, ombre douce)", "y": 0.38, "max_chars": 42, "max_lines": 2, "max_words": None,
        "text": {"size": 46, "weight": 600, "color": "#ffffff", "shadow_opacity": 0.85, "shadow_blur": 10,
                 "shadow_y": 3, "line_height": 1.25},
    },
}

FIN_PHRASE = (".", "!", "?", "…")
COLLE = ("'", "’", ",", ".", "!", "?", ";", ":", "…", ")", "-")


def joindre(mots: list[str]) -> str:
    """Recolle les mots de Whisper : « n » + « 'a » → « n'a », pas d'espace avant la ponctuation."""
    out = ""
    for w in mots:
        w = w.strip()
        if not w:
            continue
        if out and not w.startswith(COLLE) and not out.endswith(("'", "’", "-", "(")):
            out += " "
        out += w
    return out


def decouper(mots, max_chars=42, max_lines=2, max_words=None, max_dur=4.0, pause=0.6):
    """Regroupe les mots (avec start / end timeline) en sous-titres {start, end, text}."""
    caps, lignes, ligne, debut, fin, n = [], [], [], None, None, 0

    def fermer():
        nonlocal lignes, ligne, debut, fin, n
        if ligne:
            lignes.append(ligne)
        if lignes:
            caps.append({"start": debut, "end": fin, "text": "\n".join(joindre(l) for l in lignes)})
        lignes, ligne, debut, fin, n = [], [], None, None, 0

    for m in mots:
        w = m["w"].strip()
        if not w:
            continue
        if debut is not None:
            trop_long = m["end"] - debut > max_dur
            blanc = m["start"] - fin > pause
            if trop_long or blanc or (max_words and n >= max_words):
                fermer()
        if debut is not None and len(joindre(ligne + [w])) > max_chars and not w.startswith(COLLE):
            if len(lignes) + 1 >= max_lines:
                fermer()
            else:
                lignes.append(ligne)
                ligne = []
        if debut is None:
            debut = m["start"]
        ligne.append(w)
        fin = m["end"]
        n += 1
        if w.endswith(FIN_PHRASE):
            fermer()
    fermer()
    # Rester un peu plus longtemps à l'écran, sans jamais chevaucher le suivant.
    for c, nxt in zip(caps, caps[1:] + [None]):
        c["end"] = max(c["end"] + 0.15, c["start"] + 0.4)
        if nxt is not None:
            c["end"] = min(c["end"], nxt["start"])
        c["start"], c["end"] = round(c["start"], 3), round(c["end"], 3)
    return [c for c in caps if c["end"] - c["start"] > 0.05]


def piste(doc):
    return next((t for t in doc["tracks"] if t.get("role") == "subtitles"), None)


def ops(doc, caps, style="classique", replace=True, start=0.0, end=None):
    """Opérations : piste dédiée (créée au besoin), anciens sous-titres de la plage retirés, nouveaux posés."""
    st = STYLES[style]
    W, H = doc["settings"]["width"], doc["settings"]["height"]
    k = min(W, H) / 1080
    tr = piste(doc)
    out = []
    if tr is None:
        out.append({"op": "add_track", "kind": "video", "name": "Sous-titres", "role": "subtitles"})
        tid = "$0"
    else:
        tid = tr["id"]
        if replace:
            out += [{"op": "delete_clip", "clip": c["id"]} for c in doc["clips"].values()
                    if c["track"] == tid and c["start"] + c["duration"] > start and (end is None or c["start"] < end)]
    text = {**st["text"]}
    for f in ("size", "padding", "stroke_width", "shadow_blur", "shadow_y", "radius"):
        if f in text:
            text[f] = round(text[f] * k, 1)
    for c in caps:
        i = len(out)
        out.append({"op": "add_clip", "type": "text", "track": tid, "start": c["start"],
                    "duration": round(c["end"] - c["start"], 3), "name": c["text"].replace("\n", " ")[:40],
                    "text": {**text, "content": c["text"]}, "props": {"y": round(st["y"] * H)}})
        for prop, keys in st.get("keys", {}).items():
            out.append({"op": "set_keyframes", "clip": f"${i}", "prop": prop, "keys": keys})
    return out


def _ts(t):
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def srt(doc) -> str:
    tr = piste(doc)
    if tr is None:
        return ""
    clips = sorted((c for c in doc["clips"].values() if c["track"] == tr["id"] and c["type"] == "text"),
                   key=lambda c: c["start"])
    blocs = [f"{i}\n{_ts(c['start'])} --> {_ts(c['start'] + c['duration'])}\n{c['text']['content']}\n"
             for i, c in enumerate(clips, 1)]
    return "\n".join(blocs)
