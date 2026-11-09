"""Serveur de l'éditeur : source de vérité unique, diffusion en direct.

    .venv/Scripts/python serveur.py [dossier_projet] [--port 8765]

Interface : http://127.0.0.1:8765  ·  Le copilote IA passe par copilote_mcp.py.
"""
from __future__ import annotations

import argparse
import asyncio
import copy
import json
import os
import shutil
import struct
import subprocess
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI, File, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from moteur import export as exp
from moteur import medias, texte
from moteur.effets import BUILTIN, CONTRAT_GLSL
from moteur.projet import OPS, OpError, Projet, duree_totale, nouveau_document

ROOT = Path(__file__).parent
UI = ROOT / "ui"
# Images brutes de plusieurs Mo : pas de compression (trop lente), taille max relevée (4K = 33 Mo).
# Python qui possède faster-whisper (environnement séparé : torch, CUDA…).
_WH = ROOT.parent / "montage" / ".voice-venv" / "Scripts" / "python.exe"
WHISPER_PY = os.environ.get("EDITEUR_WHISPER_PY") or (str(_WH) if _WH.exists() else "")
UVICORN_WS = {"ws_per_message_deflate": False, "ws_max_size": 256 * 1024 * 1024}


class Etat:
    def __init__(self, dossier: Path, port: int):
        self.dossier = dossier
        self.port = port
        self.fichier = dossier / "project.json"
        doc = json.loads(self.fichier.read_text("utf-8")) if self.fichier.exists() else nouveau_document()
        self.projet = Projet(doc)
        self.clients: set[WebSocket] = set()
        self.attente: dict[str, asyncio.Future] = {}
        self.ui_state: dict = {"playhead": 0.0, "selection": [], "playing": False}
        self._save_task: asyncio.Task | None = None
        self.export: dict = {"state": "idle"}
        self.cache_mots: dict = {}
        self.proxys_a_refaire: list[str] = []

    # --- persistance (écriture atomique, regroupée) ---
    def planifier_sauvegarde(self):
        if self._save_task is None or self._save_task.done():
            self._save_task = asyncio.get_running_loop().create_task(self._sauver())

    async def _sauver(self):
        await asyncio.sleep(0.3)
        tmp = self.fichier.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.projet.doc, ensure_ascii=False, indent=1), "utf-8")
        os.replace(tmp, self.fichier)

    # --- diffusion ---
    async def diffuser(self, msg: dict, sauf: WebSocket | None = None):
        data = json.dumps(msg, ensure_ascii=False)
        morts = []
        for ws in list(self.clients):
            if ws is sauf:
                continue
            try:
                await ws.send_text(data)
            except Exception:
                morts.append(ws)
        for ws in morts:
            self.clients.discard(ws)

    async def appliquer(self, ops, label, origin, internal=False):
        results, patches = self.projet.appliquer(ops, label=label, origin=origin, internal=internal)
        if patches:
            await self.diffuser({"type": "patch", "rev": self.projet.rev, "patches": patches,
                                 "origin": origin, "label": label or ops[0]["op"]})
            self.planifier_sauvegarde()
        return results

    async def historique(self, sens):
        r = self.projet.annuler() if sens == "undo" else self.projet.retablir()
        if r is None:
            return None
        entry, patches = r
        await self.diffuser({"type": "patch", "rev": self.projet.rev, "patches": patches,
                             "origin": entry["origin"], "label": f"{'Annuler' if sens == 'undo' else 'Rétablir'} : {entry['label']}",
                             "history": sens})
        self.planifier_sauvegarde()
        return entry["label"]

    async def systeme(self, patches):
        self.projet.systeme(patches)
        await self.diffuser({"type": "patch", "rev": self.projet.rev, "patches": patches, "origin": "systeme",
                             "label": "", "silent": True})
        self.planifier_sauvegarde()

    def snapshot_msg(self):
        return {"type": "snapshot", "rev": self.projet.rev, "project": self.projet.doc,
                "builtin_effects": BUILTIN, "journal": self.projet.journal()}

    # --- requêtes vers l'interface (rendu d'image, compilation de shader) ---
    async def demander_ui(self, msg: dict, timeout=10.0):
        if not self.clients:
            raise HTTPException(409, "Aucune interface ouverte : ouvre http://127.0.0.1:%d" % self.port)
        rid = uuid.uuid4().hex
        fut = asyncio.get_running_loop().create_future()
        self.attente[rid] = fut
        ws = next(iter(self.clients))
        try:
            await ws.send_text(json.dumps({**msg, "id": rid}))
            return await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError:
            raise HTTPException(504, "L'interface n'a pas répondu à temps.")
        finally:
            self.attente.pop(rid, None)


