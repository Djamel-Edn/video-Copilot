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
import re
import sys
import time
import uuid
from collections import deque
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI, File, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from moteur import export as exp
from moteur import audio, gpu, images, medias, sous_titres, texte
from moteur.effets import BUILTIN, CONTRAT_GLSL
from moteur.projet import OPS, OpError, Projet, duree_totale, nouveau_document, spans

# Exécutable (PyInstaller) : ui/, outils/ et moteur/ sont embarqués dans sys._MEIPASS ; le dépôt
# n'est plus à côté, donc Documents/montage se cherche depuis le dossier personnel.
FIGE = getattr(sys, "frozen", False)
ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
UI = ROOT / "ui"
MONTAGE = Path(os.environ.get("EDITEUR_MONTAGE")
               or (Path.home() / "Documents" / "montage" if FIGE else ROOT.parent / "montage"))
# Images brutes de plusieurs Mo : pas de compression (trop lente), taille max relevée (4K = 33 Mo).
# Python qui possède faster-whisper (environnement séparé : torch, CUDA…).
_WH = MONTAGE / ".voice-venv" / "Scripts" / "python.exe"
WHISPER_PY = os.environ.get("EDITEUR_WHISPER_PY") or (str(_WH) if _WH.exists() else "")
# Génération d'images : même environnement local par défaut (torch + diffusers + peft).
IMAGE_PY = os.environ.get("EDITEUR_IMAGE_PY") or WHISPER_PY
PERF_IMAGES = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "editeur" / "images_perf.json"
UVICORN_WS = {"ws_per_message_deflate": False, "ws_max_size": 256 * 1024 * 1024}


# Données de l'utilisateur (projets récents…). EDITEUR_DONNEES les redirige pendant les essais,
# pour qu'un test n'écrase pas la liste des projets que l'exécutable rouvre.
DONNEES = Path(os.environ.get("EDITEUR_DONNEES") or Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "editeur")
RECENTS = DONNEES / "recents.json"


def projet_de(dossier: Path) -> Path | None:
    """Le dossier de projet à ouvrir pour ce choix, ou None si ce n'en est pas un. Choisir le
    dossier d'une vidéo (montage/<vidéo>) ouvre son assemblage."""
    if (dossier / "project.json").is_file():
        return dossier
    if (dossier / "assemblage" / "project.json").is_file():
        return dossier / "assemblage"
    return None


def nom_projet(dossier: Path) -> str:
    """« paradoxe-creme-solaire » plutôt que « assemblage » pour l'assemblage d'une vidéo."""
    return dossier.parent.name if dossier.name == "assemblage" else dossier.name


def recents() -> list[str]:
    try:
        return [p for p in json.loads(RECENTS.read_text("utf-8")) if (Path(p) / "project.json").is_file()]
    except (OSError, ValueError):
        return []


def memoriser_projet(dossier: Path) -> None:
    """Projets récents (le premier est rouvert par le double-clic de l'exécutable)."""
    try:
        DONNEES.mkdir(parents=True, exist_ok=True)
        liste = [str(dossier)] + [p for p in recents() if Path(p) != dossier]
        RECENTS.write_text(json.dumps(liste[:8], ensure_ascii=False, indent=1), "utf-8")
        (DONNEES / "dernier_projet.txt").write_text(str(dossier), "utf-8")
    except OSError:
        pass


class Etat:
    def __init__(self, dossier: Path, port: int):
        self.port = port
        self.clients: set[WebSocket] = set()
        self.attente: dict[str, asyncio.Future] = {}
        self._save_task: asyncio.Task | None = None
        self.export: dict = {"state": "idle"}
        self.charger(dossier)
        self.images = GenerateurImages(self)

    def charger(self, dossier: Path):
        """(Re)charge un projet dans cet état : au démarrage, et quand l'utilisateur en ouvre un autre."""
        self.dossier = dossier
        self.fichier = dossier / "project.json"
        doc = json.loads(self.fichier.read_text("utf-8")) if self.fichier.exists() else nouveau_document()
        self.projet = Projet(doc)
        self.ui_state = {"playhead": 0.0, "selection": [], "playing": False}
        self.cache_mots = {}
        self.proxys_a_refaire: list[str] = []

    async def ecrire_maintenant(self):
        """Sauvegarde immédiate (avant de changer de projet : rien ne doit se perdre)."""
        if self._save_task and not self._save_task.done():
            self._save_task.cancel()
        tmp = self.fichier.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.projet.doc, ensure_ascii=False, indent=1), "utf-8")
        os.replace(tmp, self.fichier)

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


