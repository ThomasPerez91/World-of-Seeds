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
- V2-12 PR `#64` was merged into `develop_V2` at
  `2b870a52c6c4aeb834e1f883f7bb2675b277562d`.
- V2-13 PR `#65` was merged into `develop_V2` at
  `5a1bab12be33681ccb6ad964dda18c40a57482c5`.
- V2-13A PR `#66` was merged into `develop_V2` at
  `782a7ff41817481a7bf1f929064380560c5cd6b3`.
- V2-13B PR `#67` was merged into `develop_V2` at
  `2514ddba888680df2702686d333231741ee64747`.
- V2-13C PR `#68` was merged into `develop_V2` at
  `86bda53ce4a8d4982b1ec2173f07c60f69017b59`.
- Local macOS roadmap PR `#69` was merged into `develop_V2` at
  `d02af28ed268475675b7f51d7a732d9bf4a88354`.
- V2-14 PR `#70` was merged into `develop_V2` at
  `4a72074536b2496723d2fa9af5286744f64babde`.
- V2-15 PR `#71` was merged into `develop_V2` at
  `3a611873e01de8ff156292b170b735483b1d0b0d`.
- V2-16 PR `#72` was merged into `develop_V2` at
  `13188b63cf5ab185233b9926282ebe018586ca2a`.
- V2-17 PR `#73` was merged into `develop_V2` at
  `a7dafce1258d822ffea073918f8d5c072fa4abdb`.
- V2-18 PR `#74` was merged into `develop_V2` at
  `7fc48c73b00e8be95c3c46dac285456070fa15b7`.
- V2-18A PR `#75` was merged into `develop_V2` at
  `2f1787b1039c112b6aee0901ae18f816618a9b30`.
- V2-19 PR `#76` was merged into `develop_V2` at
  `f728471529d84ee43907101f819acba53eac65c9`.
- V2-20 PR `#77` was merged into `develop_V2` at
  `5e624f7e9094ea9441b10dd57690a9a68460b337`.
- V2-21 PR `#78` was merged into `develop_V2` at
  `5dba6977a4956b127c6c5a25428e522b78254eba`.
- V2-22 PR `#79` was merged into `develop_V2` at
  `eaa436581a3ab449b33790e90c6dc0f8ea052177`.
- V2-23 PR `#80` was merged into `develop_V2` at
  `145d0d3b2fd4df4536e16107f8c2511804faa7f8`.
- V2-24 PR `#81` was merged into `develop_V2` at
  `129dcce13feb120ceb6ddac9cdbc93f3fb162ccb`.
- V2-25 PR `#82` was merged into `develop_V2` at
  `13e8e43b2c40c245ca111b4b573872f7c1cdebbf`.
- V2-26 PR `#83` was merged into `develop_V2` at
  `0f43d18c04f755270309e3df48e356a8c73f803d`.
- V2-27 PR `#84` was merged into `develop_V2` at
  `cfc847d0aa2c0ef64e7d2b7f98a801d8edbe7987`.
- V2-28 PR `#85` was merged into `develop_V2` at
  `5de4cd0282fd1397c810a0a693e455cada4c6c9e`.

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

## V2-13 — qBittorrent priority and bandwidth control

- Added a deterministic adapter from the V2-12 scheduler result to a bounded qBittorrent
  control plan: selected torrents retain scheduler priority order, while the remaining caller-
  supplied control set is stopped.
- Added a typed, bounded PostgreSQL-authoritative global qB download-limit option. A configured
  cap is divided exactly and deterministically across admitted torrents; zero keeps qB unlimited.
- Added qBittorrent 5 `stop`/`start`, per-torrent `setDownloadLimit`, and relative `topPrio`
  control through the isolated V2 gateway without changing the V1 integration.
- Every batch performs one bounded infohash lookup and validates the category, server-derived
  save path, fixed V2 tag, per-storage identity tag, state, and limit for every torrent before
  the first mutation. Missing or external torrents therefore cannot cause partial control.
- Reconciliation compares observed state and download limits before writing, accepts both qB 5
  stopped states and legacy paused states, and reapplies relative priority from lowest to highest
  so a fresh process recreates scheduler order after restart.
- Explicit qB rejections such as disabled torrent queueing remain non-retryable; transport,
  server, incomplete-snapshot, and missing-torrent failures remain safely retryable.
- Control operations always carry explicit canonical infohashes, are capped at 200 torrents, and
  never use qB's `all` selector or mutate global qB limits that could affect external torrents.
- No scheduler process/lease, periodic reconciliation loop, API, frontend, schema migration,
  shared-storage implementation, V1 runtime behavior, or `master` change is included.

## V2-13A — Durable singleton scheduler runtime

- Added a separately executable scheduler runtime with cooperative shutdown and a PostgreSQL
  singleton lease that is renewed independently when the configured qB cycle is longer than the
  lease heartbeat.
- Added a durable singleton state and per-user deficit rows so weighted-fair rounds, credits, and
  the round-robin cursor survive process replacement instead of restarting fairness from zero.
- Persisted a monotonic desired generation plus per-torrent desired admission, priority, and
  download limit before qB is called; the applied generation advances only after qB success.
- A failed or interrupted qB call therefore leaves explicit unapplied intent. After lease expiry,
  another scheduler owner recomputes and safely reconciles the complete bounded control set.
- Candidate construction is PostgreSQL-authoritative and deterministic: one earliest active
  beneficiary is selected for each physical torrent, while torrents with no active request are
  emitted as stopped controls if they were previously admitted.
- The control set is locked and capped at 200 rows before any external effect. Larger unexpected
  sets fail closed for later administrative reconciliation instead of partially controlling qB.
- Added an additive/reversible migration, SQLite recovery tests, and a real PostgreSQL row-lock
  concurrency test ensuring that only one live scheduler owner can mutate qB.
- The executable is not activated in Compose before the isolated qB service exists. Worker effect
  handlers, qB-to-domain state synchronization, multi-account routing, storage, API, frontend,
  V1 behavior, and `master` remain outside this PR.

## V2-13B — Worker effects and qB state synchronization

- Added concrete `ADD_TORRENT` and `SYNC_TORRENT` handlers backed by the existing
  C411/NewGreedy and qBittorrent V2 gateways when the complete integration configuration is
  present. The current qB-free foundation Compose keeps its inert worker until V2-29.
- Added a private payload spool that validates uploaded metainfo and removes every tracker
  credential before the first durable write. Only an opaque storage-key filename and a
  secret-free `/announce` URL remain on disk; symlink payloads are refused.
- The infrastructure passkey is injected only in worker memory immediately before the bounded
  qB request. Successful or reconciled adds remove the staged payload; cleanup failure is safe
  and does not convert an accepted qB effect into a duplicate retry.
- Added replay-safe domain transitions for managed torrents and all active requests, including
  durable retry timestamps aligned with job backoff and terminal `ERROR` after exhausted or
  permanent failures.
- Added an owned, bounded qB state snapshot API and normalized qB download/upload/stopped/error
  states into `DOWNLOADING`, `PAUSED`, `READY`, or `ERROR` without exposing raw responses.
- Added a periodic PostgreSQL-authoritative sync enqueuer using the dynamic sync interval and a
  partial unique index, so at most one queued/running sync exists per physical torrent and an
  integration outage cannot grow the queue without bound.
- Added an additive/reversible migration plus payload security, handler transition, retry,
  coalescing, qB ownership/state, and existing worker/scheduler regression tests.
- Multi-account routing, shared content storage, API ingestion, frontend, Compose activation,
  V1 behavior, and `master` remain outside this PR.

## V2-13C — Multi-account tracker/qB routing

- Added a bounded, strict deployment-only account registry whose credentials are held as
  `SecretStr`; PostgreSQL continues to store only opaque tracker and qB account UUIDs.
- Each route pairs exactly one tracker account with one qB instance. Duplicate, zero, partial,
  public-service, malformed, oversized, or unknown routes fail with bounded safe codes that do
  not echo configuration input.
- New torrents select a route deterministically from their canonical infohash after sorting by
  opaque UUIDs. Assignment is protected by the existing row lock, replay is stable, and removing
  an assigned route fails closed rather than silently moving a live torrent to another account.
- Worker add and sync effects now resolve the persisted route before any integration call. A
  successful C411/NewGreedy/qB add records one idempotent, secret-safe proxy health activity for
  the assigned tracker reference.
- Scheduler control identities now carry the opaque qB account reference. The deployment router
  validates the complete bounded set before grouping idempotent controls by qB instance and
  aggregates their safe results; a failure leaves the scheduler generation unapplied.
- Added deterministic assignment, removal, concurrent PostgreSQL assignment, secret handling,
  internal-origin, batch bound, per-account control, worker activity, scheduler plan, and existing
  tracker/qB regression tests.
- No database migration, encrypted secret database, Compose activation, storage, API, frontend,
  V1 behavior, or `master` change is included in this PR.

## Roadmap amendment — local macOS validation

- Added V2-18A after the shared-storage, request-API, and first V2 UI tasks so a complete local
  torrent-request smoke path is exercised before the production-oriented Rise2 composition.
- Required a developer-only Compose profile with active API, worker, scheduler, PostgreSQL,
  Redis and qBittorrent plus a controlled tracker integration that needs no real passkey.
- Made clean-clone startup, idempotent migrations/bootstrap, worker crash recovery, private
  service networks, isolated cleanup, and UI-visible durable state explicit exit criteria.
- Required evidence on Docker Desktop for both Apple Silicon and Intel Macs without assuming
  Linux UID/GID `1000` or `/srv` host paths.
- Kept ingress, monitoring, production secrets and V1 import in V2-28 through V2-31; V2-29 now
  depends on the successful local validation instead of discovering integration gaps during
  the Rise2 deployment task.

## V2-14 — Shared physical storage

- Added a descriptor-based `SharedContentStore` that derives exactly one physical directory
  from the server-owned storage UUID under `content/<opaque-uuid-hex>`.
- Content and managed directories are opened with `O_DIRECTORY`, `O_NOFOLLOW`, and
  `O_CLOEXEC` when supported; symlinked roots and per-torrent collisions fail closed.
- The store exposes validated directory descriptors rather than absolute host paths and never
  accepts a user-provided path or performs a recursive scan.
- The worker now prepares and validates the shared directory after metainfo validation but
  before the first qBittorrent effect. Unsafe storage becomes the bounded permanent error
  `shared_storage_invalid`, and qBittorrent is not called.
- Empty-directory removal is descriptor-anchored and refuses non-empty content, preserving
  downloaded data for the later lifecycle task.
