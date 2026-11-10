"""Application desktop : le serveur tourne en arrière-plan, l'interface
s'ouvre dans une vraie fenêtre Windows (WebView2, le moteur de Chromium).

    .venv/Scripts/python app_desktop.py [dossier_projet]
    Editeur.exe [dossier_projet]          (version empaquetée, voir construire_exe.cmd)

Sans dossier, l'exécutable rouvre le dernier projet, ou demande lequel ouvrir.
Le copilote IA (copilote_mcp.py) se branche sur la même adresse locale,
donc l'IA monte en direct dans cette fenêtre.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

FIGE = getattr(sys, "frozen", False)
DONNEES = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "editeur"
JOURNAL = DONNEES / "editeur.log"
DERNIER = DONNEES / "dernier_projet.txt"

# Sans console (exécutable fenêtré), sys.stdout vaut None et le moindre print ou message
# d'uvicorn ferait planter l'appli sans un mot : tout part dans un journal.
if FIGE or sys.stdout is None:
    DONNEES.mkdir(parents=True, exist_ok=True)
    _j = open(JOURNAL, "a", encoding="utf-8", buffering=1)
    sys.stdout = sys.stderr = _j

import argparse  # noqa: E402
import socket  # noqa: E402
import threading  # noqa: E402
import time  # noqa: E402
import traceback  # noqa: E402

import httpx  # noqa: E402
import uvicorn  # noqa: E402
import webview  # noqa: E402

import serveur  # noqa: E402


def message(texte: str, titre: str = "Éditeur", erreur: bool = True) -> None:
    """Boîte de dialogue Windows : sans console, c'est le seul moyen de prévenir."""
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {texte}", flush=True)
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, texte, titre, 0x10 if erreur else 0x40)


def port_libre(port: int) -> bool:
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", port)) != 0


def est_un_editeur(port: int) -> bool:
    try:
        return httpx.get(f"http://127.0.0.1:{port}/api/ui_state", timeout=2).status_code == 200
    except httpx.HTTPError:
        return False


def choisir_projet() -> Path | None:
    """Dernier projet ouvert s'il existe encore, sinon un dossier à choisir."""
    try:
        p = Path(DERNIER.read_text("utf-8").strip())
        if (p / "project.json").exists():
            return p
    except OSError:
        pass
    import tkinter
    from tkinter import filedialog
    racine = tkinter.Tk()
    racine.withdraw()
    depart = serveur.MONTAGE if serveur.MONTAGE.exists() else Path.home()
    choix = filedialog.askdirectory(title="Dossier du projet à ouvrir (ex. montage/<vidéo>/assemblage)",
                                    initialdir=str(depart), mustexist=False)
    racine.destroy()
    return Path(choix) if choix else None


def main():
    ap = argparse.ArgumentParser()
    defaut = None if FIGE else str(serveur.ROOT / "projets" / "demo")
    ap.add_argument("dossier", nargs="?", default=defaut)
    ap.add_argument("--port", type=int, default=int(os.environ.get("EDITEUR_PORT", 8765)))
    ap.add_argument("--test", type=float, default=0, help="ferme la fenêtre après N s (vérification)")
    a = ap.parse_args()
    dossier = Path(a.dossier).resolve() if a.dossier else choisir_projet()
    if dossier is None:
        return
    srv = None
    if port_libre(a.port):
        dossier.mkdir(parents=True, exist_ok=True)
        etat = serveur.Etat(dossier, a.port)
        srv = uvicorn.Server(uvicorn.Config(serveur.creer_app(etat), host="127.0.0.1", port=a.port,
                                            log_level="warning", **serveur.UVICORN_WS))
        threading.Thread(target=srv.run, daemon=True).start()
        t0 = time.time()
        while not srv.started:
            if time.time() - t0 > 30:
                message(f"Le serveur de l'éditeur n'a pas démarré en 30 s.\nJournal : {JOURNAL}")
                return
            time.sleep(0.05)
        titre = f"Éditeur — {dossier.name}"
        # Seul l'exécutable retient le dernier projet (son double-clic le rouvre) ; un lancement
        # de développement ou de test ne doit pas changer ce que le raccourci ouvre.
        if FIGE and a.test == 0:
            try:
                DONNEES.mkdir(parents=True, exist_ok=True)
                DERNIER.write_text(str(dossier), "utf-8")
            except OSError:
                pass
    elif est_un_editeur(a.port):
        # Un éditeur tourne déjà (ex. serveur.py) : on ouvre juste une fenêtre dessus.
        print(f"Un éditeur tourne déjà sur le port {a.port} : ouverture d'une fenêtre sur ce projet.")
        titre = "Éditeur"
    else:
        message(f"Le port {a.port} est pris par un autre programme.\n"
                f"Relance avec : Editeur.exe --port 8770")
        return
    win = webview.create_window(titre, f"http://127.0.0.1:{a.port}",
                                width=1600, height=960, min_size=(1100, 700), background_color="#111317")

    def verifier():
        time.sleep(a.test)
        r = win.evaluate_js("JSON.stringify({app: !!window.app, gl2: !!document.createElement('canvas').getContext('webgl2'),"
                            " ws: window.app && window.app.ws && window.app.ws.readyState})")
        print("vérification fenêtre :", r, flush=True)
        win.destroy()

    webview.start(verifier if a.test else None)
    if srv is not None:
        srv.should_exit = True


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except BaseException:
        traceback.print_exc()
        message(f"L'éditeur s'est arrêté sur une erreur.\nDétails dans le journal :\n{JOURNAL}")
        raise SystemExit(1)
