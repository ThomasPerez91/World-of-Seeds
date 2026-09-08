# World of Seeds — Agent Context

## Purpose

Ce document est le handoff durable pour les agents travaillant sur World of Seeds.
Il décrit l'état de production, l'architecture, les invariants de sécurité et les règles de contribution.
Les états de tâche volatils appartiennent à `PROGRESS.md`.

## Produit et production

World of Seeds est une application privée de gestion de seedbox avec :

- gestionnaire de fichiers authentifié ;
- soumission et suivi de torrents ;
- stockage physique partagé et déduplication ;
- workers durables et scheduler équitable ;
- administration des comptes, quotas, options et opérations de récupération ;
- observabilité Prometheus/Grafana.

La ligne de production active est **V2**.

- version stable : `2.0.0` ;
- production : Rise2 ;
- domaine public : `world-of-seeds.fr` ;
- V1 `1.3.3` : legacy/rollback seulement.

## Direction produit post-2.0 — refonte UX planifiée

La prochaine évolution produit validée est une refonte progressive de l'interface utilisateur autour d'un **Dashboard torrent-centric**.

Cette section décrit une direction durable approuvée, pas un état déjà déployé : les PR UX-01 à UX-06 doivent être intégrées séparément et conserver une application fonctionnelle entre chaque étape.

Cible utilisateur :

- après authentification, le Dashboard devient l'accueil principal ;
- le parcours central est ajout `.torrent` -> file/téléchargement -> READY -> récupération locale -> suppression/désabonnement ;
- l'ancien navigateur de fichiers utilisateur, la corbeille utilisateur et les actions de création libre de dossiers ne font plus partie de la cible UX et doivent être retirés lors de UX-05 après audit de leurs dépendances techniques ;
- les écrans admin restent disponibles et sont harmonisés avec le nouveau design lors de UX-06.

Principes de design :

- palettes claire et sombre plus lisibles et moins oppressantes que l'UI 2.0 initiale ;
- thème `light`, `dark` ou `system` avec préférence persistée par utilisateur ;
- langue FR/EN conservée comme préférence utilisateur ;
- surfaces/cartouches compacts, boutons modernes et hiérarchie visuelle plus dense ;
- éviter les grands titres et espaces vides qui réduisent la densité utile ;
- réserver les couleurs fortes aux vrais états d'erreur, avertissements et actions destructrices ;
- **mobile-first obligatoire** pour toute nouvelle UI : concevoir les styles et la hiérarchie d'abord pour les petits écrans, puis enrichir progressivement l'expérience aux breakpoints tablette et desktop ;
- le responsive est un critère de Definition of Done de **chaque PR UX**, pas une finition reportée à UX-06 : aucun débordement horizontal, contrôles tactiles utilisables, textes/noms longs correctement bornés, cartes/accordéons/actions lisibles et opérables sur mobile ;
- toute modification UI doit être vérifiée sur des largeurs représentatives mobile, tablette et desktop, avec un soin particulier porté aux états chargement/vide/erreur et aux contenus longs.

Principes de scope :

- réutiliser en priorité les données et contrats déjà exposés par l'API ;
- UX-02 à UX-04 ne doivent pas ajouter du backend uniquement pour afficher seeders, peers, ETA, vitesse qBittorrent, ratio ou une télémétrie globale de récupération ;
- le frontend ne doit jamais contacter qBittorrent ou NewGreedy directement ;
- si une donnée de confort n'est pas déjà disponible, l'omettre de la première refonte et la traiter ultérieurement dans une tâche dédiée ;
- la file de récupération affichée pendant cette première refonte est la file locale du contrôleur navigateur existant : nombre de transferts actifs, concurrence maximale locale et positions disponibles pour les éléments en attente ; elle n'est pas une file globale autoritaire inter-utilisateurs ou multi-appareils ;
- l'annulation d'un torrent doit conserver le modèle V2 existant : désabonnement d'un utilisateur lorsqu'il reste d'autres droits actifs, puis lifecycle de purge seulement lorsqu'il ne reste plus de demande active ;
- V2-32D reste bloquée : ne pas prétendre supprimer précisément les statistiques NewGreedy lors d'une dernière annulation tant que NewGreedy n'offre pas le contrat full-hash requis.

