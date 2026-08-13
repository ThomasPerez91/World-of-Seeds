# Architecture de la V1

## Objectif et frontière de sécurité

World of Seeds est une application privée, accessible d'abord par tunnel SSH. Le conteneur applicatif ne reçoit qu'un seul montage de l'hôte :

```text
/srv/seedbox  ->  /data
```

Il ne monte ni la racine du serveur, ni `/var/run/docker.sock`, ni la configuration qBittorrent. Un utilisateur standard ne verra que sa propre racine logique. L'administrateur disposera d'une vue dédiée et explicite pour changer d'utilisateur.

L'authentification protège les données et les API. Elle ne remplace pas la restriction réseau : lors d'une future exposition HTTPS, l'ordre des défenses sera pare-feu ou liste d'adresses autorisées, TLS, authentification, limitation des tentatives, puis autorisations applicatives.

## Choix de stack

| Élément | Choix | Motif |
| --- | --- | --- |
| API | FastAPI | Typage Python, validation Pydantic, API asynchrone et tests simples. |
| Frontend | React + Vite + TypeScript | Interface riche sans coût inutile de SSR ou de serveur Next.js. |
| Données | PostgreSQL + SQLAlchemy 2 + Alembic | Comptes, sessions révocables, audit et métadonnées de corbeille. |
| Authentification | Session opaque en cookie | Révocation immédiate lors d’une suspension ou suppression d’accès, contrairement à un JWT autonome. |
| Mot de passe | Argon2id | Hash lent recommandé ; aucun mot de passe n'est stocké ou journalisé en clair. |
| Production | Une image applicative | FastAPI sert l'API et les fichiers React compilés sur une même origine. |
| Proxy | Aucun en V1 privée | Le port est lié à `127.0.0.1`. Nginx et TLS seront ajoutés avant toute exposition publique. |

## Arborescence cible du dépôt

```text
World-of-Seeds/
├── .github/workflows/       # CI maintenant, CD lors du déploiement accompagné
├── backend/
│   ├── app/
│   │   ├── api/             # Routes HTTP et dépendances d'autorisation
│   │   ├── core/            # Configuration, base de données, sécurité
│   │   ├── files/           # Résolution sûre des chemins et opérations disque
│   │   ├── integrations/    # Adaptateur qBittorrent réservé à la V2
│   │   ├── models/          # Modèles SQLAlchemy
│   │   ├── schemas/         # Contrats Pydantic
│   │   └── services/        # Cas d'usage métier
│   ├── migrations/          # Versions Alembic
│   └── tests/
├── frontend/
│   └── src/
│       ├── api/             # Client HTTP typé
│       ├── components/      # Composants accessibles et réutilisables
│       ├── features/        # auth, fichiers, corbeille, admin
│       └── styles/
├── docs/
├── compose.yaml
└── Dockerfile
```

Les dossiers vides ne sont pas créés artificiellement ; ils apparaîtront avec leur première fonctionnalité.

## Organisation des données de la seedbox

L'organisation cible est :

```text
/data/
├── admin/
│   ├── downloads/
│   └── watch/
├── utilisateur/
│   ├── downloads/
│   └── watch/
└── .trash/
    └── <user-uuid>/
        └── <trash-entry-uuid>/
```

Chaque compte conserve un UUID immuable en base, même si son nom change. Le nom de connexion est volontairement limité à une forme sûre (`A-Z`, `a-z`, `0-9`, `_`, `-`) et correspond exactement au nom du dossier. L'unicité est insensible à la casse : `Shadowsun` et `shadowsun` représentent le même identifiant. La création du compte et celle du dossier forment une seule opération métier : un échec SQL retire uniquement le dossier nouvellement créé et encore vide. Un changement de nom verrouille la ligne utilisateur, vérifie les collisions en base et sur disque, puis utilise sous Linux `renameat2(RENAME_NOREPLACE)` : une destination apparue entre la vérification et le renommage n'est jamais écrasée. L'opération échoue de manière sûre si cette primitive atomique n'est pas disponible. La base est ensuite mise à jour et un mécanisme de compensation remet le dossier à son ancien nom si la transaction SQL échoue.

Les opérations de racine utilisent des descripteurs de répertoire, les variantes `*at` des appels système et `O_NOFOLLOW`. Les chemins historiques à la racine restent intacts. La stratégie de coexistence et la migration qBittorrent différée sont détaillées dans [`storage-migration.md`](storage-migration.md).

La corbeille est indexée par UUID utilisateur afin qu'un renommage de compte ne casse pas ses éléments supprimés. La base stocke le chemin relatif d'origine, la date de suppression, l'identité filesystem et le propriétaire. Sur disque, chaque élément porte un UUID aléatoire indépendant de son nom d'origine.