class GenerateurImages:
    """Processus de génération persistant (modèle chargé une fois) et file de travaux.

    Les états et la progression partent vers l'interface en messages « images ». Le
    processus s'arrête après IDLE secondes sans travail pour rendre la mémoire (~4 Go)."""
    IDLE = 300

    def __init__(self, etat: "Etat"):
        self.etat = etat
        self.proc: asyncio.subprocess.Process | None = None
        self.device: str | None = None
        self.state = "off"          # off | starting | downloading | loading | ready | busy | error
        self.detail: dict = {}
        self.erreur: str | None = None
        self.jobs: dict[str, dict] = {}
        self.ordre: list[str] = []
        self.file: asyncio.Queue | None = None
        self.task: asyncio.Task | None = None
        self.stderr: deque = deque(maxlen=60)
        self.pref = "auto"
        self.gpus = None
        self.importer = None        # branché par creer_app
        self.placer = None
        self._dernier_envoi = 0.0

    # --- mesures de vitesse propres à cette machine ---
    def perf(self) -> dict:
        try:
            return json.loads(PERF_IMAGES.read_text("utf-8"))
        except (OSError, ValueError):
            return {}

    def noter_perf(self, device: str, v: float):
        d = self.perf()
        d[device] = {"v": v, "date": time.strftime("%Y-%m-%d %H:%M")}
        PERF_IMAGES.parent.mkdir(parents=True, exist_ok=True)
        PERF_IMAGES.write_text(json.dumps(d, indent=1), "utf-8")

    def calcul(self) -> str:
        if self.gpus is None:
            self.gpus = gpu.detecter()
        return images.choisir_calcul(self.pref, self.gpus, self.perf())

    def status(self) -> dict:
        if self.gpus is None:
            self.gpus = gpu.detecter()
        spec = images.MODELES["leger"]
        return {"available": bool(IMAGE_PY and Path(IMAGE_PY).exists()), "state": self.state,
                "detail": self.detail, "error": self.erreur, "device": self.device, "pref": self.pref,
                "calcul": self.calcul(), "perf": self.perf(), "weights": images.poids_presents(),
                "model": spec["label"], "poids_go": spec["poids_go"], "licence": spec["licence"],
                "gpus": [{"name": g.name, "vendor": g.vendor, "vram_mo": g.vram_mo} for g in self.gpus],
                "styles": {k: v[0] for k, v in images.STYLES.items()}, "formats": list(images.FORMATS),
                "jobs": [self.jobs[j] for j in self.ordre[-30:]]}

    async def publier(self, force=True):
        now = time.time()
        if not force and now - self._dernier_envoi < 0.4:
            return
        self._dernier_envoi = now
        await self.etat.diffuser({"type": "images", **self.status()})

    # --- travaux ---
    def soumettre(self, req: dict, origin: str) -> dict:
        doc = self.etat.projet.doc
        style = req.get("style") or "illustration"
        prompt = images.prompt_complet(req.get("prompt", ""), style)
        aspect = req.get("aspect") or "sequence"
        w, h = images.taille(doc, aspect)
        qualite = req.get("quality") or "rapide"
        if qualite not in images.QUALITES:
            raise ValueError(f"Qualité inconnue : {qualite!r}. Possibles : {list(images.QUALITES)}.")
        count = max(1, min(int(req.get("count") or 1), 4))
        seed = req.get("seed")
        if seed is not None and seed != "":
            seed = int(seed)
        else:
            seed = None
        jid = "img" + uuid.uuid4().hex[:8]
        slug = re.sub(r"[^a-z0-9]+", "_", req["prompt"].lower())[:32].strip("_") or "image"
        job = {"id": jid, "state": "queued", "prompt": req["prompt"].strip(), "full_prompt": prompt,
               "negative": req.get("negative") or images.NEGATIF, "style": style, "aspect": aspect,
               "quality": qualite, "count": count, "seed": seed, "width": w, "height": h, "origin": origin,
               "slug": slug, "place": req.get("place"), "progress": 0.0, "media": [], "clips": [],
               "error": None, "seconds": None, "created": time.time()}
        est = images.estimation(self.perf(), self.calcul(), w, h, qualite, count)
        job["estimate_s"] = round(est) if est else None
        self.jobs[jid] = job
        self.ordre.append(jid)
        if self.file is None:
            self.file = asyncio.Queue()
        self.file.put_nowait(jid)
        if self.task is None or self.task.done():
            self.task = asyncio.get_running_loop().create_task(self.boucle())
        return job

    async def annuler(self, jid: str) -> bool:
        job = self.jobs.get(jid)
        if not job or job["state"] not in ("queued", "running"):
            return False
        running = job["state"] == "running"
        job.update(state="cancelled")
        if running:
            await self.arreter()        # torch ne s'interrompt pas proprement : on arrête le processus
        await self.publier()
        return True

    async def boucle(self):
        while True:
            try:
                jid = await asyncio.wait_for(self.file.get(), self.IDLE)
            except asyncio.TimeoutError:
                await self.arreter()
                await self.publier()
                return
            job = self.jobs.get(jid)
            if not job or job["state"] != "queued":
                continue
            try:
                await self.executer(job)
            except Exception as e:
                if job["state"] != "cancelled":
                    job.update(state="error", error=str(e)[-600:])
            await self.publier()

    async def _lire(self) -> dict | None:
        proc = self.proc
        while proc is not None:
            line = await proc.stdout.readline()
            if not line:
                return None
            try:
                return json.loads(line.decode("utf-8", "replace"))
            except ValueError:
                continue
        return None

    async def _vider_stderr(self, proc):
        while True:
            line = await proc.stderr.readline()
            if not line:
                return
            self.stderr.append(line.decode("utf-8", "replace").rstrip())

    def _erreur_proc(self, msg: str) -> str:
        tail = [l for l in self.stderr if l and "warn" not in l.lower()][-6:]
        return msg + ("\n" + "\n".join(tail) if tail else "")

    async def assurer(self, device: str):
        if self.proc and self.proc.returncode is None and self.device == device:
            return
        await self.arreter()
        if not IMAGE_PY or not Path(IMAGE_PY).exists():
            raise RuntimeError("Aucun Python avec torch + diffusers : définis EDITEUR_IMAGE_PY.")
        self.state, self.detail, self.erreur = "starting", {"device": device}, None
        await self.publier()
        env = {**os.environ, "HF_HUB_DISABLE_SYMLINKS_WARNING": "1", "PYTHONIOENCODING": "utf-8"}
        self.proc = await asyncio.create_subprocess_exec(
            IMAGE_PY, str(ROOT / "outils" / "image_worker.py"), "--accel", device,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            env=env, creationflags=exp.NO_WINDOW, limit=1 << 20)
        self.stderr.clear()
        asyncio.get_running_loop().create_task(self._vider_stderr(self.proc))
        self.state = "loading"
        await self.publier()
        while True:
            ev = await self._lire()
            if ev is None:
                if self.proc:
                    await self.proc.wait()
                self.proc = None
                self.state = "error"
                self.erreur = self._erreur_proc("Le générateur s'est arrêté au démarrage.")
                raise RuntimeError(self.erreur)
            if ev["event"] == "download":
                self.state, self.detail = "downloading", ev
                await self.publier(force=False)
            elif ev["event"] == "ready":
                self.device, self.state, self.detail = ev["device"], "ready", ev
                return
            elif ev["event"] == "fatal":
                await self.arreter()
                self.state, self.erreur = "error", ev["error"]
                raise RuntimeError(ev["error"])

    async def executer(self, job: dict):
        await self.assurer(self.calcul())
        job["state"], job["started"], job["device"] = "running", time.time(), self.device
        self.state = "busy"
        await self.publier()
        out_dir = self.etat.dossier / "media" / "ia"
        req = {"id": job["id"], "prompt": job["full_prompt"], "negative": job["negative"],
               "width": job["width"], "height": job["height"], "quality": job["quality"],
               "seed": job["seed"], "count": job["count"], "out_dir": str(out_dir), "stem": job["slug"]}
        self.proc.stdin.write((json.dumps(req) + "\n").encode())
        await self.proc.stdin.drain()
        while True:
            ev = await self._lire()
            if ev is None:
                self.proc = None
                self.state = "off"
                if job["state"] == "cancelled":
                    return
                raise RuntimeError(self._erreur_proc("Le générateur s'est arrêté pendant le calcul."))
            if ev.get("id") != job["id"]:
                continue
            if ev["event"] == "progress":
                job["progress"] = round((ev["image"] + ev["step"] / ev["steps"]) / ev["count"] * 0.97, 3)
                job["step"] = f"image {ev['image'] + 1}/{ev['count']} · étape {ev['step']}/{ev['steps']}"
                await self.publier(force=False)
            elif ev["event"] == "error":
                self.state = "ready"
                raise RuntimeError(ev["error"])
            elif ev["event"] == "done":
                self.state = "ready"
                self.noter_perf(self.device, ev["s_par_etape_mpx"])
                mids = []
                for f, seed in zip(ev["files"], ev["seeds"]):
                    meta = {"prompt": job["prompt"], "full_prompt": job["full_prompt"], "negative": job["negative"],
                            "style": job["style"], "aspect": job["aspect"], "quality": job["quality"], "seed": seed,
                            "model": images.MODELES["leger"]["base"], "device": self.device}
                    mids.append(await self.importer(Path(f), meta, job["origin"]))
                job.update(state="done", progress=1.0, media=mids, seeds=ev["seeds"], seconds=ev["seconds"])
                if job.get("place"):
                    job["clips"] = await self.placer(job)
                return

    async def arreter(self):
        p, self.proc = self.proc, None
        self.device = None
        if p and p.returncode is None:
            try:
                p.stdin.write(b'{"cmd": "quit"}\n')
                await asyncio.wait_for(p.wait(), 3)
            except Exception:
                p.kill()
                await p.wait()
        if self.state != "error":
            self.state = "off"


