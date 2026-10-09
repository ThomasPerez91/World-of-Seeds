# World of Seeds — Agent Context

## Purpose

Ce document est le handoff durable pour les agents travaillant sur World of Seeds.
Il décrit l'état de production, l'architecture, les invariants de sécurité et les règles de contribution.
Les états de tâche volatils appartiennent à `PROGRESS.md`.

## Produit et production

World of Seeds est une application privée de gestion de seedbox avec :

- soumission et suivi de torrents ;
- Dashboard utilisateur torrent-centric ;
- stockage physique partagé et déduplication ;
- récupération READY vers le navigateur ;
- workers durables et scheduler équitable ;
- administration des comptes, quotas, options et opérations de récupération ;
- observabilité Prometheus/Grafana.

La ligne de production active est **V2**.

- version stable : `2.3.13` ;
- production : Rise2 ;
- domaine public : `world-of-seeds.fr` ;
- V1 `1.3.3` : legacy/rollback seulement.

## Direction produit post-2.0 — UX torrent-centric

La refonte UX post-2.0 est structurée autour d'un **Dashboard torrent-centric**.

Cible utilisateur durable :

- après authentification, le Dashboard est l'accueil principal ;
- le parcours central est ajout `.torrent` -> file/téléchargement -> READY -> récupération locale -> suppression/désabonnement ;
- le navigateur de fichiers utilisateur, la corbeille utilisateur, les actions de création libre de dossiers et les workspaces métier personnels ne font plus partie du runtime moderne ;
- les écrans admin restent disponibles et sont harmonisés avec le design system lors de UX-06.

Principes de design :

- thème unique Forest / Green sombre, indépendant du système d'exploitation et du compte ;
- langue FR/EN conservée comme préférence utilisateur ;
- surfaces/cartouches compacts, boutons modernes et hiérarchie visuelle dense ;
- éviter les grands titres et espaces vides qui réduisent la densité utile ;
- réserver les couleurs fortes aux vrais états d'erreur, avertissements et actions destructrices ;
- **mobile-first obligatoire** pour toute nouvelle UI ;
- le responsive est un critère de Definition of Done de **chaque PR UX** : aucun débordement horizontal, contrôles tactiles utilisables, textes/noms longs bornés, cartes/accordéons/actions lisibles et opérables sur mobile ;
- toute modification UI doit être vérifiée sur des largeurs représentatives mobile, tablette et desktop, avec les états chargement/vide/erreur et les contenus longs.

Principes de scope :

- réutiliser en priorité les données et contrats existants ;
- le frontend ne doit jamais contacter qBittorrent ou NewGreedy directement ;
- la file de récupération affichée est celle du contrôleur navigateur local : nombre de transferts actifs, concurrence maximale locale et positions disponibles pour les éléments en attente ; elle n'est pas une file globale autoritaire inter-utilisateurs ou multi-appareils ;
- les téléchargements natifs gérés par le navigateur ne sont ni persistés ni suivis par WoS ; aucun bloc Dashboard de récupération locale n'est affiché pour ces téléchargements. Les transferts de dossiers et fichiers réellement gérés par WoS conservent leur file et leurs actions dans « Mes téléchargements » ;
- l'annulation d'un torrent conserve le modèle V2 : désabonnement d'un utilisateur lorsqu'il reste d'autres droits actifs, puis lifecycle de purge seulement lorsqu'il ne reste plus de demande active ;
- V2-32D reste bloquée : ne pas prétendre supprimer précisément les statistiques NewGreedy lors d'une dernière annulation tant que NewGreedy n'offre pas le contrat full-hash requis.

Principes de récupération locale :

- la File System Access API est le chemin direct privilégié lorsqu'elle est disponible ; le ZIP
  reste une solution de repli indépendante et ne doit jamais masquer ce chemin ;
- les flux HTTP sont bornés par utilisateur et globalement ; un administrateur n'est pas soumis au
  plafond individuel mais reste soumis au plafond global de protection du serveur ;