async def verifier_medias(etat: Etat):
    """Médias importés avant la gestion de la rotation : relire leurs dimensions d'affichage."""
    for mid, m in list(etat.projet.doc["media"].items()):
        if m["kind"] != "video" or "rotation" in m or not Path(m["path"]).exists():
            continue
        try:
            info = await asyncio.to_thread(medias.probe, Path(m["path"]))
        except Exception:
            continue
        patches = [{"op": "set", "path": ["media", mid, "rotation"], "value": info.get("rotation", 0)}]
        if (info.get("width"), info.get("height")) != (m.get("width"), m.get("height")):
            patches += [{"op": "set", "path": ["media", mid, k], "value": info[k]} for k in ("width", "height")]
            # Ancien proxy fait avec la mauvaise orientation de cadre : on le refait.
            if m.get("proxy"):
                patches.append({"op": "set", "path": ["media", mid, "proxy"], "value": None})
                etat.proxys_a_refaire.append(mid)
        await etat.systeme(patches)


def creer_app(etat: Etat) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_app):
        async def demarrage():
            await verifier_medias(etat)
            for mid in etat.proxys_a_refaire:
                m = etat.projet.doc["media"][mid]
                await proxy(mid, Path(m["path"]), m["kind"])
        asyncio.get_running_loop().create_task(demarrage())
        yield

    app = FastAPI(title="editeur", lifespan=lifespan)
    origines = {f"http://127.0.0.1:{etat.port}", f"http://localhost:{etat.port}"}

    @app.middleware("http")
    async def garde_origine(request: Request, call_next):
        # Une page web quelconque ne doit pas pouvoir piloter l'éditeur local.
        o = request.headers.get("origin")
        if o is not None and o not in origines:
            return JSONResponse({"detail": "origine refusée"}, status_code=403)
        response = await call_next(request)
        if request.url.path.startswith("/ui/"):
            # Revalider à chaque chargement : une mise à jour de l interface est prise tout de suite.
            response.headers["Cache-Control"] = "no-cache"
        return response

    def err(e: OpError):
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)

    @app.get("/")
    async def index():
        return FileResponse(UI / "index.html", headers={"Cache-Control": "no-store"})

    @app.get("/api/project")
    async def get_project():
        return {"rev": etat.projet.rev, "project": etat.projet.doc,
                "duration": duree_totale(etat.projet.doc), "journal": etat.projet.journal()}

    @app.get("/api/ui_state")
    async def get_ui_state():
        return {**etat.ui_state, "interfaces_ouvertes": len(etat.clients)}

    @app.post("/api/ops")
    async def post_ops(body: dict):
        try:
            res = await etat.appliquer(body.get("ops"), body.get("label", ""), body.get("origin", "ia"))
        except OpError as e:
            return err(e)
        return {"ok": True, "results": res, "rev": etat.projet.rev}

    @app.post("/api/undo")
    async def undo():
        return {"ok": True, "undone": await etat.historique("undo")}

    @app.post("/api/redo")
    async def redo():
        return {"ok": True, "redone": await etat.historique("redo")}

    @app.get("/api/effects")
    async def effects():
        return {"contract": CONTRAT_GLSL, "builtin": BUILTIN, "custom": etat.projet.doc["effects"],
                "ops": sorted(OPS)}

    @app.post("/api/effects/define")
    async def define_effect(body: dict):
        """Compile d'abord le shader dans l'interface, n'applique que s'il compile."""
        compile_info = {"ok": None, "log": "non vérifié : aucune interface ouverte"}
        if etat.clients:
            compile_info = await etat.demander_ui({"type": "compile_request", "effect": {
                "name": body.get("name"), "glsl": body.get("glsl"), "params": body.get("params") or {}}})
            if not compile_info.get("ok"):
                return JSONResponse({"ok": False, "error": "Le shader ne compile pas.",
                                     "log": compile_info.get("log", "")}, status_code=400)
        try:
            await etat.appliquer([{**body, "op": "define_effect"}], f"Effet « {body.get('name')} »", "ia")
        except OpError as e:
            return err(e)
        return {"ok": True, "compiled": compile_info}

    @app.post("/api/snapshot")
    async def snapshot(body: dict):
        r = await etat.demander_ui({"type": "snapshot_request", "t": float(body.get("t", etat.ui_state["playhead"])),
                                    "width": int(body.get("width", 960))}, timeout=15)
        if not r.get("ok"):
            raise HTTPException(500, r.get("log", "rendu impossible"))
        return {"ok": True, "t": r.get("t"), "jpeg_base64": r["data"].split(",", 1)[1]}

    async def importer(path: Path):
        try:
            info = await asyncio.to_thread(medias.probe, path)
        except Exception as e:
            raise HTTPException(400, f"Lecture impossible : {e}")
        (mid,) = await etat.appliquer([{"op": "add_media", "media": info}], f"Import {path.name}", "ui", internal=True)
        if info["kind"] != "image" and not (info["kind"] == "audio" and info["browser_ok"]):
            asyncio.get_running_loop().create_task(proxy(mid, path, info["kind"]))
        return mid

    async def proxy(mid, path, kind):
        dst = etat.dossier / "proxies" / (f"{mid}.m4a" if kind == "audio" else f"{mid}.mp4")
        try:
            await asyncio.to_thread(medias.make_proxy, path, dst, kind)
            await etat.systeme([{"op": "set", "path": ["media", mid, "proxy"], "value": str(dst.relative_to(etat.dossier))}])
        except Exception as e:
            await etat.systeme([{"op": "set", "path": ["media", mid, "proxy_error"], "value": str(e)[:300]}])

    @app.post("/api/import")
    async def import_path(body: dict):
        p = Path(body.get("path", "")).expanduser()
        if not p.is_file():
            raise HTTPException(404, f"Fichier introuvable : {p}")
        mid = await importer(p)
        return {"ok": True, "media": etat.projet.doc["media"][mid]}

    @app.post("/api/upload")
    async def upload(file: UploadFile = File(...)):
        dest = etat.dossier / "media" / Path(file.filename or "media.bin").name
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            dest = dest.with_name(f"{dest.stem}_{uuid.uuid4().hex[:6]}{dest.suffix}")
        with dest.open("wb") as f:
            shutil.copyfileobj(file.file, f)
        mid = await importer(dest)
        return {"ok": True, "media": etat.projet.doc["media"][mid]}

    @app.get("/media/{mid}")
    async def media(mid: str, preview: int = 0):
        m = etat.projet.doc["media"].get(mid)
        if m is None:
            raise HTTPException(404)
        if preview and m.get("proxy"):
            return FileResponse(etat.dossier / m["proxy"])
        return FileResponse(m["path"])

    @app.get("/api/fonts")
    async def fonts():
        return {"fonts": await asyncio.to_thread(medias.polices_systeme)}

    @app.get("/media/{mid}/thumb")
    async def thumb(mid: str):
        m = etat.projet.doc["media"].get(mid)
        if m is None or m["kind"] == "audio":
            raise HTTPException(404)
        if m["kind"] == "image":
            return FileResponse(m["path"])
        dst = etat.dossier / "cache" / f"{mid}.jpg"
        if not dst.exists():
            await asyncio.to_thread(medias.vignette, Path(m["path"]), dst, m["duration"])
        return FileResponse(dst)

    @app.get("/media/{mid}/peaks")
    async def peaks(mid: str):
        m = etat.projet.doc["media"].get(mid)
        if m is None or m["kind"] == "image" or not m.get("has_audio", m["kind"] == "audio"):
            raise HTTPException(404)
        dst = etat.dossier / "cache" / f"{mid}.peaks.json"
        if not dst.exists():
            await asyncio.to_thread(medias.forme_onde, Path(m["path"]), dst)
        return FileResponse(dst, media_type="application/json")

    @app.websocket("/ws")
    async def ws_endpoint(ws: WebSocket):
        if ws.headers.get("origin") not in origines:
            await ws.close(code=1008)
            return
        await ws.accept()
        etat.clients.add(ws)
        await ws.send_text(json.dumps(etat.snapshot_msg(), ensure_ascii=False))
        try:
            while True:
                msg = json.loads(await ws.receive_text())
                t = msg.get("type")
                if t == "ops":
                    try:
                        res = await etat.appliquer(msg["ops"], msg.get("label", ""), "ui")
                        if msg.get("req"):
                            await ws.send_text(json.dumps({"type": "ops_result", "req": msg["req"], "ok": True,
                                                           "results": res}))
                    except OpError as e:
                        await ws.send_text(json.dumps({"type": "error", "error": str(e), "req": msg.get("req")}))
                elif t in ("undo", "redo"):
                    await etat.historique(t)
                elif t == "ui_state":
                    etat.ui_state.update({k: msg[k] for k in ("playhead", "selection", "playing") if k in msg})
                elif t == "reply":
                    fut = etat.attente.get(msg.get("id"))
                    if fut and not fut.done():
                        fut.set_result(msg)
        except WebSocketDisconnect:
            pass
        finally:
            etat.clients.discard(ws)

    # ------------------------------------------------------------ transcription et montage par le texte
    def transcripts():
        out = {}
        for mid, m in etat.projet.doc["media"].items():
            if m.get("transcript"):
                f = etat.dossier / m["transcript"]
                if f.exists():
                    if mid not in etat.cache_mots or etat.cache_mots[mid][0] != f.stat().st_mtime:
                        etat.cache_mots[mid] = (f.stat().st_mtime, json.loads(f.read_text("utf-8"))["words"])
                    out[mid] = etat.cache_mots[mid][1]
        return out

    @app.post("/api/transcribe")
    async def transcribe(body: dict):
        mid = body.get("media")
        m = etat.projet.doc["media"].get(mid)
        if m is None:
            raise HTTPException(404, f"Média inconnu : {mid!r}.")
        if m["kind"] == "image" or not (m["kind"] == "audio" or m.get("has_audio")):
            raise HTTPException(400, "Ce média n'a pas de son à transcrire.")
        if not WHISPER_PY or not Path(WHISPER_PY).exists():
            raise HTTPException(501, "Aucun Python avec faster-whisper : définis EDITEUR_WHISPER_PY.")
        if (m.get("transcript_state") or {}).get("state") == "running":
            raise HTTPException(409, "Transcription déjà en cours.")
        dst = etat.dossier / "transcripts" / f"{mid}.json"
        dst.parent.mkdir(parents=True, exist_ok=True)
        await etat.systeme([{"op": "set", "path": ["media", mid, "transcript_state"],
                             "value": {"state": "running", "progress": 0.0}}])

        async def run():
            try:
                proc = await asyncio.create_subprocess_exec(
                    WHISPER_PY, str(ROOT / "outils" / "transcrire_worker.py"), m["path"], str(dst),
                    "--modele", body.get("model", "large-v3-turbo"), "--langue", body.get("language", "fr"),
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, creationflags=exp.NO_WINDOW)
                last = 0.0
                while True:
                    line = await proc.stdout.readline()
                    if not line:
                        break
                    t = line.decode("utf-8", "replace").strip()
                    if t.startswith("PROGRESS"):
                        pr = float(t.split()[1])
                        if pr - last >= 0.05:
                            last = pr
                            await etat.systeme([{"op": "set", "path": ["media", mid, "transcript_state"],
                                                 "value": {"state": "running", "progress": round(pr, 2)}}])
                err = (await proc.stderr.read()).decode("utf-8", "replace")
                if await proc.wait() != 0 or not dst.exists():
                    raise RuntimeError(err[-600:] or "échec de la transcription")
                n = len(json.loads(dst.read_text("utf-8"))["words"])
                await etat.systeme([
                    {"op": "set", "path": ["media", mid, "transcript"], "value": str(dst.relative_to(etat.dossier))},
                    {"op": "set", "path": ["media", mid, "transcript_state"], "value": {"state": "done", "words": n}}])
            except Exception as e:
                await etat.systeme([{"op": "set", "path": ["media", mid, "transcript_state"],
                                     "value": {"state": "error", "error": str(e)[-400:]}}])

        asyncio.get_running_loop().create_task(run())
        return {"ok": True, "message": "Transcription lancée (environ la durée du média sur le processeur)."}

    @app.get("/api/transcript/{mid}")
    async def transcript(mid: str):
        words = transcripts().get(mid)
        if words is None:
            raise HTTPException(404, "Pas de transcription pour ce média.")
        return {"media": mid, "words": words}

    @app.get("/api/transcript_timeline")
    async def transcript_timeline(start: float = 0.0, end: float | None = None):
        mots = texte.mots_timeline(etat.projet.doc, transcripts())
        return {"words": [m for m in mots if m["end"] > start and (end is None or m["start"] < end)]}

    @app.post("/api/text_edit")
    async def text_edit(body: dict):
        """Coupe des hésitations, des silences, ou des mots (indices dans la transcription de la séquence)."""
        mots = texte.mots_timeline(etat.projet.doc, transcripts())
        action = body.get("action")
        if action == "fillers":
            plages, label = texte.plages_hesitations(mots), "Retirer les hésitations"
        elif action == "silences":
            seuil, garder = float(body.get("min_gap", 0.7)), float(body.get("keep", 0.25))
            plages, label = texte.plages_silences(mots, seuil, garder), f"Raccourcir les silences (> {seuil:g} s)"
        elif action == "words":
            i, j = int(body["from"]), int(body["to"])
            if not (0 <= i <= j < len(mots)):
                raise HTTPException(400, "Indices de mots hors de la transcription.")
            plages = [texte.plage_mots(mots, i, j)]
            label = "Couper : « " + " ".join(m["w"] for m in mots[i:j + 1])[:60] + " »"
        else:
            raise HTTPException(400, "action : fillers, silences ou words.")
        if not plages:
            return {"ok": True, "cuts": 0, "removed_seconds": 0}
        try:
            await etat.appliquer(texte.ops_couper(plages), body.get("label") or label, body.get("origin", "ui"))
        except OpError as e:
            return err(e)
        return {"ok": True, "cuts": len(plages), "removed_seconds": round(sum(b - a for a, b in plages), 2)}

    # ------------------------------------------------------------ export
    def chemin_defaut():
        return etat.dossier / "exports" / f"{etat.dossier.name}_{time.strftime('%Y%m%d-%H%M%S')}.mp4"

    @app.get("/api/export/options")
    async def export_options():
        return {"encoders": await asyncio.to_thread(exp.encodeurs_disponibles),
                "default_path": str(chemin_defaut()), "duration": duree_totale(etat.projet.doc),
                "settings": etat.projet.doc["settings"]}

    @app.get("/api/export/status")
    async def export_status():
        return etat.export

    @app.post("/api/export")
    async def export_request(body: dict):
        """Pour le copilote : demande à l'interface ouverte de lancer l'export."""
        if etat.export.get("state") == "running":
            raise HTTPException(409, "Un export est déjà en cours.")
        if not etat.clients:
            raise HTTPException(409, "Aucune interface ouverte : l'export a besoin du moteur de rendu de l'interface.")
        await next(iter(etat.clients)).send_text(json.dumps({"type": "export_request", "options": body}))
        return {"ok": True, "message": "Export lancé dans l'interface. Suis-le avec export_status."}

    @app.post("/api/reveal")
    async def reveal(body: dict):
        p = Path(body.get("path", ""))
        if not p.exists():
            raise HTTPException(404, "Fichier introuvable.")
        subprocess.Popen(["explorer", f"/select,{p}"])
        return {"ok": True}

    @app.websocket("/ws/export")
    async def ws_export(ws: WebSocket):
        if ws.headers.get("origin") not in origines:
            await ws.close(code=1008)
            return
        await ws.accept()
        if etat.export.get("state") == "running":
            await ws.send_text(json.dumps({"type": "error", "error": "Un export est déjà en cours."}))
            await ws.close()
            return
        opts = json.loads(await ws.receive_text())
        doc = copy.deepcopy(etat.projet.doc)
        st = doc["settings"]
        W, H, fps = st["width"], st["height"], st["fps"]
        ow = int(opts.get("width") or W) // 2 * 2
        oh = int(opts.get("height") or H) // 2 * 2
        t0 = max(0.0, float(opts.get("start") or 0))
        t1 = float(opts.get("end") or duree_totale(doc))
        if t1 - t0 < 1 / fps:
            await ws.send_text(json.dumps({"type": "error", "error": "Rien à exporter : la séquence est vide."}))
            await ws.close()
            return
        out = Path(opts.get("path") or chemin_defaut()).expanduser()
        if out.suffix.lower() != ".mp4":
            out = out.with_suffix(".mp4")
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp = out.with_name(out.stem + ".part.mp4")
        encoder = opts.get("encoder", "x264")
        if encoder not in await asyncio.to_thread(exp.encodeurs_disponibles):
            encoder = "x264"
        inputs, graph, alab = exp.audio_graph(doc, t0, t1)
        cache = etat.dossier / "cache"
        cache.mkdir(exist_ok=True)
        graph_file = None
        if graph:
            graph_file = cache / "export_audio.txt"
            graph_file.write_text(graph, "utf-8")
        # YUV 4:2:0 dans les deux sens (3 Mo au lieu de 8 en 1080p) quand la largeur s'y prête.
        fmt = "yuv420p" if ow % 8 == 0 and oh % 2 == 0 and opts.get("fmt") != "rgba" else "rgba"
        expected = ow * oh * 3 // 2 if fmt == "yuv420p" else ow * oh * 4
        log_path = cache / "export_ffmpeg.log"
        log = log_path.open("wb")
        enc = subprocess.Popen(exp.encoder_command(tmp, ow, oh, fps, t1 - t0, opts.get("quality", "haute"), encoder,
                                                   inputs, graph_file, alab, in_fmt=fmt),
                               stdin=subprocess.PIPE, stderr=log, creationflags=exp.NO_WINDOW)
        n = exp.frame_count(t0, t1, fps)
        started = time.time()
        etat.export = {"state": "running", "frame": 0, "frames": n, "path": str(out), "fps": 0.0,
                       "encoder": encoder, "width": ow, "height": oh}
        await ws.send_text(json.dumps({"type": "plan", "frames": n, "width": ow, "height": oh, "fps": fps,
                                       "t0": t0, "doc": doc, "encoder": encoder, "path": str(out), "fmt": fmt},
                                      ensure_ascii=False))
        decs: dict = {}
        out_scale = min(ow / W, oh / H)
        try:
            # À la chaîne : pendant que l'interface rend l'image i, on décode déjà
            # l'image i+1 et on encode l'image i-1.
            tim = {"decode": 0.0, "rendu": 0.0, "encode": 0.0}

            def build(i):
                t0b = time.time()
                t = exp.frame_time(t0, i, fps)
                active = exp.video_clips_at(doc, t)
                ids = {c["id"] for c in active}
                for cid in [k for k in decs if k not in ids]:
                    decs.pop(cid).close()
                head, payload = [], []
                for c in active:
                    d = decs.get(c["id"])
                    if d is None:
                        w, h = exp.decode_size(doc, c, out_scale)
                        d = decs[c["id"]] = exp.Decodeur(
                            doc["media"][c["media"]]["path"], c["in"] + (t - c["start"]) * c["speed"], c["speed"],
                            fps, w, h, c["start"] + c["duration"] - t)
                    payload.append(d.read())
                    head.append({"id": c["id"], "w": d.w, "h": d.h, "fmt": d.fmt})
                hb = json.dumps({"i": i, "t": t, "clips": head}).encode()
                tim["decode"] += time.time() - t0b
                return struct.pack("<I", len(hb)) + hb + b"".join(payload)

            def write(frame):
                t0w = time.time()
                enc.stdin.write(frame)
                tim["encode"] += time.time() - t0w

            nxt = asyncio.create_task(asyncio.to_thread(build, 0))
            writing = None
            sent = 0

            async def send_next():
                nonlocal sent, nxt
                pkt = await nxt
                await ws.send_bytes(pkt)
                sent += 1
                if sent < n:
                    nxt = asyncio.create_task(asyncio.to_thread(build, sent))

            # Deux images en vol : l'interface rend l'une pendant que l'autre voyage.
            await send_next()
            if n > 1:
                await send_next()
            for i in range(n):
                t0r = time.time()
                msg = await ws.receive()
                tim["rendu"] += time.time() - t0r
                if msg["type"] == "websocket.disconnect" or msg.get("text"):
                    raise asyncio.CancelledError
                frame = msg.get("bytes") or b""
                if len(frame) != expected:
                    raise RuntimeError(f"Image {i} : {len(frame)} octets reçus au lieu de {expected}.")
                if sent < n:
                    await send_next()
                if writing is not None:
                    await writing
                writing = asyncio.create_task(asyncio.to_thread(write, frame))
                el = time.time() - started
                etat.export.update(frame=i + 1, fps=round((i + 1) / el, 1) if el > 0 else 0.0,
                                   timing={k: round(v, 1) for k, v in tim.items()})
            if writing is not None:
                await writing
            enc.stdin.close()
            rc = await asyncio.to_thread(enc.wait)
            log.close()
            if rc != 0:
                raise RuntimeError("FFmpeg a échoué : " + log_path.read_text("utf-8", "replace")[-800:])
            os.replace(tmp, out)
            el = time.time() - started
            etat.export = {"state": "done", "path": str(out), "frames": n, "seconds": round(el, 1),
                           "size_mb": round(out.stat().st_size / 1e6, 1), "encoder": encoder,
                           "fps": round(n / el, 1) if el > 0 else 0.0, "timing": {k: round(v, 1) for k, v in tim.items()}}
            await ws.send_text(json.dumps({"type": "done", **etat.export}))
        except (asyncio.CancelledError, WebSocketDisconnect):
            etat.export = {"state": "cancelled", "path": str(out)}
        except Exception as e:
            etat.export = {"state": "error", "error": str(e)}
            try:
                await ws.send_text(json.dumps({"type": "error", "error": str(e)}))
            except Exception:
                pass
        finally:
            for d in decs.values():
                d.close()
            if enc.poll() is None:
                enc.kill()
            if not log.closed:
                log.close()
            if tmp.exists():
                try:
                    tmp.unlink()
                except OSError:
                    pass

    app.mount("/ui", StaticFiles(directory=UI), name="ui")
    return app


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dossier", nargs="?", default=str(ROOT / "projets" / "demo"))
    ap.add_argument("--port", type=int, default=int(os.environ.get("EDITEUR_PORT", 8765)))
    a = ap.parse_args()
    dossier = Path(a.dossier).resolve()
    dossier.mkdir(parents=True, exist_ok=True)
    etat = Etat(dossier, a.port)
    print(f"Projet : {dossier}\nInterface : http://127.0.0.1:{a.port}")
    uvicorn.run(creer_app(etat), host="127.0.0.1", port=a.port, log_level="warning", **UVICORN_WS)


if __name__ == "__main__":
    main()