- Production configuration now requires both WOS and qBittorrent to address the shared mount
  as `/data`; V1 paths, APIs, models, migrations, and `master` are unchanged.
- V2-14 is implemented on `feat/v2-shared-physical-storage` from the merged PR #69 commit.

## V2-15 — Logical quotas and disk-pressure admission

- Added additive `StorageLedger` and per-user `UserStorageUsage` counters with non-negative
  database constraints and migration backfill from existing managed torrents and active rights.
- Every deduplicated request now updates logical and physical counters in the same transaction;
  idempotent replay adds nothing, and shared content counts physically once but logically once
  for each owner.
- Added typed admission policy snapshots sourced from the existing PostgreSQL storage options,
  with user and managed-byte quotas plus projected free-byte/free-percent disk reserves.
- Disk pressure is normalized to `NORMAL`, `WARNING`, or `CRITICAL`. Warning admits work;
  critical blocks only new physical content, so an already-downloaded shared torrent can still
  receive another authorized logical reference.
- The shared store obtains capacity through `fstatvfs` on a validated data-root descriptor; no
  web request performs a recursive filesystem scan.
- Added a bounded SQL reconciler that repairs at most 500 user counters per call, recomputes the
  physical ledger from PostgreSQL, and records a safe disk snapshot and pressure state.
- Quota and pressure failures expose only `user_quota_exceeded`, `managed_quota_exceeded`, or
  `disk_pressure_critical`; no path, option secret, or filesystem detail is returned.
- V2-15 is implemented on `feat/v2-storage-quotas-pressure` from the merged V2-14 commit.

## V2-16 — Versioned TorrentFile manifests

- Extended strict metainfo parsing to emit one canonical physical relative path per file. A
  single-file torrent maps to its validated name; a multi-file torrent is rooted under its
  validated torrent name and rejects duplicate, non-UTF-8, traversal, slash, NUL, or oversized
  components before persistence.
- Added deterministic SHA-256 manifest checksums, monotonic versions, file count, and total size
  metadata to `ManagedTorrent`, with additive constraints and a reversible migration.
- Manifest replacement locks the managed torrent, verifies contiguous indexes and the exact
  canonical total size, deletes/reinserts rows in bounded batches, and is a no-op on exact replay.
- The worker persists the sanitized manifest before shared-directory preparation and before the
  qBittorrent effect; replay therefore reconstructs the same rows without duplicating versions.
- Added manifest pagination capped at 500 rows, ordered by file index, with explicit stale-version
  and incomplete-row detection for future resumable clients.
- No content-directory scan, user path, tracker URL, passkey, API response, frontend behavior,
  V1 behavior, or `master` change is included in V2-16.
- V2-16 is implemented on `feat/v2-torrent-manifests` from the merged V2-15 commit.

## V2-17 — V2 torrent request API

- Added a separate authenticated `/api/v2/torrents` contract without changing the V1 API.
- Multipart submission validates the filename and bounded payload, removes uploaded tracker
  credentials in memory, and never accepts a client path, owner, qB option, or account ref.
- Each accepted new physical torrent writes its managed row, owner request, and idempotent
  `ADD_TORRENT` job in one PostgreSQL transaction, then signals Redis only after commit.
- A secret-free staged payload is removed if the SQL transaction fails; repeated active
  submissions return the existing request and never create another physical torrent or job.
- Admission applies PostgreSQL-authoritative upload/size/active limits, logical and managed
  quotas, and a descriptor-derived disk-pressure snapshot with bounded API error codes.
- Added an owner-filtered, bounded, paginated PostgreSQL listing. It does not query qBittorrent
  and exposes no infohash, storage key, path, tracker URL, account reference, or secret.
- Added durable normalized progress on `ManagedTorrent`; worker synchronization persists it
  for the API and the upcoming interface, with an additive reversible migration.
- V2-17 is implemented on `feat/v2-torrent-request-api` from the merged V2-16 commit.

## V2-18 — My downloads V2 interface

- Reconnected the personal downloads page to the authenticated `/api/v2/torrents` contract;
  the browser reads durable PostgreSQL states and never receives qBittorrent identifiers.
- Added the exact durable request states, normalized progress, bounded error presentation,
  idempotent-submission feedback, and storage-pressure warning feedback.
- Added server-side pagination with a fixed 10-row page, previous/next controls, total count,
  and deterministic refresh of the current page.
- Replaced unbounded cards with a fixed-layout table contained in its own scroll region; long
  names and errors use ellipsis while their full torrent name remains available as a title.
- Upload and refresh controls remain on one action line, and every row keeps its bounded action
  in a non-wrapping cell.
- Kept CSP compatibility by using React events and CSS classes only: no inline style, script,
  dynamic HTML, server path, tracker URL, storage key, or infohash was added to the UI.
- Added focused interaction, pagination, durable-state, long-name, error, keyboard, and axe
  accessibility regression coverage.
- V2-18 is implemented on `feat/v2-my-downloads-ui` from the merged V2-17 commit.

## V2-18A — Reproducible local macOS validation

- Added a complete developer Compose override with API/frontend, durable worker, scheduler,
  PostgreSQL, Redis, pinned multi-architecture qBittorrent, and a development-only controlled
  NewGreedy health fixture.
- The local profile uses the isolated `world-of-seeds-v2-local` project, private backend
  network, and four named volumes. Only the API/frontend port is published to host loopback;
  it has no `/srv` bind mount, Docker socket, host UID/GID, or forced architecture.
- API startup applies Alembic migrations idempotently. A development-only seed helper creates
  or rotates a disposable non-administrator account and initializes PostgreSQL-authoritative
  V2 options without versioning a real identity or credential.
- Added a single launcher for validated build/start, status, smoke, and project-scoped cleanup.
  Cleanup removes only local V2 containers, networks, and volumes.
- Added an end-to-end smoke scenario that stops the worker, uploads a generated C411 fixture,
  proves the durable queued job, resumes processing, verifies the authenticated API/UI state,
  exact qB infohash presence, applied scheduler generation, and no duplicate add after worker
  restart.
- Added a normalized Compose policy validator with regression tests for the service set,
  loopback/private exposure, fixed container identity, named storage, multi-arch qB pin, and
  forbidden bind/socket paths.
- Added the macOS clean-clone guide and explicit Docker Desktop checklist for Apple Silicon
  `arm64` and Intel `amd64`; those two host checks remain manual while Linux CI runs the same
  profile and smoke scenario.
- V2-18A is implemented on `feat/v2-local-macos-validation` from the merged V2-18 commit.

## V2-19 — Per-file download API

- Added authenticated owner-only `GET` and `HEAD` download endpoints addressed exclusively by
  the durable request and manifest-file UUIDs; no client path, storage key, infohash, or qB
  identifier is accepted or exposed.
- A file is downloadable only while both its owner request and managed torrent are `READY`, with
  the persisted manifest version/checksum and file size treated as authoritative.
- Physical traversal starts from the opaque shared-content descriptor and opens every component
  with no-follow semantics. Missing, resized, non-regular, or symlinked content fails closed.
- Added strong manifest-derived ETags, `Last-Modified`, `If-Range`, single HTTP byte ranges,
  `HEAD`, safe content disposition, bounded chunks, and explicit `416` responses.
- Added durable SQL `DownloadLease` rows with transactional per-user concurrency enforcement,
  expired-lease reclamation, periodic renewal during slow/back-pressured streams, and guaranteed
  release on completion, disconnect, invalid range, or open failure.
- Added configurable per-user/global byte pacing using the existing PostgreSQL-authoritative
  download options. The limiter is process-local, matching the single-API-process V2 topology.
- Added focused ownership, readiness, Range/ETag, mutation, symlink, lease, and rate-limit tests.
- V2-19 is implemented on `feat/v2-file-download-api` from the merged V2-18A commit.

## V2-20 — Recursive browser download

- Added an owner-only paginated download-manifest endpoint that locks the ready managed torrent
  while building each page and derives an opaque snapshot identifier from the request UUID,
  manifest checksum, and manifest version.
- Every browser-managed file request presents that snapshot identifier. A changed request or
  manifest fails with the bounded `download_snapshot_changed` contract before a lease or file is
  opened.
- Added the primary File System Access API workflow to the ready-download action: the browser
  selects a local directory, safely recreates manifest subdirectories, and streams each file
  directly without preparing a server archive or buffering the complete content.
- Transfers use at most two concurrent HTTP streams, validate the manifest-version and resumed
  `Content-Range` response, write incrementally, and expose aggregate byte/file progress.
- Pause aborts current HTTP streams after retaining committed local offsets; resume reopens those
  files with `keepExistingData` and requests only the remaining Range. Cancel aborts every active
  stream and leaves the explicit partial local files under user control.
- Unsupported browsers receive an explicit bounded notice. Individual-file and small-folder ZIP
  fallbacks remain intentionally reserved for V2-21.
- Added focused snapshot API, ownership/change, directory recreation, concurrency, Range resume,
  React interaction, CSP, and accessibility coverage.
- V2-20 is implemented on `feat/v2-recursive-browser-download` from the merged V2-19 commit.

## V2-21 — Compatible download fallbacks

- Extended stable download snapshots with a server-authoritative ZIP-availability flag derived
  from the PostgreSQL archive-size option and a bounded 50,000-entry ceiling.
- Added snapshot-bound individual-file URLs for browsers without File System Access API; the UI
  paginates at 50 links and never renders an unbounded manifest page at once.
- Added a streamed ZIP endpoint available only to the request owner while content remains ready
  and unchanged. It reads the existing manifest rows rather than recursively scanning storage.
- ZIP entries use `ZIP_STORED`, descriptor-safe no-follow file opens, bounded chunks, Zip64, one
  process-wide archive slot, the existing per-user download concurrency/rate controls, and a
  durable renewable lease. No temporary archive or complete in-memory ZIP is created.
- Oversized/many-file content omits the ZIP action and is rejected server-side with the bounded
  `torrent_archive_too_large` contract; users retain the individual-file fallback.
- Added focused individual-link, snapshot-query, streamed-ZIP content/mode, lease cleanup, React
  compatibility-mode, CSP, pagination, and accessibility coverage.
- V2-21 is implemented on `feat/v2-download-fallbacks` from the merged V2-20 commit.

## V2-22 — Shared-content lifecycle

- Added an owner-only CSRF-protected cancellation endpoint. Cancelling one shared reference
  decrements only that user's logical usage; the physical torrent remains available while any
  other active request exists.
- Cancelling the final reference moves the managed torrent to `PURGE_PENDING`, records the
  configurable retention deadline, cancels obsolete active effects, and queues one durable,
  generation-keyed `PURGE_TORRENT` job.