- les ZIP complets et de dossiers partagent la même file FIFO globale ;
- le scheduler conserve une cadence de synchronisation rapide, mais ses décisions de rotation qB
  utilisent un quantum séparé afin de préserver l'équité sans provoquer de churn excessif.

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

## Redémarrage de l’API et reprises READY

L’image démarre via `python -m app.server` : SIGTERM/SIGINT active le drain, les nouveaux appels
reçoivent 503/Retry-After, Uvicorn accorde cinq secondes aux requêtes en cours puis les annule.
Le lifespan attend leur nettoyage jusqu’à deux secondes avant de fermer Redis/SQL, dans le
budget Docker API inchangé. Les transferts fichiers/dossiers gérés par WoS réessaient réseau,
5xx et EOF prématuré avec Range et snapshot constant, offset relu sur disque, huit retries
bornés et timers annulables. Les erreurs de disque/droits/snapshot restent terminales.
Les transferts natifs et ZIP gardent leurs contrats distincts ; aucune restauration de
contrôleur après refresh n’est introduite. Voir `docs/download-deploy-resume.md`.

## Politique de version post-audit

Chaque mise à jour livrée incrémente la version de `0.0.1` ; les commits correctifs d’une même livraison ne créent pas de version supplémentaire. La version `3.0.0` sera publiée après réalisation et validation de l’ensemble des améliorations de l’audit.

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

### Modèle de stockage moderne

Le runtime moderne n'utilise plus de filesystem métier personnel `/data/<username>`.

- `SharedContentStore` est l'autorité filesystem du contenu torrent ;
- le contenu physique reste sous `/data/content/<storage-key>` ;
- `ManagedTorrent` représente une copie physique partagée ;
- `TorrentFile` représente son manifeste ;
- `TorrentRequest` représente le droit/abonnement d'un utilisateur ;
- plusieurs utilisateurs peuvent partager le même `ManagedTorrent` sans duplication physique ;
- la suppression d'un droit ou d'un compte ne supprime pas la copie physique tant qu'une autre demande active existe ;
- la dernière référence passe par le lifecycle de purge normal ;
- `GET /api/v2/storage` expose au Dashboard la capacité disque partagée sans réintroduire un navigateur de fichiers ;
- les primitives HTTP Range/stream utilisées par READY sont indépendantes de l'ancien filesystem utilisateur.

Ne pas renommer `content/<storage-key>` ni migrer physiquement le stockage dans une PR applicative ordinaire. Un changement de disposition disque demande une opération OPS dédiée et un rollback explicite.

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

- Toutes les routes torrent/READY/stockage utilisateur exigent un utilisateur authentifié.
- Les routes d'administration exigent le rôle administrateur.
- Le serveur résout l'utilisateur ; ne jamais faire confiance à un username envoyé par le client.
- Les droits sur le contenu torrent sont portés par `TorrentRequest`, pas par un chemin de workspace fourni par le client.
- Le client ne peut jamais choisir un chemin hôte ou un save path qBittorrent.
- Ne jamais révéler chemins hôte, secrets, passkeys ou données d'un autre utilisateur dans une réponse API.

Les opérations Argon2 des chemins asynchrones passent par un pool de threads dédié au processus API : deux calculs simultanés, dix opérations en cours ou en attente au total, délai de réponse maximal de cinq secondes. Une annulation ou un timeout ne libère le budget d’un calcul déjà exécuté qu’à sa fin réelle ; les opérations abandonnées encore en file conservent leur place jusqu’à leur retrait par un worker, qui saute alors le calcul. Cela borne aussi la file interne de l’exécuteur en cas d’annulations répétées. La surcharge renvoie une erreur générique `503` avec `Retry-After`, sans affaiblir les hashes existants. Le pool est fermé proprement au shutdown. Connexion et changements d’identifiants lisent un snapshot, libèrent la transaction SQL avant toute vérification ou attente crypto, puis reprennent les verrous et revalident le hash et l’état du compte avant écriture. Le provisioning prépare le hash avant ses verrous de quota/idempotence ; les replays API externes ne recalculent pas de mot de passe.

