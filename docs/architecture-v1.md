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
| Authentification | Session opaque en cookie | Révocation immédiate et meilleure adaptation aux comptes temporaires qu'un JWT autonome. |
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
├── users/
│   ├── admin/
│   │   ├── downloads/
│   │   └── watch/
│   └── utilisateur/
│       ├── downloads/
│       └── watch/
└── .trash/
    └── <user-uuid>/
        └── <trash-entry-uuid>/
```

Chaque compte conserve un UUID immuable en base, même si son nom change. Le nom de connexion est volontairement limité à une forme sûre (`a-z`, `0-9`, `_`, `-`) et correspond au nom du dossier. La création du compte et celle du dossier forment une seule opération métier : un échec SQL retire uniquement le dossier nouvellement créé et encore vide. Un changement de nom verrouille la ligne utilisateur, vérifie les collisions, puis utilise sous Linux `renameat2(RENAME_NOREPLACE)` : une destination apparue entre la vérification et le renommage n'est jamais écrasée. L'opération échoue de manière sûre si cette primitive atomique n'est pas disponible. La base est ensuite mise à jour et un mécanisme de compensation remet le dossier à son ancien nom si la transaction SQL échoue.

Les opérations de racine utilisent des descripteurs de répertoire, les variantes `*at` des appels système et `O_NOFOLLOW`. Les chemins historiques à la racine restent intacts. La stratégie de coexistence et la migration qBittorrent différée sont détaillées dans [`storage-migration.md`](storage-migration.md).

La corbeille est indexée par UUID utilisateur afin qu'un renommage de compte ne casse pas ses éléments supprimés. La base stockera le chemin relatif d'origine, la date de suppression et le propriétaire.

> La migration des actuels `/srv/seedbox/downloads` et `/srv/seedbox/watch` ne doit pas être lancée tant que qBittorrent utilise ces chemins. Déplacer un fichier encore seedé sans prévenir qBittorrent le mettrait en erreur. Une procédure contrôlée sera réalisée avec le déploiement.

## Authentification et comptes

- aucune route d'inscription publique ;
- création initiale de l'administrateur par commande interactive dans le conteneur ;
- création d'un compte temporaire uniquement depuis l'administration ;
- identifiants aléatoires affichés une seule fois ;
- changement obligatoire du nom et du mot de passe à la première connexion ;
- date d'expiration, désactivation et révocation de toutes les sessions ;
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

Une simple comparaison de chaînes ou un unique `resolve()` n'est pas suffisant face aux changements concurrents. Le gestionnaire ouvre chaque composant demandé depuis un descripteur de la racine avec les primitives Linux `*at` et `O_NOFOLLOW`. Les liens symboliques sont affichés comme bloqués et ne peuvent pas être suivis ou parcourus. Les mutations travailleront à partir des descripteurs des dossiers parents.

La liste expose le nom, le type, la taille des fichiers, la date de modification et le type MIME estimé. La taille d'un dossier n'est pas calculée récursivement : cette opération serait coûteuse et pourrait ralentir le serveur sur plusieurs dizaines de gigaoctets. L'utilisation affichée correspond au système de fichiers qui porte `/data`. Une réponse est plafonnée à 5 000 éléments afin qu'un dossier anormalement volumineux ne sature pas la mémoire de l'application.

Les opérations prévues sont :

- liste et statistiques avec `lstat` ;
- renommage et déplacement atomique sur le même montage ;
- mise à la corbeille par renommage atomique ;
- restauration avec détection de collision ;
- suppression définitive sans jamais suivre de lien symbolique.

## Téléchargements volumineux

Le serveur n'appelle jamais `read()` sans limite sur un fichier. Le fichier est ouvert depuis le descripteur de son dossier parent avec `O_NOFOLLOW`, puis envoyé par blocs d'au plus 1 Mio depuis un thread de travail. Le descripteur reste attaché au même fichier jusqu'à la fin du transfert et est fermé même si le client interrompt la connexion, y compris si l'envoi échoue avant le premier en-tête ou le premier bloc. La réponse fournit `Content-Length`, `Last-Modified`, `ETag` et `Accept-Ranges: bytes`.

Les requêtes avec une plage `bytes` unique et valide reçoivent `206 Partial Content`; une plage invalide, multiple ou impossible reçoit `416 Range Not Satisfiable`. `If-Range` compare l'ETag ou la date de modification avant une reprise. Les plages fermées, ouvertes, suffixées et invalides, les fichiers vides, les interruptions et un fichier sparse de 40 Gio sont couverts par les tests. Les plages multiples ne sont pas nécessaires à la reprise et exigeraient une réponse multipart plus complexe ; elles sont donc explicitement refusées en V1.

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
2. **Authentification** : migrations, admin initial, sessions, CSRF, comptes temporaires et page de connexion.
3. **Racines utilisateurs** : création/renommage contrôlé des dossiers et migration préparatoire.
4. **Navigation** : liste, fil d'Ariane, espace disque et règles anti-traversal.
5. **Téléchargement robuste** : flux, HTTP Range, reprise et tests de charge ciblés.
6. **Mutations** : déplacer et renommer avec confirmations UI.
7. **Corbeille** : suppression récupérable, restauration et purge définitive.
8. **Responsive et accessibilité** : finition desktop/mobile, clavier, focus et lecteurs d'écran.
9. **Déploiement** : image GHCR, secrets, migration et GitHub Action de déploiement validée ensemble.

L'intégration qBittorrent reste hors V1, mais son futur client vivra derrière une interface dans `backend/app/integrations/qbittorrent`.
