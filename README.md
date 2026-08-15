# World of Seeds

Interface web privée de gestion de seedbox, conçue pour être déployée en Docker sur un serveur Ubuntu.

## État du projet

Les capacités fonctionnelles principales de la V1 sont en place :

- API FastAPI typée ;
- interface React/Vite TypeScript ;
- PostgreSQL non exposé sur l'hôte ;
- authentification par session et comptes administrés sans expiration ;
- espaces `/data/<username>/downloads` créés depuis une structure JSON versionnée ;
- renommage coordonné du compte et de son dossier avec compensation en cas d'échec SQL ;
- navigation sécurisée avec métadonnées, fil d'Ariane et espace disque ;
- téléchargement en flux avec HTTP Range, reprise, ETag et Last-Modified ;
- renommage et déplacement atomiques sans écrasement, avec confirmations dans l’interface ;
- corbeille privée par utilisateur, restauration avec détection de collision et purge définitive ;
- pages d’administration dédiées aux utilisateurs, au stockage global et au nettoyage des corbeilles ;
- image Docker unique pour l'API et le frontend ;
- montage hôte limité à `/srv/seedbox:/data` ;
- contrôles de qualité automatisés.

La V1 applicative est complète. À partir de la version `v1.1.0`, la publication d'une
release stable ciblant `master` construit une image immuable dans GHCR puis la déploie sur
OVH par une identité SSH restreinte. Le déclenchement manuel reste disponible en secours.

## Démarrage local sans Docker

Backend :

```bash
cd backend
uv sync --dev
uv run uvicorn app.main:app --reload
```

Frontend :

```bash
cd frontend
npm ci
npm run check
npm run test
npm run dev
```

Après une modification des dépendances Python, le verrou destiné à l'image Docker se
régénère depuis `uv.lock` :

```bash
cd backend
uv lock
uv export --frozen --no-dev --no-emit-project --no-header \
  --format requirements.txt --output-file requirements.lock
```

La CI refuse une divergence entre ces deux fichiers. L'image installe uniquement les
versions et empreintes cryptographiques ainsi exportées.

## Déploiement Docker

Le déploiement ne doit pas être lancé avant d'avoir configuré `.env` et préparé les permissions de `/srv/seedbox` :

```bash
cp .env.example .env
docker compose config
docker compose build
docker compose up -d postgres
docker compose run --rm app alembic -c backend/alembic.ini upgrade head
docker compose run --rm app python -m app.cli migrate-workspaces
docker compose up -d app
docker compose exec app python -m app.cli create-admin --username admin
```

L'application écoute uniquement sur `127.0.0.1:18081`. Depuis un Mac :

```bash
ssh -N -L 18081:127.0.0.1:18081 ovh
```

Puis ouvrir <http://127.0.0.1:18081>.

Le mot de passe administrateur est demandé interactivement et n'est ni placé dans `.env`, ni écrit dans les logs. Pour l'accès initial par tunnel HTTP, `WOS_COOKIE_SECURE=false`. Cette valeur devra devenir `true` en même temps que l'ajout de HTTPS.

La commande de création de l'administrateur initialise aussi
`/srv/seedbox/admin/downloads`. `APP_UID` et `APP_GID` doivent donc
correspondre à une identité ayant le droit de créer des dossiers sous `/srv/seedbox`.
Il ne faut pas appliquer de `chown -R` ou de `chmod -R` à l'aveugle sur les données
existantes ; les permissions seront vérifiées précisément pendant le déploiement accompagné.

La commande `migrate-workspaces` déplace de façon atomique les anciens espaces
`/srv/seedbox/users/<username>` vers `/srv/seedbox/<username>`. Elle est idempotente,
refuse toute collision et ne touche jamais aux répertoires qBittorrent historiques
`/srv/seedbox/downloads` et `/srv/seedbox/watch`. Elle retire uniquement les anciens
`watch` propres aux utilisateurs lorsqu'ils sont vides ; un dossier non vide est conservé
mais masqué par le navigateur.

La conception détaillée et le découpage des prochaines PR sont documentés dans
[`docs/architecture-v1.md`](docs/architecture-v1.md). La coexistence avec les chemins
qBittorrent actuels est décrite dans
[`docs/storage-migration.md`](docs/storage-migration.md).

Le déploiement GitHub Actions et l'installation sécurisée de l'identité technique OVH
sont détaillés pas à pas dans
[`docs/deployment-ovh.md`](docs/deployment-ovh.md).