La connexion consomme un budget agrégé par IP avant tout calcul : trente tentatives, réussies ou non, par fenêtre fixe de soixante secondes par défaut (`WOS_AUTH_IP_MAX_ATTEMPTS`, `WOS_AUTH_IP_WINDOW_SECONDS`). Le cache est limité à 10 000 IP hachées ; les entrées expirent et la saturation refuse de nouveaux budgets sans évincer les budgets actifs. Ce budget complète le verrouillage persistant IP/utilisateur existant. Il est non durable et se réinitialise au redémarrage. L’adresse client vient exclusivement de `request.client`, résolue par Uvicorn depuis l’ingress de confiance configuré ; aucun header transmis directement par le client n’est lu par la route.

## Préférences d’interface

- World of Seeds utilise un thème unique Forest / Green. Le rendu ne dépend ni du thème du système d'exploitation, ni du compte utilisateur. Aucune préférence de thème n'est stockée ; la migration `20260925_34` supprime l'ancien champ sans intervention sur les comptes existants.
- La préférence de langue FR/EN reste persistée et modifiable. La prise en charge de `prefers-reduced-motion` reste active pour limiter les animations ; seule la sélection d'une palette visuelle est supprimée.

## Invariants filesystem et téléchargement

- Aucun chemin hôte n'est accepté depuis le client.
- Les chemins de manifeste torrent sont relatifs au contenu géré et doivent rester bornés par les validations serveur.
- Refuser chemins absolus, `..`, évasions de racine et traversées de symlinks lors de toute résolution filesystem.
- Les ouvertures sensibles utilisent des résolutions sûres/descripteurs et `O_NOFOLLOW` lorsque prévu par les primitives de téléchargement.
- Ne jamais résoudre un problème de permissions avec `chmod 777`.
- Les téléchargements READY privilégient la File System Access API pour reconstruire localement les dossiers ; le ZIP reste un fallback de compatibilité et ne doit pas être présenté en parallèle lorsque `showDirectoryPicker()` est disponible.
- La file locale alterne les travaux concurrents. Dans un dossier, elle favorise les petits fichiers par salves bornées puis force le plus ancien afin d'améliorer le temps de complétion sans affamer les gros fichiers.
- Les téléchargements READY conservent Range, leases et validation du manifeste. L’ordonnanceur asynchrone de l’unique processus API démarre à cinq flux rapides puis ajuste la cible (5 à 64) toutes les 15 secondes selon le débit sortant total du serveur mesuré par Prometheus et la capacité montante configurée (125 Mo/s sur Rise2). Il ouvre davantage de flux sous 80 % de charge si des fichiers attendent ; au-delà de 95 %, il réduit les admissions futures sans rétrograder un transfert déjà rapide, qui conserve sa voie jusqu’à sa fin ou sa déconnexion. Les autres flux avancent à 1 Kio/s ; chaque compte peut ouvrir au plus deux attentes. La priorité à chaque place libérée combine ancienneté et bonus de taille restante plafonné à cinq minutes, ce qui évite de repousser indéfiniment les gros fichiers ; une nouvelle requête ne dépasse pas les attentes existantes. Les ZIP ont leurs limites distinctes. Sans télémétrie fraîche, la dernière décision est conservée. Le plafond global HTTP configuré, s’il existe, est appliqué aux octets réellement transmis sans réserver une fraction fixe aux clients lents.
- Le plafond de flux simultanés par utilisateur s'applique aux comptes standards. Les administrateurs en sont exemptés, mais conservent une lease par flux et restent soumis aux rate limits et protections globales.
- Les noms longs et chemins imbriqués ne doivent pas provoquer de débordement horizontal mobile.

## Torrent et sécurité tracker

