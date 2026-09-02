# World of Seeds — Agent Context

## Purpose

This document is the stable handoff for agents working on World of Seeds.
It describes the product, invariants, security boundaries, and contribution rules.
Keep volatile task status out of this file; use `PROGRESS.md` for that.

## Product

World of Seeds is a private seedbox management application.
It provides authenticated users with a browser-based file manager.
It also provides controlled torrent submission to a shared qBittorrent service.
Administrators manage users, storage, and instance-wide functional options.
The current production line is V1.
The stable V1 maintenance release documented here is `1.3.3`.

## Repository and branches

- Repository: `ThomasPerez91/World-of-Seeds`.
- `master` is the stable V1 production/release branch.
- `develop` is the V1 preservation and maintenance branch.
- `develop_V2` is the permanent V2 integration branch.
- V1 maintenance starts from and returns to `develop` through a pull request.
- V1 releases move from `develop` to `master` through a pull request.
- Every V2 task starts from the latest `develop_V2` and returns to `develop_V2` through a
  pull request.
- Never merge V2 feature work directly to `develop` or `master`.
- Do not merge when required CI checks are red.

## Technology

- Backend: Python, FastAPI, SQLAlchemy, Alembic, Pydantic.
- Database: PostgreSQL.
- Frontend: React, TypeScript, Vite.
- Frontend tests: Vitest, Testing Library, axe accessibility checks.
- Backend tests: pytest.
- Formatting and linting: Ruff.
- Type checking: mypy and TypeScript.
- Packaging and locking: uv and npm.
- Runtime delivery: Docker Compose and a production container image.

## Deployment topology

- The V1 application stack is driven by `compose.yaml` and
  `deploy/compose.production.yaml`.
- PostgreSQL is internal and must not publish a host port.
- The application port is bound to the host only as documented.
- qBittorrent is an external dependency used through its Web API.
- The host seedbox root is `/srv/seedbox`.
- Both the application and qBittorrent must see that root as `/data`.
- A user's download directory is `/data/<username>/downloads`.
- Never solve permissions by introducing `chmod 777`.
- Deployment variables are documented in `.env.example` and deployment docs.
- The V2 target is a separate Rise2 stack integrating WOS API/workers, PostgreSQL, Redis,
  qBittorrent, NewGreedy, ingress, Prometheus, Grafana, node-exporter, and cAdvisor.
- Rise2 uses secrets, networks, volumes, storage, and monitoring isolated from V1.
- The pinned NewGreedy 1.7.5 runtime keeps its writable CA in a dedicated volume mounted at
  `/root/.mitmproxy`. Its config is a read-only `/app/config.ini` bind, while `stats.json`,
  `torrent_registry.json`, `newgreedy.log`, and `purge_pending.json` are individually backed by
  one root-owned persistent state directory. Do not replace these paths with `/app/data`, mount a
  volume over `/app`, or force the service away from the image's validated root user. NewGreedy
  retains all dropped capabilities, no-new-privileges, a read-only root filesystem, and no host
  port.

## Authentication and authorization

- All user file and torrent routes require authentication.
- Administrative routes additionally require the administrator role.
- Server-side code resolves the authenticated user; never trust a client username.
- User workspace validation happens before any filesystem or qBittorrent action.
- A user may act only inside the workspace assigned to that user.
- API responses must not reveal server paths, credentials, or other users' data.

## File-manager invariants

- All paths are interpreted relative to the authenticated user's workspace.
- Reject absolute paths, `..` traversal, and escapes from the workspace root.
- Reject symlink traversal for protected file operations.
- File rename accepts only a new basename from the client.
- The backend preserves and reconstructs the protected file extension.
- Compound extensions such as `.tar.gz` must remain intact.
- Files without an extension and hidden files require explicit test coverage.
- Folder creation is limited to one new path component at a time.
- A successful folder creation refreshes the listing and shows a success notice.
- A failed folder creation keeps the user on the page and shows an error notice.
- The UI exposes separate Name and Extension columns.
- Long names must not cause horizontal page overflow on mobile widths.
- Breadcrumbs may wrap or scroll within their own container.