- A new request during retention atomically cancels the pending purge and restores the existing
  physical copy. A request during `PURGING` receives a bounded conflict; a fully purged torrent
  is safely reactivated through a new staged add while retaining the canonical managed row.
- The worker rechecks active requests, retention, and renewable download leases before deletion,
  marks the lifecycle `PURGING` across external effects, and retries qBittorrent/filesystem
  ambiguity idempotently.
- qBittorrent deletion validates the WOS category, opaque save path, and identity tags before
  requesting `deleteFiles=true`; external torrents are never mutated. The shared-content purge
  is descriptor-based, bounded, recursive, symlink-safe, and idempotent.
- Successful purge clears the SQL manifest, decrements physical managed accounting, and preserves
  cancelled request history. Lease acquisition/renewal now locks and revalidates ready ownership
  so a stream cannot renew after lifecycle revocation.
- Added an additive reversible lifecycle migration and focused cancellation, shared-owner,
  retention/reactivation, race, lease, qB ownership, filesystem, API, and worker tests.
- V2-22 is implemented on `feat/v2-shared-content-lifecycle` from the merged V2-21 commit.

## V2-23 — Common accessible React confirmations and toasts

- Replaced the remaining imperative DOM alert implementation with one application-level React
  feedback provider. Confirmations are queued, focus-trapped, Escape/backdrop cancellable, restore
  focus to their trigger, and focus the safe action first for destructive operations.
- Added a bounded internal toast region with polite/assertive live semantics, explicit dismissal,
  automatic expiry, at most three visible messages, and no inline style or dynamic HTML.
- Migrated file creation/mutation, personal trash restore/permanent deletion, and their success or
  error feedback to the common React contract. Permanent deletion remains explicitly confirmed.
- Exposed V2-22 cancellation in the downloads table for every non-terminal request, with a
  destructive confirmation, per-row busy state, bounded API errors, success toast, and refresh of
  the durable SQL state.
- Extended the reproducible local profile smoke beyond upload/worker/scheduler checks: it creates a
  controlled READY file, validates the authenticated manifest, one-byte HTTP Range response,
  streamed ZIP fallback, CSRF cancellation, and retained `PURGE_PENDING|CANCELLED|QUEUED` state.
- Added focused confirmation queue/focus/CSP/axe coverage, cancellation interaction coverage, and
  local-smoke contract assertions.
- V2-23 is implemented on `feat/v2-common-feedback` from the merged V2-22 commit.

## V2-24 — Complete responsive behavior

- Added explicit portrait and landscape layout contracts through CSS media queries, so an
  orientation change applies without reloading React state.
- Converted the V2 downloads table to labelled cards on mobile/tablet portrait while preserving
  the bounded horizontally scrollable table in landscape and desktop layouts.
- Made administration navigation horizontally scrollable at constrained widths and prevented
  tabs from shrinking below their touch target.
- Bounded common and mutation dialogs against the dynamic viewport, included safe-area insets,
  and converted destructive confirmation actions to a mobile bottom-sheet layout.
- Kept internal toasts within the mobile viewport and existing file/browser actions usable at
  the supported 320, 375, 390, and 430 pixel widths.
- Added source-policy regression tests for breakpoints, orientation rules, safe areas, labelled
  table cards, and the absence of reload-driven layout behavior.
- V2-24 is implemented on `feat/v2-responsive` from the merged V2-23 commit.

## V2-25 — Central administration

- Added an admin-only V2 overview backed by the PostgreSQL option registry, scheduler singleton,
  storage ledger, logical usage counters, and immutable option audit records.
- Exposed typed/versioned safe options grouped by category while keeping secrets, infrastructure
  URLs, paths, ports, and credentials outside the response and editable contract.
- Added CSRF-protected option updates through `PostgresOptionsRegistry`; the authenticated admin
  is persisted as audit actor and validation remains atomic and cross-option aware.
- Exposed scheduler desired/applied generations, synchronization and lease state, rounds, managed
  and logical bytes, disk pressure, and central/user quotas without filesystem or qB scans.
- Migrated the administration settings screen to the V2 source of truth and added bounded status
  cards plus the ten most recent audit entries while preserving restart handling and accessibility.
- Added focused authorization, CSRF, audit attribution, scheduler/storage, TypeScript, React, and
  accessibility regression coverage.
- V2-25 is implemented on `feat/v2-central-admin` from the merged V2-24 commit.

## V2-26 — Administrative reconciliation

- Added an admin-only bounded reconciliation report across PostgreSQL managed torrents, the
  qBittorrent inventory, and opaque top-level shared-content directories.
- qB inventory is capped at 200 records and classifies WOS identity from category, save path, and
  opaque tags. External torrents are counted and reported read-only; no control/delete method is
  reachable from the reconciliation path.
- Shared storage inventory opens the content root by descriptor, never follows symlinks, never
  descends recursively, and reports unsafe entries or truncation instead of mutating them.
- Classified missing qB/storage content, identity mismatch, orphan WOS qB records, orphan storage,
  unavailable integrations, and unsafe entries with bounded severity and action codes.
- Added the report to the admin storage screen with explicit external read-only wording and a
  visible truncation notice.
- Added focused authorization, degradation, mismatch, external-read-only, symlink, bounds, React,
  CSP, and accessibility coverage.
- V2-26 is implemented on `feat/v2-admin-reconciliation` from the merged V2-25 commit.

## V2-27 — Application metrics

- Added a Prometheus text endpoint and in-process API middleware with bounded method, route-template,
  status-class, and fixed-duration-bucket dimensions.
- Exposed durable job counts by fixed state, oldest queue age, retry attempts, bounded recent job
  duration, scheduler desired/applied generations, and active download leases.
- Exposed database scrape latency, Redis and qBittorrent health/latency, shared storage byte gauges,
  and one-hot pressure state without user, filename, infohash, tracker, or secret labels.
- Kept PostgreSQL authoritative: scrape-time metrics aggregate existing rows and never mutate jobs,
  scheduler state, leases, or storage accounting.
- Extended the complete local-profile smoke to assert the required metric families and reject the
  fixture name or infohash in exposition output.
- Added focused route-cardinality, operational-family, identifier-redaction, health, job, Redis,
  local-helper, Ruff, mypy, and syntax coverage.
- V2-27 is implemented on `feat/v2-application-metrics` from the merged V2-26 commit.

## V2-28 — Monitoring stack

- Added an opt-in Compose monitoring profile with pinned Prometheus, Grafana, node-exporter, and
  cAdvisor images, dedicated named data volumes, an internal monitoring network, and explicit
  Prometheus retention.
- Kept Prometheus, node-exporter, and cAdvisor without host ports; Grafana alone binds to host
  loopback with anonymous access and self-registration disabled.
- Prometheus is the sole bridge to the private application backend and scrapes the secret-safe V2
  metrics endpoint; monitoring containers do not mount application storage, qB data, or integration
  credentials.
- Provisioned a versioned Grafana datasource and operational dashboard for API traffic/latency,
  durable jobs, queue age, storage, dependencies, host CPU/RAM, and container CPU.
- Added alert rules for missing targets, blocked/failed jobs, storage pressure, qBittorrent, Redis,
  API/server errors, PostgreSQL/API availability, host CPU/RAM/disk/I/O pressure, and repeated
  container restarts.
- Added normalized Compose-policy regression coverage and a real monitoring smoke that requires all
  four Prometheus targets, all alert rules, Grafana health, authenticated search, and the provisioned
  dashboard.
- Extended the macOS helper and checklist with `monitoring-up` and `monitoring-smoke`; Docker Desktop
  metrics intentionally describe its Linux VM and the profile remains disposable and V1-isolated.
- V2-28 was squash-merged through PR `#85` into `develop_V2` at
  `5de4cd0282fd1397c810a0a693e455cada4c6c9e`.

## V2-28A — Scheduler authority and active download slots

- Made the PostgreSQL-backed scheduler the sole download authority: qBittorrent additions are
  created paused and become eligible only through a scheduler control generation.
- Set the global active-download default to two while retaining the typed PostgreSQL option and
  its existing 1-to-200 bound; the value is reloaded on every scheduler cycle without restart.
- Scheduler cost now uses bounded remaining bytes derived from total size and progress, including
  safe behavior for unknown, zero, complete, non-finite, and inconsistent values.
- READY torrents are excluded from download-slot controls and their desired download fields are
  cleared transactionally when qBittorrent reports completion, so completed torrents may continue
  seeding without occupying a download slot or receiving a scheduler stop.
- Added regression coverage for initially paused additions, one/two dynamic slots, ten queued
  torrents, progress at 99 percent, restart recovery, READY seeding, and malformed progress input.
- V2-28A was squash-merged through PR `#87` into `develop_V2` at
  `055cc30cdbdb68292349fdbae3672880fb636de5`.

## V2-28B — Durable anti-stall and cooldown

- Added qBittorrent's bounded cumulative downloaded-byte observation to the existing safe snapshot;
  no speed-only signal can classify a torrent as stalled.
- Persisted only the durable scheduler observations required for restart safety: last useful
  progress time, last downloaded bytes, stall count, and scheduler-specific next eligibility.
- A selected torrent with no useful byte or progress delta for 60 seconds now releases its slot
  without deleting partial data; repeated stalls use centralized 3, 5, then 10 minute cooldowns.
- Initial scheduler starts and post-cooldown resumes open a fresh observation window, while any real
  progress clears the cooldown and stall count. Repeated observations during a cooldown are
  idempotent and do not cause pause/resume thrashing.
- Added a reversible PostgreSQL migration plus regression coverage for 99-percent stalls, very slow
  progress, repeated stalls, recovery, scheduler restart during cooldown, 100 eligible torrents for
  two slots, and an empty eligible set.
- V2-28B was squash-merged through PR `#88` into `develop_V2` at
  `fe0c92abb22050a105dd1afb1cf37b0fbc25faa6`.

## V2-28C — Shared-torrent fairness and bounded backlog

- A shared physical torrent is now represented once with every active owner as a possible charged
  beneficiary. The weighted-fair selector removes the physical candidate from every owner queue
  after one selection, so scheduling can never create or charge a duplicate physical torrent.
- The persisted scheduler user cursor rotates the charged owner deterministically across cycles and
  process restarts. Per-user caps, deficits, and distinct future account weights are applied to the
  chosen beneficiary, with another owner remaining available when one cap is saturated.
- Replaced the fail-closed 200-row query with a durable circular `(created_at, id)` scan cursor.
  Every currently active download remains in the control set; remaining capacity up to 200 advances
  through queued and cooling torrents without losing the scheduler lease or generation semantics.