- Parser le bencode strictement.
- Aucune whitelist d'extensions ne s'applique aux fichiers contenus dans un torrent : tout fichier ordinaire est accepté, avec ou sans extension (y compris `.exe`, `.dll`, `.bin`, `.pak` et `README`). Le suffixe du nom ne doit pas être confondu avec l'attribut torrent exécutable `x`, qui reste interdit.
- La sécurité de l'ingestion repose sur les chemins et leurs collisions (y compris Unicode et casse), les attributs des fichiers (symlink, exécutable et attributs inconnus interdits), les métadonnées torrent, les trackers et les limites applicatives (taille, fichiers, profondeur et upload).
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

## Abonnements READY et rétention physique

Chaque `TorrentRequest` READY est un abonnement utilisateur distinct. Son délai commence à
`ready_at` et se termine à `unsubscribe_at`, selon `WOS_TORRENT_AUTO_UNSUBSCRIBE_HOURS`.
À cette échéance, seul cet abonnement expire et disparaît du listing actif de son propriétaire.

Le `ManagedTorrent` reste le contenu physique partagé. Tant qu'un autre abonnement actif existe,
il reste disponible sans purge. Après le dernier désabonnement, la grâce physique configurée par
`WOS_TORRENT_RETENTION_HOURS` commence : la purge est planifiée et idempotente, mais le contenu
n'est pas supprimé immédiatement. Les leases déjà engagés peuvent terminer ; aucun nouveau droit
expiré n'est accordé.

## Temps réel et transferts navigateur

L'interface torrent charge un état PostgreSQL autoritaire puis reçoit des événements WebSocket non autoritaires.

- pas de polling complet toutes les dix secondes ;
- après reconnexion, faire une resynchronisation GET autoritaire ;
- un WebSocket idle ne doit pas maintenir de session SQL.

Les téléchargements récursifs utilisent un manifeste paginé/progressif et une concurrence bornée. Ne pas attendre un manifeste énorme complet avant de démarrer les premiers fichiers.

La taille des lots `.torrent` reste limitée côté interface pour les comptes standards. Un administrateur peut déposer un lot de taille quelconque, mais le pipeline conserve une concurrence technique d'upload bornée afin de ne pas ouvrir une requête par fichier simultanément.

Conserver ces mécanismes dans le Dashboard/les accordéons au lieu de créer un second système de suivi parallèle.

## Observabilité

Prometheus/Grafana surveillent application, jobs, scheduler, DB, Redis, qB, stockage et hôte.

Les métriques doivent rester sans secrets et à cardinalité bornée.

Les exporters internes ne doivent pas publier de nouveaux ports hôte sans décision explicite.

Les métriques HTTP agrégées sont exposées sur `/api/v2/metrics` : voies et admissions, pics, ancienneté d’attente, délai de voie rapide/premier corps ASGI, octets acceptés par ASGI, issues de réponse et fraîcheur de la mesure réseau. Aucun label utilisateur/fichier/lease. Les compteurs décrivent l’envoi serveur, pas l’enregistrement sur le disque du client. Les heartbeats des fichiers READY utilisent une tâche indépendante et de courtes sessions SQL pour survivre à la contre-pression réseau ; une perte de lease termine le flux.

La campagne reproductible et ses limites sont documentées dans `docs/http-download-load.md`. Le résultat local à 50 clients ne valide pas la capacité Rise2 avec PostgreSQL, disque réel et qB actif ; cette validation opérateur reste requise.

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

## Sauvegarde PostgreSQL quotidienne (2.3.6)

`scripts/rise2_v2_database_backup.py` sauvegarde la base en ligne et restaure chaque dump dans
un PostgreSQL jetable sans réseau avant chiffrement age. Le helper explicite installe le timer
quotidien uniquement après première réussite. Le déploiement app ne l'active pas sur Rise2.
Destination locale `/var/backups/world-of-seeds-v2/postgres`, 14 jours + huit hebdomadaires,
clé locale privée si aucune clé existante, métriques textfile et alertes Prometheus.
Copie hors hôte, clé hors hôte, activation et réception d'alertes nécessitent une preuve opérateur.
Ne jamais annoncer des sauvegardes production actives sur la seule base de la CI.
Procédure : `docs/database-backups-rise2.md`. La reprise complète des films/configs/qB/NG reste
celle de `docs/backup-restore-rise2-v2.md`.

