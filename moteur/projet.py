"""Document timeline, opérations validées, transactions, annuler / rétablir.

Toute modification du projet — souris ou IA — passe par `Projet.appliquer`,
qui reçoit une liste d'opérations de haut niveau. Chaque opération est
traduite en correctifs élémentaires (`set` / `del` sur un chemin JSON),
appliqués immédiatement, avec leur inverse. Un lot qui échoue est annulé
entièrement. Un lot réussi forme une seule étape d'annulation.
"""
from __future__ import annotations

import copy
import math
import re

from .effets import BUILTIN

EPS = 1e-6
ANIM_PROPS = ("x", "y", "scale", "rotation", "opacity", "volume")
DEFAULT_PROPS = {"x": 0.0, "y": 0.0, "scale": 1.0, "rotation": 0.0, "opacity": 1.0, "volume": 1.0}
DEFAULT_TEXT = {
    "content": "Titre", "font": "Inter, Segoe UI, Arial, sans-serif", "size": 96, "weight": 700,
    "color": "#ffffff", "align": "center", "line_height": 1.2,
    "stroke_color": "#000000", "stroke_width": 0, "background": None, "padding": 24,
    "italic": False, "letter_spacing": 0, "uppercase": False,
    "shadow_color": "#000000", "shadow_opacity": 0.0, "shadow_blur": 12, "shadow_x": 0, "shadow_y": 4,
    "background_opacity": 1.0, "radius": 8,
}
TEXT_NUM = {"size": (1, None), "weight": (100, 900), "line_height": (0.5, 4), "stroke_width": (0, None),
            "padding": (0, None), "letter_spacing": (None, None), "shadow_opacity": (0, 1),
            "shadow_blur": (0, None), "shadow_x": (None, None), "shadow_y": (None, None),
            "background_opacity": (0, 1), "radius": (0, None)}
EASES = ("linear", "ease_in", "ease_out", "ease_in_out", "hold")
MAX_GLSL = 20_000
_NAME = re.compile(r"^[a-z][a-z0-9_]{1,40}$")
_PARAM = re.compile(r"^[a-z][a-z0-9_]{0,30}$")
_MISSING = object()


class OpError(Exception):
    """Opération refusée. Le message est fait pour être lu par l'IA."""


# ---------------------------------------------------------------- document

def nouveau_document(width=1920, height=1080, fps=30) -> dict:
    return {
        "version": 1,
        "settings": {"width": width, "height": height, "fps": fps, "background": "#000000"},
        "seq": 0,
        "media": {},
        "effects": {},
        "tracks": [
            {"id": "V1", "kind": "video", "name": "V1", "hidden": False, "muted": False, "locked": False},
            {"id": "V2", "kind": "video", "name": "V2", "hidden": False, "muted": False, "locked": False},
            {"id": "V3", "kind": "video", "name": "V3", "hidden": False, "muted": False, "locked": False},
            {"id": "A1", "kind": "audio", "name": "A1", "hidden": False, "muted": False, "locked": False},
            {"id": "A2", "kind": "audio", "name": "A2", "hidden": False, "muted": False, "locked": False},
        ],
        "clips": {},
    }


def duree_totale(doc) -> float:
    return max((c["start"] + c["duration"] for c in doc["clips"].values()), default=0.0)


# ---------------------------------------------------------------- correctifs

def _get(doc, path):
    cur = doc
    for k in path:
        if not isinstance(cur, dict) or k not in cur:
            return _MISSING
        cur = cur[k]
    return cur


def _parent(doc, path):
    cur = doc
    for k in path[:-1]:
        cur = cur[k]
    return cur


def appliquer_correctif(doc, p):
    parent = _parent(doc, p["path"])
    if p["op"] == "set":
        parent[p["path"][-1]] = copy.deepcopy(p["value"])
    elif p["op"] == "del":
        parent.pop(p["path"][-1], None)
    else:
        raise ValueError(p["op"])


class Transaction:
    def __init__(self, doc):
        self.doc = doc
        self.fwd: list[dict] = []
        self.inv: list[dict] = []
        self.links: dict = {}

    def link_for(self, key):
        """Même nouvel identifiant de lien pour tous les clips d'un groupe touchés dans ce lot."""
        if key not in self.links:
            self.links[key] = self.new_id("lk")
        return self.links[key]

    def set(self, path, value):
        path = list(path)
        old = _get(self.doc, path)
        self.inv.append({"op": "del", "path": path} if old is _MISSING
                        else {"op": "set", "path": path, "value": copy.deepcopy(old)})
        p = {"op": "set", "path": path, "value": copy.deepcopy(value)}
        appliquer_correctif(self.doc, p)
        self.fwd.append(p)

    def delete(self, path):
        path = list(path)
        old = _get(self.doc, path)
        if old is _MISSING:
            return
        self.inv.append({"op": "set", "path": path, "value": copy.deepcopy(old)})
        p = {"op": "del", "path": path}
        appliquer_correctif(self.doc, p)
        self.fwd.append(p)

    def rollback(self):
        for p in reversed(self.inv):
            appliquer_correctif(self.doc, p)
        self.fwd, self.inv = [], []

    def new_id(self, prefix):
        n = self.doc["seq"] + 1
        self.set(["seq"], n)
        return f"{prefix}{n}"


