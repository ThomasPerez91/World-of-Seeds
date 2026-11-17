# Comparaison visuelle

À chaque pull request qui touche `frontend/`, le workflow `Visual regression` :

1. construit le frontend de la branche de base et celui de la PR ;
2. ouvre les écrans principaux dans Chromium, en bureau (1440 × 900) et en mobile (390 × 844) :
   connexion, mentions légales, tableau de bord (avec un torrent déplié et le menu du compte
   ouvert), les trois sections des paramètres, et les pages d'administration utilisateurs,
   qBittorrent, nettoyage, paramètres et stockage ;
3. compare les captures pixel par pixel.

Le rapport s'affiche dans le résumé du job. L'artefact `visual-regression` contient les
captures `base/`, `head/` et, pour chaque écran modifié, une image `diff/` où les pixels changés
sont en rouge. Pour chaque écran modifié, le rapport cite aussi les lignes de texte visible qui
ont changé et les éléments situés dans la zone modifiée, avec leurs styles calculés qui diffèrent
(couleur, police, ombre, transformation…), ce qui suffit souvent sans ouvrir les images.

Le job est **indicatif** : il échoue quand un écran change, mais il ne fait pas partie des
contrôles requis, donc il ne bloque jamais une fusion. Une PR qui ne devait rien changer
visuellement doit le garder vert ; une PR qui change le rendu volontairement le rend rouge, et
les images servent alors à vérifier le changement.

## Sans backend, et sans faux positifs

`fixtures.py` simule l'API (session, santé, stockage, débit réseau, torrents, utilisateurs,
pages d'administration) ; le WebSocket d'événements est accepté sans jamais rien émettre. Un
appel non simulé reçoit une 404 et apparaît dans le rapport : complétez alors `fixtures.py`.

Pour que deux builds identiques donnent les mêmes pixels :

- l'horloge du navigateur est figée sur `NOW` ;
- les animations, transitions et flous d'arrière-plan (`backdrop-filter`, rendus de façon
  légèrement variable par le moteur logiciel) sont coupés ;
- rien ne peut être survolé, et le focus laissé par les clics est retiré ;
- les barres de défilement sont masquées : pendant la capture pleine page, leur apparition
  changeait la largeur utile et pouvait réorganiser une grille ;
- le numéro de version affiché devient `0.0.0`, pour qu'une montée de version ne change pas
  tous les écrans ;
- chaque écran est capturé une fois stabilisé : trois captures identiques de suite.
- un écran différent est recapturé des deux côtés, jusqu'à deux fois : le rendu logiciel place
  parfois un écran autrement d'une capture à l'autre, à textes et styles identiques. Il ne
  compte comme modifié que s'il diffère à chaque essai, ce qu'un changement de CSS fait toujours ;
  sinon le rapport le signale comme écart intermittent ignoré.

Un écran ne compte comme modifié qu'au-delà de 20 pixels différents, sur n'importe quel canal de
couleur. En dessous, le rapport l'indique comme du bruit de rendu, typiquement le lissage d'une
petite icône.

## En local

```bash
cd frontend && npm ci && npx vite build && cp -r dist /tmp/base   # sur la branche de base
cd frontend && npx vite build                                      # sur la branche à comparer
pip install playwright==1.63.0 pillow==12.3.0 && python -m playwright install chromium
python visual-regression/capture.py --base /tmp/base --head frontend/dist --out /tmp/report
```

## Retirer la fonctionnalité

Supprimer `.github/workflows/visual-regression.yml` et ce dossier : rien d'autre n'en dépend.
