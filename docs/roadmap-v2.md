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

## Refonte UX post-2.0

### Direction produit validée

L'interface utilisateur est **torrent-centric**.

Le parcours principal est :

```text
Connexion
  -> Dashboard
     -> ajout d'un .torrent
     -> suivi de la file et du téléchargement
     -> contenu READY
     -> récupération sur le poste local
     -> suppression / désabonnement
```

Le navigateur de fichiers utilisateur, la corbeille utilisateur, les workspaces métier personnels et les actions de création/gestion libre de dossiers ne font plus partie du runtime moderne.

Le stockage physique torrent reste partagé et dédupliqué. `ManagedTorrent` représente la copie physique, `TorrentFile` son manifeste et `TorrentRequest` le droit/abonnement utilisateur. `SharedContentStore` reste l'autorité filesystem du contenu torrent sous `content/<storage-key>`.

La refonte conserve les invariants V2 : PostgreSQL reste autoritaire, Redis non autoritaire, le frontend ne pilote jamais qBittorrent ou NewGreedy directement, les leases/règles de rétention restent applicables et aucun chemin hôte n'est exposé au client.

### Règles de scope UX

Pour les premiers écrans de la refonte, réutiliser les contrats et données déjà disponibles avant d'ajouter de la télémétrie backend.

En particulier, UX-02 à UX-04 n'ajoutent pas de backend uniquement pour exposer seeders, peers, ETA qBittorrent, ratio, débit qBittorrent ou télémétrie globale de récupération.

La file de récupération présentée reste celle gérée localement par le contrôleur navigateur. Elle peut afficher le nombre de récupérations actives, la concurrence maximale locale et la position des éléments en attente disponibles dans ce contrôleur. Elle ne doit pas être présentée comme une file globale autoritaire multi-appareils.

### Plan de PR

| Tâche | Risque | Dépendances | Statut | Scope |
| --- | --- | --- | --- | --- |
| UX-00 | RAPIDE | aucune | TERMINE | Formaliser la direction produit, le découpage des PR et les contraintes de scope. |
| UX-01 | MOYEN | UX-00 | TERMINE | Design system, palettes claire/sombre, composants UI modernes, préférence `light/dark/system`, préférences langue/thème, nouveau shell et login. |
| UX-02 | MOYEN | UX-01 | TERMINE | Nouveau Dashboard utilisateur avec cartouches torrents, récupération locale et stockage. |
| UX-03 | MOYEN | UX-02 | TERMINE | Gestionnaire de torrents en accordéons en conservant drag/drop, multi-upload, progression, queue, WebSocket, annulation/désabonnement et rétention. |
| UX-04 | MOYEN | UX-03 | TERMINE | Recomposer l'expérience READY et la récupération locale sans nouvelle télémétrie backend. |
| UX-05 | ELEVE | UX-04 | TERMINE | Retirer l'ancien espace utilisateur Fichiers/Corbeille/création de dossiers et nettoyer le code frontend mort. |
| UX-05B | ELEVE | UX-05 | TERMINE | Supprimer le filesystem/workspace utilisateur legacy, migrer la capacité Dashboard vers un contrat partagé dédié et nettoyer routes/files/trash/workspaces morts sans toucher au `SharedContentStore` torrent. |
| UX-06 | MOYEN | UX-05B | TERMINE | Harmoniser l'administration avec le design system, finaliser responsive/accessibilité, supprimer les reliquats frontend morts et effectuer le nettoyage final. |

### UX-01 — Design system, thèmes et préférences

Objectifs accomplis :

- palettes Light/Dark à tokens partagés ;
- thèmes `light`, `dark`, `system` persistés ;
- préférence de langue FR/EN conservée ;
- primitives UI légères réutilisables ;
- login, shell, paramètres et credentials alignés sur le design system ;
- mobile-first obligatoire.

### UX-02 — Dashboard utilisateur

Le Dashboard est la page d'accueil authentifiée avec :

1. **Torrents** : actifs, READY/terminés et en attente à partir des données torrent existantes.
2. **Récupérations** : état du contrôleur local du navigateur.
3. **Stockage** : capacité disque partagée.

Le Dashboard reste compact et responsive.

### UX-03 — Gestionnaire de torrents en accordéons

L'ancienne table a été remplacée par des cartes/accordéons conservant :

- nom, taille, état, progression et queue ;
- annulation/désabonnement ;
- dates/rétention/détails déjà exposés ;
- pagination, WebSocket, drag/drop et multi-upload.