Le découpage de référence est `UX-01` à `UX-06` dans `docs/roadmap-v2.md`. `PROGRESS.md` indique la tâche courante.

## Repository et branches

Repository : `ThomasPerez91/World-of-Seeds`.

Branches courantes :

- `master` : production V2 ;
- `develop` : intégration V2 ;
- `develop_V2` : historique de construction V2, à ne plus utiliser pour les nouveaux travaux.

Flux obligatoire :

```text
feature/* -> develop -> master -> CI vert -> Rise2
```

Règles :

- chaque changement part du dernier `develop` sur une branche dédiée ;
- chaque branche revient vers `develop` via PR ;
- une promotion production passe par une PR `develop -> master` ;
- ne jamais pousser directement sur `develop` ou `master` ;
- ne jamais merger avec un check requis rouge ;
- résoudre les conversations de review avant merge ;
- `master` et `develop` sont protégées, force-push et suppression interdits.

Checks obligatoires sur les branches protégées :

- `backend` ;
- `frontend` ;
- `Container image` ;
- `Dependency and image security` ;
- `Validate restricted Rise2 deploy path`.

## Technologie

- Backend : Python, FastAPI, SQLAlchemy, Alembic, Pydantic.
- Base : PostgreSQL.
- Coordination/cache non autoritaire : Redis.
- Frontend : React, TypeScript, Vite.
- Tests frontend : Vitest, Testing Library, axe.
- Tests backend : pytest.
- Lint/format : Ruff.
- Typage : mypy + TypeScript.
- Packaging : uv + npm.
- Runtime : Docker Compose.
- Torrent : qBittorrent + NewGreedy.
- Ingress : Caddy.
- Monitoring : Prometheus, Grafana, node-exporter, cAdvisor.

## Topologie Rise2

Checkout opérateur :

`/opt/world-of-seeds-v2`

Environment/secrets :

`/etc/world-of-seeds-v2/environment`

Compose de production :

`deploy/compose.rise2.v2.yaml`

Services principaux :

- `ingress` ;
- `migrate` ;
- `api` ;
- `worker` (répliqué, normalement 2) ;
- `scheduler` (singleton) ;
- `postgres` ;
- `redis` ;
- `qbittorrent` ;
- `newgreedy` ;
- monitoring.

Seul l'ingress publie les ports publics 80/443. PostgreSQL, Redis, qBittorrent et NewGreedy ne publient aucun port hôte public.

## Stockage et persistance

Racine média V2 hôte :

`/srv/world-of-seeds-v2/data`

Elle est montée dans WOS et qBittorrent comme :

`/data`

Ce stockage est un bind mount hôte, pas un filesystem éphémère de conteneur.

Les données structurées et états techniques utilisent des volumes/paths persistants dédiés :

- PostgreSQL : `postgres_v2_data` ;
- Redis : `redis_v2_data` ;
- qBittorrent : `qbittorrent_v2_config` ;
- NewGreedy state : `/srv/world-of-seeds-v2/newgreedy-state` ;
- Prometheus/Grafana/Caddy : volumes V2 dédiés.

Ne jamais utiliser `docker compose down --volumes` dans une opération de déploiement ou rollback ordinaire.

Le stockage média Rise2 est volontairement distinct de la V1. Les opérations de backup/restauration doivent préserver le contrat documenté dans les scripts et runbooks Rise2.

## Déploiement production

Le workflow `Deploy V2 to Rise2` est le canal production officiel.

Déclencheurs :

- automatique après succès du workflow `CI` sur un `push` de `master` ;
- manuel via `workflow_dispatch` pour une opération contrôlée.

Contrat de sécurité :

