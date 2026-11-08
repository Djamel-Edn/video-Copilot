"""Application desktop : le serveur tourne en arrière-plan, l'interface
s'ouvre dans une vraie fenêtre Windows (WebView2, le moteur de Chromium).

    .venv/Scripts/python app_desktop.py [dossier_projet]

Le copilote IA (copilote_mcp.py) se branche sur la même adresse locale,
donc l'IA monte en direct dans cette fenêtre.
"""
from __future__ import annotations

import argparse
import os
import socket
import sys
import threading
import time
from pathlib import Path

import httpx
import uvicorn
import webview

import serveur


def port_libre(port: int) -> bool:
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", port)) != 0


def est_un_editeur(port: int) -> bool:
    try:
        return httpx.get(f"http://127.0.0.1:{port}/api/ui_state", timeout=2).status_code == 200
    except httpx.HTTPError:
        return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dossier", nargs="?", default=str(serveur.ROOT / "projets" / "demo"))
    ap.add_argument("--port", type=int, default=int(os.environ.get("EDITEUR_PORT", 8765)))
    ap.add_argument("--test", type=float, default=0, help="ferme la fenêtre après N s (vérification)")
    a = ap.parse_args()
    dossier = Path(a.dossier).resolve()
    srv = None
    if port_libre(a.port):
        dossier.mkdir(parents=True, exist_ok=True)
        etat = serveur.Etat(dossier, a.port)
        srv = uvicorn.Server(uvicorn.Config(serveur.creer_app(etat), host="127.0.0.1", port=a.port,
                                            log_level="warning", **serveur.UVICORN_WS))
        threading.Thread(target=srv.run, daemon=True).start()
        while not srv.started:
            time.sleep(0.05)
        titre = f"Éditeur — {dossier.name}"
    elif est_un_editeur(a.port):
        # Un éditeur tourne déjà (ex. serveur.py) : on ouvre juste une fenêtre dessus.
        print(f"Un éditeur tourne déjà sur le port {a.port} : ouverture d'une fenêtre sur ce projet.")
        titre = "Éditeur"
    else:
        sys.exit(f"Le port {a.port} est pris par un autre programme. Essaie : --port 8770")
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
    main()
