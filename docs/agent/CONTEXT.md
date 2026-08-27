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

- Users upload a single `.torrent` file through authenticated multipart upload.
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
- A shared physical torrent may serve several users. Its scheduling beneficiary must be selected
  by a deterministic, restart-stable, weighted-fair policy rather than permanently charging the
  first requester.
- Scheduler processing stays bounded, but a backlog larger than one window must continue through
  pagination or cursors instead of stopping the whole scheduler.

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
- A qBittorrent reset or state loss must reconcile deterministically with PostgreSQL and shared
  storage. Missing qB rows cannot remain as permanent phantom downloads in the UI.
- Administrative recovery operations may reconcile, cancel, or purge orphaned requests through
  safe business actions. They never delete files automatically while ownership or physical state
  is ambiguous.

## Torrent user experience

- The upload page supports drag and drop and an explicit file-selection control.
- The file input must retain an accessible label.
- Notices cover success, error, warning, information, and progress states.
- The page shows only torrents associated with the current user.
- Status refresh is periodic and must not duplicate existing submissions.
- Long torrent names wrap or truncate without breaking mobile layout.
- Mobile behavior is covered at 320, 375, 390, and 430 pixel widths.

## Notifications

- Use the shared notification system for user-visible operation feedback.
- Use success for completed actions.
- Use error for failed actions that need correction or retry.
- Use warning for degraded or risky states.
- Use information for neutral guidance.
- Use progress for operations still running.
- Notifications must not contain secrets or internal absolute paths.

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
