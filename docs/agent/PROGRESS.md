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
- V2-03 PR `#55` was merged into `develop_V2` at
  `8ea43fe1f49ffaa5a898e1f0a12d44e9de702267`.
- V2-04 PR `#56` was merged into `develop_V2` at
  `3b443ea46932e4aac728d85f3a4e0279ece061f0`.
- V2-05 PR `#57` was merged into `develop_V2` at
  `3e518645dc5eded4f9c6280095d27428f5b36385`.
- V2-06 PR `#58` was merged into `develop_V2` at
  `66e122c7ed7a086be5bfe0d1a95098a6525e1647`.
- V2-07 PR `#59` was merged into `develop_V2` at
  `ab89407eabacadc0fa05c9a5f143a5fa67f3c035`.
- V2-08 PR `#60` was merged into `develop_V2` at
  `7d1ad6141814f4a3a64f5292726a5be9c1a423e8`.
- V2-09 PR `#61` was merged into `develop_V2` at
  `51af8185f911d176669b4e7cb4b0b5b4482fd2eb`.
- V2-10 PR `#62` was merged into `develop_V2` at
  `940469426008a51041596b0b9facf906159009ef`.
- V2-11 PR `#63` was merged into `develop_V2` at
  `0eedd65fe20dd91dac3deb49000e2def49aabd9e`.
- V2-12 is implemented on the dedicated `feat/v2-weighted-fair-scheduler` branch from
  `develop_V2` commit `c357802d871ab8c6d7fcbf78ae81cbb2991c7b7e`.

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

## V2-04 — Durable torrent jobs

- Added the PostgreSQL-authoritative `TorrentJob` model with the exact normative states
  `QUEUED`, `RUNNING`, `COMPLETED`, `FAILED`, and `CANCELLED`.
- Added a globally unique idempotency key, generic bounded job type, managed-torrent link,
  optional originating-request link, attempts, availability, deadlines, claims, safe error
  codes, cancellation intent, and completion timestamps.
- Added SQL constraints that reject exhausted queued jobs, incomplete running claims,
  invalid attempt limits, and terminal jobs without a completion timestamp.
- Added oldest-ready selection with PostgreSQL `FOR UPDATE SKIP LOCKED`, claim ownership,
  bounded renewal, execution timeout, retry backoff, and expired-claim recovery.
- Queued cancellation is immediate; running cancellation persists intent and becomes final
  only at a worker checkpoint or after external-effect reconciliation.
- Added an additive and reversible Alembic migration plus targeted state, idempotence,
  retry, timeout, crash-recovery, cancellation, ownership, and PostgreSQL concurrency tests.
- No Redis client, scheduler policy, worker process, qBittorrent effect, API, or frontend is
  included in V2-04.

## V2-05 — Fault-tolerant Redis coordination

- Added the async Redis runtime client as an optional V1-safe dependency and configured it
  only in the isolated V2 Compose stack.
- Added strict secret-safe Redis URL parsing, bounded connect/socket timeouts, a validated
  namespace, cache TTL/stale windows, and a bounded signal queue length.
- Added best-effort job wake-up signals backed by a bounded Redis list; Redis remains a
  nudge only and PostgreSQL remains the durable job authority and polling fallback.
- Added namespaced JSON cache-aside with explicit `MISSING`, `FRESH`, and `STALE` states,
  TTL expiry, invalid-payload eviction, post-commit invalidation support, and authoritative
  loader fallback when Redis is empty or unavailable.
- A configured Redis outage degrades public system health without failing PostgreSQL
  readiness or blocking authoritative application reads.
- Added clean async shutdown and a V2 Compose policy requiring the private internal Redis
  service URL with no published Redis port.
- Added fake-failure, cache reconstruction, signal, TTL, health, configuration, Compose
  policy, and real-Redis integration tests.
- No scheduler algorithm, worker process, PostgreSQL domain query cache consumer, API
  mutation, qBittorrent effect, frontend, or Rise2 deployment is included in V2-05.

## V2-06 — Typed and audited PostgreSQL options

- Added PostgreSQL-authoritative `DatabaseOption` rows with explicit boolean, integer, or
  select storage columns, SQL type invariants, persisted bounds/choices, editability,
  restart requirements, and monotonic versions.
