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
- supervision privée de NewGreedy et qBittorrent, liste des torrents et configuration NewGreedy contrôlée ;
- redémarrage NewGreedy médié par systemd, sans socket Docker dans le conteneur applicatif ;
- image Docker unique pour l'API et le frontend ;
- montage hôte limité à `/srv/seedbox:/data` ;
- contrôles de qualité automatisés.

La V1 applicative est complète et la conception de la V2 est engagée. Toute PR de release
fusionnée dans `master` prépare une release en brouillon, construit l’image depuis le commit
immuable, vérifie sa version, publie la release puis déploie le digest validé sur OVH. Le
déclenchement manuel reste disponible en secours.

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

`VERSION` est la source canonique de version applicative. Pour préparer une nouvelle
version et mettre à jour ses miroirs Python/npm :

```bash
python3 scripts/versioning.py set 1.3.0
python3 scripts/versioning.py check --expected-tag v1.3.0 --print-version
```

La CI vérifie les miroirs et le label OCI de l’image avant qu’une release stable puisse être
publiée.

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

En production, WOS rejoint aussi le réseau Docker externe `torrent-internal` pour joindre
les API de NewGreedy et qBittorrent sans publier de nouveau port. Ce réseau ne donne aucun
accès au socket Docker. Le redémarrage NewGreedy passe par un fichier de requête borné sous
`/srv/seedbox/.wos-control` et un service systemd limité à la recréation de cet unique
service.

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

La V1 reste documentée dans [`docs/architecture-v1.md`](docs/architecture-v1.md). La cible
V2, ses transitions métier et son découpage de PR sont décrits dans
[`docs/architecture-v2.md`](docs/architecture-v2.md),
[`docs/state-machines-v2.md`](docs/state-machines-v2.md) et
[`docs/roadmap-v2.md`](docs/roadmap-v2.md). La coexistence avec les chemins qBittorrent
actuels est détaillée dans [`docs/storage-migration.md`](docs/storage-migration.md).

Le déploiement GitHub Actions et l'installation sécurisée de l'identité technique OVH
sont détaillés pas à pas dans
[`docs/deployment-ovh.md`](docs/deployment-ovh.md).
