# World of Seeds

Interface web privée de gestion de seedbox, conçue pour être déployée en Docker sur un serveur Ubuntu.

## État du projet

Les cinq premières étapes de la V1 sont en place :

- API FastAPI typée ;
- interface React/Vite TypeScript ;
- PostgreSQL non exposé sur l'hôte ;
- authentification par session et comptes temporaires administrés ;
- espaces `/data/users/<username>/{downloads,watch}` créés avec chaque compte ;
- renommage coordonné du compte et de son dossier avec compensation en cas d'échec SQL ;
- navigation sécurisée avec métadonnées, fil d'Ariane et espace disque ;
- téléchargement en flux avec HTTP Range, reprise, ETag et Last-Modified ;
- image Docker unique pour l'API et le frontend ;
- montage hôte limité à `/srv/seedbox:/data` ;
- contrôles de qualité automatisés.

Les mutations et la corbeille sont ajoutées par petites pull requests indépendantes.

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
npm run dev
```

## Déploiement Docker

Le déploiement ne doit pas être lancé avant d'avoir configuré `.env` et préparé les permissions de `/srv/seedbox` :

```bash
cp .env.example .env
docker compose config
docker compose build
docker compose up -d postgres
docker compose run --rm app alembic -c backend/alembic.ini upgrade head
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
`/srv/seedbox/users/admin/{downloads,watch}`. `APP_UID` et `APP_GID` doivent donc
correspondre à une identité ayant le droit de créer des dossiers sous `/srv/seedbox`.
Il ne faut pas appliquer de `chown -R` ou de `chmod -R` à l'aveugle sur les données
existantes ; les permissions seront vérifiées précisément pendant le déploiement accompagné.

La conception détaillée et le découpage des prochaines PR sont documentés dans
[`docs/architecture-v1.md`](docs/architecture-v1.md). La coexistence avec les chemins
qBittorrent actuels est décrite dans
[`docs/storage-migration.md`](docs/storage-migration.md).