- Added append-only option audit events with unique option/version pairs, old/new values,
  change source, timestamp, and a nullable administrator reference preserved with
  `ON DELETE SET NULL`.
- Added idempotent registry initialization from the existing safe functional option specs;
  defaults receive a version-1 bootstrap audit event without an actor.
- Added transactional row locking, active-admin attribution, no-op detection, shared type,
  bounds and cross-option validation, and explicit metadata drift detection.
- Unknown keys and keys resembling credentials, tokens, passkeys, passwords, or other
  secrets are rejected before any SQL value or audit event is written.
- Added an additive and reversible Alembic migration plus targeted default, typing, bounds,
  actor, version, audit, no-op, drift, secret separation, and SQL constraint tests.
- The V1 `.options` store remains unchanged as the V1 runtime authority; no V2 admin API,
  frontend, scheduler consumer, or infrastructure-secret editor is included in V2-06.

## V2-07 — Transactional infohash deduplication

- Added a transaction-scoped service that validates a canonical lowercase SHA-1 infohash,
  bounded torrent name, signed 64-bit size, timezone-aware timestamp, and active owner
  before writing torrent state.
- Added PostgreSQL and SQLite conflict-safe inserts that converge concurrent uploads on
  the SQL-unique `ManagedTorrent.info_hash` without deriving or accepting a storage path.
- A first owner creates the physical managed torrent and its request; later owners reuse
  the same managed row and receive independent `TorrentRequest` ownership rows.
- Repeating an active request for the same owner is idempotent, while a prior terminal
  request permits a new active right under the existing partial unique index.
- Existing canonical name/size conflicts are rejected instead of overwriting managed
  metadata, and the service leaves commit or rollback entirely to its caller.
- Added targeted validation, ownership, rollback, metadata-conflict, repeated-request,
  terminal-request, two-owner, and real PostgreSQL concurrency tests.
- No API endpoint, Redis signal, `TorrentJob`, worker, qBittorrent call, filesystem action,
  schema migration, or frontend change is included in V2-07.

## V2-08 — Separate durable worker process

- Added a dedicated worker runtime using the application image with a separate
  `python -m app.worker` command and no HTTP port or edge-network access.
- PostgreSQL remains authoritative: the worker claims only registered job types, renews
  owned claims in an independent heartbeat transaction, and recovers abandoned claims.
- Redis wake-up signals shorten the bounded SQL polling delay only; missing or unavailable
  Redis cannot lose or authorize a job.
- Added bounded concurrency, exponential retry with jitter, permanent secret-safe failure
  codes, cooperative cancellation checkpoints, execution deadlines, and clean shutdown.
- A forced shutdown stops the handler but deliberately leaves its durable `RUNNING` claim
  for expiry and replay instead of falsely completing or cancelling the external effect.
- Normalized UTC comparisons at the existing timezone-naive SQL boundary so fresh worker
  sessions can safely claim, renew, retry, and recover jobs on SQLite and PostgreSQL.
- Added worker runtime, heartbeat, retry, permanent failure, unsupported type, abandoned
  claim, graceful/forced stop, polling wake-up, Compose isolation, and real PostgreSQL tests.
- No qBittorrent/NewGreedy handler, filesystem mutation, API, scheduler, frontend, schema
  migration, V1 Compose change, or `master` change is included in V2-08.

## V2-09 — qBittorrent V2 gateway

- Added a dedicated V2 qBittorrent gateway without changing the existing V1 client.
- Every add derives its save path from the server-owned data root and opaque storage key;
  callers cannot supply a path, category, tag, or other qBittorrent mutation option.
- New torrents receive the fixed `wos-v2` category plus global and per-storage-key WOS V2
  identity tags.
- A mandatory infohash preflight makes retries idempotent and refuses to add or mutate an
  existing torrent whose category, identity tags, or save path do not match the managed row.
- Explicit qBittorrent authentication failures and add rejections remain failures. Transport,
  read, malformed-success, and server-response ambiguity is reconciled by exact infohash; an
  owned match succeeds, while a missing result remains retryable without hiding a rejection.
- Added bounded response parsing and targeted tests for fixed identity, idempotent replay,
  external ownership conflicts, accepted-but-timed-out reconciliation, retryable ambiguity,
  explicit rejection, authentication, and input validation.
- No worker job handler, C411/NewGreedy normalization, API, schema, filesystem mutation,
  frontend, V1 client behavior, Compose, or `master` change is included in V2-09.