> La migration des actuels `/srv/seedbox/downloads` et `/srv/seedbox/watch` ne doit pas être lancée tant que qBittorrent utilise ces chemins. Déplacer un fichier encore seedé sans prévenir qBittorrent le mettrait en erreur. Une procédure contrôlée sera réalisée avec le déploiement.

## Authentification et comptes

- aucune route d'inscription publique ;
- création initiale de l'administrateur par commande interactive dans le conteneur ;
- création d'un compte uniquement depuis l'administration ;
- identifiants aléatoires affichés une seule fois ;
- changement obligatoire du nom et du mot de passe à la première connexion ;
- comptes permanents sans durée de validité ;
- suspension réversible et révocation immédiate de toutes les sessions ;
- suppression logique de l’accès sans suppression automatique du dossier utilisateur ;
- cookie de session `HttpOnly`, `SameSite=Strict`, `Secure` dès HTTPS ;
- jeton CSRF pour chaque requête qui modifie des données ;
- réponses de connexion génériques et limitation des tentatives ;
- documentation OpenAPI désactivée en production.

Commande à exécuter après application des migrations :

```bash
docker compose exec app python -m app.cli create-admin --username admin
```

Le mot de passe est saisi sans écho dans le terminal. Il ne transite pas par les variables d'environnement ou les logs Docker.

La page de connexion sera la seule vue accessible anonymement. Cela limite l'exposition fonctionnelle, mais le caractère privé repose réellement sur la restriction réseau et les contrôles serveur, pas sur le fait de cacher du JavaScript au navigateur.

## Accès sûr aux fichiers

Toutes les API utilisent des chemins relatifs à une racine déjà autorisée. La chaîne `../../etc/passwd`, un chemin absolu, un octet nul ou un composant `..` est rejeté avant tout accès.

Une simple comparaison de chaînes ou un unique `resolve()` n'est pas suffisant face aux changements concurrents. Le gestionnaire ouvre chaque composant demandé depuis un descripteur de la racine avec les primitives Linux `*at` et `O_NOFOLLOW`. Les liens symboliques sont affichés comme bloqués et ne peuvent pas être suivis, parcourus, renommés ou déplacés. Les mutations travaillent à partir des descripteurs des dossiers parents.

La liste expose le nom, le type, la taille des fichiers, la date de modification et le type MIME estimé. La taille d'un dossier n'est pas calculée récursivement : cette opération serait coûteuse et pourrait ralentir le serveur sur plusieurs dizaines de gigaoctets. L'utilisation affichée correspond au système de fichiers qui porte `/data`. Une réponse est plafonnée à 5 000 éléments afin qu'un dossier anormalement volumineux ne sature pas la mémoire de l'application.

Les opérations prévues sont :

- liste et statistiques avec `lstat` ;
- renommage et déplacement atomique sur le même montage ;
- mise à la corbeille par renommage atomique ;
- restauration avec détection de collision ;
- suppression définitive sans jamais suivre de lien symbolique.

Le renommage et le déplacement utilisent `renameat2(RENAME_NOREPLACE)` entre les
descripteurs des dossiers source et destination. Ils sont donc instantanés même pour un
fichier de plusieurs dizaines de gigaoctets, à condition que les deux dossiers soient sur
le même système de fichiers, et ne remplacent jamais une destination existante. L'identité
de l'élément est contrôlée après l'appel atomique ; une substitution concurrente est remise
à sa place sans suivre son éventuelle cible. Les racines obligatoires `downloads` et `watch`
ne peuvent pas être renommées ou déplacées, et un dossier ne peut pas être déplacé dans
lui-même ou l'un de ses descendants.

Les routes `PATCH /api/v1/files/rename` et `POST /api/v1/files/move` exigent une session
valide, des identifiants définitifs et le jeton CSRF associé. L'interface demande une
confirmation, signale les collisions et rappelle qu'une mutation manuelle peut désynchroniser
un torrent encore suivi par qBittorrent.

La mise en corbeille utilise elle aussi `renameat2(RENAME_NOREPLACE)` : déplacer un fichier
de plusieurs dizaines de gigaoctets ne le copie pas et ne le charge pas en mémoire. La
métadonnée PostgreSQL et le déplacement filesystem sont coordonnés par compensation. Si
l’écriture SQL échoue, l’élément reprend sa place d’origine ; si la suppression SQL d’une
restauration échoue, il retourne dans la corbeille. Les valeurs nécessaires à ces retours
arrière sont copiées avant tout rollback afin de ne jamais dépendre d’un objet ORM expiré.

