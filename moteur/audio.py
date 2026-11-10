"""Son : zones de voix et baisse automatique de la musique sous la voix.

La baisse est écrite en images clés de volume sur les clips de musique :
visible et modifiable dans l'interface, identique à l'aperçu et à l'export.
"""
from __future__ import annotations


def zones_depuis_mots(mots, fusion=0.6):
    """Zones de parole [début, fin] (temps timeline) depuis la transcription de la séquence."""
    zones = []
    for m in sorted(mots, key=lambda m: m["start"]):
        if zones and m["start"] - zones[-1][1] <= fusion:
            zones[-1][1] = max(zones[-1][1], m["end"])
        else:
            zones.append([m["start"], m["end"]])
    return [(round(a, 3), round(b, 3)) for a, b in zones]


def zones_depuis_pics(clips_voix, pics, seuil=0.06, fusion=0.6, mini=0.15):
    """Zones où les clips de voix ont du son (forme d'onde), si pas de transcription.
    clips_voix : clips {start, duration, in, speed, media} ; pics : media → {per_sec, peaks}."""
    zones = []
    for c in clips_voix:
        pk = pics.get(c["media"])
        if not pk:
            continue
        ps, peaks = pk["per_sec"], pk["peaks"]
        n = int(c["duration"] * ps)
        actif = None
        for i in range(n + 1):
            t = c["start"] + i / ps
            src = c["in"] + (t - c["start"]) * c["speed"]
            j = int(src * ps)
            v = (peaks[j] if 0 <= j < len(peaks) else 0.0) * c["props"]["volume"]
            if v >= seuil and actif is None:
                actif = t
            elif v < seuil and actif is not None:
                zones.append([actif, t])
                actif = None
        if actif is not None:
            zones.append([actif, c["start"] + c["duration"]])
    zones.sort()
    out = []
    for a, b in zones:
        if out and a - out[-1][1] <= fusion:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [(round(a, 3), round(b, 3)) for a, b in out if b - a >= mini]


def cles_baisse(clip, zones, profondeur_db=-12.0, attaque=0.25, relache=0.5):
    """Images clés de volume (temps du clip) qui baissent la musique pendant les zones de voix."""
    base = clip["props"]["volume"]
    bas = round(base * 10 ** (profondeur_db / 20), 4)
    dur = clip["duration"]
    # Fusionner les zones trop proches pour remonter entre deux (évite le « pompage »).
    loc = []
    for a, b in zones:
        a, b = a - clip["start"], b - clip["start"]
        if b <= -relache or a >= dur + attaque:
            continue
        if loc and a - loc[-1][1] < attaque + relache:
            loc[-1][1] = max(loc[-1][1], b)
        else:
            loc.append([a, b])
    if not loc:
        return []
    keys = []
    for a, b in loc:
        for t, v, ease in ((a - attaque, base, "ease_in_out"), (a, bas, "linear"),
                           (b, bas, "ease_in_out"), (b + relache, base, "linear")):
            keys.append({"t": round(min(max(t, 0.0), dur), 4), "v": v, "ease": ease})
    # Deux clés au même instant : on garde la dernière (bord du clip).
    propres = {}
    for k in keys:
        propres[k["t"]] = k
    return sorted(propres.values(), key=lambda k: k["t"])


def ops_baisse(doc, music_ids, zones, profondeur_db=-12.0, attaque=0.25, relache=0.5):
    ops = []
    for cid in music_ids:
        c = doc["clips"][cid]
        keys = cles_baisse(c, zones, profondeur_db, attaque, relache)
        ops.append({"op": "set_keyframes", "clip": cid, "prop": "volume", "keys": keys})
    return ops