- la révision cible est le SHA exact du CI `master` réussi ;
- un run obsolète est refusé si le HEAD de `master` a changé ;
- l'image est construite pour `linux/amd64` ;
- l'image est publiée sur GHCR et déployée par digest immuable ;
- l'identité SSH `wosdeploy` est dédiée, sans shell général, avec commande forcée ;
- le helper root vérifie à nouveau le HEAD de `master`, l'historique fast-forward et les labels OCI ;
- le déploiement standard recrée seulement la couche applicative WOS/ingress ;
- PostgreSQL, Redis, qBittorrent et NewGreedy doivent rester présents, healthy et non recréés ;
- les changements sensibles qB/NewGreedy/topologie Compose bloquent le canal automatique.

Etat du dernier déploiement :

`/var/lib/world-of-seeds-v2/deploy/current.env`

## Authentification et autorisation

- Toutes les routes fichiers/torrents exigent un utilisateur authentifié.
- Les routes d'administration exigent le rôle administrateur.
- Le serveur résout l'utilisateur ; ne jamais faire confiance à un username envoyé par le client.
- Les workspaces sont validés côté serveur avant toute opération filesystem ou qBittorrent.
- Un utilisateur ne peut agir que dans son workspace.
- Ne jamais révéler chemins hôte, secrets, passkeys ou données d'un autre utilisateur dans une réponse API.

## Préférences d’interface

- `User.preferred_theme` est obligatoire, vaut `light`, `dark` ou `system` et a pour défaut serveur `system` (migration `20260908_23`). Le champ est exposé dans les réponses utilisateur.
- `PATCH /api/v1/auth/theme` reçoit `{ "preferred_theme": "dark" }` et retourne `AuthResponse`, avec les mêmes exigences authentification/CSRF que la langue. Aucune modification des règles de credentials/session.
- `data-theme="light|dark"` sur `document.documentElement` représente le thème effectif ; `system` est une préférence, jamais une palette CSS.
- Le bootstrap externe same-origin `theme-bootstrap.js` applique avant React la dernière préférence locale (`wos.preferred-theme`), ou `system` si absente/invalide/inaccessible. Le compte devient autoritaire lors de la connexion/restauration de session ; le cache ne contient aucun secret.
- Le provider englobe tous les écrans, suit les changements de `prefers-color-scheme` en mode système et centralise les écritures. Une sauvegarde échouée rétablit le choix précédent sans invalider la session. Les réponses d’une session quittée sont ignorées.

## Invariants filesystem

- Les chemins clients sont relatifs au workspace authentifié.
- Refuser chemins absolus, `..`, évasions de racine et traversées de symlinks.
- Les opérations sensibles utilisent des résolutions sûres/descripteurs quand possible.
- Ne jamais résoudre un problème de permissions avec `chmod 777`.
- Les extensions protégées sont reconstruites côté serveur lors d'un rename.
- Les noms longs et chemins imbriqués ne doivent pas provoquer de débordement horizontal mobile.

## Torrent et sécurité tracker

- Parser le bencode strictement.
- Le hash est calculé depuis les octets bruts exacts du dictionnaire `info`; ne jamais le réencoder avant calcul.
- Les trackers sont allowlistés côté serveur.
- Les passkeys et credentials restent des secrets de déploiement ; ne jamais les persister dans les tables métier, logs, diagnostics ou réponses frontend.
- Les références multi-comptes sont opaques côté domaine.

## qBittorrent / NewGreedy

qBittorrent et NewGreedy sont des services internes Rise2.

- qB utilise le même stockage `/data` que WOS ;
- le save path est dérivé côté serveur ;
- le client ne peut pas choisir un chemin hôte ;
- le scheduler est l'autorité de start/stop ;
- qB doit recevoir un torrent arrêté avant la première décision scheduler ;
- les torrents externes/non WOS restent hors contrôle destructif de WOS ;
- NewGreedy proxy les trackers, pas les peers ;
- NewGreedy garde sa CA et son état persistant dédiés ;
- aucun port qB/NewGreedy n'est publié sur l'hôte.

V2-32D reste bloqué : NewGreedy v1.7.5 ne garantit pas une suppression exacte et durable par SHA-1 complet. Ne pas implémenter de contournement par préfixe/reset global/édition directe.

