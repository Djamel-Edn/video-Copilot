"""Serveur MCP : les outils que le copilote IA utilise pour monter en direct.

Le serveur de l'éditeur (serveur.py) doit tourner. Ce fichier ne touche jamais
le projet directement : il passe par la même API que l'interface, donc chaque
action de l'IA est validée, diffusée en direct et annulable par Ctrl+Z.
"""
from __future__ import annotations

import base64
import os

import httpx
from mcp.server.mcpserver import Image, MCPServer

BASE = os.environ.get("EDITEUR_URL", "http://127.0.0.1:8765")

INSTRUCTIONS = """\
Tu es le copilote d'un éditeur vidéo. L'utilisateur voit la timeline et l'aperçu
pendant que tu travailles : chaque appel à apply_ops apparaît immédiatement.

Méthode :
1. get_timeline (et get_ui_state pour savoir où est sa tête de lecture / sa
   sélection : « ici », « ce clip » renvoient à ça).
2. Agis par petits lots cohérents avec apply_ops, chacun avec un label clair en
   français (« Coupe des silences », « Zoom lent sur le plan 2 ») : un lot =
   une étape d'annulation pour l'utilisateur.
3. Vérifie visuellement avec snapshot quand le résultat est visuel.
   Pour couper dans la parole, lis get_transcript (temps timeline de chaque
   mot) et coupe par text_edit ou cut_range — jamais au hasard.
4. Si aucun effet intégré ne convient (list_effects), écris-en un avec
   define_effect (shader GLSL). Si la compilation échoue, lis le log, corrige,
   recommence. Puis applique-le avec add_effect et anime-le avec set_keyframes.
Temps en secondes. Ne supprime jamais de contenu que l'utilisateur n'a pas
demandé de supprimer. Si une opération est refusée, le message dit pourquoi.
"""

mcp = MCPServer("editeur", instructions=INSTRUCTIONS)


def _call(method, path, body=None, timeout=20.0):
    try:
        r = httpx.request(method, BASE + path, json=body, timeout=timeout)
    except httpx.ConnectError:
        raise RuntimeError(f"L'éditeur ne tourne pas sur {BASE}. Lance : .venv/Scripts/python serveur.py")
    try:
        data = r.json()
    except ValueError:
        data = {"error": r.text}
    if r.status_code >= 400:
        msg = data.get("error") or data.get("detail") or r.text
        if data.get("log"):
            msg += "\n--- log de compilation ---\n" + data["log"]
        raise RuntimeError(msg)
    return data


def _compact(doc):
    media = {m["id"]: {"name": m["name"], "kind": m["kind"], "duration": round(m["duration"], 3),
                       **({"size": f"{m['width']}x{m['height']}"} if "width" in m else {})}
             for m in doc["media"].values()}
    clips = []
    for c in sorted(doc["clips"].values(), key=lambda c: (c["track"], c["start"])):
        e = {"id": c["id"], "type": c["type"], "track": c["track"], "name": c["name"],
             "start": round(c["start"], 3), "end": round(c["start"] + c["duration"], 3),
             "duration": round(c["duration"], 3)}
        if c["type"] == "media":
            e.update(media=c["media"], **{"in": round(c["in"], 3)})
        if c["speed"] != 1:
            e["speed"] = c["speed"]
        props = {k: v for k, v in c["props"].items() if v != {"scale": 1, "opacity": 1, "volume": 1}.get(k, 0)}
        if props:
            e["props"] = props
        if c["type"] == "text":
            e["text"] = c["text"]["content"]
        if c["effects"]:
            e["effects"] = [{"id": f["id"], "effect": f["effect"], "enabled": f["enabled"], "params": f["params"]}
                            for f in c["effects"]]
        if c["keyframes"]:
            e["keyframes"] = c["keyframes"]
        clips.append(e)
    return {"settings": doc["settings"], "tracks": [{"id": t["id"], "kind": t["kind"], "hidden": t["hidden"],
                                                      "muted": t["muted"]} for t in doc["tracks"]],
            "media": media, "clips": clips, "custom_effects": sorted(doc["effects"])}


