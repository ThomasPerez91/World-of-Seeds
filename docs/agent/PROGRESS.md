# World of Seeds — Progress

## Etat courant — 25 septembre 2026

World of Seeds V2 est désormais la ligne de production active.

- Version applicative cible : `2.3.2`.

## Release 2.3.2 — retrait du résumé de récupération native

- Supprime le bloc « Récupération locale » du Dashboard et le suivi persistant des téléchargements natifs du navigateur, y compris la restauration après refresh.
- Les liens natifs READY et ZIP déclenchent toujours le navigateur sans fabriquer de statut ; la file réelle du `BrowserDownloadManager` reste visible uniquement dans « Mes téléchargements » pour les transferts gérés.
- Nettoie callbacks, styles, traductions et tests associés à l'ancien résumé. Aucun historique artificiel n'est restauré depuis le navigateur.

## Release 2.3.1 — thème Forest / Green unique

- Le mode clair et la préférence système sont retirés. Les tokens sombres actuels deviennent la palette canonique ; aucune préférence d'OS ni ancienne valeur de stockage local ne peut sélectionner une autre palette.
- Suppression du bootstrap, du provider, du sélecteur, de l'API et des variantes CSS de thème clair ; les contrôles natifs utilisent un `color-scheme: dark` statique.
- Migration `20260925_34` : suppression de la contrainte et de la colonne de thème des comptes existants ; langue, session et `prefers-reduced-motion` inchangés.

## Release 2.3.0 — fichiers ordinaires dans les torrents

- Supprime la whitelist des extensions des fichiers contenus dans les `.torrent` : les jeux Windows, fichiers sans extension et formats inconnus suivent la même validation que les médias.
- Conserve les rejets liés aux métadonnées malformées, chemins dangereux ou ambigus, symlinks, attributs exécutables `x` et inconnus, trackers et limites de taille/nombre ; le contrôle de l'extension du fichier `.torrent` déposé reste applicable.
- Supprime le code d'erreur obsolète lié aux extensions et ses traductions ; couvre les téléchargements unitaires et les envois successifs du batch par tests d'API.

## Release 2.2.15 — cohérence desktop et mobile

- Fermeture de deux media queries dans `styles.css` qui absorbaient les styles suivants lors de la compilation ; test de parsing strict sur toutes les feuilles CSS.
- Tableaux administratifs à colonnes fixes et noms tronqués avec les tooltips existants ; lignes utilisateurs restaurées en véritables lignes de tableau sur desktop.
- Composition mobile unifiée, tri sous forme de boutons qui reviennent à la ligne, surfaces forêt et cases à cocher émeraude, bouton Enregistrer compact.
- Cards de téléchargements réagencées, arrondis du fond sélectionné, espacement des détails et suppression des largeurs minimales de l’aperçu mobile.
- Validation : build et tests frontend ; inspection visuelle locale indisponible (accès localhost bloqué par le navigateur de validation).

## Release 2.2.14 — récupération locale priorisée et File API explicite

- la File System Access API est l'action principale des contenus multi-fichiers et mémorise une
  destination WoS dans le sélecteur natif ; le ZIP n'est plus affiché en parallèle lorsqu'elle est
  disponible ;
- le diagnostic distingue contexte non sécurisé, API absente et refus d'accès au dossier, avec un
  message exploitable notamment sous Brave ;
- les petits fichiers locaux sont servis en priorité par salves bornées, puis le fichier le plus
  ancien est forcé afin qu'un gros fichier ne soit jamais affamé ;
- les permis de flux du navigateur alternent entre travaux concurrents au lieu de laisser un seul
  dossier monopoliser la file locale ;
- les protections Range, snapshots, leases, limites serveur et reprise restent inchangées.

## Release 2.2.13 — concurrence qBittorrent dynamique

- le scheduler mesure le débit réellement observé sur une fenêtre durable et augmente par paliers
  le nombre de torrents actifs lorsque le débit reste sous la cible et qu'une file attend ;
- le plafond dur, les limites par utilisateur, le scheduler pondéré par taille et les protections
  anti-famine restent autoritaires ;
- une fenêtre de cooldown après achèvement évite de perturber l'ensemble actif pendant la nouvelle
  mesure.

## Release 2.2.6 — supervision qBittorrent et NewGreedy