## Folder archive downloads

- Folder downloads are streamed as ZIP archives.
- Archives use `ZIP_STORED`; do not recompress user data.
- Archive bytes are generated directly into the HTTP response; do not create a temporary ZIP.
- Admit only one concurrent folder archive per application process.
- Source traversal must be descriptor based where supported.
- Use `O_NOFOLLOW` protections where available.
- Refuse symlinks and path escapes instead of following them.
- Enforce the configured maximum source size before producing the archive.
- Release the archive concurrency slot after the response completes, disconnects, or fails.
- Never write generated archives into a user's visible download tree.

## Torrent submission

- Each authenticated multipart upload carries one `.torrent` file. A frontend multi-upload batch
  issues several independent bounded requests rather than creating one large backend transaction.
- The server parses bencode strictly and rejects malformed metainfo.
- The exact raw bencoded `info` dictionary bytes define the info hash.
- Do not re-encode `info` before computing the hash.
- Announce URLs are restricted to the configured C411 tracker host allowlist.
- The tracker URL is rewritten server-side to include the WOS passkey.
- The default allowed hosts are `c411.org` and `tk.c411.tw`.
- The canonical path is `/announce/{URL-encoded WOS passkey}` on an allowed tracker.
- The WOS passkey is read only from `WOS_C411_PASSKEY`.
- It is represented as a secret value in application settings.
- A passkey supplied inside uploaded metainfo is discarded from memory.
- Never persist a user passkey in the database.
- Never return a passkey to the frontend or an API client.
- Never write a passkey to logs, notifications, options, or diagnostics.
- Functional options must not expose or store this passkey.

## qBittorrent integration

- Rise2 derives the private qB bootstrap from the existing deployment integration registry;
  no independent WebUI credential source or manual UI initialization is required. Host validation
  and CSRF stay enabled, `qbittorrent` is explicitly allowed, and NewGreedy proxies trackers but
  never peers. Runtime reconciliation occurs before qB starts and preserves unrelated profile state.
- Workers/scheduler read the existing registry through a Compose secret at process startup, keeping
  it out of normalized Compose environment output. The API never receives that secret.

- qBittorrent login must support the documented Web API response variants.
- HTTP 204 and a body equal to `Ok.` are accepted login results.
- `Fails.`, HTTP 401, and request failures are handled as failures.
- The save path is always derived server-side.
- The client cannot provide or override `save_path`.
- The derived save path is `/data/<username>/downloads`.
- Validate the user's workspace before submitting anything to qBittorrent.
- Store only safe torrent metadata needed for user-specific status display.
- The `user_torrents` table associates a submission with its owner.
- Torrent listings are filtered by the authenticated user.
- Normalize qBittorrent states before sending them to the frontend.
- Polling must tolerate temporary qBittorrent unavailability.

## V2 scheduler authority and download slots

- The V2 scheduler is the sole authority that decides which managed torrents may download.
- The global number of active downloads is PostgreSQL-configurable; the expected operating
  range is normally one or two, but no implementation may hardcode either value.
- A torrent must be added to qBittorrent stopped or through an equivalent safe sequence, so it
  cannot download before the first scheduler decision.
- Completed torrents may keep seeding in qBittorrent and consume zero active-download slots.
- Scheduling cost is based on robust remaining bytes, not the original total size alone.
- Weighted fairness, persistent deficit, size classes, aging, anti-starvation, per-user caps, and
  future account weights remain required when active-slot authority is hardened.
- Stall detection is based on durable useful-progress observations, not only an instantaneous
  zero download speed. PostgreSQL owns cooldown and retry state so a restart cannot erase it.