- Added a reversible PostgreSQL migration and boundary coverage for 199, 200, 201, 500, and 1,000
  torrents, including mixed sizes and cooldowns, plus shared-owner caps, weights, restart stability,
  deterministic order, and single-physical-selection guarantees.
- V2-28C was squash-merged through PR `#90` into `develop_V2` at
  `ca423f29a7f8dbf9fcadbe6a9d6ba495c3ca45a4`.

## V2-28D — Realtime state without full polling

- Added a closed, secret-safe realtime event contract containing only an allowed transition type,
  opaque request UUID, and timestamp; channels are namespaced per authenticated user and fan out to
  every open browser tab.
- Worker, scheduler, creation, cancellation, ready, stall, resume, pause, start, and failure paths
  publish best-effort Redis events only after their PostgreSQL transaction has committed. Redis
  failure never changes the durable outcome.
- Added an authenticated API WebSocket whose short authentication session is closed before accept;
  idle connections wait only on Redis and send a network heartbeat without retaining or polling SQL.
- Removed the ten-second downloads-page polling loop. The page performs an initial authoritative
  GET, refreshes on significant events, resynchronizes after reconnect or `resync_required`, and
  retains every explicit `Actualiser` action.
- Added bounded fan-out coverage at 10, 25, 50, and 100 connections, multi-tab delivery, Redis loss,
  reconnect/resync behavior, closed payload validation, and domain publication regression tests.
- V2-28D was squash-merged through PR `#91` into `develop_V2` at
  `25c1aab07afab6004ddedd72688df4f6f167f6d4`.

## V2-28E — Scalable recursive transfer and integrity-safe resume

- Replaced the recursive browser workflow's complete-manifest preload with a first-page request and
  progressive snapshot-bound pagination. File transfer starts from that first page while at most one
  following page is fetched, and the in-memory queue never exceeds two 500-entry pages.
- Removed the growing completed-file set and releases partial-offset state after each completed file,
  keeping controller memory bounded for synthetic 50,000-file manifests while retaining two file
  streams and one manifest-page request as the maximum concurrent work.
- Resume now checks the actual local file size when supported and may only reduce, never advance, the
  last controller-confirmed offset. HTTP `Content-Range` is parsed exactly before resumed writes.
- Local offsets advance only after `writer.write` resolves. Failed close, seek, permission, removed
  device, disk-full, abort, and cancel paths close or abort their resources and roll back to a proven
  durable offset so retries cannot skip unwritten bytes.
- Every progressive page must preserve snapshot ID, version, totals, page size, and expected offset;
  changed or incomplete manifests fail closed. Cancel aborts both file and manifest requests.
- Added focused first-page, bounded-prefetch, 50,000-file, local-size reconciliation, write/close
  failure, pause/resume, cancel, snapshot-change, and inconsistent-Range tests.
- V2-28E was squash-merged through PR `#92` into `develop_V2` at
  `b03fa4e9ee51909a812cde459545b00039a5a281`.

## V2-28F — PostgreSQL, metrics, and reconciliation performance

- Audited the periodic frontend refresh, scheduler, qB sync, metrics, health, shared-storage
  reconciliation, HTTP/ZIP streams, realtime WebSocket, filesystem reconciliation, and external
  monitoring paths against their actual transaction boundaries. Scheduler/qB operations, streams,
  and idle realtime waits already release SQL resources before slow work and required no change.
- Replaced the storage reconciler's per-user aggregate and usage-row lookup loop with one grouped
  logical-byte query plus one bounded usage-row fetch. Query count is now constant for a batch and
  is regression-tested with 10, 100, and 500 accounts.
- Added a reconstructible, secret-free 15-second operational metrics snapshot. Concurrent scrapes
  coalesce behind one refresh and reuse the bounded last-500-job audit instead of repeating it on
  every Prometheus scrape; fixed state labels remain low-cardinality.
- Metrics and public health explicitly release their SQL transaction before Redis, qBittorrent, or
  NewGreedy probes. Admin reconciliation commits its bounded, detached database inventory before
  filesystem traversal and qBittorrent inventory calls.
- No schema migration or durable architecture change was required. PostgreSQL remains authoritative;
  cache loss only causes a bounded snapshot rebuild.
- V2-28F was squash-merged through PR `#93` into `develop_V2` at
  `8ba66965b4ad778fcd83dca3b274a56ae44784e5`.

## V2-28G — Runtime hardening and operational recovery

- The explicit V2 runtime profile now fails before startup unless secure cookies, a non-wildcard public host,
  non-demo PostgreSQL credentials on an internal service, Redis, fixed container paths, and the
  single-process API topology are all explicit and valid.
- Production workers require one unambiguous deployment integration registry and reject demo
  tracker/qB secrets with bounded codes that never interpolate credentials. Development/test
  workers may intentionally remain integration-free.
- Compose and its validators enforce exactly one API process. A deterministic concurrency test
  exercises the existing process-local download limiter with 100 distinct accounts; no distributed
  Redis semaphore is introduced without load evidence. The default V1 profile retains its existing
  production startup contract.
- `managed_bytes` is documented as declared capacity reserved by all non-purged managed torrents,
  distinct from filesystem-observed `disk_total_bytes` and `disk_free_bytes`.
- An exact missing qB torrent is distinct from a transient lookup failure. Worker sync makes that
  absence a permanent bounded failure with torrent state `ERROR`, preventing endless retries and UI
  ghosts after a qB reset.
- The admin recovery API verifies exact qB and shared-storage presence before mutating SQL. It can
  cancel all active orphan rights without touching physical data, or purge manifests/accounting
  metadata only when both checks prove physical content absent. External qB torrents and ambiguous
  storage remain read-only/manual-review conditions. A bounded SQL snapshot is revalidated under
  the torrent lock after external probes; any new request or job transition aborts recovery, and
  metadata purge refuses every active worker job.
- No schema migration, automatic qB removal, recursive filesystem deletion, or V2-28H monitoring
  work is included.
- V2-28G was squash-merged through PR `#94` into `develop_V2` at
  `d078c40832ea3c310186f7574dd6532ef2b6e189`.

## V2-28H — Portable Linux/macOS monitoring

- Kept the Linux and Rise2 node-exporter root mount on read-only `rslave`, preserving dynamic host
  mount visibility on the production target.
- Added a versioned Docker Desktop overlay that explicitly replaces `rslave` on `/host/root` with
  private `rprivate` propagation supported by macOS Docker Desktop; no ignored local file or
  privileged workaround is required.
- `local_v2.sh` and the monitoring smoke select the platform contract automatically on macOS or
  Linux, with a bounded `WOS_V2_MONITORING_PLATFORM` override for policy/CI validation.
- Compose policy tests validate both normalized contracts and reject a missing Linux `rslave` or a
  Docker Desktop root bind that was not overridden to `rprivate`.
- Local documentation now states that node-exporter and cAdvisor on Intel/Apple Silicon macOS
  primarily observe Docker Desktop's Linux VM rather than the physical Mac host.
- No Rise2 composition, V1 change, monitoring privilege increase, image change, or application
  runtime behavior is included.
- V2-28H was squash-merged through PR `#95` into `develop_V2` at
  `6a0db1e9964567792460575ae97701d4254f1c2d` after CI run `33216607469` passed.

## V2-29 — Complete isolated Rise2 Compose

- Added the production-only `world-of-seeds-v2-rise2` stack with Caddy ingress, one API process,
  a one-shot migration, replicated durable workers, the singleton scheduler, PostgreSQL, Redis,
  qBittorrent, NewGreedy, Prometheus, Grafana, node-exporter, and cAdvisor.
- Only Caddy publishes host ports 80/443. Backend, torrent, monitoring, and monitoring-edge are
  internal and dedicated to V2; no V1 network, volume, profile, path, secret, or data is mounted.
- WOS and NewGreedy images must be immutable digests. Stateful infrastructure uses pinned images
  and dedicated named volumes; shared media uses only the explicit `/srv/world-of-seeds-v2` host
  boundary.
- The NewGreedy config contract is owner-write/group-read mode `0640`, mounted read-only under the
  exact unprivileged UID/GID with all capabilities dropped. A host preflight verifies metadata and
  executes `test -r /app/config.ini` inside the real container identity.
- qBittorrent receives a one-time `0600` authenticated bootstrap config in its dedicated volume;
  neither its WebUI nor NewGreedy is published to the host.
- Added normalized Compose policy enforcement for immutable images, complete service/network/
  volume sets, single-process API, runtime hardening, internal-only services, and V1 isolation.
- No deployment, DNS mutation, V1 import, backup implementation, or data migration is performed.

## Documentation hardening phase after V2-28 (Phase 0)

- The main V2 profile was validated successfully on Docker Desktop macOS with
  `scripts/local_v2.sh up`; API, PostgreSQL, Redis, worker, scheduler, qBittorrent, and the
  NewGreedy fixture were operational.
- `GET /api/v1/health/ready` returned HTTP 200 with version `2.0.0-alpha.0`.
- `scripts/local_v2.sh smoke` completed with a durable `COMPLETED` job, exactly one qBittorrent
  match, zero worker-restart duplicates, scheduler application, HTTP Range, ZIP fallback,
  retained cancellation, secret-safe metrics, and UI bundle checks.
- The optional monitoring profile is not portable to the tested Docker Desktop macOS setup:
  `node-exporter` bind-mounts `/` with `propagation: rslave`, causing `monitoring-up` to fail with
  `path / is mounted on / but it is not a shared or slave mount`.
- At the Phase 0 base commit `5de4cd0282fd1397c810a0a693e455cada4c6c9e`, targeted code
  verification confirmed the hardening backlog: qB additions were not initially stopped; the
  scheduler used total size, selected the earliest request as beneficiary, and failed closed above
  200 controls; no durable anti-stall cooldown existed; the downloads page polled every ten
  seconds; recursive download fetched all manifest pages before starting; storage reconciliation
  performed per-user aggregate queries; production workers could run without the required
  integration registry; and reconciliation reported qB/DB orphans without safe recovery actions.
- The roadmap now inserts V2-28A through V2-28H before Rise2 composition and V2-32A before the
  external pilot. No functional code or migration is included in this documentation phase.
- Rise2 must explicitly validate NewGreedy's effective read access to its read-only `config.ini`.
  The V1 incident showed that UID 0 with all capabilities dropped cannot read an application-owned
  mode `0600` file merely because it is root inside the container.
