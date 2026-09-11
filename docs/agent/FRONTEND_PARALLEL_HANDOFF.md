# Frontend 2.1 parallel handoff

- `BASE_SHA`: `0347f0fd84197247887e2597a94dee113f2d0f01`
- Integration branch: `integration/frontend-2.1`
- Task branch: `frontend/language-recovery-admin-nav`
- Final version: `2.1.2`
- `HEAD_FINAL`: merge commit of the task PR on `integration/frontend-2.1` (reported with its exact SHA in the PR and final execution report)

## Components changed

- `frontend/src/components/LanguageFlag.tsx`
- `frontend/src/components/LanguageSelector.tsx`
- `frontend/src/components/icons.tsx`
- `frontend/src/features/dashboard/UserDashboardPage.tsx`
- `frontend/src/features/dashboard/UserDashboardPage.test.tsx`
- `frontend/src/features/torrents/UserDownloadsPage.tsx`
- `frontend/src/features/torrents/UserDownloadsPage.test.tsx`
- `frontend/src/App.tsx`
- `frontend/src/App.test.tsx`
- `frontend/src/i18n.test.tsx`
- `frontend/src/i18n.tsx`
- `frontend/src/wos-2-1.css`
- `frontend/package.json`
- `frontend/package-lock.json`
- `frontend/src/theme.test.tsx`
- `frontend/src/wos-premium-torrents.css`
- `frontend/src/wos-premium.css`
- version mirrors managed by `scripts/versioning.py`, plus the frontend UI version mirrors
- `scripts/validate_v2_stable_release.py` and `backend/tests/test_versioning.py` (CI compatibility for post-2.0 versions only)

## Torrent list contract

The desktop header and every torrent row use the same `--torrent-list-columns` CSS custom property. The six tracks are name, state, queue, progress, size, and actions. Actions are part of the row grid, so hover and row separation cover the complete width. Mobile switches to named grid areas and hides the desktop header.

On desktop, the header and list now read as one component: the header keeps only its top radii, the list keeps only its bottom radii, and the list top border is removed so there is one clean separator and no vertical gap.

`PAGE_SIZE` is exported once and set to `25`. Torrent list API collection uses `limit=25` and sequential offsets `0`, `25`, `50`, and so on. Client pagination also slices in groups of 25. Search and filters reset to page 1; an invalid page is clamped after realtime data changes.

## Details panel

The native row-wide `<summary>` trigger was removed. The existing `ListTree` action remains the primary accessible details control and exposes dynamic `aria-expanded`, `aria-controls`, and show/hide labels. A pointer/touch click on a non-interactive area of the row is also a shortcut that opens or closes the details. The row is deliberately not assigned `role="button"` because it contains interactive controls.

Clicks originating from `button`, `a`, `input`, `select`, `textarea`, `[role="button"]`, or the action group are ignored by the row handler. Download, delete/cancel, refresh/retry, and future interactive descendants therefore keep their own behavior without also toggling details. Open torrent IDs live in parent state, so object replacement during realtime refresh does not close a panel. IDs are pruned only when a torrent disappears from the authoritative list.

The details surface, metadata cards, READY content, ZIP action, and file rows use the current WOS dark-green tokens. Opening uses a short opacity/translation animation and honors `prefers-reduced-motion`.

## Tooltips and downloads

`Tooltip` was added to the shared UI primitives. With `overflowOnly`, it measures the rendered child through `ResizeObserver` and only exposes the visual tooltip/focus stop when the torrent name or file path is actually truncated. Full values remain available through accessible labels.

The compatibility ZIP action is rendered only when `archive_available` is true and is labelled “Télécharger le contenu en ZIP”. Individual managed and native file downloads keep their existing behavior but now use the same compact icon-only WOS control and an accessible file-specific label.

## Backend integration

This branch changes no backend contract and has no dependency on the parallel backend branch. Folder-level ZIP endpoints were not invented or consumed. If the backend synthesis introduces stable folder archive metadata/routes, the styled ZIP control can be reused during integration.

The only files under `backend/` changed by this task are authoritative version mirrors updated by `python scripts/versioning.py set --channel v2 2.1.2`; no backend behavior or contract changed.

The first CI run exposed two release-policy tests that assumed the live repository must remain at 2.0.0 forever. The validator now keeps the immutable V2-35 manifest evidence pinned to 2.0.0 while validating the current repository mirrors against their actual version. The corresponding test follows the current canonical version. This is limited to release validation/tests and does not alter runtime backend behavior.

