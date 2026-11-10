# Éditeur copilote vidéo — dossier de développement

Organisation décidée avec Djamel le 2026-11-10 :

- **Ce dossier sert uniquement à développer le logiciel** (moteur, serveur,
  interface, outils MCP du copilote). Architecture : [ARCHITECTURE.md](ARCHITECTURE.md),
  fonctionnalités et état : [README.md](README.md).
- **Les vrais montages vivent dans `Documents/montage`**, un sous-dossier par
  vidéo ; le projet d'éditeur d'une vidéo est `montage/<projet>/assemblage/`.
  Ne jamais créer ni modifier de projet de vidéo réel ici : `projets/` ne
  contient que des projets d'essai (demo, essai_*).
- Le pont `montage/outils/vers_editeur.py` parle à l'éditeur par son API HTTP
  (`/api/project`, `/api/import`, `/api/ops`). Garder ces routes compatibles,
  ou mettre le pont à jour dans le même temps.

## Règle de conception (Djamel, 2026-11-10)

**Tout élément produit par le copilote doit être gérable directement dans la
timeline**, comme un élément posé à la main : visible, sélectionnable,
déplaçable, rognable, modifiable dans l'inspecteur, supprimable. Rien de ce
que l'IA crée ne doit rester caché dans le projet ni n'être modifiable que par
l'IA. Toute nouvelle fonction du copilote se vérifie contre cette règle.

## Défauts trouvés pendant les montages

Corrigés le 2026-11-10 (détails et mesures dans le README) :
- proxy interrompu jamais refait → `medias.proxys_a_reprendre` au démarrage ;
- `snapshot` bloqué fenêtre cachée → `sources.uneImage()` n'attend plus
  indéfiniment `requestAnimationFrame`.

Mis en conformité avec la règle de conception le 2026-11-10 : suppression des
médias, gestion des effets sur mesure depuis le panneau Effets, sous-titres
retouchés gardés à la régénération, groupes liés de plus de deux clips.

Encore ouvert : **les graphiques des rendus HyperFrames sont intégrés à une
seule vidéo** ; titres et chiffres ne se modifient pas dans l'éditeur. Piste :
convertir une composition en clips natifs (textes, aplats, images clés).
Le copilote ne peut pas non plus déplacer la tête de lecture ni changer la
sélection (il les lit seulement), ni lister les polices installées.

Tester sur les ports d'essai (`.claude/launch.json` : 8771, 8773-8775), jamais
sur le 8765 : c'est souvent l'éditeur ouvert de Djamel.

## Vérifier

```bash
.venv/Scripts/python -m pytest -q tests
```