- Phase 0 was squash-merged through PR `#86` into `develop_V2` at
  `eba8d6b577ace5130b1772455c5fe9d586edbf14`; it changed documentation only and did not start
  V2-28A.

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
- PR #64 (V2-12) review and GitHub CI run `32524918074`: PASS; squash-merged into
  `develop_V2` at `2b870a52c6c4aeb834e1f883f7bb2675b277562d`.
- V2-13 targeted qB control, scheduler, database-option, and V2 gateway tests: PASS, 45 tests.
- V2-13 complete backend suite: PASS, 315 tests with 4 service-backed tests deferred to CI.
- V2-13 full backend Ruff lint/format and mypy: PASS.
- V2-13 `git diff --check`: PASS.
- PR #65 (V2-13) review and GitHub CI run `32526640405`: PASS; squash-merged into
  `develop_V2` at `5a1bab12be33681ccb6ad964dda18c40a57482c5`.
- V2-13A targeted scheduler runtime, policy, qB control, database-option, gateway, and model
  tests: PASS, 60 tests with 1 PostgreSQL concurrency test deferred to CI.
- V2-13A complete backend suite: PASS, 320 tests with 5 service-backed tests deferred to CI.
- V2-13A full backend Ruff lint/format and mypy: PASS.
- V2-13A PostgreSQL upgrade/downgrade SQL generation and `git diff --check`: PASS.
- PR #66 (V2-13A) review and GitHub CI run `32529089374`: PASS; squash-merged into
  `develop_V2` at `782a7ff41817481a7bf1f929064380560c5cd6b3`.
- V2-13B targeted worker effects, payload staging, job runtime, qB/C411 gateways, and scheduler
  runtime tests: PASS, 57 tests with 3 service-backed tests deferred to CI.
- V2-13B complete backend suite: PASS, 327 tests with 5 service-backed tests deferred to CI.
- V2-13B full backend Ruff lint/format and mypy: PASS.
- V2-13B PostgreSQL upgrade/downgrade SQL generation and `git diff --check`: PASS.
- PR #67 (V2-13B) review and GitHub CI run `32530495109`: PASS; squash-merged into
  `develop_V2` at `2514ddba888680df2702686d333231741ee64747`.
- V2-13C targeted account routing, worker effects, tracker activity, scheduler runtime/control,
  and qB gateway tests: PASS, 31 tests with 2 PostgreSQL-backed tests deferred to CI.
- V2-13C complete backend suite: PASS, 335 tests with 6 service-backed tests deferred to CI.
- V2-13C full backend Ruff lint/format and mypy: PASS.
- V2-13C `git diff --check`: PASS.
- PR #68 (V2-13C) review and GitHub CI run `32531450774`: PASS; squash-merged into
  `develop_V2` at `86bda53ce4a8d4982b1ec2173f07c60f69017b59`.
- V2-14 targeted storage, worker-effect, qB gateway, account-routing, and configuration tests:
  PASS, 56 passed with 1 PostgreSQL-backed test deferred to CI.
- V2-14 full backend suite: PASS, 335 tests with 6 service-backed tests deferred to CI.
- V2-14 full backend Ruff lint/format and mypy: PASS.
- V2-14 `git diff --check` and targeted secret scan: PASS.
- PR #70 (V2-14) review and GitHub CI run `32533226340`: PASS; squash-merged into
  `develop_V2` at `4a72074536b2496723d2fa9af5286744f64babde`.
- V2-15 targeted accounting, deduplication, shared-storage, option, and model tests: PASS,
  49 passed with 1 PostgreSQL concurrency test deferred to CI.
- V2-15 full backend suite: PASS, 342 tests with 6 service-backed tests deferred to CI.
- V2-15 full backend Ruff lint/format and mypy: PASS.
- V2-15 PostgreSQL upgrade/downgrade SQL generation, `git diff --check`, and targeted secret
  scan: PASS.
- PR #71 (V2-15) review and GitHub CI run `32533966814`: PASS; squash-merged into
  `develop_V2` at `3a611873e01de8ff156292b170b735483b1d0b0d`.
- V2-16 targeted metainfo, manifest, worker-effect, C411, V1 torrent, and model tests: PASS,
  47 tests.
- V2-16 full backend suite: PASS, 353 tests with 6 service-backed tests deferred to CI.
- V2-16 full backend Ruff lint/format and mypy: PASS.
- V2-16 PostgreSQL upgrade/downgrade SQL generation, `git diff --check`, and targeted secret
  scan: PASS.
- PR #72 (V2-16) review and GitHub CI run `32534547798`: PASS; squash-merged into
  `develop_V2` at `13188b63cf5ab185233b9926282ebe018586ca2a`.
- V2-17 targeted API, worker-effect, deduplication, storage, manifest, and V1 torrent tests:
  PASS, with 1 PostgreSQL concurrency test deferred to CI.
- V2-17 complete backend suite: PASS, 357 tests with 6 service-backed tests deferred to CI.
- V2-17 full backend Ruff lint/format and mypy: PASS.
- V2-17 PostgreSQL upgrade/downgrade SQL generation and `git diff --check`: PASS.
- PR #73 (V2-17) review and GitHub CI run `32562416237`: PASS; squash-merged into
  `develop_V2` at `a7dafce1258d822ffea073918f8d5c072fa4abdb`.
- V2-18 focused torrent UI interaction, pagination, durable-state, ellipsis, keyboard, and axe
  accessibility tests: PASS.
- V2-18 complete frontend TypeScript check, Vitest suite, and production build: PASS.
- V2-18 complete backend and container regression jobs: PASS.
- V2-18 `git diff --check` and targeted CSP/internal-identifier scan: PASS.
- PR #74 (V2-18) initial GitHub CI run `32562851301`: PASS; final documentation-only head is
  validated by run `32562947647`: PASS; squash-merged into `develop_V2` at
  `7fc48c73b00e8be95c3c46dac285456070fa15b7`.
- V2-18A targeted Compose-policy, development-helper, foundation-regression, and generated
  torrent-fixture tests: PASS.
- V2-18A complete backend suite: PASS, 369 tests with 6 service-backed tests deferred to CI.
- V2-18A full backend Ruff lint/format and mypy: PASS.
- V2-18A shell/Python/YAML syntax and `git diff --check`: PASS.
- PR #75 (V2-18A) review and GitHub CI run `32564395448`: PASS; complete Linux local-profile
  startup/smoke, backend, frontend, migrations, and container jobs are green; squash-merged into
  `develop_V2` at `2f1787b1039c112b6aee0901ae18f816618a9b30`.
- V2-19 focused download ownership/readiness, Range/ETag, mutation, symlink, lease, and rate-limit
  tests: PASS, 6 tests.
- V2-19 complete backend suite: PASS, 375 tests with 6 service-backed tests deferred to CI.
- V2-19 full backend Ruff lint/format and mypy: PASS.
- V2-19 `git diff --check`: PASS. Real PostgreSQL migration rollback/re-upgrade remains required
  in PR CI; Docker is unavailable and the local Alembic package copy is corrupted in this
  disposable development environment.
- PR #76 (V2-19) review and GitHub CI run `32565856568`: PASS; PostgreSQL migration
  rollback/re-upgrade, backend, frontend, container, and complete local-profile smoke jobs are
  green; squash-merged into `develop_V2` at `f728471529d84ee43907101f819acba53eac65c9`.
- V2-20 focused snapshot/download backend tests: PASS, 8 tests.
- V2-20 complete backend suite: PASS, 377 tests with 6 service-backed tests deferred to CI.
- V2-20 full backend Ruff lint/format and mypy: PASS.
- V2-20 `git diff --check`: PASS. The workspace has no installed Node dependencies, so complete
  TypeScript, Vitest, axe, build, container, and local-profile smoke validation remains required
  in PR CI before merge.
- PR #77 (V2-20) final GitHub CI run `32566565031`: PASS; TypeScript, 22 frontend tests, axe,
  production build, backend, migrations, container, and complete local-profile smoke jobs are
  green; squash-merged into `develop_V2` at `5e624f7e9094ea9441b10dd57690a9a68460b337`.
- V2-21 focused download fallback backend tests: PASS, 9 tests.
- V2-21 complete backend suite: PASS, 378 tests with 6 service-backed tests deferred to CI.
- V2-21 full backend Ruff lint/format and mypy: PASS.
- V2-21 `git diff --check`: PASS. TypeScript, Vitest/axe, production build, container, and local
  profile remain required in PR CI before merge.
- PR #78 (V2-21) review and GitHub CI run `32567007096`: PASS; TypeScript, frontend tests/axe,
  production build, backend, migrations, container, and complete local-profile smoke jobs are
  green; squash-merged into `develop_V2` at `5dba6977a4956b127c6c5a25428e522b78254eba`.
- V2-22 focused lifecycle, qBittorrent, shared-storage, download-lease, API, accounting, and model
  tests: PASS.
- V2-22 complete backend suite: PASS, 395 tests with 6 service-backed tests deferred to CI.
- V2-22 full backend Ruff lint/format and mypy: PASS.
- V2-22 `git diff --check`: PASS. Real PostgreSQL migration rollback/re-upgrade, frontend,
  container, and complete local-profile smoke validation remain required in PR CI before merge.
- PR #79 (V2-22) final GitHub CI run `32568201009`: PASS; PostgreSQL migration downgrade/re-upgrade,
  backend, frontend, container, and complete local-profile smoke jobs are green; squash-merged into
  `develop_V2` at `eaa436581a3ab449b33790e90c6dc0f8ea052177`.
- V2-23 TypeScript checks, 25 frontend tests including focused axe audits, and production build:
  PASS.
- V2-23 complete backend regression, Ruff lint/format, mypy, Python syntax, CSP scan, and
  `git diff --check`: PASS.
- V2-23 container build and enhanced complete local-profile smoke remain required in PR CI before
  merge.
- PR #80 (V2-23) GitHub CI run `32568965095`: PASS; backend, frontend, migrations, container,
  and enhanced complete local-profile smoke jobs are green; squash-merged into `develop_V2` at
  `145d0d3b2fd4df4536e16107f8c2511804faa7f8`.
- V2-24 targeted responsive policy, downloads, feedback, file-browser, TypeScript, production
  build, and `git diff --check`: PASS.
- V2-24 complete backend/frontend/container and local-profile smoke validation remain required in
  PR CI before merge.
- PR #81 (V2-24) final GitHub CI run `32569989613`: PASS; backend, frontend, container, and
  complete local-profile smoke jobs are green; squash-merged into `develop_V2` at
  `129dcce13feb120ceb6ddac9cdbc93f3fb162ccb`.