## Supervision HTTP READY (2.3.7)

Grafana `wos-v2-http-downloads` consomme les métriques agrégées existantes. Aucun Dashboard
utilisateur ni règle d’admission n’est modifié. Les alertes HTTP distinguent annulations/erreurs,
restent par cible API et ne déclenchent jamais sur la seule contre-pression d’un client lent.
L’indicateur `telemetry_configured` évite les fausses alertes lorsque la collecte est désactivée
volontairement (profil local sans URL Prometheus), sans exporter la configuration privée.
La CI teste leurs conditions via promtool et chaque requête du tableau via le smoke Prometheus.
Thomas reporte l’activation des sauvegardes/notifications ; ne pas annoncer ces services actifs.
Le chargement des nouvelles règles Prometheus exige une preuve OPS (bind de fichier/inode),
procédure dans `docs/http-download-supervision.md`.

## Collecte réseau mutualisée (2.3.8)

Le graphique réseau du Dashboard et `monitor_http_upload` partagent un
`NetworkThroughputCollector` par processus API. Une collecte bornée (deux
`query_range`) et une connexion HTTP réutilisable remplacent les clients
par requête. Cache serveur de quinze secondes au maximum, réduit à la
fraîcheur restante des échantillons ; aucune prolongation artificielle de
la télémétrie transmise au scheduler. Les échecs sont temporisés cinq secondes
sans servir les anciennes valeurs. Verrou asynchrone, annulation libérant
le verrou et client fermé après l'arrêt du monitor au shutdown.

Seules les mesures réseau agrégées et immuables sont partagées.
L'authentification reste obligatoire, les réponses HTTP restent `no-store`,
et les torrents/films et compteurs propres aux utilisateurs ne sont jamais
mis en cache commun. Le cache SQL des scrapes `/metrics` existant de quinze
secondes reste distinct. Les règles de slots et la cadence du monitor sont
conservées. Cache local au processus : plusieurs workers collectent chacun
leurs mesures ; aucun cache Redis durable ni migration de BDD.

## Rafraîchissements frontend (2.3.9)

`useAsyncRefresh` centralise les lectures du Dashboard (réseau, activité,
stockage), de l'état des services administratifs, du monitoring corrélé
qB/NewGreedy et des lectures configuration/aperçu/redémarrage NewGreedy.
Une seule lecture est en cours par source/composant ; les timers sautent
les ticks occupés sans annuler la requête lente. Les demandes manuelles
pendant une lecture sont regroupées en une relance. Les cadences existantes
restent inchangées. Cleanup, changement de source et suspension annulent
le signal et invalident les callbacks, même si le transport ignore abort.
Les callbacks courants n'entraînent pas de nouvelles lectures au rerender
ou au changement de langue. Isolation locale au composant ; aucun cache
commun de données utilisateur, aucune persistance de téléchargements natifs.

Les GET NewGreedy concernés sont suspendus pendant les mutations de
redémarrage/remise à zéro correspondantes et relus à leur fin ; une réponse
antérieure ne peut pas écraser le résultat de l'action. Le client API
transmet les signaux des lectures aux fetch existants. Les actions explicites
conservent authentification/CSRF/confirmation ; aucun redémarrage automatique.
Le suivi WebSocket/pagination/manifeste de Mes téléchargements et les
transferts binaires gardent leurs coordinations distinctes.

## Autorisation de provisioning externe (2.3.10)

