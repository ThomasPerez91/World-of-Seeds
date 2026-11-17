# Comparaison visuelle

À chaque pull request qui touche `frontend/`, le workflow `Visual regression` :

1. construit le frontend de la branche de base et celui de la PR ;
2. ouvre les écrans principaux dans Chromium, en bureau (1440 × 900) et en mobile (390 × 844) :
   connexion, mentions légales, tableau de bord, paramètres, administration des utilisateurs ;
3. compare les captures pixel par pixel.

Le rapport s'affiche dans le résumé du job. L'artefact `visual-regression` contient les
captures `base/`, `head/` et, pour chaque écran modifié, une image `diff/` où les pixels changés
sont en rouge.

Le job est **indicatif** : il échoue quand un écran change, mais il ne fait pas partie des
contrôles requis, donc il ne bloque jamais une fusion. Une PR qui ne devait rien changer
visuellement doit le garder vert ; une PR qui change le rendu volontairement le rend rouge, et
les images servent alors à vérifier le changement.

## Sans backend

`fixtures.py` simule l'API (session, santé, stockage, débit réseau, torrents, utilisateurs) ;
le WebSocket d'événements est accepté sans jamais rien émettre. L'horloge du navigateur est
figée sur `NOW` et les animations sont coupées, donc deux builds identiques donnent exactement
les mêmes pixels. Un appel non simulé reçoit une 404 et apparaît dans le rapport : complétez
alors `fixtures.py`.

## En local

```bash
cd frontend && npm ci && npx vite build && cp -r dist /tmp/base   # sur la branche de base
cd frontend && npx vite build                                      # sur la branche à comparer
pip install playwright==1.63.0 pillow==12.3.0 && python -m playwright install chromium
python visual-regression/capture.py --base /tmp/base --head frontend/dist --out /tmp/report
```

## Retirer la fonctionnalité

Supprimer `.github/workflows/visual-regression.yml` et ce dossier : rien d'autre n'en dépend.
