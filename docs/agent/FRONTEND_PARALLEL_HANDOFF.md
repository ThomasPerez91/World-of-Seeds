# Frontend 2.1 parallel handoff

- `BASE_SHA`: `d40aecc0732bae3de8e14e8027cbe782887cac60`
- Integration branch: `integration/frontend-2.1`
- Task branch: `frontend/torrent-list-details-ui`
- `HEAD_FINAL`: merge commit of the task PR on `integration/frontend-2.1` (reported with its exact SHA in the PR and final execution report)

## Components changed

- `frontend/src/features/torrents/UserDownloadsPage.tsx`
- `frontend/src/features/torrents/UserDownloadsPage.test.tsx`
- `frontend/src/components/ui.tsx`
- `frontend/src/i18n.tsx`
- `frontend/src/wos-premium-torrents.css`

## Torrent list contract

The desktop header and every torrent row use the same `--torrent-list-columns` CSS custom property. The six tracks are name, state, queue, progress, size, and actions. Actions are part of the row grid, so hover and row separation cover the complete width. Mobile switches to named grid areas and hides the desktop header.

`PAGE_SIZE` is exported once and set to `25`. Torrent list API collection uses `limit=25` and sequential offsets `0`, `25`, `50`, and so on. Client pagination also slices in groups of 25. Search and filters reset to page 1; an invalid page is clamped after realtime data changes.

## Details panel

The native row-wide `<summary>` trigger was removed. The existing `ListTree` action is now the only details control and exposes dynamic `aria-expanded`, `aria-controls`, and show/hide labels. Open torrent IDs live in parent state, so object replacement during realtime refresh does not close a panel. IDs are pruned only when a torrent disappears from the authoritative list.

The details surface, metadata cards, READY content, ZIP action, and file rows use the current WOS dark-green tokens. Opening uses a short opacity/translation animation and honors `prefers-reduced-motion`.

## Tooltips and downloads

`Tooltip` was added to the shared UI primitives. With `overflowOnly`, it measures the rendered child through `ResizeObserver` and only exposes the visual tooltip/focus stop when the torrent name or file path is actually truncated. Full values remain available through accessible labels.

The compatibility ZIP action is rendered only when `archive_available` is true and is labelled “Télécharger le contenu en ZIP”. Individual managed and native file downloads keep their existing behavior but now use the same compact icon-only WOS control and an accessible file-specific label.

## Backend integration

This branch changes no backend contract and has no dependency on the parallel backend branch. Folder-level ZIP endpoints were not invented or consumed. If the backend synthesis introduces stable folder archive metadata/routes, the styled ZIP control can be reused during integration.

## Synthesis notes

- Recheck the manifest types and folder ZIP contract after merging `integration/backend-2.1`.
- Re-run the authenticated dashboard visual matrix after the combined branch is reachable. The cloud browser used by this work rejected the local-only preview with `ERR_BLOCKED_BY_CLIENT`; DOM, responsive CSS, accessibility, behavior, and production build were validated locally, but no claim of an authenticated browser screenshot is made here.
- No deployment workflow is expected from this integration branch.