- deux vues dédiées ajoutées à l’administration pour superviser qBittorrent et NewGreedy ;
- inventaire qBittorrent global en lecture seule avec nom, taille, statut, progression et débits
  download/upload agrégés ;
- statistiques NewGreedy exposées avec hash, nom qBittorrent corrélé, statut, DL, UL et ratio ;
- noms longs tronqués avec tooltip et tableaux responsives alignés sur le design du dashboard ;
- rafraîchissement exclusivement manuel, sans polling automatique sur ces deux écrans ;
- endpoints V2 réservés aux administrateurs, bornés et sans exposition des secrets d’intégration.
- API de production raccordée au registre privé en lecture seule et au réseau interne `torrent`
  afin que les vues qBittorrent/NewGreedy puissent joindre leurs services sans secret inline.

## Release 2.2.5 — équité et fiabilité des transferts

- cadence de décision du scheduler qBittorrent séparée de la synchronisation, avec un quantum de
  contrôle configurable à 120 secondes par défaut pour éviter les rotations toutes les 5 secondes ;
- jobs durables ordonnés par priorité explicite : purge/récupération, ajout, jobs inconnus, puis
  synchronisation, sans perdre l'ordre FIFO à priorité égale ;
- `WOS_WORKER_CONCURRENCY` réellement appliquée au démarrage des workers ;
- limite globale durable des flux HTTP en plus de la limite par utilisateur, y compris pour les
  administrateurs ;
- une file FIFO commune et configurable pour les ZIP complets et les ZIP de dossiers ;
- erreurs de saturation et d'indisponibilité distinguées dans le Download Manager ;
- téléchargement direct vers un dossier proposé avec la File System Access API même lorsqu'un ZIP
  de secours est disponible, notamment sous Brave ;
- parcours des gros manifests effectué en flux pour l'arborescence et limité au sous-arbre demandé
  pour la création d'un ZIP de dossier.

## Release 2.2.3 — quota, auth seeds et API externe

- quota global dynamique `WOS_MAX_USER_ACCOUNTS`, appliqué par un provisioning transactionnel
  sérialisé sous PostgreSQL ;
- seed base62 de 25 caractères pour chaque compte, backfill migratoire et consultation propriétaire
  non cacheable ;
- API tierce stable `/api/external/v1` avec clients hashés, scopes, idempotence, rate limiting,
  création d’utilisateur et téléchargements isolés par seed ;
- gestion des clients API et du quota dans l’administration, seed masquée dans les paramètres du
  compte ;
- contrat documenté dans `docs/external-api-v1.md`.
- Production : Rise2.
- Branche de production : `master`.
- Branche d'intégration : `develop`.
- Le dernier `develop` doit toujours être vérifié par `fetch` avant de créer une nouvelle branche.
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

Le stockage média est un bind mount hôte partagé, hors conteneurs :

- hôte : `/srv/world-of-seeds-v2/data` ;
- conteneurs WOS/qB : `/data` ;
- contenu torrent moderne : `/data/content/<storage-key>` via `SharedContentStore`.

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

La refonte de l'expérience utilisateur autour d'un Dashboard **torrent-centric** est intégrée dans `develop` (UX-00 à UX-06 terminées).

Décisions validées :

- la page d'accueil utilisateur est le Dashboard de suivi des torrents ;
- l'ancien espace utilisateur Fichiers/Corbeille et le filesystem métier personnel sont retirés ;
- le stockage physique torrent est partagé et reste géré par `SharedContentStore` ;
- `ManagedTorrent` porte la copie physique, `TorrentFile` le manifeste et `TorrentRequest` le droit/abonnement utilisateur ;
- le Dashboard présente synthèses torrents, récupération locale et capacité du stockage partagé ;
- le gestionnaire de torrents adopte une présentation en accordéons ;
- l'ajout `.torrent`, la progression, les états de queue, le WebSocket, l'annulation/désabonnement, la rétention et le manifeste READY existants sont réutilisés ;
- la récupération affichée reste celle du contrôleur navigateur local, pas une file globale autoritaire multi-appareils ;
- le frontend ne contacte jamais qBittorrent ou NewGreedy directement ;
- le thème Forest / Green unique remplace les anciens choix visuels depuis 2.3.1 ; la langue FR/EN reste conservée ;
- le mobile-first et le responsive restent un critère de Definition of Done de chaque PR UX.

### Découpage des tâches

