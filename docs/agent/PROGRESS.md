# World of Seeds — Progress

## Current release and branches

- Stable production release: `1.3.3` (V1).
- V1.3.3 feature PR: `#50`, merged into `develop`.
- V1.3.3 release PR: `#51`, merged into `master`.
- Stable master SHA: `7ea5ff06f9fdefa59545ac33c1aecf6d9db596ae`.
- V1.3.3 feature SHA: `5ffd7fb3dd20f5c7c3d3d9094ec6ca10ec97c459`.
- `develop` was fast-forwarded to the stable master SHA after release.
- Permanent V2 integration branch `develop_V2` was created from the same stable SHA.
- V2 preparation PR `#52` was merged into `develop_V2` at
  `80a253da7b9fe57ddec39b0dfe92eaa2daca7e6b`.
- V2-01 PR `#53` was merged into `develop_V2` at
  `980b73182806b5604440b51c21f87df49c59b4e6`.
- V2-02 PR `#54` was merged into `develop_V2` at
  `b5a75f3c25f634ae7bcaef91ff9e26f2d8324ff9`.
- Active task branch: `feat/v2-shared-torrent-schema`, based on that merge commit and
  targeting `develop_V2`.

## V1 completion state

- V1.3.1 removed recursive size calculation from file listings, released SQL connections
  before streams, replaced temporary archives with bounded direct ZIP streaming, and fixed
  file-table layout.
- V1.3.2 corrected C411 tracker paths/hosts while preserving the raw infohash, removed
  SweetAlert2 inline-style CSP violations, and restored class-only accessible dialogs.
- V1.3.3 accepts and validates qBittorrent 5.2 structured add responses, keeps legacy
  `Ok.`/204 compatibility, rejects malformed/mismatched responses, and persists
  `UserTorrent` only after verified acceptance.
- Release `v1.3.3` is published and the immutable image was deployed successfully to OVH.
- Release CI: run `32474358488`, green.
- Master CI: run `32474472325`, green.
- Deployment: run `32474912048`, green.
- An earlier publish run `32474472346` hit release-list eventual consistency; its targeted
  rerun succeeded. No application rollback was required.

## V2 preparation completed in V2-00

- Synchronized `develop` to the stable V1.3.3 master commit.
- Created the permanent `develop_V2` integration branch from that exact commit.
- Replaced the obsolete workflow that sent V2 work through `develop`/`master`.
- Defined the target stack: API, durable scheduler/workers, PostgreSQL, Redis,
  qBittorrent, NewGreedy, ingress, Prometheus, Grafana, node-exporter, and cAdvisor.
- Defined PostgreSQL-authoritative models: `ManagedTorrent`, `TorrentRequest`,
  `TorrentFile`, `TrackerActivity`, `TorrentJob`, and `DownloadLease`.
- Defined durable job states `QUEUED`, `RUNNING`, `COMPLETED`, `FAILED`, `CANCELLED`,
  including claims, retries, timeouts, idempotence, and crash recovery.
- Defined infohash deduplication and one physical torrent shared by multiple user requests.
- Defined weighted fair scheduling with bounded small-job preference and anti-starvation
  aging, plus global/per-user concurrency and bandwidth limits.
- Defined shared-storage quotas, disk-pressure admission, manifest accounting, leases, and
  reconciled purges without recursive scans in web requests.
- Defined recursive browser-side folder transfer with manifest, HTTP Range,
  pause/resume/cancel, controlled concurrency, and only a small-folder ZIP fallback.
- Preserved the V1 filesystem security invariants, strict CSP, React-native confirmations,
  definite-delete confirmation, and responsive orientation requirements.
- Defined central SQL options versus deployment secrets and prepared opaque multi-account
  references without persisting plaintext passkeys.
- Added the isolated Rise2 deployment, backup, pilot, and rollback plan.
- Produced an ordered V2 roadmap from V2-01 through V2-35 with dependencies, Work levels,
  migrations, breakages, risks, and exit criteria.
- No functional V2 code, database migration, dependency, Compose service, or version bump
  is included in V2-00.

## V2-01 — CI and versioning foundation

- Selected `2.0.0-alpha.N`, then `beta.N` and `rc.N`, as the supported V2 prerelease
  formats; initialized all application mirrors at `2.0.0-alpha.0`.
- Kept the versioning tool on the stable channel by default so the unchanged V1 release and
  deployment workflows reject any V2 prerelease.
- Added an explicit V2 channel to version checks and synchronized updates.
- Added `develop_V2` push validation to the existing CI while preserving the same backend,
  frontend, and container jobs.
- Added policy regression tests for stable/V2 version separation and workflow isolation.
- Added a post-CI workflow that publishes only a green `develop_V2` revision to the
  separate `ghcr.io/thomasperez91/world-of-seeds-v2` package by immutable SHA.