@mcp.tool()
def get_timeline() -> dict:
    """Lit la timeline : réglages, pistes (V1 en bas, la dernière piste vidéo
    est au-dessus), médias, clips (temps en secondes), effets, images clés."""
    d = _call("GET", "/api/project")
    out = _compact(d["project"])
    out["total_duration"] = round(d["duration"], 3)
    out["recent_history"] = d["journal"][-10:]
    return out


@mcp.tool()
def get_ui_state() -> dict:
    """Où en est l'utilisateur : position de la tête de lecture (s), clips
    sélectionnés, lecture en cours. Sert à comprendre « ici », « ce clip »."""
    return _call("GET", "/api/ui_state")


@mcp.tool()
def apply_ops(ops: list[dict], label: str) -> dict:
    """Applique un lot d'opérations, atomiquement (tout ou rien), visible en
    direct, annulable d'un seul Ctrl+Z. `label` : description courte en français.
    Dans un lot, "$N" désigne l'identifiant créé par l'opération N (0 = première).

    Opérations (champ "op") :
    - add_track {kind: video|audio, name?}
    - update_track {track, name?, hidden?, muted?, locked?}
    - add_clip {type: media|text|solid, media? (id), track?, start, duration?, in?,
        speed?, props?, text?, color? (#rrggbb, pour solid), name?, overwrite?: bool}
        text = {content, font (nom de police installée), size (px), weight (100-900),
                italic, uppercase, color, align (left|center|right), line_height,
                letter_spacing (px), stroke_color, stroke_width, shadow_color,
                shadow_opacity (0-1), shadow_blur, shadow_x, shadow_y,
                background (#rrggbb|null), background_opacity, padding, radius}
    - update_clip {clip, start?, duration?, in?, speed?, track?, props?, text?,
        color?, name?, overwrite?}
    - delete_clip {clip}
    - split_clip {clip, at}  -> id de la partie droite
    - duplicate_clip {clip, start?, track?, overwrite?}  -> id de la copie
    - ripple_delete {clips: [ids]}  : supprime et resserre leur piste
    - separate_audio {clip, track?}  -> id du clip audio : le son d'une vidéo passe
        sur une piste audio, LIÉ à l'image (même champ "link"), la vidéo devient
        muette (audio_muted). Scinder l'un scinde l'autre ; pour déplacer, rogner
        ou supprimer une paire liée, envoie l'opération pour LES DEUX clips.
    - link_clips {clips: [ids]} · unlink_clips {clips: [ids]}
    - cut_range {start, end, tracks?: [ids], ripple?: true}  : retire la plage
        sur les pistes (toutes par défaut) et resserre ce qui suit si ripple.
    - add_effect {clip, effect, params?, index?}  -> id de l'instance (fxN)
    - update_effect {clip, fx, params?, enabled?, index?}
    - remove_effect {clip, fx}
    - set_keyframes {clip, prop, keys: [{t, v, ease?}]}  ; keys vide = retire
        prop : x, y (px, depuis le centre, y vers le bas), scale (1 = ajusté au
        cadre), rotation (°, sens horaire), opacity (0-1), volume (0-2),
        ou "fx.<id_instance>.<paramètre>" pour animer un effet.
        t : secondes depuis le DÉBUT DU CLIP. ease : linear, ease_in,
        ease_out, ease_in_out, hold.
    - set_keyframe {clip, prop, t, v, ease?}  : ajoute ou remplace UNE image clé
    - remove_keyframe {clip, prop, t}
    - update_settings {width?, height?, fps?, background?}
    - delete_effect_def {name}
    Chevauchement sur une même piste refusé sauf overwrite: true.
    """
    return _call("POST", "/api/ops", {"ops": ops, "label": label, "origin": "ia"})