- A shared physical torrent enters the weighted-fair queue of every active owner but may be
  selected only once per cycle. The persisted user cursor rotates the charged beneficiary across
  cycles and restarts; the selected beneficiary's cap, deficit, and future account weight apply,
  without ever creating another physical torrent.
- Each scheduler control cycle contains every currently active download first, then fills the
  remaining capacity up to 200 from a circular `(created_at, id)` PostgreSQL scan cursor. The
  cursor is durable in the singleton scheduler row, so backlogs beyond one window keep progressing
  across cycles and process restarts instead of blocking the scheduler.
- A completed managed torrent records its first `READY` timestamp and a durable automatic
  expiration derived from the historical number of distinct requesting users. Later requests may
  extend that deadline but never shorten it; cancelled and expired requests remain part of the
  historical popularity count.
- READY expiration is claimed from a partial indexed PostgreSQL scan in bounded batches. It expires
  active rights and accounting atomically, persists `PURGE_PENDING`, a scheduler stop intent, and
  one immediate purge job. Redis only accelerates worker wake-up and realtime refresh.
- Entering `PURGE_PENDING`, including cancellation of the last owner while downloading, must be
  stopped through the scheduler's qBittorrent control gateway. The API never issues qB start/stop
  calls. Physical deletion remains worker-side and waits for all durable download leases to end.

## V2 realtime state delivery

- The V2 downloads page performs one authoritative PostgreSQL-backed load, then receives only
  significant domain transitions through an API WebSocket; it must not poll the complete list
  every ten seconds.
- A worker or scheduler publishes a lightweight Redis event only after the corresponding database
  transaction commits. Redis and WebSockets remain non-authoritative and may lose events.
- Reconnection performs an authoritative GET resynchronization, and the UI retains an explicit
  manual refresh action.
- Progress changes do not produce an event for every fractional percentage update.
- An idle WebSocket holds no SQL session and performs no periodic SQL query; its heartbeat is
  network-only.

## V2 recursive transfer scalability

- Recursive browser downloads consume a stable manifest snapshot progressively: fetch the first
  page, start transfers, then prefetch later pages through a bounded queue.
- The browser must not load a complete very large manifest before transferring the first file.
- Resume offsets become durable only after the corresponding local write has succeeded. Resume
  validates the actual local size whenever the browser API permits it and handles write, close,
  abort, permission, missing-device, and disk-full failures without skipping bytes.

## V2 operational recovery

- Production workers fail fast when required integration configuration is missing or invalid;
  development and test fixtures remain explicitly supported.
- Only the scheduler and workers may receive the production integration registry and join the
  torrent network. They publish secret-free, per-account integration health and immutable,
  bounded qBittorrent inventory snapshots to PostgreSQL. The API, Prometheus metrics, and
  administrative reconciliation consume those durable observations without integration
  credentials or direct qBittorrent/NewGreedy access; stale or incomplete observations fail
  closed.
- The V2 API runs as exactly one process until a representative load test authorizes a topology
  change. Download/archive admission remains process-local under that enforced topology; Redis is
  not promoted to a durable or distributed limiter without measured need.
- The deterministic 100-account suite and the disposable complete-profile smoke validate the
  single-process invariants and bounded PostgreSQL use in CI. They do not replace the sustained
  CPU/RAM/I/O and failure-injection acceptance on Rise2; that host validation remains mandatory
  before the pilot and before any API process-count change.
- `StorageLedger.managed_bytes` is the declared capacity reserved by non-purged managed torrents,
  including content not fully downloaded yet. It is not a filesystem measurement; observed media
  capacity is represented separately by `disk_total_bytes` and `disk_free_bytes`.
- A qBittorrent reset or state loss must reconcile deterministically with PostgreSQL and shared
  storage. Missing qB rows cannot remain as permanent phantom downloads in the UI.