Aucune télémétrie qB/NewGreedy décorative n'a été ajoutée.

### UX-04 — READY et récupération locale

Capacités conservées et recomposées :

- fichier unique téléchargeable directement ;
- torrent multi-fichiers consultable ;
- téléchargement fichier par fichier ;
- téléchargement complet via le contrôleur navigateur existant ;
- manifeste paginé ;
- fallback ZIP lorsqu'il est disponible ;
- pause/reprise/annulation/progression et concurrence locale bornée.

### UX-05 — Retrait du legacy utilisateur fichiers/corbeille

L'UI utilisateur Fichiers/Corbeille a été retirée en premier, avec nettoyage des composants/actions frontend devenus morts et redirection des anciens liens `?path=` vers le Dashboard.

Cette étape avait volontairement conservé temporairement les briques backend dont le Dashboard dépendait encore pour la capacité stockage. UX-05B a ensuite supprimé ce dernier couplage.

### UX-05B — Suppression du filesystem utilisateur legacy

UX-05B aligne le runtime sur le modèle torrent-centric durable :

- aucun filesystem métier personnel `/data/<username>` n'est requis par le runtime moderne ;
- `ManagedTorrent` représente la copie physique partagée, `TorrentFile` son manifeste et `TorrentRequest` le droit/abonnement utilisateur ;
- le Dashboard lit la capacité via `GET /api/v2/storage` au lieu de conserver `/api/v1/files` pour deux métriques ;
- les routes/services de browsing, création, renommage, déplacement, téléchargement arbitraire et corbeille utilisateur ont été retirés lorsqu'ils n'avaient plus de consommateur moderne ;
- `WorkspaceManager` a été retiré du lifecycle des comptes et du runtime moderne ;
- `TrashEntry` et `trash_entries` sont supprimés par migration Alembic `20260909_24_drop_legacy_user_trash.py`, avec downgrade couvert ;
- l'administration n'expose plus de navigation/métriques de corbeille legacy ;
- les primitives HTTP Range/stream nécessaires à READY ont été extraites du navigateur legacy ;
- `SharedContentStore` reste l'autorité filesystem moderne et conserve `content/<storage-key>` ; aucune migration physique/renommage de contenu n'est faite ;
- la suppression d'un utilisateur/droit conserve le lifecycle existant : une copie partagée reste présente tant qu'une autre demande active existe, et la dernière référence passe par la purge normale ;
- les smokes Rise2 et la policy de stockage ont été adaptés au modèle partagé.

Validation : migrations aller/retour, Ruff, mypy, pytest, frontend check/tests/build, sécurité, image/smokes V2 et policy Rise2 verts sur la PR #157 avant finalisation documentaire.

### UX-06 — Harmonisation et finition

UX-06 clôt la séquence de refonte :

- le shell administration et les vues Utilisateurs, Services, Paramètres et Stockage sont alignés sur les primitives du design system ;
- la composition admin est mobile-first, avec navigation et actions tactiles, noms longs bornés et enrichissement progressif tablette/desktop ;
- les états loading/error, badges, cartes, progression et boutons sont harmonisés sans changer les contrats métier ;
- `Dialog` est devenu un composant générique partagé hors du namespace legacy `features/files`, avec focus trap, Escape, restauration du focus et test axe ;
- le dernier module frontend `features/files`, l'ancien écran admin corbeille rendu obsolète par UX-05B et les contrats client correspondants sont supprimés ;
- la policy responsive vérifie explicitement le contrat mobile-first admin et l'absence de réintroduction du filesystem frontend ;
- le Dashboard, les accordéons torrent, READY, la récupération locale, qBittorrent, NewGreedy, Redis, scheduler, rétention et stockage partagé ne changent pas fonctionnellement.

Validation du HEAD fonctionnel UX-06 `dad0ff8a09a2029f557fd135ffd8896629e854b5` avant finalisation documentaire : frontend check/tests/build, backend Ruff/format/mypy/pytest, sécurité, image/smokes V2 et policy Rise2 verts. Aucune conversation de review ouverte sur la PR #158.

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

La séquence prioritaire **UX-00 → UX-06 est terminée**. Aucun UX-07 n'est créé implicitement.

Les nouveaux sujets doivent être créés explicitement à partir d'un besoin produit ou opérateur, puis classés par risque :

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