- V2-25 targeted admin/options/scheduler/storage backend tests, Ruff lint/format, mypy, TypeScript,
  React tests, production build, and `git diff --check`: PASS.
- V2-25 complete backend/frontend/container and local-profile smoke validation remain required in
  PR CI before merge.
- PR #82 (V2-25) GitHub CI run `32570408433`: PASS; backend, frontend, container, migrations, and
  complete local-profile smoke are green; squash-merged into `develop_V2` at
  `13e8e43b2c40c245ca111b4b573872f7c1cdebbf`.
- V2-26 targeted reconciliation/qB/shared-storage/admin backend tests, Ruff lint/format, mypy,
  TypeScript, React/axe tests, production build, and `git diff --check`: PASS.
- V2-26 complete backend/frontend/container and local-profile smoke validation remain required in
  PR CI before merge.
- PR #83 (V2-26) final GitHub CI run `32570921393`: PASS; backend, frontend, container, and complete
  local-profile smoke are green; squash-merged into `develop_V2` at
  `0f43d18c04f755270309e3df48e356a8c73f803d`.
- V2-27 targeted metrics/health/jobs/Redis/local-helper tests, Ruff lint/format, mypy, Python syntax,
  and `git diff --check`: PASS.
- V2-27 complete backend/frontend/container and metrics-aware local-profile smoke validation remain
  required in PR CI before merge.
- PR #84 (V2-27) GitHub CI run `32571270725`: PASS; backend, frontend, container, and
  metrics-aware complete local-profile smoke are green; squash-merged into `develop_V2` at
  `cfc847d0aa2c0ef64e7d2b7f98a801d8edbe7987`.
- V2-28 targeted monitoring/local Compose-policy tests: PASS, 21 tests.
- V2-28 complete backend regression suite: PASS, with 6 service-backed tests deferred to CI.
- V2-28 targeted Ruff lint/format, mypy, Python/shell syntax, YAML/JSON parsing, and
  `git diff --check`: PASS.
- PR #85 (V2-28) GitHub CI run `32573254177`: PASS; real image startup, Prometheus
  target/rule checks, Grafana provisioning, backend, frontend, container, and complete Linux
  local-profile smoke are green; squash-merged into `develop_V2` at
  `5de4cd0282fd1397c810a0a693e455cada4c6c9e`.
- Post-V2-28 main-profile validation on Docker Desktop macOS: PASS. Monitoring-profile startup:
  FAIL with the confirmed `node-exporter` root-bind `rslave` portability issue tracked in V2-28H.
- Phase 0 documentation structure, roadmap ordering, secret scan, and `git diff --check`: PASS.
- Phase 0 PR #86 GitHub CI run `33087085365`: PASS; backend, frontend, container, complete Linux
  local-profile smoke, and monitoring smoke are green.
- V2-28A targeted scheduler, weighted-fair policy, qBittorrent gateway, and worker-effect tests:
  PASS, 53 tests with 1 PostgreSQL locking test deferred to CI.
- V2-28A complete backend suite: PASS, 421 tests with 6 service-backed tests deferred to CI.
- V2-28A targeted Ruff lint/format, mypy, and `git diff --check`: PASS.
- V2-28A GitHub CI run `33090676466`: PASS; backend with real PostgreSQL, frontend, container,
  complete local-profile smoke, and monitoring smoke are green.
- V2-28B targeted scheduler, worker-effect, qBittorrent gateway, and model tests: PASS, 56 tests
  with 1 PostgreSQL locking test deferred to CI.
- V2-28B complete backend suite: PASS, 426 tests with 6 service-backed tests deferred to CI.
- V2-28B PostgreSQL migration upgrade/downgrade SQL generation: PASS.
- V2-28B GitHub CI run `33092073758`: PASS; backend with real PostgreSQL, frontend, container,
  complete local-profile smoke, and monitoring smoke are green; squash-merged through PR `#88`
  into `develop_V2` at `fe0c92abb22050a105dd1afb1cf37b0fbc25faa6`.
- V2-28C targeted weighted-fair, scheduler runtime, worker-effect, and model tests: PASS, 60 tests
  with 1 PostgreSQL locking test deferred to CI.
- V2-28C complete backend suite: PASS, 435 tests with 6 service-backed tests deferred to CI.
- V2-28C full Ruff lint/format, mypy, and `git diff --check`: PASS.
- V2-28C PostgreSQL migration upgrade/downgrade SQL generation: PASS.
- V2-28C GitHub CI run `33151352347`: PASS; real PostgreSQL migration, backend, frontend,
  container, complete local-profile smoke, and monitoring smoke are green.
- V2-28D targeted Redis/realtime/API/worker/scheduler tests: PASS, 57 tests with 2 real-service
  tests deferred to CI.
- V2-28D complete backend suite: PASS, 446 tests with 6 service-backed tests deferred to CI.
- V2-28D full Ruff lint/format and mypy: PASS.
- V2-28D GitHub CI run `33154157035`: PASS; 452 backend tests with real PostgreSQL/Redis,
  TypeScript, Vitest, build, container, complete local-profile smoke, and monitoring smoke are green.
- V2-28E complete frontend suite: PASS, 34 tests including the synthetic 50,000-file manifest.
- V2-28E TypeScript application/node checks, production Vite build, and `git diff --check`: PASS.
- V2-28F targeted metrics, health, admin reconciliation, and storage-accounting suite: PASS,
  22 tests including constant-query validation at 10, 100, and 500 accounts.
- V2-28F complete backend suite: PASS, 450 tests with 6 service-backed tests deferred to CI.
- V2-28F Ruff lint/format and strict mypy checks: PASS.
- V2-28F GitHub CI run `33172814341`: PASS; 456 backend tests with real PostgreSQL/Redis, frontend,
  container, complete local-profile smoke, and monitoring smoke are green; squash-merged through
  PR `#93` into `develop_V2` at `8ba66965b4ad778fcd83dca3b274a56ae44784e5`.
- V2-28G targeted runtime/config/Compose/qB/account-routing/worker/reconciliation/download suite:
  PASS, 114 tests with 1 service-backed test deferred to CI.
- V2-28G complete backend suite: PASS, 473 tests with 6 service-backed tests deferred to CI.
- V2-28G full Ruff lint/format and strict mypy checks: PASS.
- V2-28G GitHub CI run `33196526847`: PASS; 479 backend tests with real PostgreSQL/Redis,
  frontend, production/V2 containers, complete local smoke, and monitoring smoke are green; both
  automated review findings were fixed and resolved before squash-merge through PR `#94`.
- V2-28H targeted monitoring policy suite: PASS, 11 tests covering Linux and Docker Desktop.
- V2-28H shell/Python syntax, targeted Ruff lint/format, and `git diff --check`: PASS.
- Docker is unavailable in the development workspace; both normalized Compose variants and the
  real monitoring smoke remain required in PR CI before merge.
- V2-29 targeted Rise2 Compose policy suite: PASS, 9 tests.
- V2-29 shell/Python syntax, targeted Ruff lint/format, and `git diff --check`: PASS.
- Docker is unavailable in the development workspace; normalized production Compose validation is
  required in PR CI. Real NewGreedy UID/GID readability remains a mandatory Rise2 host preflight.
- V2-30 targeted encrypted-backup policy and payload-integrity suite: PASS, 12 tests.
- V2-30 Python/shell syntax, targeted Ruff lint/format, strict mypy, and `git diff --check`: PASS.
- The real encrypted artifact flow and isolated PostgreSQL restore drill require `age` and Docker
  on Rise2; they remain mandatory operational acceptance checks before production backup approval.
- V2-30 GitHub CI run `33218782500`: PASS; backend, frontend, container, complete local-profile,
  and monitoring validation are green; squash-merged through PR `#97` into `develop_V2` at
  `7144d1190a6f0bd3eac52600948c437ba2ccb938`.
- V2-31 targeted inventory/import/idempotence/conflict/rollback suite: PASS, 17 tests.
- V2-31 complete backend suite: PASS, 513 tests with 6 service-backed tests deferred to CI.
- V2-31 Ruff lint/format, strict mypy, migration SQL generation, and `git diff --check`: PASS.
- A real V1 snapshot/read-only role and the V2 physical-data reconciliation remain mandatory
  operational acceptance checks; no V1 database or content is accessed by repository tests.
- V2-31 GitHub CI run `33238486399`: PASS; backend, frontend, container, complete local-profile,
  and monitoring validation are green; squash-merged through PR `#98` into `develop_V2` at
  `3aef71ca21985986db1ad76cacd960cdf7aee2e6`.
- V2-32 scheduler/API/WS/download/import-security targeted suites: PASS, including 1/10/25/50/100
  accounts, 1/2 slots, 100 sockets, multi-tab/reconnect policy, Range and bounded manifests.
- V2-32 complete backend suite: PASS, 530 tests with 6 real-service tests deferred to CI.
- V2-32 Ruff lint/format, strict mypy, TypeScript, 34 frontend tests, production build, locked
  Python/frontend audits, Trivy Dockerfile scan, shell/YAML validation, and `git diff --check`:
  PASS locally.
- PR #99 automated review finding: FIXED and resolved. Trivy now targets the application
  Dockerfile explicitly, while normalized Compose policy permits `privileged: true` only for
  cAdvisor and rejects it for every other monitoring or Rise2 service.
- V2-32 CI findings: FIXED. The image upgrade removes fixable OpenSSL CVE-2026-14456; shared-file
  lease admission revalidates the complete right atomically and fails closed after revocation;
  the complete-profile load fixture is now registered and verified in qBittorrent before becoming
  `READY`, so the production sync worker remains enabled during the measurement.
- PR #99 GitHub CI run `33249450228`: PASS; 536 backend tests with real PostgreSQL/Redis,
  frontend, application image, locked dependency audits, Trivy image/configuration scans,
  migrations, complete local profile, and monitoring smoke are green.
- The real CI load passed at 1/10/25/50/100 accounts. At 100 accounts and 300 authenticated API
  requests, median latency was 1862.3 ms and p95 was 2397.0 ms with zero bounded contention
  retries. The same run passed 100 WebSockets across 25 accounts/four tabs plus 25 reconnects,
  used 9 PostgreSQL connections, and retained zero idle transactions or download leases.
- No V2-32 database migration is required. Sustained Rise2 CPU/RAM/I/O measurement, real traffic
  shape, and injected dependency failures remain mandatory before the pilot; no Rise2 deployment
  is part of V2-32.

## V2-32A — Internationalisation FR/EN

- Added one centralized React translation catalogue and context for French and English without
  duplicating page components; French remains the compatibility default.