- **UX-00 — TERMINE** : planification documentaire de la refonte.
- **UX-01 — TERMINE** : design system, préférence de langue, login/settings/shell ; les anciens thèmes sont retirés depuis 2.3.1.
- **UX-02 — TERMINE** : nouveau Dashboard et ses cartouches.
- **UX-03 — TERMINE** : gestionnaire de torrents en accordéons.
- **UX-04 — TERMINE** : expérience READY et récupération locale intégrées aux accordéons.
- **UX-05 — TERMINE** : retrait de l'espace utilisateur Fichiers/Corbeille.
- **UX-05B — TERMINE** : suppression du filesystem/workspace utilisateur legacy et consolidation sur le stockage partagé torrent.
- **UX-06 — TERMINE** : harmonisation admin, responsive/accessibilité et nettoyage frontend final.

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

## UX-01 — historique du design system et des préférences (thèmes retirés en 2.3.1)

- Palettes Light/Dark à tokens partagés.
- L'ancien système de thèmes et sa migration `20260908_23` appartiennent à l'historique UX-01 ; la migration `20260925_34` l'a supprimé en 2.3.1.
- Seule la langue FR/EN reste configurable dans les préférences et le menu compte.
- Primitives natives légères Button, IconButton, Card, Badge, Progress, Accordion et StateMessage.
- Login, credentials, shell et paramètres compacts et mobile-first.

## UX-02 — Nouveau Dashboard utilisateur torrent-centric

- Trois cartouches : activité torrent, récupération locale du navigateur et stockage.
- Agrégation torrent paginée côté frontend sans nouvel endpoint décoratif.
- Erreurs et chargements isolés par cartouche.
- Dashboard comme accueil authentifié.
- Layout mobile-first.

## UX-03 — Gestionnaire de torrents en accordéons

- Liste de cartes `details/summary` avec résumé compact, progression, état, queue, rétention et actions.
- Détails limités aux données déjà exposées.
- Pagination, WebSocket/reconnect/resync, annulation/désabonnement, drag/drop et multi-upload conservés.
- Contrôles tactiles et noms longs bornés.

## UX-04 — Expérience READY et récupération locale

- Manifeste READY chargé à la demande et paginé.
- Mono-fichier en téléchargement natif ; multi-fichiers avec liens individuels et téléchargement complet.
- `RecursiveDownloadController` réutilisé pour la récupération locale.
- Pause/reprise/annulation/progression locale conservées.
- Suppression READY distincte de l'annulation d'une récupération locale.

## UX-05 — Retrait du legacy utilisateur Fichiers/Corbeille

- Le shell authentifié ne propose plus Fichiers ni Corbeille.
- Les anciens liens `?path=...` sont ignorés et nettoyés.
- Les composants frontend user files/trash et les actions de mutation associées ont été retirés.
- Cette étape avait volontairement conservé temporairement les routes/workspaces backend nécessaires à l'ancien contrat de stockage du Dashboard avant UX-05B.

## UX-05B — Suppression du filesystem utilisateur legacy et stockage partagé

- `GET /api/v1/files`, les routes de mutation fichier et les routes trash utilisateur ont été retirées du runtime moderne.
- Les services de browsing/mutation/workspace utilisateur legacy ont été supprimés ; le package `app.files` ne conserve plus que la compatibilité minimale des primitives HTTP de téléchargement READY.
- `WorkspaceManager` n'est plus requis par la création, le renommage ou la suppression de comptes.
- `TrashEntry` et la table `trash_entries` sont retirés via migration Alembic `20260909_24_drop_legacy_user_trash.py` avec downgrade couvert.
- Le Dashboard lit désormais la capacité partagée via `GET /api/v2/storage` au lieu d'utiliser le navigateur de fichiers pour obtenir deux métriques.
- `SharedContentStore` reste l'autorité filesystem du contenu torrent et conserve la disposition physique `/data/content/<storage-key>` ; aucun renommage disque n'est introduit par cette PR.
- Le lifecycle torrent et la déduplication ne changent pas : plusieurs `TorrentRequest` peuvent partager un `ManagedTorrent`; la suppression d'un compte/droit ne détruit pas la copie tant qu'une autre demande active existe, et la dernière référence passe par la purge normale.
- Les primitives HTTP Range/stream nécessaires aux téléchargements READY ont été extraites du filesystem legacy afin de conserver les comportements Range/leases/ZIP existants.
- L'administration n'expose plus de navigation ou métriques de corbeille legacy ; les reliquats frontend non accessibles peuvent être supprimés lors du nettoyage final UX-06 sans rouvrir le backend legacy.
- Les smokes/policies Rise2 ont été adaptés au stockage partagé.

