# Roadmap World of Seeds V2

## Etat de la roadmap

La roadmap de construction initiale V2 est **terminée**.

World of Seeds V2 `2.0.0` est en production sur Rise2. Les étapes V2-00 à V2-35 ont conduit du socle CI/Compose jusqu'au pilote, à la release candidate, à la release stable et au cutover production.

L'unique dette nommée de cette séquence qui reste volontairement ouverte est **V2-32D**, bloquée par l'API NewGreedy v1.7.5 et non bloquante pour la production actuelle.

Cette roadmap reste le résumé historique de la construction 2.0. Les nouveaux développements ne doivent pas continuer artificiellement la numérotation V2-36, V2-37, etc. sauf décision explicite ; ils partent comme tâches post-2.0 orientées fonctionnalité, maintenance, sécurité ou exploitation.

## Règles d'exécution post-2.0

Le flux historique basé sur `develop_V2` est clos.

Pour chaque nouveau changement :

1. partir du dernier `develop` ;
2. créer une branche dédiée (`feat/*`, `fix/*`, `ops/*`, `docs/*`, etc.) ;
3. limiter la PR au scope annoncé ;
4. lancer les tests ciblés pendant le développement ;
5. ouvrir une PR vers `develop` ;
6. attendre les checks requis et résoudre les conversations ;
7. merger dans `develop` ;
8. promouvoir vers la production avec une PR `develop -> master` ;
9. après merge `master`, le CI doit être vert avant tout déploiement automatique Rise2.

`master` et `develop` sont protégées. Aucun push direct, force-push ou suppression de ces branches.

Checks requis :

- `backend` ;
- `frontend` ;
- `Container image` ;
- `Dependency and image security` ;
- `Validate restricted Rise2 deploy path`.

## Historique de construction 2.0

| Phase | Statut | Résultat |
| --- | --- | --- |
| V2-00 à V2-06 | TERMINE | Architecture, CI/versioning, Compose local, schéma partagé, jobs durables, Redis tolérant aux pannes et options PostgreSQL. |
| V2-07 à V2-13C | TERMINE | Déduplication, workers, gateway qB, C411/NewGreedy, activité tracker, scheduler équitable, contrôles qB et routage multi-comptes. |
| V2-14 à V2-18A | TERMINE | Stockage partagé, quotas, manifestes, API torrent, UI téléchargements et smoke local reproductible. |
| V2-19 à V2-28 | TERMINE | HTTP Range, transferts récursifs, fallback ZIP, lifecycle, UX responsive, admin, réconciliation, métriques et observabilité. |
| V2-28A à V2-28H | TERMINE | Autorité scheduler, anti-stall, backlog partagé, WebSocket, transferts scalables, optimisation SQL/métriques, hardening runtime et portabilité monitoring. |
| V2-29 | TERMINE | Compose Rise2 complet et isolé. |
| V2-30 | TERMINE | Sauvegarde/restauration et procédures de restore. |
| V2-31 | TERMINE | Outillage d'import V1 optionnel et réconciliation. |
| V2-32 | TERMINE | Sécurité, charge, scans, pannes/latences, WebSockets et charge 100 comptes. |
| V2-32A | TERMINE | Internationalisation FR/EN. |
| V2-32B | TERMINE | UX/UI/mobile, toasts, confirmations et multi-upload borné. |
| V2-32C | TERMINE | Rétention READY automatique et purge durable. |
| V2-32D | BLOQUE / NON BLOQUANT | NewGreedy v1.7.5 ne permet pas une suppression exacte et durable par SHA-1 complet. |
| V2-32E | TERMINE | Avertissements visuels avant expiration READY. |
| V2-32F | TERMINE | Visibilité des files/rangs et récupération locale. |
| V2-33 | TERMINE | Pilote réel Rise2, critères GO/NO-GO et rollback. |
| V2-34 | TERMINE | Release candidate `2.0.0-rc.1`, validation Rise2 et compatibilité rollback. |
| V2-35 | TERMINE | Stable `2.0.0`, promotion du digest testé, Git tag/release, cutover Rise2. |

## Jalons de validation désormais acquis

### Runtime

- API V2, workers et scheduler en production Rise2 ;
- PostgreSQL/Redis persistants ;
- qBittorrent/NewGreedy intégrés sans ports hôte publics ;
- stockage média partagé sous `/srv/world-of-seeds-v2/data` ;
- monitoring et alerting actifs.

### Sécurité / qualité

- CI backend/frontend/container/security ;
- scans de dépendances/image ;
- migrations et rollback validés ;
- secret handling borné ;
- protections de branches GitHub actives.

### Performance / robustesse

- charge CI 100 comptes ;
- WebSockets multi-tabs/reconnect ;
- jobs durables et reprise après crash ;
- scheduler borné/anti-starvation ;
- sauvegarde off-host et restore drill ;
- rollback applicatif réel Rise2 validé.

### Release / production

- stable `2.0.0` publiée ;
- DNS production sur Rise2 ;
- HTTPS production ;
- déploiement manuel GitHub Actions validé ;
- déploiement automatique après CI `master` vert validé.

## Dette connue

### V2-32D — NewGreedy purge cleanup

Ne pas contourner le blocage actuel par :

- suppression sur préfixe 8 caractères ;
- reset global ;
- édition manuelle de `stats.json` comme pseudo-source autoritaire ;
- mutation qui peut être réécrasée par l'état en mémoire NewGreedy.

Critère de réouverture : NewGreedy expose une suppression exacte par full SHA-1, idempotente, cohérente entre mémoire et persistance, ou la dépendance est remplacée par un composant offrant ce contrat.

## Backlog post-2.0

Aucune nouvelle fonctionnalité n'est imposée par cette roadmap au moment du cutover.

Les prochains sujets doivent être créés explicitement à partir d'un besoin produit ou opérateur, puis classés par risque :

- **RAPIDE** : changement local, pas de migration/topologie ;
- **MOYEN** : plusieurs couches ou migration additive simple ;
- **ELEVE** : concurrence, stockage, sécurité, migration importante ou infrastructure production.

Pour une modification d'infrastructure sensible Rise2 (Compose, qBittorrent, NewGreedy, volumes, réseaux, ingress), ne pas compter sur le canal de déploiement applicatif automatique : préparer une procédure opérateur dédiée et un rollback explicite.

## Definition of Done post-2.0

Une tâche est terminée lorsque :

- le scope annoncé est respecté ;
- les tests ciblés passent ;
- les migrations/rollback sont couverts si concernés ;
- aucune donnée sensible n'est exposée ;
- les checks requis de PR sont verts ;
- les conversations sont résolues ;
- `PROGRESS.md` est mis à jour si l'état opérationnel change ;
- `CONTEXT.md` est mis à jour seulement pour une décision durable ;
- la promotion `develop -> master` est séparée de la PR de feature si la mise en production doit être contrôlée.