## Account settings 2.1.2

The Settings page now uses a two-column application layout with a 240 px navigation rail and one active content panel. The sections are:

- **General**: language and username;
- **Security**: current/new/confirmation password form.

The active section uses local React state, with General selected by default. Only the active panel is rendered. Existing locale persistence, username update/toast/navbar refresh, password validation, and post-password session behavior are unchanged.

The `COMPTE` eyebrow, the complete Light/Dark/System selector UI, and the triangle/forest SVG pseudo-element were removed at their source. Theme state and persistence infrastructure remain available to avoid an unrelated architectural deletion. The global WOS forest background remains unchanged.

The page is flatter, with section separators instead of giant cards. The back control is an aligned inline flex row. At 820 px and below, the sidebar becomes horizontally scrollable tabs above the content and the main layout becomes one column, preventing page-level horizontal overflow.

## Language flags

The Unicode `🇫🇷` / `🇬🇧` glyphs have been removed from `LanguageSelector`. The reusable `LanguageFlag` component uses the bundled `country-flag-icons@1.6.20` React 3x2 SVG components (`FR` and `GB`), so rendering does not depend on Apple Color Emoji, Segoe UI Emoji, an operating-system font, or an external CDN.

The compact login toggle and the General account-settings selector share this component. The native `<select>` keeps plain localized option text while the adjacent SVG reflects the selected locale. Button and select accessible names remain textual (`Français` / `English` through the existing localized labels); flags are decorative.

## Local recovery audit and fix

Before this task, the complete path was:

- managed file/folder action → `showSaveFilePicker` / `showDirectoryPicker` → `BrowserDownloadManager` → `RecursiveDownloadController` streaming fetch → `managerSnapshot` → `onLocalTransferChanged` → `LocalDownloadCard`;
- compatibility file or ZIP action → native `<a download>` endpoint → browser download manager, with no update to `managerSnapshot` or `LocalDownloadCard`.

The first path already reports real received bytes, total size, percentage, queue state, completion, and failures without buffering the whole file. It is unchanged. The second path was the reason the Dashboard card appeared disconnected on browsers using native downloads.

Native file and streamed-ZIP clicks now create a lightweight local entry containing an ID, display name, kind, `started` status, and start timestamp. Recent entries are stored under `wos.local-download-starts`, capped at ten, and removed after 30 minutes. The card displays the latest name and the count of other starts. It deliberately displays no byte count, percentage, speed, or `completed` state because a normal page cannot query Chrome/Brave/Firefox native download progress. Managed streams continue to show only their real progress and error state.

## Administration navigation

The primary authenticated navigation now renders `Dashboard`, `Paramètres`, and `Administration` for users whose API model has `user.is_admin === true`. Non-admin users receive no Administration element at all. The entry uses the shared ghost navigation button, a Lucide `ShieldCheck` icon, the existing `admin-users` route, and the same `aria-current` active treatment for all admin subviews.

Administration was not reintroduced into `AccountMenu`; that dropdown remains limited to the validated account identity and logout action. Existing `user.is_admin` route guards remain in place, and backend authorization is unchanged.

## Validation for language/recovery/admin navigation

- SVG flags and absence of emoji rendering tested for FR and EN;
- login and General settings locale changes and accessible names tested;
- native single-file, individual-file, and ZIP starts tested;
- multiple recent starts, stale-entry cleanup, persistence restoration, and absence of fake progress tested;
- managed recovery error rendering retained and tested;
- admin/non-admin navigation, existing admin route, active state, and minimal dropdown tested;
- responsive navigation tightened below 680 px so three admin entries do not create horizontal overflow.

## Synthesis notes

- Recheck the manifest types and folder ZIP contract after merging `integration/backend-2.1`.
- Re-run the authenticated dashboard visual matrix after the combined branch is reachable. The cloud browser used by this work rejected the local-only preview with `ERR_BLOCKED_BY_CLIENT`; DOM, responsive CSS, accessibility, behavior, and production build were validated locally, but no claim of an authenticated browser screenshot is made here.
- When synthesizing the parallel frontend/backend work, preserve the 2.1.2 version mirrors and resolve any version-only overlap mechanically; do not take unrelated backend changes from this frontend branch.
- No deployment workflow is expected from this integration branch.