@mcp.tool()
def undo() -> dict:
    """Annule la dernière étape (humaine ou IA)."""
    return _call("POST", "/api/undo")


@mcp.tool()
def redo() -> dict:
    """Rétablit la dernière étape annulée."""
    return _call("POST", "/api/redo")


@mcp.tool()
def list_effects(include_glsl: bool = False) -> dict:
    """Liste les effets disponibles (intégrés et sur mesure) avec leurs
    paramètres, plus le contrat GLSL pour en écrire de nouveaux."""
    d = _call("GET", "/api/effects")
    def short(e):
        out = {"label": e["label"], "blend": e["blend"], "params": e["params"]}
        if include_glsl:
            out["glsl"] = e["glsl"]
        return out
    return {"contract": d["contract"], "builtin": {k: short(v) for k, v in d["builtin"].items()},
            "custom": {k: short(v) for k, v in d["custom"].items()}}


@mcp.tool()
def define_effect(name: str, glsl: str, params: dict | None = None, label: str = "",
                  blend: str = "over", description: str = "") -> dict:
    """Crée (ou remplace) un effet sur mesure en écrivant un shader GLSL.
    Le shader est compilé dans l'interface AVANT d'être accepté ; en cas
    d'erreur, le log de compilation est renvoyé : corrige et rappelle.

    glsl : uniquement `vec4 effect(vec2 uv) { ... }` (+ fonctions d'aide).
      src(uv), below(uv), u_time (s depuis le début du clip), u_dur, u_res (px),
      p_<param>. uv (0,0) = bas-gauche. Pas de #version, de uniform ni de main().
    params : {"nom": {"type": "float", "default": 0.5, "min": 0, "max": 1, "label": "..."}}
      ou {"nom": {"type": "color", "default": [1, 0, 0]}}
    blend : "over" (se superpose) ou "replace" (remplace l'image composée :
      transitions, modes de fusion, avec below()).
    Ensuite : apply_ops add_effect {clip, effect: name}.
    """
    return _call("POST", "/api/effects/define", {"name": name, "glsl": glsl, "params": params or {},
                                                 "label": label or name, "blend": blend,
                                                 "description": description})


@mcp.tool()
def snapshot(t: float | None = None, width: int = 768) -> Image:
    """Rend l'image de la timeline à l'instant t (s) et la renvoie, pour
    vérifier visuellement un effet, un cadrage, un titre. Déplace la tête de
    lecture de l'utilisateur à t (il voit ce que tu regardes)."""
    body = {"width": max(160, min(1600, int(width)))}
    if t is not None:
        body["t"] = t
    d = _call("POST", "/api/snapshot", body, timeout=30)
    return Image(data=base64.b64decode(d["jpeg_base64"]), format="jpeg")


@mcp.tool()
def import_media(path: str) -> dict:
    """Importe un fichier local (vidéo, audio, image) par son chemin absolu.
    Renvoie l'id du média à utiliser dans add_clip."""
    d = _call("POST", "/api/import", {"path": path}, timeout=120)
    m = d["media"]
    return {"id": m["id"], "name": m["name"], "kind": m["kind"], "duration": m["duration"]}