- Added a responsive language selector to anonymous, first-login, user, and administration
  surfaces. Authenticated choices are stored on the user and restored on later sessions.
- Added the additive `users.preferred_locale` migration with an explicit `fr`/`en` database
  constraint. First-login users may save the preference before changing generated credentials.
- Replaced phrase-dependent auth, file, and trash error contracts with stable structured codes and
  fields; the frontend translates codes and uses localized safe fallbacks instead of displaying
  backend phrases.
- Localized login, account, file browser, mutations, trash, downloads, every administration
  destination and embedded service control, dialogs, notifications, navigation, and accessibility
  labels on mobile and desktop.
- Locale persistence is best-effort after a successful login, remains available on the mandatory
  credential-change screen, and never invalidates an authenticated session. Torrent admission and
  recursive-transfer failures are rendered from stable codes in both languages.
- Dates, numbers, progress values, and byte units use the active locale through `Intl`; responsive
  header policy permits longer English labels without horizontal overflow.
- V2-32A complete backend suite: PASS, 531 tests with 6 real-service tests deferred to CI.
- V2-32A Ruff lint/format, strict mypy, Alembic single-head validation, TypeScript, 38 frontend
  tests including axe/i18n coverage, production build, and `git diff --check`: PASS locally.
- The five P2 review findings on PR `#101` were fixed, answered, and resolved: locale persistence
  can no longer invalidate authentication, first-login locale persists, torrent rejection and
  recursive-transfer codes are translated, and all admin destinations are localized.
- V2-32A GitHub CI run `33258087092`: PASS; dependency/image security, real PostgreSQL migration,
  backend, 38 frontend tests, production image, complete local profile, bounded load, and
  monitoring smoke are green; squash-merged through PR `#101` into `develop_V2` at
  `63cd40f03b5869f0eae7e39b189f656a014101fa`.

## V2-32B — Frontend UX / UI polish & mobile

- Removed confirmation-only dialogs from move-to-trash, restore, user-access deletion, and torrent
  cancellation. These reversible or recoverable actions execute directly with explicit danger
  styling, disabled pending states, double-submit guards, and accessible success/error toasts.
- Permanent user-trash deletion, administrative purge, and NewGreedy statistics reset retain an
  explicit danger confirmation in the normal page flow. The inline control is non-modal, supports
  cancel/confirm by keyboard, and returns focus to the recreated source action after cancellation.
- Reduced the global feedback provider to an accessible toast stack of at most five items. Toasts
  do not steal focus, pause expiration while hovered or focused, remain dismissible, and use longer
  bounded durations for errors and progress; durable load/reconnect state remains inline.
- Torrent submission now accepts multiple files from both the native picker and drag/drop. Each
  file keeps an independent backend request and visible state; batches are capped at 50 files and
  exactly three workers, continue after partial failure, and finish with one aggregate toast and
  one authoritative list refresh.
- Same-batch duplicates, empty/wrong-extension files, successful deduplication, 409, 413, 422,
  quota/507, 503, storage pressure, and network failures have distinct bounded outcomes. Input and
  drop surfaces are reusable after completion; no page reload or torrent polling was introduced.
- Added semantic palette tokens for backgrounds, surfaces, borders, text, primary actions,
  success, warning, danger, information, and focus. Mobile layouts now include dedicated inline
  confirmation and upload-queue cards, bounded long-name handling, touch-sized actions, and
  breakpoints at 680, 430, and 360 px while retaining the existing card conversion for tables.
- Responsive source/layout checklist: 320, 360, 375, 390, 430, 768 px, desktop, portrait and
  landscape; file/torrent names over 200 characters, nested paths, long allowed usernames,
  navigation, tables/cards, drop zone, toast stack, inline danger actions, and remaining maintenance
  dialogs were checked for local containment and accessible names. The repository has no installed
  Playwright/Puppeteer or browser binary, so no new heavyweight screenshot stack was added.
- V2-32B local validation: PASS — 54 frontend tests across 12 files, axe checks, FR/EN catalogue,
  TypeScript, production Vite build, `git diff --check`, Ruff lint/format, strict mypy, and 553
  backend tests with 6 service-backed tests deferred.
- PR `#105` initial GitHub CI run `33330479493` (`#229`): PASS — backend with real
  PostgreSQL/Redis and migrations, frontend, dependency/image security, production image, complete
  Compose profile, bounded load, WebSocket, and monitoring validation are green. No review thread
  or review finding was open when the documentation commit was prepared.
- PR `#105` review follow-up: the two P2 findings were fixed, answered, and resolved. Monotonic
  request generations prevent a delayed WebSocket refresh from replacing the authoritative
  post-upload listing, and cancelling an inline administrative purge restores focus to its opener.
  Both paths have explicit frontend regressions.
- PR `#105` corrected head `642ad30072c738598da3b771d57981100451eab7`: GitHub CI run
  `33331011403` (`#231`) PASS — backend with real PostgreSQL/Redis and migrations, 54 frontend
  tests, dependency/image security, production image, complete Compose profile, bounded load,
  WebSocket, and monitoring validation are green; no review thread remains open.
- PR `#105` final GitHub CI run `33331199577` (`#232`): PASS on the exact documented head
  `8540509a0b4e020fb5129fa016a0e49843bc9f56`; squash-merged into `develop_V2` at
  `af35086e50d1e179b878e2999656349352cf9209` with both review threads resolved.
- No dependency, backend contract, database migration, deployment configuration, or version change
  is included. The separate V2-33 draft PR `#103` remains untouched and paused.
- Documentation PR `#104` initial GitHub CI run `33316787183` (`#224`): PASS — backend,
  frontend, dependency/image security, production image, complete Compose profile, bounded load,
  WebSocket, and monitoring validation are green.
- PR `#104` review finding: addressed by explicitly retaining accessible non-modal inline
  confirmation for irreversible trash deletion and administrative purge.

## V2-32C — Automatic READY torrent retention

- Added durable `ready_at` and `retention_expires_at` timestamps to each physical managed torrent.
  Retention is computed from all historical distinct request owners, including cancelled and
  expired requests: 5 days for one user, 6 for 2–3, 7 for 4–5, 8 for 6–7, 9 for 8–9, and 10 from
  10 users onward. A later distinct request may only extend the deadline from the first READY.
- Added an indexed PostgreSQL retention reaper limited to 200 rows per claim. At the exact deadline
  it locks the managed torrent, expires every active request, decrements each owner's logical usage,
  records `PURGE_PENDING`, persists a scheduler stop intent, and creates one generation-keyed
  immediate purge job. Restart and Redis-loss behavior remains driven by SQL state.
- New requests are accepted and may extend retention strictly before the deadline. At or after the
  deadline, and once purge has begun, they fail closed rather than reviving an object being removed;
  a request after `PURGED` starts a fresh physical lifecycle.
- Existing HTTP leases keep already-open transfers alive. New leases fail after request expiration,
  while the durable purge job retries until all leases have ended.
- Fixed last-owner cancellation for an in-progress torrent: `PURGE_PENDING` now carries a durable
  stop intent prioritized by the scheduler. qBittorrent start/stop remains scheduler-only, failed
  control is replayed, partial files remain until purge, and the existing WOS ownership guard keeps
  external torrents untouched.
- Added additive, reversible migration `20260831_22` with partial indexes for due retention and
  pending stop intents. Existing READY rows are backfilled from their best-known durable update
  time and historical distinct-owner count.
- Local validation: PASS — 582 backend tests collected, 575 passed and 7 real-service integrations
  deferred; full Ruff lint and format checks, strict mypy, 54 frontend tests, TypeScript, production
  build, one Alembic head, offline PostgreSQL upgrade/targeted downgrade SQL, and
  `git diff --check`. The exact request/expiration PostgreSQL race remains covered by CI.
- PR `#106` initial documented head `47ee337489bb25563c666b60e0383d02540ba3d8`: backend,
  frontend, migrations, dependency and image security passed in CI run `33372522601` (`#236`). The
  complete-profile smoke exposed a fixture that forced `READY` without the new durable timestamps;
  the fixture now records a five-day retention deadline before testing HTTP downloads.
- Review follow-up fixes one P1 and three P2 findings: purge retries until the scheduler has
  confirmed STOP; an existing lease renews while the reaper transition is pending; manual
  `PURGE_PENDING` reactivation rejects the exact expired READY boundary; and purge STOPs use a
  separate bounded qB batch so 200 already-active controls are never truncated. Each path has an
  explicit regression, including the 200-active-plus-STOP boundary.
- Corrected-head CI run `33373651931` (`#237`) passed backend, frontend, migrations, security, image
  construction, and the complete smoke. Its load phase exposed the second synthetic READY fixture;
  the 100-account fixture now calls the production retention calculator after qB verification,
  producing the expected ten-day deadline instead of bypassing the new invariant.
- Follow-up CI run `33374065479` (`#238`) activated the PostgreSQL request/expiration race and
  confirmed `SKIP LOCKED` may correctly defer a locked due row to the next bounded batch. The test
  now requires the request to be rejected and exactly one expiration across the initial claim plus
  replay, instead of depending on which transaction acquires the row first.
- PR `#106` corrected functional head `07a8841e8d962f69681fa2688762b2d9dd02ea33`:
  GitHub CI run `33374412646` (`#239`) PASS — backend with PostgreSQL/Redis and the exact race,
  migration rollback/upgrade, frontend, dependency/image security, production image, complete
  smoke, bounded 100-account load, WebSockets, and monitoring are green. The P1 and three P2 review
  threads are answered and resolved; no review thread remains open.
- PR `#106` final documentation head `53c59f92f5da3fc49df8f2f6decdcfeb630de56b`:
  GitHub CI run `33401939599` (`#240`) PASS on the exact merged tree — backend with
  PostgreSQL/Redis, migrations, frontend, dependency/image security, production image, complete
  smoke, bounded 100-account load, WebSockets, and monitoring are green. No review thread remains
  open. Squash merge into `develop_V2`: `552c4618ce26b89ac772e558631685dec7aa3d9c`.

## V2-32D — NewGreedy purge cleanup (blocked)

- Source inspection of NewGreedy v1.7.5 at commit
  `bc864011c1652ac9388f44c6f322bffdf87c2257` confirmed that
  `DELETE /api/stats/{ih}` truncates every supplied hash to eight hexadecimal characters.
- NewGreedy's torrent statistics are keyed globally by that short prefix inside one service
  instance, without route/passkey isolation, and the web endpoint edits the persisted JSON without
  removing the proxy's authoritative in-memory entry. A later flush may restore the deleted row.
