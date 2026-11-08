"""Choix automatique du modèle local de génération d'images selon le GPU.

La détection ne télécharge rien. Le choix est une table simple, lisible et
testée : plus de mémoire vidéo → modèle plus grand et meilleure qualité.
Licences retenues : toutes autorisent l'usage commercial des images (vidéos
monétisées), sous les restrictions d'usage de chaque licence — à relire avant
publication. Les modèles « Turbo » de Stability AI sont exclus (licence non
commerciale).
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


@dataclass
class Gpu:
    vendor: str          # nvidia | amd | intel | aucun
    name: str
    vram_mo: int         # 0 si inconnu


@dataclass
class Choix:
    niveau: str
    modele: str          # identifiant Hugging Face
    accel: str           # cuda | directml | cpu
    resolution: int
    etapes: int
    licence: str
    poids_go: float      # taille approximative à télécharger
    note: str


def detecter() -> list[Gpu]:
    gpus = []
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=10, creationflags=NO_WINDOW)
        for line in out.stdout.strip().splitlines():
            name, mem = [x.strip() for x in line.rsplit(",", 1)]
            gpus.append(Gpu("nvidia", name, int(float(mem))))
    except (FileNotFoundError, subprocess.TimeoutExpired, ValueError):
        pass
    if gpus:
        return gpus
    try:  # autres cartes : nom seulement (la mémoire rapportée par Windows est peu fiable)
        out = subprocess.run(["powershell", "-NoProfile", "-Command",
                              "(Get-CimInstance Win32_VideoController).Name"],
                             capture_output=True, text=True, timeout=15, creationflags=NO_WINDOW)
        for name in out.stdout.strip().splitlines():
            n = name.lower()
            vendor = "amd" if ("amd" in n or "radeon" in n) else "intel" if "intel" in n else None
            if vendor:
                gpus.append(Gpu(vendor, name.strip(), 0))
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return gpus


TIERS = [
    # (vram minimale en Mo, choix)
    (15_000, Choix("haut", "black-forest-labs/FLUX.1-schnell", "cuda", 1024, 4, "Apache-2.0", 24.0,
                   "Meilleure qualité et texte lisible dans l'image ; ~5-15 s par image.")),
    (7_500, Choix("moyen", "stabilityai/stable-diffusion-xl-base-1.0 + ByteDance/SDXL-Lightning (4 étapes)", "cuda",
                  1024, 4, "CreativeML OpenRAIL++-M", 7.0, "Bonne qualité 1024 px ; ~3-8 s par image.")),
    (3_500, Choix("léger", "stable-diffusion-v1-5/stable-diffusion-v1-5 + latent-consistency/lcm-lora-sdv1-5", "cuda",
                  768, 4, "CreativeML OpenRAIL-M", 2.5,
                  "Tient dans 4 Go en demi-précision ; 512-768 px, ~3-6 s par image ; agrandir ensuite si besoin.")),
]

CPU = Choix("processeur", "stable-diffusion-v1-5/stable-diffusion-v1-5 + latent-consistency/lcm-lora-sdv1-5", "cpu",
            512, 4, "CreativeML OpenRAIL-M", 2.5, "Sans carte compatible : lent (~30-90 s par image), mais possible.")


def choisir(gpus: list[Gpu]) -> Choix:
    nvidia = [g for g in gpus if g.vendor == "nvidia"]
    if nvidia:
        best = max(nvidia, key=lambda g: g.vram_mo)
        for vmin, c in TIERS:
            if best.vram_mo >= vmin:
                return c
        return CPU
    if any(g.vendor == "amd" for g in gpus):
        c = TIERS[2][1]
        return Choix(c.niveau, c.modele, "directml", c.resolution, c.etapes, c.licence, c.poids_go,
                     "Carte AMD : accélération DirectML, à valider sur la machine.")
    return CPU


if __name__ == "__main__":
    g = detecter()
    print("GPU détectés :", g or "aucun")
    print("Choix :", choisir(g))