Validation de la PR #157 avant finalisation documentaire :

- migrations upgrade/downgrade/upgrade : vert ;
- Ruff check + format : verts ;
- mypy app/tests : vert ;
- pytest backend : vert ;
- frontend check/tests/build : verts ;
- Dependency and image security : vert ;
- Container image + smokes V2 : vert ;
- V2 Rise2 deploy policy : vert.

## UX-06 — Harmonisation administration, responsive et finition

- Le shell administration et les vues Utilisateurs, Services, Paramètres et Stockage utilisent les primitives partagées `Button`, `Card`, `Badge`, `Progress` et `StateMessage` lorsque pertinent.
- La composition admin est isolée dans `frontend/src/features/admin/admin.css`, avec base mobile-first puis enrichissements tablette/desktop.
- La navigation admin conserve `aria-current`, des cibles tactiles d'au moins 2,75 rem et des contenus longs bornés sans overflow horizontal attendu.
- Le dialogue générique a été extrait de l'ancien namespace `features/files` vers `components/Dialog`, avec gestion du focus, fermeture clavier et tests axe conservés.
- Les derniers reliquats frontend du filesystem/trash utilisateur supprimé par UX-05B ont été retirés : module `features/files`, ancien écran admin corbeille devenu sans backend moderne, contrats API associés et route frontend morte.
- Le Dashboard, le gestionnaire torrent, READY, les récupérations locales et les contrats backend torrent restent inchangés.
- La policy responsive interdit la réintroduction du module filesystem frontend et vérifie le contrat mobile-first de l'administration.

Validation du HEAD fonctionnel UX-06 `dad0ff8a09a2029f557fd135ffd8896629e854b5` avant le commit documentaire final :

- frontend `npm run check` : vert ;
- frontend `npm run test` : vert ;
- frontend `npm run build` : vert ;
- backend Ruff / format / mypy / pytest : verts ;
- `Container image` et smokes V2 : verts ;
- `Dependency and image security` : vert ;
- `V2 Rise2 deploy policy` : vert ;
- aucune conversation de review ouverte sur la PR #158.

## Consolidation post-UX-06

Une passe de cohérence post-refonte retire les derniers contrats morts du filesystem utilisateur : namespace backend `app.files`, schémas V1 Files/Torrents non montés, client HTTP stockage dupliqué, traductions et styles de l'ancienne corbeille. La documentation publique/légale est réalignée sur le stockage partagé. La suppression d'un compte admin exige désormais une confirmation qui décrit les vrais effets sur les `TorrentRequest` et le lifecycle partagé.

Cette consolidation ne modifie ni le schéma PostgreSQL, ni `UserTorrent` historique, ni le stockage physique, ni qBittorrent/NewGreedy, ni le lifecycle torrent.

## Release 2.2.2 — récupération native et privilèges administrateur

- La File System Access API devient le mode principal pour récupérer un torrent multi-fichiers ou un sous-dossier : le manifeste READY est streamé fichier par fichier et l'arborescence est reconstruite dans la destination choisie.
- Le ZIP reste disponible uniquement comme fallback lorsque `showDirectoryPicker()` est absent et lorsque les limites d'archive existantes l'autorisent.
- Les torrents mono-fichier conservent `showSaveFilePicker()` ou le téléchargement HTTP standard selon les capacités du navigateur.
- Les comptes administrateurs n'ont plus de plafond applicatif sur la taille d'un lot `.torrent`; la concurrence du pipeline d'upload reste fixée à trois requêtes.
- La policy de récupération expose explicitement `unlimited: true` et une limite `null` pour les administrateurs. Chaque flux conserve sa lease et tous les rate limits/protections globales restent actifs.
- Les comptes standards conservent les plafonds existants de lot et de flux simultanés.

## Prochaine tâche

La séquence **UX-00 → UX-06 est terminée**. Aucun chantier UX supplémentaire n'est présumé automatiquement.

Le prochain développement doit repartir d'un besoin produit, maintenance, sécurité ou exploitation explicitement défini, depuis le `develop` courant après intégration de la PR #158. La promotion en production reste une PR séparée `develop → master`.