## V2-10 — C411 and NewGreedy integration

- Added a worker-facing composition gateway that checks NewGreedy readiness before the
  infrastructure C411 passkey is injected and immediately submits normalized metainfo to
  the V2 qBittorrent gateway.
- Reused the strict existing bencode parser and raw `info` preservation path without
  changing V1 behavior; the expected managed infohash must match before qB is called.
- C411 `announce` and `announce-list` entries remain restricted to the configured allowlist,
  are rebuilt as `/announce/<encoded-passkey>`, and user passkeys are absent from outgoing
  metainfo.
- The composite returns only the qB add state: it never returns tracker URLs, normalized
  secret-bearing metainfo, or passkeys to a caller.
- Added a separate read-only NewGreedy V2 gateway exposing only bounded `/api/health`; its
  origin must resolve to the internal `newgreedy` service and cannot contain credentials,
  paths, query strings, or fragments.
- Added targeted tests for raw-info preservation, both C411 hosts, `announce-list`, user
  passkey removal, host rejection, infohash mismatch, NewGreedy outage/invalid responses,
  internal-origin enforcement, secret-safe results/errors, and invalid infrastructure config.
- No API route, worker registration, tracker activity persistence, multiple-account policy,
  Compose service, frontend, schema, filesystem, V1 behavior, or `master` change is included.

## V2-11 — Secret-safe tracker activity

- Added append-only `TrackerActivity` rows linked to a managed torrent and an opaque tracker
  account UUID, with a unique event UUID for idempotent replay.
- Activity type, outcome, and diagnostic are closed enums rather than arbitrary text; success
  forbids a diagnostic and degraded/failed outcomes require one of the bounded safe codes.
- The activity schema deliberately has no URL, response body, message, payload, passkey,
  credential, or other free-form diagnostic column.
- Added nullable opaque tracker and qBittorrent account UUID references to `ManagedTorrent`
  so future account selection can persist identity without putting secrets in PostgreSQL.
- Added transactional one-time account assignment: identical replay is accepted, while
  silent reassignment or an activity for another account reference is rejected.
- Added an additive/reversible migration and targeted tests for persistence, replay,
  collision detection, immutable assignment, account matching, diagnostic consistency,
  enum enforcement, and the absence of secret-bearing columns.
- No account-selection algorithm, encrypted secret storage, TrackerActivity API, scheduler,
  worker registration, frontend, Compose service, V1 behavior, or `master` change is included.

## V2-12 — Weighted fair scheduler

- Added a deterministic, side-effect-free weighted deficit round-robin policy that returns an
  explicit ledger for later transactional persistence by the singleton scheduler.
- Added small, medium, and large remaining-size classes with bounded costs, so several small
  downloads can complete quickly while large downloads accumulate enough credit to progress.
- Added bounded wait-time aging that can reduce, but never eliminate, a torrent's scheduling
  cost and combines with accumulated deficit to prevent starvation.
- Added global and per-user active limits, deterministic per-user queue order, and one explicit
  beneficiary per physical torrent so a user's queue cannot silently duplicate shared content.
- Added future-compatible per-user weights; weighted users receive a larger share while the
  round-robin cursor preserves service for standard-weight users.
- Stalled torrents are reported separately and do not consume scarce active slots until a later
  health snapshot marks them eligible again.
- Added seven typed, bounded, PostgreSQL-authoritative scheduler options, including cross-option
  validation for ordered size thresholds.
- Added deterministic simulations covering small-job preference, continuous-arrival
  anti-starvation, aging, weighted shares, global/per-user limits, stalled torrents, duplicate
  physical torrents, and input-order independence.
- No qBittorrent pause/resume, speed control, scheduler process/lease, API, frontend, schema
  migration, Compose service, V1 runtime behavior, or `master` change is included.

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
- V2-03 GitHub CI run `32488849874`: PASS; PostgreSQL migration rollback/re-upgrade,
  backend, frontend, and container jobs are green.
- V2-04 targeted model/job tests: PASS, 19 tests with the PostgreSQL-only concurrency test
  deferred to PR CI.
- V2-04 targeted Ruff lint/format and mypy: PASS.
- V2-04 PostgreSQL upgrade/downgrade SQL generation and `git diff --check`: PASS.
- V2-04 GitHub CI run `32494379045`: PASS; real PostgreSQL concurrency, migration
  rollback/re-upgrade, backend, frontend, and container jobs are green.