- Exact, durable SHA-1 cleanup is therefore unsupported. WOS does not use a global reset, direct
  storage edit, or prefix deletion as a workaround. V2-32D remains blocked until NewGreedy exposes
  a full-hash, idempotent, in-memory-and-persistent deletion contract.
- V2-32D is not a prerequisite for V2-32E, V2-32F, or the Rise2 pilot unless an explicit later
  decision changes that ordering. The existing draft V2-33 PR `#103` remains untouched and paused.

## V2-32E — Visual READY expiration warnings

- Added the existing PostgreSQL `retention_expires_at` deadline to owner-filtered V2 torrent-list,
  create, and manifest responses. The field is normalized as an absolute UTC timestamp and no
  infohash, storage key, tracker/qB account reference, passkey, URL, or host path is exposed.
- Added semantic orange warnings from the exact 48-hour boundary through more than 24 hours, then
  danger-red warnings from the exact 24-hour boundary until expiry. Icon, relative text, and an
  absolute localized date make the warning independent of color.
- The downloads list uses one compact badge per READY torrent. The compatibility manifest shows
  one detailed warning at its root rather than repeating it for every file; ordinary file-manager
  content outside the managed-torrent lifecycle remains unchanged.
- A local deadline-aligned timer updates presentation without server polling or `aria-live`
  countdown announcements. It hides an elapsed countdown but never changes READY to EXPIRED;
  PostgreSQL and authoritative API/WebSocket resynchronization remain responsible for state.
- Added the closed, secret-free `torrent.retention_extended` event. A real shared-deadline extension
  publishes an invalidation to every active owner so each client reloads the same physical deadline
  and discards any stale value.
- Added deterministic boundary, local-clock, extension, shared-owner, expired-state, FR/EN,
  manifest-root, 200-plus-character name, 320/360/375/390/430/768/desktop, and axe coverage.
- Review hardening queues one follow-up resync when an event overlaps an in-flight list refresh and
  reloads an open compatibility manifest on a matching shared-deadline extension, so neither view
  can retain the earlier authoritative value.
- Local validation is green: 26 targeted backend tests, the complete backend suite (577 passed,
  7 skipped), 48 targeted frontend tests, the complete frontend suite (77 passed), TypeScript,
  Vite production build, Ruff check/format, mypy, and `git diff --check`.
- No migration, retention-policy recalculation, qBittorrent/NewGreedy mutation, server polling,
  dependency, version change, V2-33 work, or final audit is included.

## V2-32F — Queue visibility

- Added secret-free `queue_status`, `queue_position_estimate`, and `queue_total_estimate` fields to
  owner-filtered V2 torrent responses. READY, EXPIRED, CANCELLED, and ERROR requests expose no
  queue position; selected, stalled, and cooling torrents expose only their accurate qualitative
  state rather than a misleading normal rank.
- The numeric value is explicitly an estimate of one physical `ManagedTorrent`'s rank in the
  eligible backlog's deterministic `(created_at, id)` order, which is the bounded database order
  scanned by the real scheduler. One SQL window query ranks the physical waiting set and returns
  only managed torrents present on the requested API page. The public rank deliberately does not
  rotate with the durable scan cursor and does not copy or predict weighted-fair selection.
- All beneficiaries of a shared managed torrent receive the same physical estimate. The response
  contains no infohash, storage key, scheduler ledger/deficit, owner identity, qB/C411 route,
  passkey, tracker URL, or host path, and the read-only calculation cannot change scheduler state
  or qBittorrent controls.
- Added one secret-free global `torrent.queue_changed` invalidation after significant scheduler
  selection changes. Every connected owner, including those beyond the current 200-torrent
  scheduler window, resynchronizes with the authoritative GET; the global payload contains no
  request or user identifier. Progress and scan-cursor micro-changes do not create events or
  polling, and a selection cycle publishes at most one global invalidation.
- “Mes téléchargements” now presents the estimated rank, “Prochainement” for rank one, qualitative
  selected/stall/cooldown labels, a non-FIFO disclaimer, and the total estimated waiting backlog in
  centralized FR/EN copy with text plus icon and existing design tokens.
- The bounded recursive browser controller now exposes up to eight active, exact waiting,
  completed, paused, failed, or cancelled file entries. Waiting positions are exact only inside
  the client-controlled queue; manifest paging, concurrency, resume integrity, and DownloadLease
  authority are unchanged.
- Added a concise FR/EN atomic-torrent explanation. V2-32F does not add qBittorrent per-file
  priority or selective BitTorrent download: file choice remains available only after READY for
  transfer from the existing manifest to the user's device.
- Coverage includes stable ranks across circular-cursor movement/restart, one and multiple waiting
  torrents, a 1,005-item
  backlog, shared ownership, selected/stalled/cooldown/READY states, read-only query count,
  secret-safe API output, scheduler invalidation bounds, positions 1/9/158/1,005, authoritative
  WebSocket resync, local positions 1/2/3 with two active files, FR/EN, 200-plus-character names,
  320/360/375/390/430/768/desktop layouts, and axe.
- Local validation: PASS — 589 backend tests collected (582 passed, 7 real-service integrations
  deferred), 82 frontend tests, TypeScript, Vite production build, Ruff check/format, strict mypy,
  version consistency, deployment-script syntax, and `git diff --check`. Docker is unavailable in
  this workspace, so Compose, real PostgreSQL/Redis, image, security, smoke, bounded-load,
  WebSocket, and monitoring policies remain delegated to PR CI.
- PR `#108` functional head `5c06650d326546955dd846efc35d23580d114e9c`: GitHub CI run
  `33495898684` (`#246`) PASS — backend with real PostgreSQL/Redis and migrations, 82 frontend
  tests, dependency/image security, production and V2 images, complete Compose smoke, bounded
  load, WebSocket, and monitoring validation are green. No review thread or review finding is
  open on that head.
- PR `#108` review follow-up fixes both P2 findings: public ranks now stay stable while the bounded
  scheduler scan cursor rotates, and one identifier-free global invalidation reaches connected
  owners both inside and outside the current 200-torrent control window after a real selection
  change. Explicit regressions cover cursor movement, global fan-out, closed payload shape, and one
  event per selection cycle without a database owner scan.
- No migration, dependency, configuration, version, NewGreedy, retention-policy, deployment, or
  V2-33 change is included. V2-32D remains blocked and non-blocking; draft PR `#103` remains
  untouched.

## Pre-pilot hardening audit for V2-33

- Confirmed and fixed the public monitoring boundary: Rise2 ingress returns `404` for
  `/api/v2/metrics`, sets `Referrer-Policy: no-referrer` uniformly, overwrites forwarded client
  identity, and the API trusts only the fixed Caddy address. Prometheus keeps private API-network
  access.
- Replaced API-side legacy integration polling with secret-free, durable PostgreSQL observations
  produced per account by the scheduler. Metrics, health, the admin overview, and reconciliation
  now fail closed on stale/incomplete observations and do not receive the integration registry.
- Added stable, bounded reconciliation across database, immutable qBittorrent snapshots, and
  streamed shared storage, with opaque cursors and explicit boundary coverage at 0, 1, 199, 200,
  201, 500, and 1,000 entries. Administrative recovery now enqueues idempotent durable worker jobs
  and persists their results instead of performing network or storage effects in the API process.
- Kept V1 behavior isolated while removing the legacy torrent route from the V2 router and unused
  legacy frontend calls. V2 hides NewGreedy editing and WOS/NewGreedy restart controls because the
  Rise2 runtime does not support those mutations safely.
- User deletion now cancels active torrent requests through the normal locked lifecycle and
  accounting path while preserving shared physical copies. Scheduler eligibility excludes deleted
  and inactive users.
- Made large recursive downloads progressively consume server-paginated manifests, rejected ZIP
  sources that end before their declared size while releasing descriptors, and made torrent
  correlation require a full hash or one unambiguous prefix.
- Centralized explicit FR/EN copy for all 36 V2 option keys, 44 NewGreedy fields, and seven
  NewGreedy sections; known controls no longer depend on backend French phrases.
- Finding 10 was adjusted after inspection: arbitrary proxy spoofing was not trusted, but Uvicorn
  did not trust Caddy at all, so rate limiting could collapse clients onto the ingress address.
  Fixed addresses plus an overwritten forwarded chain now preserve the real client without opening
  a broad trusted-proxy range.
- Added migration `20260829_21` for durable integration health and bounded qBittorrent inventory
  snapshots. Offline PostgreSQL upgrade and targeted downgrade SQL generation pass.
- Local validation: PASS — 553 backend tests with 6 real-service tests deferred, full Ruff lint and
  format checks, strict mypy, 40 frontend tests, TypeScript, production build, Compose/Caddy policy
  tests, offline PostgreSQL upgrade/targeted downgrade SQL, and `git diff --check`.
- PR `#102` review follow-up: the three findings are fixed, answered, and resolved. Recovery jobs
  preserve their exact enqueue-time snapshot, a failed recovery can be re-enqueued without losing
  concurrent idempotency, and durable observation validity accounts for both the configured cadence
  and the duration of a complete multi-account cycle.
- Corrected PR head `dcbc527cf7a7c6bbcf536281a9613dc58827726c`: GitHub CI run `33314519448`
  (`#220`) PASS — backend with real PostgreSQL/Redis and migrations, frontend, dependency and image
  security, production image, complete Compose profile, bounded load, WebSocket, and monitoring
  validation are green. Docker remains unavailable only in the local workspace.

## Known constraints

- `master` and `develop` remain V1-only; V2 branches and PRs target `develop_V2`.
- V1 qBittorrent remains external and shares `/srv/seedbox:/data` with WOS.
- Rise2 V2 must not reuse V1 secrets, networks, volumes, database, qB profile, or storage
  before an explicitly approved import.
- PostgreSQL is authoritative for durable jobs and destructive decisions; Redis loss must
  remain recoverable.
- Secrets and complete tracker URLs must never reach logs, metrics, options, DB business
  rows, browser responses, or agent documents.
- The base V2 Compose remains a foundation stack. The separate runnable local profile uses
  only disposable local data and must not reuse Rise2 or V1 secrets/data.

## Next task

- V2-32F is the only active task. After its review, green CI, and merge, the next roadmap task is
  `V2-33 — Pilote Rise2`; do not start it automatically.
- V2-32D remains blocked by NewGreedy v1.7.5 and is not a pilot prerequisite unless explicitly
  decided otherwise.
- Do not continue, rebase, close, or merge the existing V2-33 draft PR `#103` automatically; its
  host phase requires a separate explicit decision and Rise2 authority.