@mcp.tool()
def export_video(path: str = "", quality: str = "haute", scale: float = 1.0, start: float = 0.0,
                 end: float | None = None, encoder: str = "x264") -> dict:
    """Lance l'export MP4 (H.264 + AAC) de la séquence, rendu par l'interface
    ouverte avec le même moteur que l'aperçu. Rend la main tout de suite :
    suis l'avancement avec export_status.
    path : chemin complet du .mp4 (vide = dossier exports du projet) ;
    quality : haute | moyenne | legere ; scale : 1 = résolution de la séquence,
    0.5 = moitié ; start / end : plage en secondes (end vide = jusqu'à la fin) ;
    encoder : x264 ou nvenc (si disponible)."""
    s = _call("GET", "/api/project")["project"]["settings"]
    body = {"quality": quality, "encoder": encoder, "start": start,
            "width": int(s["width"] * scale) // 2 * 2, "height": int(s["height"] * scale) // 2 * 2}
    if path:
        body["path"] = path
    if end is not None:
        body["end"] = end
    return _call("POST", "/api/export", body)


@mcp.tool()
def export_status() -> dict:
    """État de l'export : idle, running (frame / frames, fps), done (path,
    seconds, size_mb), cancelled ou error."""
    return _call("GET", "/api/export/status")


@mcp.tool()
def transcribe_media(media: str, language: str = "fr") -> dict:
    """Lance la transcription mot à mot d'un média (faster-whisper, local).
    Compter environ la durée du média. L'avancement apparaît dans
    get_timeline (media.<id>.transcript_state)."""
    return _call("POST", "/api/transcribe", {"media": media, "language": language})


@mcp.tool()
def get_transcript(start: float = 0.0, end: float | None = None) -> dict:
    """Transcription de la SÉQUENCE : les mots visibles dans la timeline, dans
    l'ordre, avec leur temps timeline (start, end en s), leur clip et leur
    indice i (pour text_edit action "words"). Les mots déjà coupés n'y sont plus.
    Pour couper une phrase : cut_range sur [start du premier mot, end du dernier]
    via apply_ops, ou text_edit {action: "words", from: i, to: j}."""
    q = f"?start={start}" + (f"&end={end}" if end is not None else "")
    d = _call("GET", "/api/transcript_timeline" + q)
    full = _call("GET", "/api/transcript_timeline")["words"]
    index = {(w["clip"], w["start"]): k for k, w in enumerate(full)}
    words = [{"i": index[(w["clip"], w["start"])], "w": w["w"], "start": w["start"], "end": w["end"],
              "clip": w["clip"], **({"low_confidence": True} if w["p"] < 0.5 else {})} for w in d["words"]]
    return {"words": words, "text": " ".join(w["w"] for w in words)}


@mcp.tool()
def text_edit(action: str, from_index: int | None = None, to_index: int | None = None,
              min_gap: float = 0.7, keep: float = 0.25, label: str = "") -> dict:
    """Montage par le texte, en une étape annulable :
    - action "fillers" : retire les hésitations (euh, hum, ben…) ;
    - action "silences" : réduit chaque blanc de plus de min_gap s à keep s ;
    - action "words" : coupe les mots from_index..to_index (indices de get_transcript).
    Toutes les pistes sont resserrées ensemble pour garder la synchro."""
    body = {"action": action, "min_gap": min_gap, "keep": keep, "origin": "ia", "label": label}
    if action == "words":
        body.update({"from": from_index, "to": to_index})
    return _call("POST", "/api/text_edit", body)


@mcp.tool()
def generate_subtitles(style: str = "classique", max_words: int | None = None, max_chars: int | None = None,
                       max_lines: int | None = None, start: float = 0.0, end: float | None = None,
                       save_srt: bool = False) -> dict:
    """Sous-titres automatiques depuis la transcription de la séquence (transcris
    d'abord avec transcribe_media). Chaque sous-titre devient un clip texte sur la
    piste « Sous-titres » (créée au besoin, remplacée à chaque génération) :
    modifiable ensuite avec update_clip (texte, style) comme n'importe quel texte.
    style : classique (bas de l'écran, fond), shorts (gros, 1 à 3 mots, apparition
    en zoom), minimal (blanc, ombre). save_srt : écrit aussi le .srt dans exports/."""
    body = {"style": style, "start": start, "origin": "ia"}
    for k, v in (("max_words", max_words), ("max_chars", max_chars), ("max_lines", max_lines), ("end", end)):
        if v is not None:
            body[k] = v
    r = _call("POST", "/api/subtitles", body)
    if save_srt:
        r["srt"] = _call("POST", "/api/subtitles/save_srt", {})["path"]
    return r


if __name__ == "__main__":
    mcp.run()