# ---------------------------------------------------------------- validation

def _num(v, name, lo=None, hi=None, strict_lo=False):
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        raise OpError(f"« {name} » doit être un nombre (reçu {v!r}).")
    v = float(v)
    if lo is not None and (v <= lo if strict_lo else v < lo):
        raise OpError(f"« {name} » doit être {'>' if strict_lo else '≥'} {lo} (reçu {v}).")
    if hi is not None and v > hi:
        raise OpError(f"« {name} » doit être ≤ {hi} (reçu {v}).")
    return v


def _hex_to_rgb(s):
    m = re.fullmatch(r"#?([0-9a-fA-F]{6})", s.strip())
    if not m:
        raise OpError(f"Couleur invalide : {s!r} (attendu #rrggbb ou [r, g, b] entre 0 et 1).")
    h = m.group(1)
    return [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]


def _color(v, name):
    if isinstance(v, str):
        return _hex_to_rgb(v)
    if isinstance(v, list) and len(v) == 3:
        return [_num(x, name, 0, 1) for x in v]
    raise OpError(f"« {name} » doit être une couleur #rrggbb ou [r, g, b] entre 0 et 1.")


def _hex(v, name):
    if not isinstance(v, str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", v):
        raise OpError(f"« {name} » doit être une couleur #rrggbb.")
    return v


def _clip(doc, cid):
    c = doc["clips"].get(cid)
    if c is None:
        raise OpError(f"Clip inconnu : {cid!r}. Clips existants : {sorted(doc['clips'])[:30]}.")
    return c


def _track(doc, tid):
    for t in doc["tracks"]:
        if t["id"] == tid:
            return t
    raise OpError(f"Piste inconnue : {tid!r}. Pistes : {[t['id'] for t in doc['tracks']]}.")


def _effect_def(doc, name):
    d = BUILTIN.get(name) or doc["effects"].get(name)
    if d is None:
        raise OpError(f"Effet inconnu : {name!r}. Intégrés : {sorted(BUILTIN)} ; "
                      f"sur mesure : {sorted(doc['effects'])}. Crée-le d'abord avec define_effect.")
    return d


def _clip_kind(doc, c):
    if c["type"] == "media":
        kind = doc["media"][c["media"]]["kind"]
        # Le son d'une vidéo posé sur une piste audio (« son séparé ») est un clip audio.
        tr = next((t for t in doc["tracks"] if t["id"] == c["track"]), None)
        if kind == "video" and tr is not None and tr["kind"] == "audio":
            return "audio"
        return kind
    return c["type"]


def _track_accepts(track, kind, media=None):
    if track["kind"] == "audio" and kind == "video" and media is not None and media.get("has_audio"):
        return True
    return (track["kind"] == "audio") == (kind == "audio")


def _check_source_range(doc, c):
    if c["type"] != "media":
        return
    m = doc["media"][c["media"]]
    if m["kind"] == "image":
        return
    used = c["in"] + c["duration"] * c["speed"]
    if c["in"] < -EPS or used > m["duration"] + 1e-3:
        raise OpError(
            f"Le clip {c['id']} dépasse la source {m['name']} : in={c['in']:.3f} + "
            f"durée×vitesse={c['duration'] * c['speed']:.3f} > {m['duration']:.3f} s.")


def _overlaps(doc, track, start, end, exclude=()):
    return [c["id"] for c in doc["clips"].values()
            if c["track"] == track and c["id"] not in exclude
            and c["start"] < end - EPS and c["start"] + c["duration"] > start + EPS]


def _check_free(doc, c):
    hits = _overlaps(doc, c["track"], c["start"], c["start"] + c["duration"], exclude=(c["id"],))
    if hits:
        raise OpError(f"Chevauchement sur la piste {c['track']} avec {hits}. "
                      "Passe overwrite: true pour écraser, ou cut_range pour libérer la place.")


def _anim_key_ok(doc, c, key):
    if key in ANIM_PROPS:
        return
    m = re.fullmatch(r"fx\.([A-Za-z0-9_]+)\.([a-z][a-z0-9_]*)", key)
    if m:
        inst = next((e for e in c["effects"] if e["id"] == m.group(1)), None)
        if inst is None:
            raise OpError(f"Effet {m.group(1)!r} absent du clip {c['id']}.")
        d = _effect_def(doc, inst["effect"])
        p = d["params"].get(m.group(2))
        if p is None or p["type"] != "float":
            raise OpError(f"Paramètre animable inconnu : {m.group(2)!r} (seuls les float s'animent).")
        return
    raise OpError(f"Propriété non animable : {key!r}. Animables : {list(ANIM_PROPS)} "
                  "ou « fx.<id_effet>.<paramètre> ».")


def _params(doc, effect_name, given, base=None):
    d = _effect_def(doc, effect_name)
    out = dict(base) if base else {k: copy.deepcopy(p["default"]) for k, p in d["params"].items()}
    for k, v in (given or {}).items():
        p = d["params"].get(k)
        if p is None:
            raise OpError(f"Paramètre inconnu pour {effect_name} : {k!r}. Attendus : {list(d['params'])}.")
        out[k] = _color(v, k) if p["type"] == "color" else _num(v, k)
    return out


def _keys(v, name):
    if not isinstance(v, list):
        raise OpError(f"« {name} » doit être une liste d'images clés {{t, v, ease}}.")
    out = []
    for k in v:
        if not isinstance(k, dict) or "t" not in k or "v" not in k:
            raise OpError("Chaque image clé doit avoir « t » (secondes depuis le début du clip) et « v ».")
        ease = k.get("ease", "linear")
        if ease not in EASES:
            raise OpError(f"ease inconnu : {ease!r}. Possibles : {list(EASES)}.")
        out.append({"t": _num(k["t"], "t"), "v": _num(k["v"], "v"), "ease": ease})
    return sorted(out, key=lambda k: k["t"])


def _shift_keys(tx, cid, dt):
    c = tx.doc["clips"][cid]
    if not c["keyframes"] or abs(dt) < EPS:
        return
    tx.set(["clips", cid, "keyframes"],
           {k: [{**kf, "t": kf["t"] + dt} for kf in lst] for k, lst in c["keyframes"].items()})


# ---------------------------------------------------------------- opérations

def op_add_track(tx, op):
    kind = op.get("kind", "video")
    if kind not in ("video", "audio"):
        raise OpError("kind doit valoir video ou audio.")
    prefix = "V" if kind == "video" else "A"
    tid = op.get("id") or f"{prefix}{1 + sum(t['kind'] == kind for t in tx.doc['tracks'])}"
    if any(t["id"] == tid for t in tx.doc["tracks"]):
        tid = tx.new_id(prefix)
    track = {"id": tid, "kind": kind, "name": op.get("name", tid), "hidden": False, "muted": False, "locked": False}
    if op.get("role"):
        track["role"] = str(op["role"])   # ex. "subtitles" : piste des sous-titres automatiques
    tracks = list(tx.doc["tracks"])
    if op.get("before") is not None:      # insérer sous une piste donnée (ex. sous les sous-titres)
        tracks.insert(tracks.index(_track(tx.doc, op["before"])), track)
    else:
        tracks.append(track)
    tx.set(["tracks"], tracks)
    return tid


def op_update_track(tx, op):
    t = _track(tx.doc, op.get("track"))
    i = tx.doc["tracks"].index(t)
    new = dict(t)
    for k in ("name", "hidden", "muted", "locked"):
        if k in op:
            new[k] = op[k] if k == "name" else bool(op[k])
    tracks = list(tx.doc["tracks"])
    tracks[i] = new
    tx.set(["tracks"], tracks)
    return t["id"]


def op_add_clip(tx, op):
    doc = tx.doc
    ctype = op.get("type", "media")
    if ctype not in ("media", "text", "solid"):
        raise OpError("type doit valoir media, text ou solid.")
    if ctype == "media":
        mid = op.get("media")
        if mid not in doc["media"]:
            raise OpError(f"Média inconnu : {mid!r}. Médias : {sorted(doc['media'])}.")
        kind = doc["media"][mid]["kind"]
    else:
        mid, kind = None, ctype
    if "track" in op:
        track = _track(doc, op["track"])
    else:
        track = next((t for t in doc["tracks"] if _track_accepts(t, kind)), None)
        if track is None:
            raise OpError("Aucune piste compatible : ajoute-en une avec add_track.")
    if not _track_accepts(track, kind, doc["media"][mid] if mid else None):
        raise OpError(f"Un clip {kind} ne va pas sur la piste {track['kind']} {track['id']}.")
    speed = _num(op.get("speed", 1.0), "speed", 0, 16, strict_lo=True)
    src_in = _num(op.get("in", 0.0), "in", 0)
    if "duration" in op:
        dur = _num(op["duration"], "duration", 0, strict_lo=True)
    elif ctype == "media" and kind != "image":
        dur = (doc["media"][mid]["duration"] - src_in) / speed
        if dur <= EPS:
            raise OpError("« in » est au-delà de la fin du média.")
    else:
        dur = 5.0
    start = _num(op.get("start", 0.0), "start", 0)
    cid = op.get("id") or tx.new_id("c")
    if cid in doc["clips"]:
        raise OpError(f"L'identifiant {cid!r} existe déjà.")
    props = dict(DEFAULT_PROPS)
    for k, v in (op.get("props") or {}).items():
        if k not in DEFAULT_PROPS:
            raise OpError(f"Propriété inconnue : {k!r}. Possibles : {list(DEFAULT_PROPS)}.")
        props[k] = _num(v, k)
    clip = {"id": cid, "type": ctype, "track": track["id"], "start": start, "duration": dur,
            "in": src_in, "speed": speed, "props": props, "effects": [], "keyframes": {},
            "name": op.get("name") or (doc["media"][mid]["name"] if mid else ctype)}
    if mid:
        clip["media"] = mid
    if ctype == "text":
        clip["text"] = {**DEFAULT_TEXT, **_text_fields(op.get("text") or {})}
        clip["name"] = op.get("name") or clip["text"]["content"][:30]
    if ctype == "solid":
        clip["color"] = _hex(op.get("color", "#000000"), "color")
    if isinstance(op.get("auto"), dict):
        # Empreinte d'un clip généré (sous-titres…) : on saura s'il a été retouché depuis.
        clip["auto"] = {k: v for k, v in op["auto"].items() if isinstance(v, (str, int, float, bool))}
    if op.get("overwrite"):
        _cut(tx, start, start + dur, [track["id"]], ripple=False)
    tx.set(["clips", cid], clip)
    _check_source_range(doc, doc["clips"][cid])
    _check_free(doc, doc["clips"][cid])
    return cid


def _text_fields(t):
    out = {}
    for k, v in t.items():
        if k not in DEFAULT_TEXT:
            raise OpError(f"Champ texte inconnu : {k!r}. Possibles : {list(DEFAULT_TEXT)}.")
        if k in TEXT_NUM:
            lo, hi = TEXT_NUM[k]
            v = _num(v, k, lo, hi)
        elif k in ("italic", "uppercase"):
            v = bool(v)
        elif k in ("color", "stroke_color", "shadow_color"):
            v = _hex(v, k)
        elif k == "background":
            v = None if v is None else _hex(v, k)
        elif k == "align":
            if v not in ("left", "center", "right"):
                raise OpError("align : left, center ou right.")
        elif not isinstance(v, str):
            raise OpError(f"« {k} » doit être du texte.")
        out[k] = v
    return out


def op_update_clip(tx, op):
    doc = tx.doc
    cid = op.get("clip")
    c = _clip(doc, cid)
    base = ["clips", cid]
    for k, lo, strict in (("start", 0, False), ("duration", 0, True), ("in", 0, False), ("speed", 0, True)):
        if k in op:
            tx.set(base + [k], _num(op[k], k, lo, strict_lo=strict))
    if "track" in op:
        t = _track(doc, op["track"])
        if not _track_accepts(t, _clip_kind(doc, c)):
            raise OpError(f"Ce clip ne peut pas aller sur la piste {t['kind']} {t['id']}.")
        tx.set(base + ["track"], t["id"])
    for k, v in (op.get("props") or {}).items():
        if k not in DEFAULT_PROPS:
            raise OpError(f"Propriété inconnue : {k!r}. Possibles : {list(DEFAULT_PROPS)}.")
        tx.set(base + ["props", k], _num(v, k))
    if "text" in op:
        if c["type"] != "text":
            raise OpError(f"{cid} n'est pas un clip texte.")
        for k, v in _text_fields(op["text"]).items():
            tx.set(base + ["text", k], v)
    if "color" in op:
        if c["type"] != "solid":
            raise OpError(f"{cid} n'est pas un aplat de couleur.")
        tx.set(base + ["color"], _hex(op["color"], "color"))
    if "name" in op:
        tx.set(base + ["name"], str(op["name"]))
    if "link" in op:
        tx.set(base + ["link"], None if op["link"] is None else str(op["link"]))
    if "audio_muted" in op:
        tx.set(base + ["audio_muted"], bool(op["audio_muted"]))
    c = doc["clips"][cid]
    if op.get("overwrite"):
        _cut(tx, c["start"], c["start"] + c["duration"], [c["track"]], ripple=False, exclude=(cid,))
    _check_source_range(doc, c)
    _check_free(doc, c)
    return cid


def op_delete_clip(tx, op):
    cid = op.get("clip")
    _clip(tx.doc, cid)
    tx.delete(["clips", cid])
    return cid


def _split(tx, cid, at):
    c = tx.doc["clips"][cid]
    off = at - c["start"]
    right = copy.deepcopy(c)
    rid = tx.new_id("c")
    right.update(id=rid, start=at, duration=c["duration"] - off, **{"in": c["in"] + off * c["speed"]})
    right["keyframes"] = {k: [{**kf, "t": kf["t"] - off} for kf in lst] for k, lst in c["keyframes"].items()}
    right.pop("trans_in", None)
    if c.get("link"):
        # Les deux moitiés droites d'une paire image + son restent liées entre elles.
        right["link"] = tx.link_for(("split", c["link"], round(at, 4)))
    tx.set(["clips", cid, "duration"], off)
    tx.set(["clips", rid], right)
    return rid


def op_split_clip(tx, op):
    c = _clip(tx.doc, op.get("clip"))
    at = _num(op.get("at"), "at")
    if not (c["start"] + EPS < at < c["start"] + c["duration"] - EPS):
        raise OpError(f"« at » ({at}) doit tomber à l'intérieur du clip "
                      f"[{c['start']:.3f}, {c['start'] + c['duration']:.3f}].")
    partners = []
    if c.get("link") and op.get("linked", True):
        partners = [p["id"] for p in tx.doc["clips"].values()
                    if p["id"] != c["id"] and p.get("link") == c["link"]
                    and p["start"] + EPS < at < p["start"] + p["duration"] - EPS]
    rid = _split(tx, c["id"], at)
    for pid in partners:
        _split(tx, pid, at)
    return rid


def _cut(tx, start, end, tracks, ripple, exclude=()):
    doc = tx.doc
    for cid in list(doc["clips"]):
        c = doc["clips"].get(cid)
        if c is None or c["track"] not in tracks or cid in exclude:
            continue
        a, b = c["start"], c["start"] + c["duration"]
        if b <= start + EPS or a >= end - EPS:
            continue
        if a >= start - EPS and b <= end + EPS:
            tx.delete(["clips", cid])
        elif a < start and b > end:
            _split(tx, cid, end)  # la partie droite est décalée plus bas si ripple
            tx.set(["clips", cid, "duration"], start - a)
        elif a < start:
            tx.set(["clips", cid, "duration"], start - a)
        else:
            d = end - a
            tx.set(["clips", cid, "in"], c["in"] + d * c["speed"])
            tx.set(["clips", cid, "start"], end)
            tx.set(["clips", cid, "duration"], c["duration"] - d)
            _shift_keys(tx, cid, -d)
    if ripple:
        gap = end - start
        for cid, c in list(doc["clips"].items()):
            if c["track"] in tracks and cid not in exclude and c["start"] >= end - EPS:
                tx.set(["clips", cid, "start"], max(0.0, c["start"] - gap))


def op_cut_range(tx, op):
    start = _num(op.get("start"), "start", 0)
    end = _num(op.get("end"), "end", 0)
    if end <= start + EPS:
        raise OpError("« end » doit être après « start ».")
    tracks = op.get("tracks") or [t["id"] for t in tx.doc["tracks"]]
    for t in tracks:
        _track(tx.doc, t)
    _cut(tx, start, end, tracks, ripple=op.get("ripple", True))
    return None


def op_add_effect(tx, op):
    c = _clip(tx.doc, op.get("clip"))
    if _clip_kind(tx.doc, c) == "audio":
        raise OpError("Les effets visuels ne s'appliquent pas à un clip audio.")
    name = op.get("effect")
    if _effect_def(tx.doc, name).get("kind") == "transition":
        raise OpError(f"{name!r} est une transition : pose-la avec set_transition sur le clip qui entre.")
    fid = op.get("id") or tx.new_id("fx")
    if any(e["id"] == fid for e in c["effects"]):
        raise OpError(f"L'effet {fid!r} existe déjà sur {c['id']}.")
    inst = {"id": fid, "effect": name, "enabled": op.get("enabled", True),
            "params": _params(tx.doc, name, op.get("params"))}
    effects = list(c["effects"])
    idx = op.get("index")
    effects.insert(len(effects) if idx is None else int(idx), inst)
    tx.set(["clips", c["id"], "effects"], effects)
    return fid


def _effect_index(c, fid):
    for i, e in enumerate(c["effects"]):
        if e["id"] == fid:
            return i
    raise OpError(f"Effet {fid!r} absent du clip {c['id']}. Présents : {[e['id'] for e in c['effects']]}.")


def op_update_effect(tx, op):
    c = _clip(tx.doc, op.get("clip"))
    i = _effect_index(c, op.get("fx"))
    inst = copy.deepcopy(c["effects"][i])
    if "params" in op:
        inst["params"] = _params(tx.doc, inst["effect"], op["params"], base=inst["params"])
    if "enabled" in op:
        inst["enabled"] = bool(op["enabled"])
    effects = list(c["effects"])
    effects[i] = inst
    if "index" in op:
        effects.insert(int(op["index"]), effects.pop(i))
    tx.set(["clips", c["id"], "effects"], effects)
    return inst["id"]


def op_remove_effect(tx, op):
    c = _clip(tx.doc, op.get("clip"))
    i = _effect_index(c, op.get("fx"))
    fid = c["effects"][i]["id"]
    tx.set(["clips", c["id"], "effects"], [e for e in c["effects"] if e["id"] != fid])
    kf = {k: v for k, v in c["keyframes"].items() if not k.startswith(f"fx.{fid}.")}
    if kf != c["keyframes"]:
        tx.set(["clips", c["id"], "keyframes"], kf)
    return fid


def op_set_keyframes(tx, op):
    c = _clip(tx.doc, op.get("clip"))
    key = op.get("prop")
    _anim_key_ok(tx.doc, c, key)
    keys = _keys(op.get("keys", []), "keys")
    if keys:
        tx.set(["clips", c["id"], "keyframes", key], keys)
    else:
        tx.delete(["clips", c["id"], "keyframes", key])
    return key


def op_set_keyframe(tx, op):
    """Ajoute ou remplace UNE image clé à l'instant t (secondes depuis le début du clip)."""
    c = _clip(tx.doc, op.get("clip"))
    key = op.get("prop")
    _anim_key_ok(tx.doc, c, key)
    (k,) = _keys([{"t": op.get("t"), "v": op.get("v"), "ease": op.get("ease", "linear")}], "keyframe")
    if "ease" not in op:
        old = next((x for x in c["keyframes"].get(key, []) if abs(x["t"] - k["t"]) < 1e-4), None)
        if old:
            k["ease"] = old["ease"]
    keys = [x for x in c["keyframes"].get(key, []) if abs(x["t"] - k["t"]) >= 1e-4] + [k]
    tx.set(["clips", c["id"], "keyframes", key], sorted(keys, key=lambda x: x["t"]))
    return key


def op_remove_keyframe(tx, op):
    c = _clip(tx.doc, op.get("clip"))
    key = op.get("prop")
    t = _num(op.get("t"), "t")
    keys = c["keyframes"].get(key, [])
    rest = [x for x in keys if abs(x["t"] - t) >= 1e-3]
    if len(rest) == len(keys):
        raise OpError(f"Aucune image clé de {key!r} à t={t} sur {c['id']}.")
    if rest:
        tx.set(["clips", c["id"], "keyframes", key], rest)
    else:
        tx.delete(["clips", c["id"], "keyframes", key])
    return key


def op_duplicate_clip(tx, op):
    doc = tx.doc
    c = _clip(doc, op.get("clip"))
    new = copy.deepcopy(c)
    nid = op.get("id") or tx.new_id("c")
    new["id"] = nid
    new["start"] = _num(op.get("start", c["start"] + c["duration"]), "start", 0)
    if c.get("link"):
        new["link"] = tx.link_for(("dup", c["link"]))
    if "track" in op:
        t = _track(doc, op["track"])
        if not _track_accepts(t, _clip_kind(doc, c)):
            raise OpError(f"Ce clip ne peut pas aller sur la piste {t['kind']} {t['id']}.")
        new["track"] = t["id"]
    if op.get("overwrite"):
        _cut(tx, new["start"], new["start"] + new["duration"], [new["track"]], ripple=False)
    tx.set(["clips", nid], new)
    _check_free(doc, doc["clips"][nid])
    return nid


def op_separate_audio(tx, op):
    """Sépare le son d'un clip vidéo : un clip audio lié sur une piste audio ; la vidéo devient muette."""
    doc = tx.doc
    c = _clip(doc, op.get("clip"))
    m = doc["media"].get(c.get("media")) if c["type"] == "media" else None
    if m is None or _clip_kind(doc, c) != "video":
        raise OpError(f"{c['id']} n'est pas un clip vidéo sur une piste vidéo.")
    if not m.get("has_audio"):
        raise OpError(f"La vidéo {m['name']} n'a pas de son.")
    if c.get("audio_muted"):
        raise OpError(f"Le son de {c['id']} est déjà séparé.")
    a, b = c["start"], c["start"] + c["duration"]
    if "track" in op:
        tr = _track(doc, op["track"])
        if tr["kind"] != "audio":
            raise OpError(f"{tr['id']} n'est pas une piste audio.")
        tid = tr["id"]
    else:
        tid = next((t["id"] for t in doc["tracks"] if t["kind"] == "audio" and not t.get("locked")
                    and not _overlaps(doc, t["id"], a, b)), None) or op_add_track(tx, {"kind": "audio"})
    link = c.get("link") or tx.new_id("lk")
    aid = tx.new_id("c")
    vol_keys = c["keyframes"].get("volume")
    audio = {"id": aid, "type": "media", "media": c["media"], "track": tid, "start": a, "duration": c["duration"],
             "in": c["in"], "speed": c["speed"], "props": {**DEFAULT_PROPS, "volume": c["props"]["volume"]},
             "effects": [], "keyframes": {"volume": vol_keys} if vol_keys else {},
             "name": f"{c['name']} (son)", "link": link}
    tx.set(["clips", aid], audio)
    _check_free(doc, doc["clips"][aid])
    tx.set(["clips", c["id"], "link"], link)
    tx.set(["clips", c["id"], "audio_muted"], True)
    if vol_keys:
        tx.delete(["clips", c["id"], "keyframes", "volume"])
    return aid


def voisin_gauche(doc, c):
    """Clip de la même piste qui finit exactement là où celui-ci commence."""
    return next((o for o in doc["clips"].values() if o["track"] == c["track"] and o["id"] != c["id"]
                 and abs(o["start"] + o["duration"] - c["start"]) < 1e-3), None)


def spans(doc):
    """Intervalle réellement affiché de chaque clip : sa durée, plus les demi-transitions
    qui le prolongent dans la marge de sa source (avant son début, après sa fin)."""
    par_piste = {}
    for c in doc["clips"].values():
        par_piste.setdefault(c["track"], []).append(c)
    out = {}
    for clips in par_piste.values():
        clips.sort(key=lambda c: c["start"])
        for c in clips:
            out[c["id"]] = [c["start"], c["start"] + c["duration"]]
        for a, b in zip(clips, clips[1:]):
            t = b.get("trans_in")
            if t and abs(a["start"] + a["duration"] - b["start"]) < 1e-3:
                h = t["duration"] / 2
                out[a["id"]][1] += h
                out[b["id"]][0] -= h
    return out


def op_set_transition(tx, op):
    """Transition entre un clip et celui qui le précède sur la même piste (posée sur le clip qui entre)."""
    doc = tx.doc
    c = _clip(doc, op.get("clip"))
    kind = op.get("type")
    if kind is None:
        tx.delete(["clips", c["id"], "trans_in"])
        return c["id"]
    d = _effect_def(doc, kind)
    if d.get("kind") != "transition":
        raise OpError(f"{kind!r} n'est pas une transition. Transitions : "
                      f"{sorted(k for k, v in {**BUILTIN, **doc['effects']}.items() if v.get('kind') == 'transition')}.")
    left = voisin_gauche(doc, c)
    if left is None:
        raise OpError(f"Aucun clip ne finit juste avant {c['id']} sur la piste {c['track']} : "
                      "une transition relie deux clips qui se touchent.")
    dur = _num(op.get("duration", 0.5), "duration", 0, strict_lo=True)
    maxi = min(left["duration"], c["duration"])
    if dur > maxi + EPS:
        raise OpError(f"Durée trop longue : au plus {maxi:.2f} s (le plus court des deux clips).")
    base = (c.get("trans_in") or {}).get("params") if (c.get("trans_in") or {}).get("type") == kind else None
    params = {k: v for k, v in _params(doc, kind, op.get("params"), base=base).items() if k != "progress"}
    tx.set(["clips", c["id"], "trans_in"], {"type": kind, "duration": dur, "params": params})
    return c["id"]


def op_link_clips(tx, op):
    ids = op.get("clips") or []
    if len(ids) < 2:
        raise OpError("Donne au moins deux clips à lier.")
    link = tx.new_id("lk")
    for i in ids:
        _clip(tx.doc, i)
        tx.set(["clips", i, "link"], link)
    return link


def op_unlink_clips(tx, op):
    for i in op.get("clips") or []:
        c = _clip(tx.doc, i)
        if c.get("link"):
            tx.set(["clips", i, "link"], None)
    return None


def op_ripple_delete(tx, op):
    """Supprime des clips et resserre leur piste (comme « suppression avec décalage »)."""
    ids = op.get("clips") or []
    ranges = [(c["start"], c["start"] + c["duration"], c["track"]) for c in (_clip(tx.doc, i) for i in ids)]
    for a, b, tr in sorted(ranges, reverse=True):
        _cut(tx, a, b, [tr], ripple=True)
    return None


def op_define_effect(tx, op):
    name = op.get("name", "")
    if not _NAME.fullmatch(name or ""):
        raise OpError("name : minuscules, chiffres et _, 2 à 41 caractères, commençant par une lettre.")
    if name in BUILTIN:
        raise OpError(f"{name!r} est un effet intégré : choisis un autre nom.")
    glsl = op.get("glsl")
    if not isinstance(glsl, str) or "effect(" not in glsl:
        raise OpError("glsl doit contenir la fonction `vec4 effect(vec2 uv)`.")
    if len(glsl) > MAX_GLSL:
        raise OpError(f"glsl trop long ({len(glsl)} > {MAX_GLSL} caractères).")
    if re.search(r"^\s*#\s*version|\bvoid\s+main\s*\(", glsl, re.M):
        raise OpError("N'écris ni #version ni main() : le moteur les ajoute.")
    params = {}
    for pn, p in (op.get("params") or {}).items():
        if not _PARAM.fullmatch(pn):
            raise OpError(f"Nom de paramètre invalide : {pn!r}.")
        ptype = p.get("type", "float")
        if ptype == "float":
            params[pn] = {"type": "float", "default": _num(p.get("default", 0.0), pn),
                          "min": _num(p.get("min", 0.0), "min"), "max": _num(p.get("max", 1.0), "max"),
                          "label": str(p.get("label", pn))}
        elif ptype == "color":
            params[pn] = {"type": "color", "default": _color(p.get("default", [1, 1, 1]), pn),
                          "label": str(p.get("label", pn))}
        else:
            raise OpError(f"Type de paramètre {ptype!r} : float ou color.")
    kind = op.get("kind", "effect")
    if kind not in ("effect", "transition"):
        raise OpError("kind : effect ou transition.")
    blend = op.get("blend", "over")
    if kind == "transition":
        blend = "replace"
        params.setdefault("progress", {"type": "float", "default": 0.0, "min": 0.0, "max": 1.0,
                                       "label": "Progression (automatique)"})
    if blend not in ("over", "replace"):
        raise OpError("blend : over ou replace.")
    tx.set(["effects", name], {"name": name, "label": str(op.get("label", name)), "params": params,
                               "glsl": glsl, "blend": blend, "builtin": False, "kind": kind,
                               "category": "Transitions" if kind == "transition" else "Sur mesure",
                               "description": str(op.get("description", ""))})
    return name


def op_delete_effect_def(tx, op):
    name = op.get("name")
    if name not in tx.doc["effects"]:
        raise OpError(f"Effet sur mesure inconnu : {name!r}.")
    users = [c["id"] for c in tx.doc["clips"].values() if any(e["effect"] == name for e in c["effects"])]
    if users:
        raise OpError(f"Effet encore utilisé par {users} : retire-le d'abord (remove_effect).")
    tx.delete(["effects", name])
    return name


def op_update_settings(tx, op):
    for k in ("width", "height"):
        if k in op:
            v = int(_num(op[k], k, 16, 7680))
            tx.set(["settings", k], v - v % 2)
    if "fps" in op:
        tx.set(["settings", "fps"], _num(op["fps"], "fps", 1, 240))
    if "background" in op:
        tx.set(["settings", "background"], _hex(op["background"], "background"))
    return None


def op_delete_media(tx, op):
    """Retire un média du projet (le fichier sur le disque n'est jamais touché : Ctrl+Z le rend).
    S'il est encore utilisé, refus, sauf with_clips: true qui supprime aussi ses clips."""
    mid = op.get("media")
    if mid not in tx.doc["media"]:
        raise OpError(f"Média inconnu : {mid!r}.")
    users = [c["id"] for c in tx.doc["clips"].values() if c.get("media") == mid]
    if users and not op.get("with_clips"):
        raise OpError(f"Média utilisé par {len(users)} clip(s) {users} : supprime-les d'abord, "
                      "ou passe with_clips: true.")
    for cid in users:
        tx.delete(["clips", cid])
    tx.delete(["media", mid])
    return mid


def op_add_media(tx, op):
    m = op["media"]
    mid = m.get("id") or tx.new_id("m")
    tx.set(["media", mid], {**m, "id": mid})
    return mid


OPS = {
    "add_track": op_add_track, "update_track": op_update_track,
    "add_clip": op_add_clip, "update_clip": op_update_clip, "delete_clip": op_delete_clip,
    "split_clip": op_split_clip, "cut_range": op_cut_range,
    "add_effect": op_add_effect, "update_effect": op_update_effect, "remove_effect": op_remove_effect,
    "set_keyframes": op_set_keyframes, "set_keyframe": op_set_keyframe, "remove_keyframe": op_remove_keyframe,
    "duplicate_clip": op_duplicate_clip, "ripple_delete": op_ripple_delete,
    "separate_audio": op_separate_audio, "set_transition": op_set_transition, "link_clips": op_link_clips, "unlink_clips": op_unlink_clips,
    "define_effect": op_define_effect, "delete_effect_def": op_delete_effect_def,
    "update_settings": op_update_settings, "delete_media": op_delete_media,
}
INTERNAL_OPS = {**OPS, "add_media": op_add_media}


def _subst(value, results):
    """Remplace "$N" par l'identifiant créé par l'opération N du même lot."""
    if isinstance(value, str) and re.fullmatch(r"\$\d+", value):
        i = int(value[1:])
        if i >= len(results) or results[i] is None:
            raise OpError(f"Référence {value} invalide : l'opération {i} n'a pas encore créé d'objet.")
        return results[i]
    if isinstance(value, list):
        return [_subst(v, results) for v in value]
    if isinstance(value, dict):
        return {k: _subst(v, results) for k, v in value.items()}
    return value


# ---------------------------------------------------------------- historique

class Projet:
    def __init__(self, doc=None, max_history=300):
        self.doc = doc or nouveau_document()
        self.undo_stack: list[dict] = []
        self.redo_stack: list[dict] = []
        self.rev = 0
        self.max_history = max_history

    def appliquer(self, ops, label="", origin="ui", internal=False):
        """Applique un lot. Renvoie (résultats, correctifs). Lève OpError sans rien changer."""
        if not isinstance(ops, list) or not ops:
            raise OpError("ops doit être une liste non vide d'opérations.")
        table = INTERNAL_OPS if internal else OPS
        tx = Transaction(self.doc)
        results = []
        try:
            for i, op in enumerate(ops):
                if not isinstance(op, dict) or op.get("op") not in table:
                    raise OpError(f"Opération {i} inconnue : {op.get('op') if isinstance(op, dict) else op!r}. "
                                  f"Possibles : {sorted(OPS)}.")
                try:
                    results.append(table[op["op"]](tx, _subst(op, results)))
                except OpError as e:
                    raise OpError(f"Opération {i} ({op['op']}) : {e}") from None
        except Exception:
            tx.rollback()
            raise
        if tx.fwd:
            self.undo_stack.append({"label": label or ops[0]["op"], "origin": origin,
                                    "fwd": tx.fwd, "inv": list(reversed(tx.inv))})
            del self.undo_stack[:-self.max_history]
            self.redo_stack.clear()
            self.rev += 1
        return results, tx.fwd

    def systeme(self, patches):
        """Correctifs hors historique (ex. proxy prêt). Non annulables."""
        for p in patches:
            appliquer_correctif(self.doc, p)
        self.rev += 1
        return patches

    def annuler(self):
        if not self.undo_stack:
            return None
        e = self.undo_stack.pop()
        for p in e["inv"]:
            appliquer_correctif(self.doc, p)
        self.redo_stack.append(e)
        self.rev += 1
        return e, e["inv"]

    def retablir(self):
        if not self.redo_stack:
            return None
        e = self.redo_stack.pop()
        for p in e["fwd"]:
            appliquer_correctif(self.doc, p)
        self.undo_stack.append(e)
        self.rev += 1
        return e, e["fwd"]

    def journal(self, n=30):
        return [{"label": e["label"], "origin": e["origin"]} for e in self.undo_stack[-n:]]
