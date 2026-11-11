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

2026-11-11 : les scènes HyperFrames se **décomposent en calques**
(`montage/outils/decomposer.mjs` + `vers_editeur.py --calques`) : un élément
visible = un clip (vidéo d'origine, texte natif, PNG, ou vidéo transparente),
avec ses images clés. Ce dépôt y a gagné les vidéos transparentes et les
polices de projet. Limite connue : un caractère absent du découpage d'une
police (ex. « → » en Outfit 600) peut tomber sur une autre police de secours
que dans le navigateur.

Le copilote ne peut pas déplacer la tête de lecture ni changer la
sélection (il les lit seulement), ni lister les polices installées.

**Exécutable** : Djamel lance l'éditeur par `dist/Editeur/Editeur.exe` (raccourci
« Éditeur vidéo » sur son Bureau). Il n'embarque que le code du moment de sa
construction : après toute modification à livrer, relancer `construire_exe.cmd`
(et le lui dire). Tester l'exécutable avec `--test N --port 87xx` : ce mode ne
change pas le « dernier projet » que son raccourci rouvre.

Tester sur les ports d'essai (`.claude/launch.json` : 8771, 8773-8775), jamais
sur le 8765 : c'est souvent l'éditeur ouvert de Djamel. Pour tout essai qui ouvre
un projet (`/api/ouvrir`, bouton Projet), définir `EDITEUR_DONNEES` vers un dossier
temporaire : sinon l'essai remplace ses projets récents, et son raccourci rouvrirait
le projet d'essai.

**Pas de volet chat intégré** (décision de Djamel, 2026-11-11) : un volet « Copilote »
construit sur le Claude Agent SDK a été abandonné, car sans accord d'Anthropic une appli
tierce ne peut passer que par une clé API facturée à l'usage. Le copilote reste l'agent
Claude Code qui pilote l'éditeur par le serveur MCP `editeur`. Pour ne pas changer de
fenêtre : l'interface s'ouvre dans le navigateur intégré de l'app Claude. Le code du
volet est gardé dans `git stash` (« Volet Copilote (Agent SDK) abandonné… »). Ne pas le
reproposer sans que Djamel le demande.

**Ouvrir un projet** (2026-11-11) : bouton « Projet » (récents, sélecteur de dossier
Windows via `window.pywebview.api.choisir_dossier`), route `/api/ouvrir` — le projet
courant est enregistré, l'autre chargé sans redémarrer, l'interface se recharge (les
identifiants c1, m1… se recoupent d'un projet à l'autre). Un dossier sans
`project.json` n'est ouvert qu'après confirmation ; celui d'une vidéo ouvre son
`assemblage/`. Origine : Djamel avait ouvert `Documents/montage` par erreur, puis
importé `project.json` et `index.html` comme des médias.

## Vérifier

```bash
.venv/Scripts/python -m pytest -q tests
```
