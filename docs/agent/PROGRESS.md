# World of Seeds — Progress

## Current release

- Version: `1.3.0`.
- Product line: V1.
- Feature branch: `feature/final-v1-completion`.
- Feature PR: `#38` into `develop`.
- Release PR: pending, from `develop` into `master`.
- MASTER HEAD before release: `7630ae68081e370a3b43c15ad98189d4feee2c3f`.
- DEVELOP HEAD before feature merge: `d81ec069c2c93a3ecbd16c5011363fbac757da98`.
- Feature remote HEAD after accessibility fix: `b6b943209f22ff95a071e1cb90e744c502e203e6`.

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

## Validation

- Local Ruff formatting: PASS.
- Local Ruff lint: PASS.
- Local mypy for backend application and tests: PASS.
- Local backend suite: PASS, 160 tests.
- Local version consistency check: PASS, `1.3.0`.
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
- Agent handoff documentation is being added before integration.
- PR `#38` still needs to be marked ready and merged into `develop`.
- A release PR still needs to be opened from `develop` to `master`.
- The release PR must pass all required CI before merge.
- Final branch SHAs must be recorded in the completion handoff.

## Known constraints

- qBittorrent and the application must share `/srv/seedbox:/data`.
- User torrent save paths remain `/data/<username>/downloads`.
- The C411 passkey remains server-side only.
- Folder archives are temporary, uncompressed, bounded, and symlink-safe.
- PostgreSQL remains unexposed to the host network.
- No `chmod 777` workaround is acceptable.

## Next task

- Finish the V1 integration and release sequence only.
- Merge PR `#38` into `develop` after documentation CI is green.
- Open and validate the `develop` to `master` release PR for `1.3.0`.
- Merge the release only with green backend, frontend, and container checks.
- Do not start any V2 implementation as part of this release.
- After V1 is released, the only authorized next product task is `Lot V2-A`.