La restauration refuse d’écraser une destination et continue de fonctionner après un
renommage du compte. La purge récursive ne suit aucun lien symbolique, contrôle l’identité
du dossier à chaque niveau, refuse de franchir une frontière de système de fichiers et
plafonne la profondeur. Elle supprime d’abord le contenu puis la métadonnée ; une panne SQL
après la suppression produit donc une entrée fantôme qu’un nouvel appel peut nettoyer sans
danger. Les routes `GET/POST /api/v1/trash`, `POST /api/v1/trash/{id}/restore` et
`DELETE /api/v1/trash/{id}` sont isolées par utilisateur, et toutes les mutations exigent
le jeton CSRF de la session.

## Téléchargements volumineux

Le serveur n'appelle jamais `read()` sans limite sur un fichier. Le fichier est ouvert depuis le descripteur de son dossier parent avec `O_NOFOLLOW`, puis envoyé par blocs d'au plus 1 Mio depuis un thread de travail. Le descripteur reste attaché au même fichier jusqu'à la fin du transfert et est fermé même si le client interrompt la connexion, y compris si l'envoi échoue avant le premier en-tête ou le premier bloc. La réponse fournit `Content-Length`, `Last-Modified`, `ETag` et `Accept-Ranges: bytes`.

Les requêtes avec une plage `bytes` unique et valide reçoivent `206 Partial Content`; une plage invalide, multiple ou impossible reçoit `416 Range Not Satisfiable`. `If-Range` compare l'ETag ou la date de modification avant une reprise. Les plages fermées, ouvertes, suffixées et invalides, les fichiers vides, les interruptions et un fichier sparse de 40 Gio sont couverts par les tests. Les plages multiples ne sont pas nécessaires à la reprise et exigeraient une réponse multipart plus complexe ; elles sont donc explicitement refusées en V1.

## Interface responsive et accessible

L’interface conserve les actions essentielles sur écran étroit sans masquer la date de
modification. Les cibles tactiles critiques atteignent 44 px sur mobile, les noms longs
peuvent revenir à la ligne sans casser le tableau de bord, et les feuilles de confirmation
respectent les zones sûres des appareils mobiles. Une variante `forced-colors` et la
préférence `prefers-reduced-motion` sont prises en charge.

Un lien d’évitement mène directement au contenu du tableau de bord. Les listes, tableaux,
groupes d’actions, dates et états de chargement portent une sémantique explicite. Les
dialogues enferment le focus, se ferment avec Échap, rendent le focus au déclencheur et
désactivent toutes les sorties pendant une mutation. L’ordre visuel reste identique à
l’ordre clavier. Une panne backend au chargement est distinguée d’une session anonyme afin
de ne pas présenter à tort le formulaire de connexion.

Vitest, Testing Library et axe couvrent les parcours du dialogue, du gestionnaire de
fichiers, de la corbeille et de l’indisponibilité initiale. Le contraste, qui nécessite un
moteur de rendu réel, reste contrôlé directement dans la palette CSS ; toutes les autres
règles structurelles axe sont exécutées en CI.

## Isolation Docker

Le service applicatif est configuré avec :

- publication `127.0.0.1:18081:8000` uniquement ;
- utilisateur UID/GID non-root correspondant aux permissions de la seedbox ;
- système de fichiers racine du conteneur en lecture seule ;
- `/tmp` en `tmpfs` avec `noexec`, `nosuid` et `nodev` ;
- toutes les capabilities Linux supprimées ;
- `no-new-privileges` ;
- limite de processus ;
- contrôle de santé fondé sur la disponibilité réelle de PostgreSQL ;
- en-tête d'identification Uvicorn désactivé ;
- aucun port PostgreSQL publié ;
- aucun socket Docker monté.

## Découpage prévu des PR

1. **Fondations** : documentation, squelette exécutable, Docker et CI.
2. **Authentification** : migrations, admin initial, sessions, CSRF, comptes administrés et page de connexion.
3. **Racines utilisateurs** : création/renommage contrôlé des dossiers et migration préparatoire.
4. **Navigation** : liste, fil d'Ariane, espace disque et règles anti-traversal.
5. **Téléchargement robuste** : flux, HTTP Range, reprise et tests de charge ciblés.
6. **Audit de durcissement** : courses critiques, streaming, CSP, sessions et build reproductible.
7. **Mutations** : déplacer et renommer avec confirmations UI.
8. **Corbeille** : suppression récupérable, restauration et purge définitive.
9. **Responsive et accessibilité** : finition desktop/mobile, clavier, focus et lecteurs d'écran.
10. **Déploiement** : image GHCR, secrets, migration et GitHub Action de déploiement validée ensemble.

L'intégration qBittorrent reste hors V1, mais son futur client vivra derrière une interface dans `backend/app/integrations/qbittorrent`.