- Administrative recovery operations may reconcile, cancel, or purge orphaned requests through
  safe business actions. Cancelling an orphan revokes SQL rights without deleting physical data;
  metadata purge is allowed only after exact qBittorrent and shared-storage checks both prove the
  physical torrent absent. Recovery never deletes files automatically while ownership or physical
  state is ambiguous. The API only enqueues idempotent durable recovery jobs; a worker performs
  external checks and persists the result in PostgreSQL.

## Torrent user experience

- A torrent queue number exposed to users is always an estimate of the physical
  `ManagedTorrent`'s rank in the eligible backlog's deterministic `(created_at, id)` scan order.
  It deliberately does not rotate with the scheduler's circular scan cursor and is never a FIFO
  promise or browser-side scheduling prediction; the real weighted-fair selector remains the sole
  authority for admission and qBittorrent controls.
- Exact per-file positions exist only inside the bounded recursive-download queue controlled by
  the current browser. A `.torrent` remains an atomic BitTorrent acquisition; users may choose
  individual files only after READY when transferring manifest content to their own device.

- The upload page supports drag and drop and an explicit, accessible file-selection control on
  desktop and mobile. Both paths accept multiple `.torrent` files in one action.
- Multi-upload remains a set of independent backend requests driven by a bounded frontend queue.
  The batch size and concurrency are capped, each file retains its own result, one failure does not
  cancel the others, and completion may trigger one authoritative list refresh.
- The page shows only torrents associated with the current user.
- Torrent state uses the existing WebSocket plus manual/authoritative refresh model. Multi-upload
  must not reintroduce periodic list polling, a full page reload, or unbounded parallel requests.
- User torrent and manifest contracts expose only the PostgreSQL-authoritative absolute READY
  expiration. The browser may derive accessible 48-hour warning and 24-hour danger countdowns
  locally, but only a backend state transition or authoritative resynchronization may mark content
  expired. A shared retention extension invalidates every active owner's view.
- Long torrent names wrap or truncate without breaking mobile layout.
- Mobile behavior is covered at 320, 360, 375, 390, 430, and 768 pixel widths, in portrait and
  landscape, without relying on drag and drop as the only submission path.

## Notifications

- Use the shared toast system for punctual user-action feedback. Durable page states such as an
  unavailable service, a list that cannot load, or an empty result remain inline.
- Use success for completed actions.
- Use error for failed actions that need correction or retry.
- Use warning for degraded or risky states.
- Use information for neutral guidance.
- Use progress for operations still running.
- Toasts provide `aria-live` semantics, keyboard access, manual close, reasonable automatic
  expiry, bounded stacking, and a mobile-safe layout.
- Notifications must not contain secrets or internal absolute paths.

## Frontend interaction and visual policy

- Destructive delete actions do not use a confirmation-only modal. They use an explicit label and
  danger styling, remain keyboard and screen-reader accessible, disable while pending, and reject
  double submission. Irreversible trash deletion and administrative purge retain an explicit,
  accessible inline confirmation step without opening a modal. Dialogs that collect required
  information or serve a purpose beyond asking "are you sure?" remain allowed.
- The visual palette is expressed through centralized design tokens for backgrounds, surfaces,
  text, borders, semantic states, focus, hover, and elevation. Components must not accumulate
  arbitrary duplicated colors, and important contrast must meet WCAG expectations.
- Every user and administration surface is responsive across the supported mobile widths and
  desktop. Tables use cards, data labels, a dedicated mobile layout, or narrowly scoped local
  scrolling rather than causing global horizontal overflow.
- Long filenames and torrent names cannot hide actions or expand the page. Primary actions remain
  available without hover, touch targets remain usable, focus stays coherent after mutations, and
  orientation changes never require a page reload.

## Functional options

- V1 instance-wide functional options are stored through the existing option model.
- Options may control safe limits such as maximum archive source size.
- Secret credentials do not belong in functional options.
- Option changes must retain existing validation and administrative authorization.
- Restart behavior must use the centralized safe WOS restart path.
- V2 safe dynamic options are authoritative in PostgreSQL and audited.
- Infrastructure paths, service URLs, credentials, encryption keys, and TLS material remain
  environment/deployment secrets and are never editable as functional options.

