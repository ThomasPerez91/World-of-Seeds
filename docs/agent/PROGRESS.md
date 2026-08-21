# World of Seeds — Progress

## Current release

- Version prepared locally: `1.3.1`.
- Product line: V1.
- Feature PR: `#38`, merged into `develop`.
- Release PR: `#39`, merged into `master`.
- Corrective branch: `fix/release-draft-verification`.
- MASTER HEAD after the V1 merge: `3d54e623b91e69409d3e84563de07a2f67e79b12`.
- DEVELOP HEAD before the deployment fix: `c5e8ae9e55f4688e2bd75c42dd146fc2aaaf7f92`.

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

## Validation

- Local Ruff formatting: PASS.
- Local Ruff lint: PASS.
- Local mypy for backend application and tests: PASS.
- Local backend suite: PASS, 160 tests.
- Local version consistency check: PASS, `1.3.1`.
- GitHub Actions backend job: PASS.
- GitHub Actions frontend check: PASS.
- GitHub Actions frontend tests: PASS.
- GitHub Actions frontend build: PASS.
- GitHub Actions production container job: PASS.
- GitHub Actions corrected run: `32406721805`.
- Real credentials present in tracked changes: none found.

## Integration state

- Feature implementation is complete.
- The initial frontend run found one missing accessible label.
- The minimal `aria-label` correction is published.
- The corrected feature CI run is fully green.
- Agent handoff documentation is integrated into `develop` and `master`.
- PR `#38` is merged into `develop` with green CI.
- PR `#39` is merged into `master` with green PR CI.
- The first post-merge deployment stopped before the image build.
- Root cause: a read-only build token could not see the prepared draft release.
- The corrective workflow isolates draft verification in a write-scoped job.
- The build job retains read-only repository access.
- The first retry exposed an existing draft pinned to the previous master SHA.
- Draft retries now retarget only unpublished releases to the current master SHA.
- Release publication passes the repository explicitly when no checkout exists.
- Corrective CI, integration, release, and OVH deployment remain to complete.

## Known constraints

- qBittorrent and the application must share `/srv/seedbox:/data`.
- User torrent save paths remain `/data/<username>/downloads`.
- The C411 passkey remains server-side only.
- Folder archives are streamed directly, uncompressed, concurrency-bounded, and symlink-safe.
- PostgreSQL remains unexposed to the host network.
- No `chmod 777` workaround is acceptable.

## Next task

- Publish the validated `v1.3.1` hotfix through a feature PR into `develop`.
- Merge `develop` into `master` only after every required CI check is green.
- Verify CPU, PostgreSQL pool use, block I/O, readiness, and the first-byte delay of a large folder download on OVH.