- V2-05 targeted Redis/config/health/Compose policy tests: PASS, 42 tests with the real
  Redis integration test deferred to PR CI.
- V2-05 targeted Ruff lint/format and mypy: PASS.
- V2-05 GitHub CI run `32497192124`: PASS; real Redis integration, normalized Compose,
  migrations, backend, frontend, and container jobs are green.
- V2-06 targeted V1/V2 option and model regression tests: PASS, 30 tests.
- V2-06 targeted Ruff lint/format and mypy: PASS.
- V2-06 complete backend suite: PASS, 229 tests with 2 service-backed tests deferred to CI.
- V2-06 PostgreSQL upgrade/downgrade SQL generation and `git diff --check`: PASS.
- V2-06 GitHub CI run `32499070361`: PASS; PostgreSQL migrations, backend, frontend,
  container build, and V2 Compose isolation are green.
- V2-07 targeted deduplication/model/V1 torrent regression tests: PASS, 30 tests with the
  real PostgreSQL concurrency test deferred to PR CI.
- V2-07 targeted Ruff lint/format and mypy: PASS.
- V2-07 complete backend suite: PASS, 243 tests with 3 service-backed tests deferred to CI.
- V2-07 GitHub CI run `32500255919`: PASS; real PostgreSQL concurrency, backend,
  frontend, container build, and V2 Compose isolation are green.
- V2-08 targeted worker/job/Redis/Compose tests: PASS, 34 tests with 3 real-service tests
  deferred to PR CI.
- V2-08 targeted Ruff lint/format and mypy: PASS.
- V2-08 complete backend suite: PASS, 255 tests with 4 real-service tests deferred to CI.
- V2-08 normalized Compose policy and `git diff --check`: PASS; real Docker startup is
  deferred to PR CI because Docker is unavailable in the development environment.
- PR #60 (V2-08) review and GitHub CI run `32501866847`: PASS; squash-merged into
  `develop_V2` at `7d1ad6141814f4a3a64f5292726a5be9c1a423e8`.
- V2-09 targeted qBittorrent V2 gateway and V1 integration regression tests: PASS, 23 tests.
- V2-09 complete backend suite: PASS, 265 tests with 4 service-backed tests deferred to CI.
- V2-09 full backend Ruff lint/format and mypy: PASS.
- PR #61 (V2-09) review and GitHub CI run `32504351178`: PASS; squash-merged into
  `develop_V2` at `51af8185f911d176669b4e7cb4b0b5b4482fd2eb`.
- V2-10 targeted C411/NewGreedy, torrent normalization, and qB V2 tests: PASS, 30 tests.
- V2-10 complete backend suite: PASS, 279 tests with 4 service-backed tests deferred to CI.
- V2-10 full backend Ruff lint/format and mypy: PASS.
- PR #62 (V2-10) review and GitHub CI run `32505303900`: PASS; squash-merged into
  `develop_V2` at `940469426008a51041596b0b9facf906159009ef`.
- V2-11 targeted tracker activity, torrent model, and job regression tests: PASS, 28 tests
  with 1 PostgreSQL-backed test deferred to CI.
- V2-11 complete backend suite: PASS, 288 tests with 4 service-backed tests deferred to CI.
- V2-11 full backend Ruff lint/format and mypy: PASS.
- V2-11 PostgreSQL upgrade/downgrade SQL generation and `git diff --check`: PASS.
- PR #63 (V2-11) review and GitHub CI run `32512486810`: PASS; squash-merged into
  `develop_V2` at `0eedd65fe20dd91dac3deb49000e2def49aabd9e`.
- V2-12 targeted scheduler, database-option, durable-job, worker, and V1 option regression
  tests: PASS, 51 tests with 2 service-backed tests deferred to CI.
- V2-12 complete backend suite: PASS, 301 tests with 4 service-backed tests deferred to CI.
- V2-12 full backend Ruff lint/format and mypy: PASS.
- V2-12 `git diff --check`: PASS.

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

- The next roadmap task is `V2-13 — qBittorrent priority and bandwidth control`.
- Do not start V2-13 until V2-12 has passed review, required CI is green, its PR is merged into
  `develop_V2`, and explicit authorization is provided.