## Database changes

- Schema changes require an Alembic migration.
- Migrations must upgrade cleanly from the current `develop` baseline.
- Models, schemas, routes, and migrations must stay consistent.
- User-owned rows require an explicit ownership relationship.
- Database records must not contain tracker passkeys or uploaded metainfo secrets.

## Testing requirements

- Run `uv run ruff check .`.
- Run `uv run ruff format --check .`.
- Run `uv run mypy app tests` from the backend project context.
- Run the complete backend pytest suite.
- Run `npm run check` in the frontend.
- Run the complete frontend test suite.
- Run the frontend production build.
- Validate deployment artifacts and application version consistency.
- Build and start the production stack in CI.
- Verify the application image reports the expected version.
- Verify PostgreSQL is not published.
- Verify the application port exposure remains host-only.
- Add regression tests for every security boundary changed.
- Add accessibility assertions for new interactive controls.

## Versioning and release

- Keep all version declarations synchronized.
- Use `scripts/versioning.py` for version changes and consistency checks.
- Update dependency locks when dependency declarations change.
- The current V1 maintenance release version is `1.3.3`.
- The release PR targets `master` from `develop`.
- Merge the release only after backend, frontend, and container CI are green.
- Confirm the resulting `master` and `develop` commit identifiers at handoff.

## Security review checklist

- Search for credentials before committing.
- Confirm `.env` files and runtime secrets remain untracked.
- Confirm no API response includes a passkey.
- Confirm no log call includes uploaded announce URLs with credentials.
- Confirm client input cannot select another user's workspace.
- Confirm filesystem operations remain beneath the workspace root.
- Confirm archive traversal refuses symlinks.
- Confirm archive resources and concurrency slots are released on success and error.
- Confirm torrent upload accepts only the intended file type and tracker.
- Confirm the database contains ownership metadata but no passkey.

## Agent workflow

- Read this file and `PROGRESS.md` before changing the repository.
- Inspect the working tree before editing.
- Preserve unrelated user changes.
- Make focused commits with descriptive messages.
- Keep PR descriptions explicit about behavior, security, tests, and deployment.
- Read failing CI job logs only when a job fails.
- Apply the smallest correct fix and rerun the full affected workflow.
- Update `PROGRESS.md` at the end of each substantial PR.
- Update this file only when stable architecture or policy changes.
- Never place secrets, tokens, passkeys, or private URLs in agent documents.

## Official V1/V2 separation rule

V1 `1.3.3` is released. V1 maintenance remains isolated on `develop` and `master`.
V2 work is authorized only as a scoped branch from `develop_V2`, with its own pull request
back to `develop_V2`. Do not mix V1 hotfixes and V2 implementation. The future V2 release
and Rise2 deployment require an explicit, separately validated workflow; they do not imply
direct feature merges to `master`.

For every task, read only this file and `PROGRESS.md` first, then only the files required by
the task. Avoid repository-wide re-analysis and opportunistic refactors. Run targeted tests
during development and the complete CI once when ready. Update `PROGRESS.md` at the end;
change this file only for durable policy or architecture decisions.

## Authoritative references

- `README.md` for product setup and common commands.
- `docs/architecture-v1.md` for the current architecture.
- `docs/deployment-ovh.md` for production deployment.
- `.env.example` for supported environment variables without secret values.
- `compose.yaml` and `deploy/compose.production.yaml` for V1 runtime wiring.
- `docs/architecture-v2.md`, `docs/roadmap-v2.md`, and
  `docs/deployment-rise2-v2.md` for the V2 target and delivery order.
- `.github/workflows/` for required automated checks.
- `docs/agent/PROGRESS.md` for the current handoff state.