async def verifier_medias(etat: Etat):
    """Médias importés avant la gestion de la rotation : relire leurs dimensions d'affichage."""
    for mid, m in list(etat.projet.doc["media"].items()):
        a_relire = (m["kind"] == "video" and "rotation" not in m) or             (m["kind"] != "image" and m.get("has_audio", m["kind"] == "audio") and "channels" not in m)
        if not a_relire or not Path(m["path"]).exists():
            continue
        try:
            info = await asyncio.to_thread(medias.probe, Path(m["path"]))
        except Exception:
            continue
        patches = [{"op": "set", "path": ["media", mid, "channels"], "value": info.get("channels", 0)}]
        if m["kind"] == "video":
            patches.append({"op": "set", "path": ["media", mid, "rotation"], "value": info.get("rotation", 0)})
        if (info.get("width"), info.get("height")) != (m.get("width"), m.get("height")):
            patches += [{"op": "set", "path": ["media", mid, k], "value": info[k]} for k in ("width", "height")]
            # Ancien proxy fait avec la mauvaise orientation de cadre : on le refait.
            if m.get("proxy"):
                patches.append({"op": "set", "path": ["media", mid, "proxy"], "value": None})
                etat.proxys_a_refaire.append(mid)
        await etat.systeme(patches)


def creer_app(etat: Etat) -> FastAPI:
    async def demarrage():
        """À l'ouverture d'un projet : anciens médias relus, proxys perdus repris."""
        await verifier_medias(etat)
        # Proxys perdus par un arrêt pendant leur calcul : relier ceux qui sont finis,
        # refaire les autres (sinon la vidéo reste lue sans proxy, ou pas du tout).
        medias.nettoyer_partiels(etat.dossier)
        a_relier, a_refaire = medias.proxys_a_reprendre(etat.projet.doc, etat.dossier)
        if a_relier:
            await etat.systeme([{"op": "set", "path": ["media", mid, "proxy"], "value": rel}
                                for mid, rel in a_relier.items()])
        etat.proxys_a_refaire += [mid for mid in a_refaire if mid not in etat.proxys_a_refaire]
        for mid in list(etat.proxys_a_refaire):
            m = etat.projet.doc["media"][mid]
            await proxy(mid, Path(m["path"]), m["kind"])

    @asynccontextmanager
    async def lifespan(_app):
        asyncio.get_running_loop().create_task(demarrage())
        yield
        await etat.images.arreter()

    app = FastAPI(title="editeur", lifespan=lifespan)
    origines = {f"http://127.0.0.1:{etat.port}", f"http://localhost:{etat.port}"}

    # ------------------------------------------------------------ projet ouvert, changer de projet
    @app.get("/api/projet")
    async def projet_ouvert():
        return {"dossier": str(etat.dossier), "nom": nom_projet(etat.dossier),
                "recents": [{"dossier": p, "nom": nom_projet(Path(p))} for p in recents() if Path(p) != etat.dossier]}

    @app.post("/api/ouvrir")
    async def ouvrir(body: dict):
        """Ouvre un autre projet sans redémarrer. Un dossier sans project.json n'est pas ouvert
        (sauf creer: true) : ouvrir par erreur Documents/montage y aurait créé un projet vide."""
        brut = str(body.get("dossier") or "").strip().strip('"')
        if not brut:
            raise HTTPException(400, "Donne le dossier du projet.")
        d = Path(brut).expanduser()
        if not d.is_absolute():
            raise HTTPException(400, "Donne un chemin complet (ex. C:\\Users\\…\\assemblage).")
        cible = projet_de(d) if d.is_dir() else None
        if cible is None:
            if not body.get("creer"):
                return JSONResponse({"ok": False, "pas_un_projet": True, "dossier": str(d),
                                     "error": f"« {d} » ne contient pas de projet (project.json)."}, status_code=404)
            d.mkdir(parents=True, exist_ok=True)
            cible = d
        cible = cible.resolve()
        if cible == etat.dossier.resolve():
            return {"ok": True, "dossier": str(cible), "nom": nom_projet(cible), "deja": True}
        if etat.export.get("state") in ("starting", "running"):
            raise HTTPException(409, "Un export est en cours : attends sa fin pour changer de projet.")
        await etat.ecrire_maintenant()
        await etat.images.arreter()
        etat.charger(cible)
        memoriser_projet(cible)
        await etat.diffuser({**etat.snapshot_msg(), "projet": {"dossier": str(cible), "nom": nom_projet(cible)}})
        asyncio.get_running_loop().create_task(demarrage())
        return {"ok": True, "dossier": str(cible), "nom": nom_projet(cible)}

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
        params_compil = dict(body.get("params") or {})
        if body.get("kind") == "transition":   # p_progress est fourni par le moteur
            params_compil.setdefault("progress", {"type": "float", "default": 0.0, "min": 0.0, "max": 1.0})
        if etat.clients:
            compile_info = await etat.demander_ui({"type": "compile_request", "effect": {
                "name": body.get("name"), "glsl": body.get("glsl"), "params": params_compil}})
            if not compile_info.get("ok"):
                return JSONResponse({"ok": False, "error": "Le shader ne compile pas.",
                                     "log": compile_info.get("log", "")}, status_code=400)
        try:
            origine = "ui" if body.get("origin") == "ui" else "ia"   # modifié à la main depuis le panneau Effets
            await etat.appliquer([{**body, "op": "define_effect"}], f"Effet « {body.get('name')} »", origine)
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

    async def importer(path: Path, generated: dict | None = None, origin: str = "ui"):
        try:
            info = await asyncio.to_thread(medias.probe, path)
        except Exception as e:
            raise HTTPException(400, f"Lecture impossible : {e}")
        label = f"Import {path.name}"
        if generated:
            info["generated"] = generated
            info["name"] = generated["prompt"][:60]
            label = f"Image IA : {generated['prompt'][:40]}"
        (mid,) = await etat.appliquer([{"op": "add_media", "media": info}], label, origin, internal=True)
        if medias.besoin_proxy(info):
            asyncio.get_running_loop().create_task(proxy(mid, path, info["kind"]))
        return mid

    async def proxy(mid, path, kind):
        m = etat.projet.doc["media"].get(mid, {})
        dst = medias.chemin_proxy(etat.dossier, mid, kind, m.get("alpha", False))
        try:
            await asyncio.to_thread(medias.make_proxy, path, dst, kind, m)
            await etat.systeme([{"op": "set", "path": ["media", mid, "proxy"], "value": str(dst.relative_to(etat.dossier))}])
        except Exception as e:
            await etat.systeme([{"op": "set", "path": ["media", mid, "proxy_error"], "value": str(e)[:300]}])

    # ------------------------------------------------------------ images d'illustration (IA locale)
    async def placer_images(job):
        pl = job["place"] or {}
        at = float(pl.get("at", etat.ui_state["playhead"]))
        ops = images.ops_placer(etat.projet.doc, job["media"], at, float(pl.get("duration", 4.0)), pl.get("track"),
                                bool(pl.get("zoom", True)))
        res = await etat.appliquer(ops, f"Illustration : {job['prompt'][:40]}", job["origin"])
        return [r for o, r in zip(ops, res) if o["op"] == "add_clip"]

    etat.images.importer = importer
    etat.images.placer = placer_images

    @app.get("/api/images/status")
    async def images_status():
        return await asyncio.to_thread(etat.images.status)

    @app.post("/api/images/generate")
    async def images_generate(body: dict):
        try:
            job = etat.images.soumettre(body, body.get("origin", "ui"))
        except (ValueError, KeyError, TypeError) as e:
            raise HTTPException(400, str(e))
        await etat.images.publier()
        return {"ok": True, "job": job}

    @app.get("/api/images/jobs/{jid}")
    async def images_job(jid: str):
        job = etat.images.jobs.get(jid)
        if job is None:
            raise HTTPException(404, f"Travail inconnu : {jid!r}.")
        return job

    @app.post("/api/images/cancel")
    async def images_cancel(body: dict):
        return {"ok": await etat.images.annuler(body.get("job", ""))}

    @app.post("/api/images/settings")
    async def images_settings(body: dict):
        pref = body.get("device", "auto")
        if pref not in ("auto", "cuda", "cpu"):
            raise HTTPException(400, "device : auto, cuda ou cpu.")
        etat.images.pref = pref
        await etat.images.publier()
        return {"ok": True, "calcul": etat.images.calcul()}

    @app.post("/api/import")
    async def import_path(body: dict):
        p = Path(body.get("path", "")).expanduser()
        if not p.is_file():
            raise HTTPException(404, f"Fichier introuvable : {p}")
        refus = pas_un_media(p.name)
        if refus:
            raise HTTPException(400, refus)
        mid = await importer(p)
        return {"ok": True, "media": etat.projet.doc["media"][mid]}

    def pas_un_media(nom: str) -> str | None:
        """Fichiers qu'on glisse ou importe par erreur : on explique au lieu de les copier."""
        n = nom.lower()
        if n == "project.json" or n.endswith(".json"):
            return ("C'est un fichier de projet, pas un média : ouvre-le avec le bouton « Projet » "
                    "(choisis le dossier qui le contient).")
        if n.endswith((".html", ".htm")):
            return ("C'est une composition HyperFrames : passe-la par le pont "
                    "(montage/outils/vers_editeur.py <scène> --calques), qui en fait des clips.")
        return None

    @app.post("/api/upload")
    async def upload(file: UploadFile = File(...)):
        refus = pas_un_media(file.filename or "")
        if refus:
            raise HTTPException(400, refus)
        dest = etat.dossier / "media" / Path(file.filename or "media.bin").name
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            dest = dest.with_name(f"{dest.stem}_{uuid.uuid4().hex[:6]}{dest.suffix}")
        with dest.open("wb") as f:
            shutil.copyfileobj(file.file, f)
        try:
            mid = await importer(dest)
        except HTTPException:
            dest.unlink(missing_ok=True)     # illisible : ne pas laisser la copie traîner dans media/
            raise
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

    # Polices du projet : <projet>/polices/polices.json = [{family, weight, style, unicodeRange?, fichier}]
    # (le pont montage → éditeur y copie les polices exactes d'une composition HyperFrames).
    @app.get("/api/polices")
    async def polices_projet():
        d = etat.dossier / "polices"
        f = d / "polices.json"
        if not f.exists():
            return {"polices": []}
        try:
            lst = json.loads(f.read_text("utf-8"))
        except ValueError:
            return {"polices": []}
        return {"polices": [{**p, "url": f"/polices/{p['fichier']}"} for p in lst
                            if isinstance(p, dict) and p.get("family") and (d / str(p.get("fichier", ""))).is_file()]}

    @app.get("/polices/{nom}")
    async def fichier_police(nom: str):
        d = (etat.dossier / "polices").resolve()
        p = (d / nom).resolve()
        if p.parent != d or not p.is_file():
            raise HTTPException(404, "Police introuvable.")
        return FileResponse(p, headers={"Cache-Control": "no-cache"})

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

    # ------------------------------------------------------------ musique sous la voix
    async def pics_media(mid):
        m = etat.projet.doc["media"][mid]
        dst = etat.dossier / "cache" / f"{mid}.peaks.json"
        if not dst.exists():
            await asyncio.to_thread(medias.forme_onde, Path(m["path"]), dst)
        return json.loads(dst.read_text("utf-8"))

    @app.post("/api/duck")
    async def duck(body: dict):
        """Baisse les clips de musique désignés pendant la voix (images clés de volume)."""
        doc = etat.projet.doc
        music = [cid for cid in body.get("clips") or [] if cid in doc["clips"]]
        if not music:
            raise HTTPException(400, "Désigne le ou les clips de musique à baisser.")
        mots = [m for m in texte.mots_timeline(doc, transcripts()) if m["clip"] not in music]
        if mots:
            zones, source = audio.zones_depuis_mots(mots), "transcription"
        else:
            voix = []
            for c in doc["clips"].values():
                m = doc["media"].get(c.get("media")) if c["type"] == "media" else None
                tr = next((t for t in doc["tracks"] if t["id"] == c["track"]), None)
                if (c["id"] in music or m is None or m["kind"] == "image" or c.get("audio_muted")
                        or not (m["kind"] == "audio" or m.get("has_audio")) or (tr and tr.get("muted"))):
                    continue
                voix.append(c)
            pics = {}
            for c in voix:
                if c["media"] not in pics:
                    pics[c["media"]] = await pics_media(c["media"])
            zones, source = audio.zones_depuis_pics(voix, pics, float(body.get("threshold", 0.06))), "forme d'onde"
        if not zones:
            raise HTTPException(400, "Aucune voix trouvée sous ces clips (transcris la voix, ou vérifie qu'elle est sur une autre piste).")
        ops = audio.ops_baisse(doc, music, zones, float(body.get("depth_db", -12)), float(body.get("attack", 0.25)),
                               float(body.get("release", 0.5)))
        try:
            await etat.appliquer(ops, body.get("label") or "Baisser la musique sous la voix", body.get("origin", "ui"))
        except OpError as e:
            return err(e)
        return {"ok": True, "zones": len(zones), "source": source, "clips": music}

    # ------------------------------------------------------------ sous-titres automatiques
    @app.get("/api/subtitles/styles")
    async def subtitle_styles():
        return {k: {"label": v["label"], "max_chars": v["max_chars"], "max_lines": v["max_lines"],
                    "max_words": v["max_words"]} for k, v in sous_titres.STYLES.items()}

    @app.post("/api/subtitles")
    async def subtitles(body: dict):
        style = body.get("style", "classique")
        if style not in sous_titres.STYLES:
            raise HTTPException(400, f"Style inconnu : {style!r}. Styles : {list(sous_titres.STYLES)}.")
        st = sous_titres.STYLES[style]
        start = float(body.get("start") or 0.0)
        end = body.get("end")
        end = None if end is None else float(end)
        mots = [m for m in texte.mots_timeline(etat.projet.doc, transcripts())
                if m["end"] > start and (end is None or m["start"] < end)]
        if not mots:
            raise HTTPException(400, "Aucun mot dans la séquence : transcris d'abord un média (onglet Parole).")
        caps = sous_titres.decouper(mots, int(body.get("max_chars") or st["max_chars"]),
                                    int(body.get("max_lines") or st["max_lines"]),
                                    body.get("max_words") or st["max_words"])
        ops = sous_titres.ops(etat.projet.doc, caps, style, body.get("replace", True), start, end,
                              keep_edited=body.get("keep_edited", True))
        poses = sum(o["op"] == "add_clip" for o in ops)
        if ops:     # rien à faire si tout est déjà retouché à la main : pas une erreur
            try:
                await etat.appliquer(ops, body.get("label") or f"Sous-titres automatiques ({poses})", body.get("origin", "ui"))
            except OpError as e:
                return err(e)
        tr = sous_titres.piste(etat.projet.doc)
        return {"ok": True, "count": poses, "kept_edited": ops.gardes, "track": tr["id"] if tr else None}

    @app.get("/api/subtitles.srt")
    async def subtitles_srt():
        data = sous_titres.srt(etat.projet.doc)
        if not data:
            raise HTTPException(404, "Pas de piste de sous-titres.")
        name = f"{etat.dossier.name}.srt"
        return Response(data.encode("utf-8-sig"), media_type="application/x-subrip",
                        headers={"Content-Disposition": f'attachment; filename="{name}"'})

    @app.post("/api/subtitles/save_srt")
    async def subtitles_save_srt(body: dict):
        """Écrit le .srt à côté des exports (pour l'envoyer à YouTube avec la vidéo)."""
        data = sous_titres.srt(etat.projet.doc)
        if not data:
            raise HTTPException(404, "Pas de piste de sous-titres.")
        out = Path(body.get("path") or (etat.dossier / "exports" / f"{etat.dossier.name}.srt"))
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(data, "utf-8-sig")
        return {"ok": True, "path": str(out), "count": data.count(" --> ")}

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
        if etat.export.get("state") in ("running", "starting"):
            raise HTTPException(409, "Un export est déjà en cours.")
        if not etat.clients:
            raise HTTPException(409, "Aucune interface ouverte : l'export a besoin du moteur de rendu de l'interface.")
        # État « starting » tout de suite : export_status ne renvoie plus le « done » de l'export précédent.
        etat.export = {"state": "starting", "path": body.get("path")}
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
        loud = None
        cible_nom = opts.get("loudness", "youtube")
        if graph and cible_nom in exp.LOUDNESS:
            # Deux passes : mesure du mixage, puis correction linéaire vers la cible (sans pompage).
            cible = exp.LOUDNESS[cible_nom]
            mesure = await asyncio.to_thread(exp.mesurer_loudness, doc, t0, t1, cible, cache / "export_loudness.txt")
            if mesure:
                graph += f";\n[{alab}]{exp.loudnorm_filtre(cible, mesure)}[an]"
                alab = "an"
                loud = {"mesure": float(mesure["input_i"]), "cible": cible}
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
        sp = spans(doc)
        try:
            # À la chaîne : pendant que l'interface rend l'image i, on décode déjà
            # l'image i+1 et on encode l'image i-1.
            tim = {"decode": 0.0, "rendu": 0.0, "encode": 0.0}

            def build(i):
                t0b = time.time()
                t = exp.frame_time(t0, i, fps)
                active = exp.video_clips_at(doc, t, sp)
                ids = {c["id"] for c in active}
                for cid in [k for k in decs if k not in ids]:
                    decs.pop(cid).close()
                head, payload = [], []
                for c in active:
                    d = decs.get(c["id"])
                    if d is None:
                        w, h = exp.decode_size(doc, c, out_scale)
                        med = doc["media"][c["media"]]
                        # Vidéo transparente : décodée en RGBA (couche alpha comprise) au lieu du YUV.
                        d = decs[c["id"]] = exp.Decodeur(
                            med["path"], c["in"] + (t - c["start"]) * c["speed"], c["speed"],
                            fps, w, h, sp[c["id"]][1] - t, fmt="rgba" if med.get("alpha") else "yuv420p",
                            entree=medias.options_decodage(med), source_yuv=str(med.get("pix_fmt", "")).startswith("yuv"))
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
                           "fps": round(n / el, 1) if el > 0 else 0.0, "timing": {k: round(v, 1) for k, v in tim.items()},
                           "loudness": loud}
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