- The V2 image workflow does not deploy to Rise2 and cannot invoke the V1 release or OVH
  deployment workflows.
- No runtime feature, database migration, dependency, Compose service, or V1 workflow was
  added or changed outside this CI/versioning scope.

## V2-02 — Local Compose foundation

- Added a separate `compose.v2.yaml` project containing only `api`, `postgres`, and
  `redis`; the V1 Compose files remain unchanged.
- Added `.env.v2.example` with V2-prefixed variables and a required storage root distinct
  from V1.
- Pinned PostgreSQL to `17.11-alpine3.24` and Redis to `8.2.9-alpine3.22`.
- Kept PostgreSQL and Redis exclusively on the internal backend network with no published
  host ports and dedicated V2 volumes.
- Bound the temporary local API entry point to host loopback only and attached it to
  separate edge/backend networks.
- Added healthchecks for all three services and made the API wait for healthy PostgreSQL
  and Redis.
- Enabled Redis append-only persistence for the local V2 foundation; no Redis business
  client or queue is introduced before its roadmap task.
- Added a normalized Compose policy validator and regression tests for network, port,
  image, volume, healthcheck, dependency, and Docker-socket invariants.
- Extended only the V2 path of CI to validate, build, start, probe, and remove the local V2
  foundation. Existing V1 container validation remains in place.
- No qBittorrent, NewGreedy, worker, scheduler, model, migration, or Rise2 deployment is
  included in V2-02.

## V2-03 — Shared torrent schema

- Added the additive `ManagedTorrent`, `TorrentRequest`, and `TorrentFile` SQLAlchemy
  models without transforming or removing the V1 `UserTorrent` table.
- Persisted the normative managed-torrent and user-request states as constrained strings.
- Enforced one canonical lowercase 40-character infohash per physical managed torrent and
  one opaque unique storage key per managed content.
- Enforced one active `REQUESTED`, `ACTIVE`, or `READY` request per user and managed
  torrent while allowing multiple users to own requests for the same physical torrent.
- Added manifest constraints for non-negative indexes and sizes, safe relative paths, and
  unique file indexes and paths inside each managed torrent.
- Added an additive Alembic migration with a reverse migration to the V1 schema boundary.
- Extended only the V2 CI path to execute the V2 downgrade and re-upgrade against
  PostgreSQL; the V1 migration path remains upgrade-only.
- No API, worker, Redis client, qBittorrent behavior, V1 import, or Rise2 deployment is
  included in V2-03.

## Current validation

- V2-00 documentation links, Markdown structure, `git diff --check`, and targeted secret
  scan: PASS.
- V2-00 GitHub CI run `32483090455`: PASS; backend, frontend, and container green.
- V2-01 targeted versioning/policy tests: PASS, 14 tests.
- V2-01 targeted Ruff lint: PASS.
- V2-01 targeted Ruff formatting for backend tests: PASS.
- V2-01 targeted mypy: PASS.
- V2-01 version mirror and stable-channel rejection checks: PASS.
- V2-01 GitHub CI run `32484452856`: PASS; backend, frontend, and container green.
- V2-02 Compose policy tests: PASS, 7 tests.
- V2-02 targeted Ruff lint and formatting: PASS.
- V2-02 targeted mypy: PASS.
- V2-02 `git diff --check`: PASS.
- V2-02 GitHub CI run `32485704318`: PASS; backend, frontend, container, and the real
  Compose configuration/startup/isolation cycle are green.
- V2-03 model and V1 torrent regression tests: PASS, 16 tests.
- V2-03 targeted Ruff lint/format and mypy: PASS.
- V2-03 PostgreSQL upgrade/downgrade SQL generation and `git diff --check`: PASS.
- V2-03 migration execution against PostgreSQL and complete CI: pending PR CI.

## Known constraints

- `master` and `develop` remain V1-only; V2 branches and PRs target `develop_V2`.
- V1 qBittorrent remains external and shares `/srv/seedbox:/data` with WOS.
- Rise2 V2 must not reuse V1 secrets, networks, volumes, database, qB profile, or storage
  before an explicitly approved import.
- PostgreSQL is authoritative for durable jobs and destructive decisions; Redis loss must
  remain recoverable.
- Secrets and complete tracker URLs must never reach logs, metrics, options, DB business
  rows, browser responses, or agent documents.

## Next task

- Open and validate the V2-03 PR into `develop_V2`; do not merge it automatically.
- After V2-03 is reviewed and merged, the next roadmap task is
  `V2-04 — TorrentJob schema, SQL claims, timeouts, retries, and cancellation`.
- Do not start V2-04 as part of the current task.
