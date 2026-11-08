# Architecture

## L'idée qui guide tout

**La timeline est un document de données. L'humain et l'IA le modifient
par les mêmes opérations, sur le même serveur, en direct.**

```
            ┌──────────────┐   opérations    ┌───────────────────────┐
  toi  ───► │ interface web│ ──────────────► │                       │
            │ (WebGL)      │ ◄────────────── │  serveur (FastAPI)    │
            └──────────────┘  correctifs     │  - project.json       │
                  ▲  rendu d'image,          │  - historique annuler │
                  │  compilation de shader   │  - import, proxys     │
                  └───────────────────────── │                       │
                                             └──────────▲────────────┘
  copilote IA ──► serveur MCP (copilote_mcp.py) ── HTTP ┘
  (Claude Code, Claude Desktop…)
```

- **Une seule source de vérité** : le serveur. Chaque opération est validée,
  appliquée, enregistrée dans l'historique, puis diffusée à toutes les
  interfaces sous forme de correctifs (`set` / `del` sur un chemin JSON).
- **Fluidité** : une opération s'applique en moins d'une milliseconde ;
  l'interface la voit à l'image suivante. Les clips touchés par l'IA
  clignotent, le journal affiche ce qu'elle a fait.
- **Une requête de l'IA = une étape d'annulation** : `Ctrl+Z` défait tout le
  lot d'un coup, comme un geste humain.
- **Sans conflit** : les opérations passent l'une après l'autre. Si l'IA vise
  un clip que tu viens de supprimer, son opération échoue proprement avec un
  message clair, et le lot entier est annulé (atomicité).

## Comment l'IA peut faire « n'importe quel effet »

Quatre étages, du plus simple au plus libre :

| Étage | Ce que l'IA fait | Exemple de demande |
| --- | --- | --- |
| 1. Montage | couper, déplacer, rogner, scinder, supprimer une plage avec décalage | « enlève les 3 premières secondes et resserre » |
| 2. Animation | images clés sur **toute** propriété numérique, y compris les paramètres d'effet | « zoom lent de 100 à 115 % sur le plan 2 » |
| 3. Bibliothèque | 15 effets intégrés, empilables, réglables | « flou + vignette sur l'intro » |
| 4. **Effet sur mesure** | l'IA **écrit un shader GLSL**, le serveur le fait compiler par l'interface, l'erreur éventuelle lui revient, elle corrige, puis l'applique | « effet VHS avec lignes qui défilent », « transition en spirale », « mode de fusion écran » |

L'étage 4 est ce qui rend la promesse réelle : tout effet visuel qui se
calcule pixel par pixel (couleur, distorsion, transition, fusion avec les
calques du dessous, glitch, lumière, masque animé) s'écrit en shader.

L'IA **voit** aussi son travail : l'outil `snapshot` rend l'image à un instant
donné et la lui renvoie. Elle peut donc itérer : écrire → regarder → ajuster.

### Contrat d'un effet (shader)

L'IA écrit seulement une fonction :

```glsl
vec4 effect(vec2 uv) {        // uv : (0,0) = bas-gauche, (1,1) = haut-droit
    vec4 c = src(uv);         // le calque du clip (après position/échelle)
    // below(uv) : tout ce qui est composé SOUS ce clip
    // u_time : secondes depuis le début du clip ; u_dur : durée du clip
    // u_res : résolution du projet en pixels
    // p_<nom> : chaque paramètre déclaré (float ou vec3 pour une couleur)
    return c;
}
```

- `blend: "over"` (défaut) : le résultat se superpose aux calques du dessous.
- `blend: "replace"` : le résultat **remplace** l'image composée — sert aux
  transitions et aux modes de fusion, avec `below()`.
- Les paramètres `float` s'animent par images clés.

### Pourquoi un shader et pas du Python

La règle « l'IA n'exécute jamais de code quelconque » tient : un shader GLSL
tourne sur la carte graphique, dans le navigateur, **sans aucun accès** aux
fichiers, au réseau ni au système. C'est une fonction pure de pixels.
Le pire cas est une boucle trop lourde qui fait ramer l'aperçu ; on le
borne (taille du code limitée, effet désactivable d'un clic).

### Ce que les shaders ne couvrent pas (étapes suivantes)

- **Analyses** : détourage, suivi d'objet, détection de visages → ce sont des
  tâches qui produisent un masque ou des données (modèles locaux), que les
  shaders consomment ensuite.
- **Audio** : volume par images clés en v0.1 ; égaliseur, compresseur et
  baisse automatique de la musique en v0.2 (WebAudio pour l'aperçu, FFmpeg
  pour l'export).
- **Motion design lourd** : compositions HyperFrames rendues en vidéo, puis
  importées comme un média.

## Rendu

- **Aperçu** : WebGL2 dans le navigateur. Pour chaque instant : chaque piste
  vidéo de bas en haut → le clip est dessiné dans un calque (ajusté au cadre,
  puis position, échelle, rotation) → chaîne d'effets (passes successives) →
  composition sur l'image (opacité, mode `over` ou `replace`).
- **Proxys** : chaque vidéo importée reçoit une copie légère en 540p, toutes
  images clés, pour un défilement instantané.
- **Export (prochaine étape)** : le **même** moteur de rendu, image par
  image sur les originaux, envoyé à FFmpeg. Ce que tu vois est exactement ce
  qui sort.

## Fichiers

| Fichier | Rôle |
| --- | --- |
| `moteur/projet.py` | Document timeline, opérations validées, transactions, annuler/rétablir |
| `moteur/effets.py` | Bibliothèque d'effets intégrés (GLSL) |
| `moteur/medias.py` | ffprobe, génération des proxys |
| `serveur.py` | API HTTP, WebSocket, diffusion, import, envoi des médias |
| `copilote_mcp.py` | Outils exposés au copilote IA |
| `ui/` | Interface : aperçu WebGL, timeline, inspecteur, journal |
| `tests/` | Tests du moteur |
