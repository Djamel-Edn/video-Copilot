"""Montage par le texte : les mots de la transcription replacés sur la timeline,
et les plages à couper (hésitations, silences, sélection de mots).

Les mots d'un média sont en temps SOURCE. Un clip montre la portion
[in, in + durée × vitesse] de sa source : un mot y apparaît au temps timeline
start + (t_source − in) / vitesse. Les mots déjà coupés disparaissent donc tout
seuls de la transcription de la séquence.
"""
from __future__ import annotations

import re
import unicodedata

HESITATIONS = {"euh", "heu", "euuh", "euhh", "heuu", "hum", "hmm", "hm", "mmh", "mh", "bah", "ben", "eh", "ah", "uh", "um"}


def norm(w: str) -> str:
    w = unicodedata.normalize("NFD", w.lower())
    w = "".join(c for c in w if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9]", "", w)


def mots_timeline(doc: dict, transcripts: dict[str, list[dict]]) -> list[dict]:
    """Mots visibles dans la séquence, triés : {w, start, end, p, clip, media}."""
    out, vus = [], set()
    for c in sorted(doc["clips"].values(), key=lambda c: c["start"]):
        if c["type"] != "media" or c["media"] not in transcripts:
            continue
        a, b = c["in"], c["in"] + c["duration"] * c["speed"]
        for w in transcripts[c["media"]]:
            mid = (w["s"] + w["e"]) / 2
            if not (a <= mid < b):
                continue
            s = c["start"] + (max(w["s"], a) - a) / c["speed"]
            e = c["start"] + (min(w["e"], b) - a) / c["speed"]
            key = (c["media"], w["s"], round(s, 2))
            if key in vus:          # même mot vu par un clip vidéo et son clip audio
                continue
            vus.add(key)
            out.append({"w": w["w"], "start": round(s, 3), "end": round(e, 3), "p": w.get("p", 1.0),
                        "clip": c["id"], "media": c["media"]})
    out.sort(key=lambda m: m["start"])
    return out


def fusionner(plages, ecart=1e-3):
    res = []
    for a, b in sorted(plages):
        if b - a <= 1e-4:
            continue
        if res and a <= res[-1][1] + ecart:
            res[-1][1] = max(res[-1][1], b)
        else:
            res.append([a, b])
    return [(round(a, 4), round(b, 4)) for a, b in res]


def plage_mots(mots, i, j, marge=0.03):
    """Plage timeline couvrant les mots i..j inclus, avec une marge bornée par les voisins."""
    a = mots[i]["start"] - marge
    b = mots[j]["end"] + marge
    if i > 0:
        a = max(a, (mots[i - 1]["end"] + mots[i]["start"]) / 2)
    if j + 1 < len(mots):
        b = min(b, (mots[j]["end"] + mots[j + 1]["start"]) / 2)
    return max(0.0, a), b


def plages_hesitations(mots, marge=0.03):
    return fusionner([plage_mots(mots, i, i, marge) for i, m in enumerate(mots) if norm(m["w"]) in HESITATIONS])


def plages_silences(mots, seuil=0.7, garder=0.25):
    """Raccourcit chaque blanc de plus de `seuil` s entre deux mots à `garder` s."""
    res = []
    for m, n in zip(mots, mots[1:]):
        gap = n["start"] - m["end"]
        if gap > seuil:
            res.append((m["end"] + garder / 2, n["start"] - garder / 2))
    return fusionner(res)


def ops_couper(plages, tracks=None):
    """Opérations cut_range de la fin vers le début : chaque coupe ne décale pas les suivantes."""
    ops = []
    for a, b in sorted(plages, reverse=True):
        op = {"op": "cut_range", "start": a, "end": b, "ripple": True}
        if tracks:
            op["tracks"] = tracks
        ops.append(op)
    return ops