## Scheduler et jobs

PostgreSQL est l'autorité durable.

Redis est un accélérateur de coordination/cache/notifications ; une perte Redis ne doit pas supprimer la vérité métier.

Le scheduler :

- est singleton ;
- décide seul des torrents actifs ;
- respecte une limite globale configurable ;
- maintient équité pondérée, déficit, aging/anti-starvation et caps ;
- ne compte pas les torrents READY/seeding comme slots actifs ;
- utilise les octets restants/observations utiles plutôt que seulement la taille totale ;
- persiste cooldown et état de contrôle afin de survivre aux redémarrages.

Les workers :

- claim les jobs PostgreSQL durablement ;
- exécutent les effets qB/storage ;
- sont idempotents et tolèrent crash/reprise ;
- publient les transitions temps réel seulement après commit.

## Déduplication, stockage et quotas

Un `ManagedTorrent` représente un torrent physique partagé.

Les `TorrentRequest` représentent les droits utilisateurs.

- Un infohash physique ne doit pas être téléchargé plusieurs fois pour plusieurs utilisateurs.
- Les quotas utilisateurs sont logiques et transactionnels.
- Le stockage physique partagé est comptabilisé séparément.
- Les opérations de purge sont idempotentes et attendent les leases de téléchargement.
- Le stockage observé et le ledger applicatif sont des notions distinctes.

## Rétention READY

Chaque torrent physique READY possède une date de première disponibilité et une échéance durable.

La rétention dépend de la popularité historique et peut être prolongée par de nouvelles demandes avant expiration, jamais raccourcie.

A l'échéance :

- les droits actifs sont expirés atomiquement ;
- le torrent passe vers `PURGE_PENDING` ;
- un stop scheduler durable est enregistré ;
- une purge worker idempotente est créée ;
- les leases existants peuvent terminer, mais aucun nouveau droit expiré n'est accordé.

## Temps réel et transferts navigateur

L'interface torrent charge un état PostgreSQL autoritaire puis reçoit des événements WebSocket non autoritaires.

- pas de polling complet toutes les dix secondes ;
- après reconnexion, faire une resynchronisation GET autoritaire ;
- un WebSocket idle ne doit pas maintenir de session SQL.

Les téléchargements récursifs utilisent un manifeste paginé/progressif et une concurrence bornée. Ne pas attendre un manifeste énorme complet avant de démarrer les premiers fichiers.

Pendant la refonte UX, conserver ces mécanismes et les recomposer dans le Dashboard/les accordéons au lieu de créer un second système de suivi parallèle.

## Observabilité

Prometheus/Grafana surveillent application, jobs, scheduler, DB, Redis, qB, stockage et hôte.

Les métriques doivent rester sans secrets et à cardinalité bornée.

Les exporters internes ne doivent pas publier de nouveaux ports hôte sans décision explicite.

## Backup et rollback

Les sauvegardes off-host Rise2 et le restore drill sont des éléments obligatoires du dispositif de production.

Rollback applicatif :

- préserver PostgreSQL, Redis, qBittorrent, NewGreedy, CA et stockage ;
- restaurer un digest applicatif validé ;
- recréer uniquement les services applicatifs nécessaires ;
- vérifier API, workers, scheduler et intégrations ;
- ne jamais supprimer les volumes pendant un rollback ordinaire.

L'ancien serveur V1 reste seulement une possibilité de rollback trafic pendant la fenêtre décidée par l'opérateur. Ne pas supposer qu'il restera éternellement disponible.

## Documentation

- `docs/agent/CONTEXT.md` : invariants durables et architecture courante.
- `docs/agent/PROGRESS.md` : état opérationnel et dernière étape accomplie.
- `docs/roadmap-v2.md` : historique de la construction V2, règles post-2.0 et roadmap UX post-2.0.
- `docs/deployment-rise2-github-actions.md` : CI/CD production.

Toute modification durable d'architecture, de direction produit ou de flux de release doit mettre ces fichiers en cohérence dans la même PR.
