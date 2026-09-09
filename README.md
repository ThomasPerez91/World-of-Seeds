# World of Seeds

World of Seeds est une application web privée de gestion de seedbox. La ligne active est la **V2**, déployée sur Rise2 avec FastAPI, React/TypeScript, PostgreSQL, Redis, qBittorrent et NewGreedy.

## État du projet

- version de production actuelle : `2.0.0` ;
- `master` : production V2 ;
- `develop` : intégration V2 ;
- `develop_V2` : historique de construction, sans nouveau développement ;
- déploiement production : workflow GitHub Actions `Deploy V2 to Rise2` après CI `master` verte.

L'expérience utilisateur est torrent-centric : ajout d'un `.torrent`, suivi de file/progression, état READY, récupération locale et désabonnement. Il n'existe plus de navigateur de fichiers, de workspace ou de corbeille par utilisateur dans le runtime moderne.

## Architecture

PostgreSQL est l'autorité métier. Redis sert uniquement à la coordination et aux signaux éphémères. qBittorrent et NewGreedy sont des services internes et ne sont jamais contactés directement par le navigateur.

Le stockage physique est partagé :

- `ManagedTorrent` représente une copie physique ;
- `TorrentFile` représente son manifeste ;
- `TorrentRequest` représente le droit/abonnement d'un utilisateur ;
- `SharedContentStore` conserve le contenu sous `/data/content/<storage-key>` ;
- plusieurs utilisateurs peuvent donc partager une seule copie physique ;
- retirer un droit ne détruit pas le contenu tant qu'une autre demande active existe ; la dernière référence passe par le lifecycle normal de rétention/purge.

Aucun chemin hôte ni `save_path` arbitraire n'est accepté depuis le client. Les passkeys tracker restent des secrets d'infrastructure et ne sont jamais persistées dans les tables métier, logs ou réponses frontend.

## Développement local

Backend :

```bash
cd backend
uv sync --frozen --dev
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

Validation backend complète :

```bash
cd backend
uv run ruff check .
uv run ruff format --check .
uv run mypy app tests
uv run pytest
```

`VERSION` reste la source canonique de version applicative. Les changements de version passent par `scripts/versioning.py` et la CI vérifie les miroirs Python/npm ainsi que l'image.

## Contribution et livraison

Tout changement part du dernier `develop` sur une branche dédiée et revient par pull request. Les checks requis sont : backend, frontend, image conteneur, sécurité dépendances/image et policy Rise2. Aucun push direct n'est fait sur `develop` ou `master`.

La promotion en production est une PR séparée `develop -> master`. Après merge et CI `master` verte, Rise2 déploie le digest immuable validé sans recréer PostgreSQL, Redis, qBittorrent, NewGreedy ni les volumes persistants.

## Documentation

- `docs/agent/CONTEXT.md` : contexte durable et invariants actuels ;
- `docs/agent/PROGRESS.md` : état des tâches ;
- `docs/architecture-v2.md` : architecture V2 ;
- `docs/deployment-rise2-v2.md` : déploiement et rollback Rise2 ;
- `docs/roadmap-v2.md` : historique et roadmap.
