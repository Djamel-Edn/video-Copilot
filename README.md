# Éditeur — montage vidéo piloté en direct par l'IA

Application desktop. La timeline est un document de données que toi et le
copilote IA modifiez en même temps : chaque action de l'IA apparaît
instantanément, clignote dans la timeline, s'inscrit au journal et s'annule
d'un Ctrl+Z. Architecture détaillée : [ARCHITECTURE.md](ARCHITECTURE.md).

## Lancer

```bash
cd C:/Users/erosl/Documents/editeur
.venv/Scripts/python app_desktop.py            # fenêtre desktop (projet projets/demo)
.venv/Scripts/python app_desktop.py D:/videos/mon_projet
.venv/Scripts/python serveur.py                # variante : dans le navigateur, http://127.0.0.1:8765
```

Brancher le copilote : ouvrir Claude Code **dans ce dossier** (le fichier
`.mcp.json` déclare le serveur `editeur`, à approuver une fois), l'éditeur
étant lancé. Puis parler : « mets un titre sur les 4 premières secondes »,
« fais un effet VHS sur le premier plan et un zoom lent », « coupe de 0:12 à
0:15 et resserre ».

## L'interface

| Zone | Contenu |
| --- | --- |
| Gauche | **Médias** (vignettes, glisser vers la timeline) · **Effets** (par catégorie, glisser sur un clip) · **Textes** (6 préréglages animés : titre, sous-titre, bandeau, citation, sous-titres, impact) · **Journal** (toi en blanc, l'IA en orange) |
| Centre | **Moniteur programme** : déplacer, mettre à l'échelle (coins) et tourner (poignée du haut) le clip sélectionné directement dans l'image ; zones de sécurité ; zoom et qualité d'aperçu ; transport et timecode saisissable |
| Droite | **Contrôles d'effets** : Montage (début, durée, entrée, vitesse), Mouvement, Opacité, Texte, Contour, Ombre portée, Fond, Audio, Effets. **Chaque valeur numérique** a son chronomètre ⏱, ◀ ◆ ▶ pour naviguer/ajouter des images clés, et une mini-timeline où l'on glisse les losanges (clic droit : interpolation, suppression) |
| Bas | **Timeline** : outils Sélection / Lame / Texte, magnétisme, pistes masquables / muettes / verrouillables, vignettes, formes d'onde, losanges d'images clés, zoom, clic droit sur un clip |

Texte : police (toutes les polices installées), graisse 100-900, italique,
majuscules, taille, alignement, interligne, espacement des lettres, couleur,
contour, ombre (couleur, opacité, flou, décalage), fond (couleur, opacité,
marge, arrondi). Fondus d'entrée / sortie en un clic.

## Raccourcis

| Touche | Action |
| --- | --- |
| Espace · K · L · J | Lecture-pause · pause · lecture · reculer d'1 s |
| ← → (Maj : ×5) | Image par image |
| ↑ ↓ · Origine · Fin | Point de montage précédent / suivant · début · fin |
| V · C · T | Outil sélection · lame · texte |
| Ctrl+K | Scinder à la tête de lecture |
| S | Magnétisme oui / non |
| Suppr · Maj+Suppr | Supprimer · supprimer et resserrer (ou supprimer l'image clé sélectionnée) |
| Ctrl+C · Ctrl+V · Ctrl+D | Copier · coller à la tête de lecture · dupliquer |
| Alt + glisser | Dupliquer en déplaçant · Ctrl + glisser : écraser |
| Ctrl+Z · Ctrl+Y | Annuler · rétablir (y compris un lot de l'IA) |
| Ctrl+M | Exporter |
| + − \ · Ctrl+molette | Zoom timeline · tout afficher |
| Maj (moniteur) | Contraindre le déplacement / rotation par 15° |

## Ce qui marche (vérifié le 2026-11-08)

- Import vidéo / audio / image, proxys 540p générés en arrière-plan,
  vignettes et formes d'onde. Les vidéos de téléphone filmées à la verticale
  (image stockée couchée + consigne de rotation) sont lues à leurs vraies
  dimensions ; les projets plus anciens sont corrigés au démarrage.
- Timeline : déplacer (plusieurs clips, d'une piste à l'autre), rogner,
  scinder, lame, dupliquer, copier-coller, supprimer avec resserrage,
  magnétisme, verrouillage.
- Aperçu WebGL2 : position, échelle, rotation, opacité, images clés avec
  courbes sur toute propriété et tout paramètre d'effet, 15 effets intégrés
  empilables et réordonnables, textes complets, aplats, formats 16:9, 9:16,
  1:1, 4:5, 4K.
- **Effets sur mesure écrits par l'IA** (shaders GLSL) : compilés dans
  l'interface avant d'être acceptés ; l'erreur revient à l'IA qui corrige.
- L'IA **voit** le résultat (`snapshot`) et sait où est ta tête de lecture.
- Outils MCP testés en session MCP réelle ; Ctrl+Z défait un lot de l'IA.

### Export MP4 (bouton Exporter ou Ctrl+M)

- H.264 + AAC, résolution de la séquence ou 4K / 1080p / 720p, 3 qualités,
  plage de début / fin. Le copilote peut lancer l'export (`export_video`).
- **Même moteur de rendu que l'aperçu** : effets, shaders de l'IA, textes,
  images clés sortent à l'identique. Le serveur décode les vidéos image par
  image avec FFmpeg : vérifié à l'image près (35 dB face à l'image source
  attendue, contre 21 dB pour ses voisines), vitesse 200 % comprise.
- Son mixé depuis les originaux : images clés de volume, pistes muettes,
  vitesse (atempo).
- Vitesse mesurée : **~6 images/s en 1080p** sur ce PC (≈ 25-30 min pour une
  vidéo de 10 min). NVENC est proposé automatiquement s'il fonctionne
  (pilote NVIDIA ≥ 610 requis ; installé : 581 → x264).

### Montage par le texte (onglet Parole)

- **Transcrire** un média (faster-whisper `large-v3-turbo`, local, sur le
  processeur : ~1,2× la durée du média). Utilise l'environnement
  `Documents/montage/.voice-venv` ; sinon définir `EDITEUR_WHISPER_PY`.
- La transcription de la **séquence** suit la timeline : un mot coupé
  disparaît, le mot en cours est surligné, clic = aller au mot.
- Sélectionner des mots (glisser, Maj+clic) puis **Suppr** : coupe la vidéo
  et resserre toutes les pistes.
- **Retirer les hésitations** (euh, hum, ben…) et **raccourcir les silences**
  (blanc > seuil réduit à 0,25 s), chacun en une étape annulable.
- Copilote : `get_transcript`, `text_edit`, `transcribe_media`.
- Fenêtre desktop (WebView2) : WebGL2 et connexion temps réel vérifiés.

### Sous-titres automatiques (onglet Parole)

- Générés depuis la transcription de la séquence : coupure en fin de phrase,
  sur une pause ou à la longueur maximale ; « n » + « 'a » recollés en « n'a ».
- 3 styles : **Classique** (bas de l'écran, fond, 2 lignes de 42 caractères),
  **Shorts** (gros, majuscules, contour, 1 à 3 mots, apparition en zoom —
  choisi d'office en séquence verticale), **Minimal** (blanc, ombre).
- Chaque sous-titre est un **clip texte ordinaire** sur la piste
  « Sous-titres » : texte, police, couleur, position et images clés se
  corrigent dans l'inspecteur ; régénérer remplace la piste.
- **Enregistrer le .srt** (dans `exports/`) pour l'envoyer à YouTube.
- Copilote : `generate_subtitles`.

### Son séparé et clips liés

- Clic droit sur une vidéo (ou inspecteur, section Audio) → **Séparer le
  son** : le son passe sur une piste audio, **lié** à l'image (🔗) ; la vidéo
  devient muette. Les deux se sélectionnent, se déplacent, se rognent, se
  scindent, se dupliquent et se suppriment ensemble.
- **Alt + clic** : un seul des deux (pour décaler le son, faire un J-cut /
  L-cut). Clic droit : délier, lier la sélection, réactiver le son.
- Export : le son n'est jamais mixé deux fois. Une voix **mono** sort au
  même niveau que la source (avant : −3 dB par la conversion stéréo).

### Son niveau YouTube

- **Volume final à l'export** : normalisation en deux passes (mesure du
  mixage, puis correction linéaire, sans pompage). YouTube / Spotify −14 LUFS
  (défaut), podcast −16, télévision −23, ou rien. Crête vraie limitée à
  −1,5 dB. Vérifié : un mixage à −13,25 LUFS ressort à **−14,0 LUFS**, crête
  −1,8 dB. Le niveau mesuré s'affiche à la fin de l'export.
- **Musique sous la voix** : sélectionner la musique → inspecteur ou clic
  droit → *Baisser sous la voix*. Les zones de voix viennent de la
  transcription, sinon de la forme d'onde ; la baisse (−12 dB, descente
  0,25 s, remontée 0,5 s) est écrite en **images clés de volume**, visibles
  et retouchables. Copilote : `duck_music`.
- **Vu-mètres** dans le moniteur : crêtes gauche / droite et volume perçu sur
  3 s (LUFS, pondération K). Écart avec FFmpeg mesuré : moins de 0,5 dB.

### Transitions

- 8 transitions : fondu enchaîné, fondu au noir, fondu au blanc, volet,
  poussée, zoom croisé, fondu flou, iris — plus celles que l'IA écrit
  (`define_effect` avec `kind: "transition"`).
- Glisser une transition de l'onglet Effets sur un point de coupe (moitié
  gauche d'un clip : depuis le précédent ; moitié droite : vers le suivant),
  ou clic droit → *Fondu enchaîné avec le clip précédent*. Type, durée et
  réglages dans l'inspecteur (*Transition d'entrée*).
- Comme dans Premiere, les deux clips se prolongent d'une demi-durée dans la
  marge de leur source ; sans marge, l'image du bord est figée. Le son, lui,
  coupe net au point de montage.
- Vérifié à l'export : au milieu d'un fondu enchaîné, l'image correspond au
  mélange 50/50 attendu (33 dB, contre 10 dB pour chaque source seule).

## Pas encore fait

| Étape | Contenu |
| --- | --- |
| Export plus rapide | Lecture asynchrone des pixels, lots d'images ; objectif ≥ 15 images/s |
| Images d'illustration IA locale | Détection du GPU faite (`moteur/gpu.py`) ; génération à brancher, voir ci-dessous |
| Audio avancé | Égaliseur, compresseur, fondus croisés audio aux transitions |
| Paquet installable | Un `.exe` (PyInstaller) pour ne plus passer par le terminal |

## Images d'illustration générées en local

`python -m moteur.gpu` détecte la carte et choisit le modèle :

| Mémoire vidéo | Modèle | Résolution | Licence |
| --- | --- | --- | --- |
| ≥ 15 Go (NVIDIA) | FLUX.1 schnell | 1024 px | Apache-2.0 |
| 7,5-15 Go | SDXL + Lightning 4 étapes | 1024 px | OpenRAIL++-M |
| 3,5-7,5 Go | SD 1.5 + LCM 4 étapes | 512-768 px | OpenRAIL-M |
| AMD | SD 1.5 + LCM, DirectML | 512-768 px | OpenRAIL-M |
| Rien de compatible | SD 1.5 + LCM sur processeur, lent | 512 px | OpenRAIL-M |

Ton PC (GTX 1650 Max-Q, 4 Go) → niveau **léger**, environ 2,5 Go de poids à
télécharger. Les temps par image indiqués dans `gpu.py` sont des estimations,
à mesurer. Les modèles « Turbo » de Stability AI sont exclus : leur licence
interdit l'usage commercial, donc les vidéos monétisées.

## Tests

```bash
.venv/Scripts/python -m pytest -q tests
```
