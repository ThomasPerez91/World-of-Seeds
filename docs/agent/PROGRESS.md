# World of Seeds — Progress

## Etat courant — 8 septembre 2026

World of Seeds V2 est désormais la ligne de production active.

- Version applicative stable : `2.0.0`.
- Production : Rise2.
- Branche de production : `master`.
- Branche d'intégration : `develop`.
- Le dernier `develop` vérifié au démarrage de la planification UX post-2.0 est `3e1f9db9fd8af1e51334266d4747feeb941a3511` (PR #148). Tout nouvel agent doit néanmoins faire un `fetch` et vérifier le HEAD réel avant de créer sa branche.
- `develop_V2` est une branche historique/legacy de la phase de construction V2 ; ne plus l'utiliser pour les nouveaux développements.
- La V1 `1.3.3` reste figée par son tag/release et sur l'ancien serveur uniquement comme solution de rollback pendant la fenêtre de conservation ; elle n'est plus la production active et son ancien déploiement GitHub est désactivé.

## Production Rise2

Le cutover V2 est terminé : le trafic public de `world-of-seeds.fr` arrive sur Rise2.

Runtime principal :

- API FastAPI V2 ;
- deux workers durables ;
- scheduler singleton ;
- PostgreSQL ;
- Redis ;
- qBittorrent ;
- NewGreedy ;
- Caddy ;
- Prometheus, Grafana, node-exporter et cAdvisor.

Le stockage utilisateur est un bind mount hôte, hors conteneurs :

- hôte : `/srv/world-of-seeds-v2/data` ;
- conteneurs WOS/qB : `/data`.

Les données persistantes ne sont pas recréées lors d'un déploiement applicatif standard : PostgreSQL, Redis, qBittorrent et NewGreedy sont préservés et leurs IDs de conteneur sont contrôlés pendant le déploiement.

Etat/configuration opérateur :

- checkout : `/opt/world-of-seeds-v2` ;
- environnement/secrets : `/etc/world-of-seeds-v2/environment` ;
- état NewGreedy : `/srv/world-of-seeds-v2/newgreedy-state` ;
- état du dernier déploiement : `/var/lib/world-of-seeds-v2/deploy/current.env`.

Les sauvegardes off-host et le restore drill Rise2 ont été validés avant le passage en production.

## Release V2 et cutover

La chaîne V2-33 → V2-35 est terminée :

- pilote Rise2 validé ;
- release candidate `2.0.0-rc.1` validée sur Rise2 ;
- rollback applicatif réel validé ;
- release stable `2.0.0` publiée ;
- tag Git `v2.0.0` et GitHub Release créés ;
- DNS production basculé vers Rise2 ;
- HTTPS production validé ;
- observation post-cutover sans rollback.

La release stable historique reste ancrée au SHA applicatif `4814d4636e4a14edfa711c2c78da4dd5a8df2300` et au digest stable `sha256:65559c7f38d2be5c38132c0301f3ca0bae1a214e2398b2ff9fcc4fa7f82b236e`. Les commits OPS ultérieurs conservent la version applicative `2.0.0` tant qu'aucune nouvelle version produit n'est décidée.

## CI/CD production

Le canal de déploiement GitHub Actions vers Rise2 est opérationnel.

Premier passage manuel validé :

- workflow : `Deploy V2 to Rise2` ;
- run : `34204307209` ;
- SHA : `2164c4411d84fd4381f7e58370189eef86ecc263` ;
- résultat : SUCCESS.

Premier passage automatique validé :

- CI `master` : run `34205528939` / CI #443 ;
- SHA : `7010fa9f2a86cd74961bfc60eaaab5c2f7aa5f14` ;
- déploiement automatique : run `34205834353` ;
- événement : `workflow_run` ;
- résultat : SUCCESS.

Contrat courant :

1. un merge/push sur `master` lance le CI ;
2. aucun déploiement n'est autorisé si le CI échoue ;
3. après un CI `master` vert, le workflow verrouille le `head_sha` exact ;
4. il refuse un run obsolète si `master` a déjà bougé ;
5. il construit l'image `linux/amd64` exacte et la publie sur GHCR ;
6. il déploie uniquement le digest immuable via la commande SSH forcée `wosdeploy` ;
7. migrations, API, workers, scheduler, services persistants et HTTPS sont vérifiés avant succès.

`workflow_dispatch` reste disponible pour un déploiement manuel contrôlé.

Les changements d'infrastructure sensibles qBittorrent/NewGreedy/topologie Compose restent explicitement exclus du canal automatique et demandent une opération dédiée.

## Protections GitHub

`master` et `develop` sont protégées et les règles s'appliquent aussi à l'administrateur du dépôt.

Règles :

- passage par pull request ;
- zéro approbation humaine obligatoire, le dépôt étant maintenu par une seule personne ;
- branche à jour avant merge (`strict`) ;
- conversations de review résolues ;
- force-push interdit ;
- suppression de branche protégée interdite.

Checks requis :

- `backend` ;
- `frontend` ;
- `Container image` ;
- `Dependency and image security` ;
- `Validate restricted Rise2 deploy path`.

## Flux de développement courant

Le flux historique `feature/* → develop_V2` est terminé.

Pour tout nouveau travail :

1. partir du dernier `develop` ;
2. créer une branche dédiée ;
3. développer et lancer les tests ciblés ;
4. ouvrir une PR vers `develop` ;
5. laisser passer les checks obligatoires et résoudre les conversations ;
6. merger dans `develop` ;
7. lorsque le changement doit partir en production, ouvrir une PR `develop → master` ;
8. après merge sur `master`, le déploiement Rise2 est automatique uniquement après CI vert.

Ne jamais pousser directement sur `master` ou `develop`.

## Refonte UX post-2.0

La priorité produit post-2.0 est désormais une refonte de l'expérience utilisateur autour d'un Dashboard **torrent-centric**.

Décisions validées :

- la page d'accueil utilisateur devient un Dashboard de suivi des torrents ;
- l'ancien espace utilisateur Fichiers/Corbeille n'est plus la cible produit et sera retiré après audit de ses dépendances ;
- le Dashboard présente des synthèses torrents, récupération locale et stockage en réutilisant d'abord les contrats déjà disponibles ;
- le gestionnaire de torrents adopte une présentation compacte en accordéons ;
- l'ajout `.torrent` par clic et glisser/déposer, la progression, les états de queue, le WebSocket, l'annulation/désabonnement, la rétention et le manifeste READY existants sont à réutiliser, pas à réécrire ;
- la première refonte n'ajoute pas de backend seulement pour seeders, peers, ETA, vitesse qB, ratio ou télémétrie globale de récupération ;
- la récupération affichée dans cette phase correspond à la file locale du contrôleur navigateur existant : nombre actif / concurrence maximale locale et positions d'attente disponibles ; elle n'est pas une file globale autoritaire multi-appareils ;
- le thème doit offrir `light`, `dark` et `system` avec préférence utilisateur persistée ; la préférence de langue FR/EN existante reste conservée ;
- les couleurs doivent être plus claires et douces, les boutons plus modernes et les couleurs sémantiques fortes réservées aux vrais avertissements/actions destructrices ;
- le login, les paramètres, le shell utilisateur et ensuite l'administration doivent converger vers le même design system.

### Découpage des tâches

- **UX-00 — TERMINE** : planification documentaire de la refonte dans `roadmap-v2.md`, `PROGRESS.md` et `CONTEXT.md`.
- **UX-01 — A FAIRE** : design system, thèmes, préférence persistée, cartouche Préférences, login/settings/shell.
- **UX-02 — A FAIRE** : nouveau Dashboard et ses cartouches en composant les données/API existantes.
- **UX-03 — A FAIRE** : gestionnaire de torrents en accordéons avec les contrats actuels.
- **UX-04 — A FAIRE** : expérience READY et récupération locale, sans nouvelle télémétrie backend.
- **UX-05 — A FAIRE** : retrait de l'espace utilisateur Fichiers/Corbeille et nettoyage après audit de dépendances.
- **UX-06 — A FAIRE** : harmonisation admin, responsive, accessibilité et nettoyage final.

Le détail, les dépendances et la Definition of Done de chaque tâche sont dans `docs/roadmap-v2.md`.

## Dette / points encore ouverts

### V2-32D — nettoyage NewGreedy à la purge

Toujours BLOQUE et non bloquant pour la production actuelle.

NewGreedy v1.7.5 ne fournit pas de suppression exacte, durable et idempotente par SHA-1 complet. WOS ne doit pas contourner cette limite avec une suppression par préfixe, un reset global ou une édition directe non autoritaire de l'état NewGreedy.

Réévaluer uniquement si NewGreedy expose un contrat de suppression full-hash fiable ou si WOS remplace cette dépendance.

### V1

La V1 ne reçoit plus de développement normal. Elle reste seulement une référence historique et une solution de rollback temporaire tant que cette fenêtre n'est pas explicitement fermée. Ne pas réactiver son ancien workflow de déploiement.

### PR legacy

Les PR encore ouvertes contre `develop_V2` sont historiques et ne doivent pas être fusionnées telles quelles dans le nouveau flux. Toute correction encore pertinente doit être réévaluée puis réimplémentée depuis le `develop` courant.

## Prochaine tâche

**UX-01 — Design system, thèmes et préférences.**

Elle doit partir du dernier `develop` réel après merge de UX-00, sur une branche dédiée, et rester limitée aux fondations visuelles, aux préférences utilisateur et aux écrans nécessaires à leur adoption. Ne pas commencer UX-02 dans la même PR.
