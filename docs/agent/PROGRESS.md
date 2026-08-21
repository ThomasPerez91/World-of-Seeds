# World of Seeds — Progress

## Current release

- Production baseline: `1.3.2`.
- Version prepared locally: `1.3.3`.
- Product line: V1.
- Latest feature PR: `#48`, merged into `develop`.
- Latest release PR: `#49`, merged into `master`.
- Current hotfix branch: `fix/v1-qbittorrent-5-2-add-response`.
- MASTER HEAD before this hotfix: `8e20b8d578fdd68f85acdd9feed327afc089df68`.
- DEVELOP HEAD before this hotfix: `20c4897c3fbba49052123e996e2abfd27170ba72`.

## Completed in the V1 completion feature

- Bumped and synchronized the application version to `1.3.0`.
- Added authenticated `.torrent` multipart upload.
- Added strict bencode parsing and raw `info` hash preservation.
- Added the C411 tracker allowlist and server-side announce rewrite.
- Moved the WOS passkey exclusively to `WOS_C411_PASSKEY`.
- Prevented passkey persistence, response, logging, and option exposure.
- Derived qBittorrent save paths exclusively on the server.
- Added qBittorrent login response compatibility and error handling.
- Added the `user_torrents` persistence model and Alembic migration.
- Added per-user torrent listing and normalized status polling.
- Added drag-and-drop torrent upload with an accessible file selector.
- Added success, error, warning, information, and progress notices.
- Changed rename input to a basename and preserved protected extensions.
- Covered compound extensions, hidden files, and files without extensions.
- Split file-manager display into Name and Extension columns.
- Added secure folder downloads as uncompressed ZIP archives.
- Added controlled temporary archive storage and automatic cleanup.
- Added traversal, symlink, and archive-size protections.
- Added single-level new-folder creation and user feedback.
- Hardened mobile layout for breadcrumbs and long names.
- Updated deployment configuration and documentation for shared `/data` mounts.
- Updated dependency locks and added the multipart dependency.

## Prepared in the V1.3.1 performance hotfix

- Removed recursive folder-size calculation from ordinary file listings.
- Ended the read-only authentication transaction before route and stream processing.
- Replaced temporary folder ZIP creation with direct, uncompressed HTTP streaming.
- Limited folder archive generation to one concurrent request per application process.
- Kept archive traversal, source-size, entry-count, and symlink protections.
- Fixed file-table column sizing, truncated long names with an ellipsis, and kept actions on one line.
- Replaced file mutation, deletion, restoration, and failure notices with SweetAlert2 dialogs.
- Added regression coverage for SQL transaction release, non-recursive listings, immediate ZIP output, and archive concurrency.

## Prepared in the V1.3.2 tracker and CSP hotfix

- Rewrites authorized C411 announces to `/announce/{URL-encoded WOS passkey}`.
- Uses `c411.org` and `tk.c411.tw` as the default tracker allowlist.
- Preserves raw `info` bytes and the original info hash while removing uploaded passkeys.
- Keeps unauthorized tracker hosts rejected for `announce` and `announce-list`.
- Removes SweetAlert2, whose runtime `background` and `color` styles violated the strict CSP.
- Replaces it with a class-only accessible modal without inline styles or CSP relaxation.
- Verifies torrent progress, notices, and file-operation dialogs render without inline styles.

## Prepared in the V1.3.3 qBittorrent response hotfix

- Accepts the structured success response returned by qBittorrent 5.2.x after torrent upload.
- Validates the returned counters and exact expected infohash instead of accepting arbitrary 2xx responses.
- Keeps compatibility with legacy `200 Ok.` and `204 No Content` success responses.
- Keeps explicit rejections, malformed responses, authentication failures, and mismatched hashes rejected.
- Persists the `UserTorrent` association after qBittorrent confirms the expected torrent was accepted.

## Validation

- Local Ruff formatting: PASS.
- Local Ruff lint: PASS.
- Local mypy for backend application and tests: PASS.
- Local backend suite for this hotfix: targeted PASS, 19 tests; complete PASS, 165 tests.
- Local version consistency check: PASS, `1.3.3`.
- GitHub Actions backend job: PASS.
- GitHub Actions frontend check: PASS.
- GitHub Actions frontend tests: PASS.
- GitHub Actions frontend build: PASS.
- GitHub Actions production container job: PASS.
- GitHub Actions corrected run: `32406721805`.
- Real credentials present in tracked changes: none found.

## Integration state

- The `1.3.1` performance hotfix is integrated into `develop` and `master` through PRs `#46` and `#47` with green CI.
- The `1.3.2` tracker/CSP hotfix is integrated into `develop` and `master` through PRs `#48` and `#49`.
- The `1.3.3` qBittorrent response hotfix is implemented and targeted tests are green locally.
- No V2 code, database migration, or CSP relaxation is included.

## Known constraints

- qBittorrent and the application must share `/srv/seedbox:/data`.
- User torrent save paths remain `/data/<username>/downloads`.
- The C411 passkey remains server-side only.
- Folder archives are streamed directly, uncompressed, concurrency-bounded, and symlink-safe.
- PostgreSQL remains unexposed to the host network.
- No `chmod 777` workaround is acceptable.

## Next task

- Publish the `1.3.3` hotfix through a PR into `develop`.
- Run the complete GitHub CI once and merge only when backend, frontend, and container jobs are green.
- Open and validate the `1.3.3` release PR from `develop` to `master`.