`POST /api/external/v1/users` conserve une copie immuable du client ID et
de l'empreinte de clé authentifiés. Après le calcul du mot de passe hors
transaction et l'acquisition du mutex de quota, il relit la ligne client
avec `FOR UPDATE`/`populate_existing`, vérifie empreinte, activation,
révocation et scope `users:create`, puis conserve ce verrou jusqu'au commit
du compte/audits/idempotence. Une révocation validée avant cette acquisition
interdit la création ; une création ayant acquis le verrou finit avant que
la révocation puisse être confirmée. Aucun verrou SQL pendant le calcul
crypto. Le replay rapide revalide également l'autorisation, sans calcul
crypto ni mutation de quota. Les refus libèrent la transaction et gardent
les contrats d'erreur 401/403 existants.

Ordre des verrous pour une création nouvelle : mutex quota, ligne client,
lecture idempotence/quota. Le replay rapide ne prend pas le mutex quota.
La révocation administrative ne prend que la ligne client ; pas d'inversion
quota/client. Tests PostgreSQL avec sessions indépendantes pour les deux
ordres création/révocation, en plus des régressions de cache ORM périmé.
Pas de nouveau scope, route, champ Discord, migration ou bot activé ;
le périmètre reste l'API externe existante et sa sûreté transactionnelle.

## Reprise de la supervision périodique — 2.3.11

`V2IntegrationObservabilityPublisher.run` absorbe les erreurs SQLAlchemy et OSError (y compris connexion socket/DNS/timeout brute asyncpg) au niveau du cycle, après rollback/fermeture des contextes de session, et attend la cadence configurée avant de refaire un cycle complet. Cela empêche une panne PostgreSQL de la supervision d'annuler le scheduler voisin dans le TaskGroup de `scheduler_service`. Le code de journal constant est `integration_observability_database_unavailable`, sans exception SQL ni secrets. Les observations existantes conservent leur validité originale ; les sets incomplets/périmés restent indisponibles. Les erreurs inattendues et CancelledError remontent ; request_stop réveille l'attente de reprise. Les garanties de slots et d'équité des téléchargements ne changent pas.

## Claims de jobs et fencing de tentative — 2.3.12

Les transitions d'un job RUNNING exigent un propriétaire et des échéances de claim/exécution strictement futures, comparées en UTC. Un worker retardé laisse le claim expiré à `recover_expired_torrent_jobs`, même si la récupération n'est pas encore passée. Chaque heartbeat et finalisation verrouille/recharge le job et compare également `attempt_count` au snapshot immuable : une tentative obsolète ne peut pas renouveler/finaliser le claim d'une nouvelle tentative du même worker. Une finalisation ayant perdu le claim sort sans écrire de transition ni d'état torrent, avec `torrent_worker_claim_lost`. L'heure de finalisation d'échec est lue après le verrou. Les effets externes déjà exécutés ne peuvent pas être défaits par ce fencing ; les règles d'idempotence/réconciliation existantes restent nécessaires.

La récupération de claims expirés honore `cancel_requested_at` avant le retry ou l'épuisement des tentatives : le job devient CANCELLED et ne peut pas être réclamé/réexécuté.

## Pannes de connexion du worker — 2.3.13

Le polling/récupération/claim, l'enqueue sync et la rétention tolèrent les SQLAlchemyError ainsi que les OSError brutes (socket/DNS/timeout asyncpg). Codes de log constants, sans détail SQL/connexion. Démarrage : options réessayées toutes les cinq secondes ; pas de worker/effet avant chargement valide. SIGINT/SIGTERM sont installés avant la connexion initiale ; arrêt/annulation annule puis attend la lecture ; le même événement d'arrêt pilote ensuite les tâches runtime sans remplacer les handlers signal. Redis/engine ferment dans la sortie commune. Erreurs inattendues et configuration invalide ne sont pas masquées. Un heartbeat indisponible annule le handler ; une finalisation indisponible laisse le claim à la récupération SQL après expiration/backoff. Les effets externes déjà exécutés restent soumis aux règles de réconciliation/idempotence. Rétention : cadence normale d'une heure, mais retry de cinq secondes après panne SQL ; sync/worker gardent leur cadence bornée habituelle. Aucune nouvelle migration ni modification de la politique des slots HTTP.
