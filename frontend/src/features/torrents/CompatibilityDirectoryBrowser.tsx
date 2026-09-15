import {
  type ReactNode,
  useCallback,
  useEffect,
  useState,
} from "react";
import {
  Archive,
  ChevronDown,
  ChevronRight,
  Download,
  File,
  Folder,
  FolderOpen,
} from "lucide-react";

import {
  api,
  type TorrentDownloadDirectoriesV2,
  type TorrentDownloadDirectoryV2,
  type TorrentDownloadFileV2,
  type TorrentDownloadManifestPageV2,
} from "../../api/client";
import { Button, StateMessage, Tooltip } from "../../components/ui";
import { useI18n } from "../../i18n";
import { supportsManagedFileDownload } from "./downloadManager";

interface DirectoryListingState {
  error: string;
  loading: boolean;
  response: TorrentDownloadDirectoriesV2 | null;
}

export function CompatibilityDirectoryBrowser({
  fallbackLoading,
  onDownloadFile,
  onLoadFallbackPage,
  onNativeDownload,
  snapshot,
  torrentId,
}: {
  fallbackLoading: boolean;
  onDownloadFile: (file: TorrentDownloadFileV2) => void;
  onLoadFallbackPage: (offset: number) => void;
  onNativeDownload: (name: string, kind: "archive" | "file") => void;
  snapshot: TorrentDownloadManifestPageV2;
  torrentId: string;
}) {
  const { apiError, formatBytes, t } = useI18n();
  const [listings, setListings] = useState<Record<string, DirectoryListingState>>({});
  const [openPaths, setOpenPaths] = useState<Set<string>>(() => new Set());
  const managedFiles = supportsManagedFileDownload();

  const loadDirectories = useCallback((parent: string | null, offset = 0) => {
    const key = parent ?? "";
    setListings((current) => ({
      ...current,
      [key]: { error: "", loading: true, response: current[key]?.response ?? null },
    }));
    const controller = new AbortController();
    void api.getTorrentDownloadDirectoriesV2(torrentId, parent, controller.signal, offset)
      .then((response) => {
        if (
          response.snapshot_id !== snapshot.snapshot_id
          || response.path !== key
          || !Array.isArray(response.directories)
          || !Array.isArray(response.files)
          || response.offset !== offset
        ) {
          throw new Error("manifest_changed");
        }
        setListings((current) => ({
          ...current,
          [key]: {
            error: "",
            loading: false,
            response: offset > 0 && current[key]?.response !== null && current[key]?.response !== undefined
              ? {
                  ...response,
                  offset: 0,
                  files: [...(current[key]?.response?.files ?? []), ...response.files],
                }
              : response,
          },
        }));
      })
      .catch((caught: unknown) => {
        if (caught instanceof DOMException && caught.name === "AbortError") return;
        setListings((current) => ({
          ...current,
          [key]: {
            error: apiError(caught, "downloads.manifestFailed"),
            loading: false,
            response: current[key]?.response ?? null,
          },
        }));
      });
    return () => controller.abort();
  }, [apiError, snapshot.snapshot_id, torrentId]);

  useEffect(() => loadDirectories(null), [loadDirectories]);

  function toggleDirectory(directory: TorrentDownloadDirectoryV2) {
    const path = directory.relative_path;
    setOpenPaths((current) => {
      const next = new Set(current);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });
    if (listings[path] === undefined) loadDirectories(path);
  }

  function renderFile(file: TorrentDownloadFileV2, depth: number): ReactNode {
    const name = file.relative_path.split("/").at(-1) ?? file.relative_path;
    return (
      <li key={file.id} className="ready-tree-file">
        <div className={`ready-tree-file-row ready-tree-depth-${Math.min(depth, 6)}`}>
          <span className="ready-file-label">
            <File aria-hidden="true" />
            <Tooltip content={file.relative_path} overflowOnly className="ready-file-path">
              <strong>{file.relative_path}</strong>
            </Tooltip>
          </span>
          <span className="ready-tree-file-size">{formatBytes(file.size)}</span>
          {managedFiles ? (
            <Tooltip content={t("common.download")}>
              <button
                type="button"
                className="ready-file-download-button"
                aria-label={t("downloads.downloadNamedFile", { name: file.relative_path })}
                onClick={() => onDownloadFile(file)}
              >
                <Download aria-hidden="true" />
              </button>
            </Tooltip>
          ) : (
            <Tooltip content={t("common.download")}>
              <a
                className="ready-file-download-button"
                href={api.torrentFileDownloadUrlV2(torrentId, file.id, snapshot.snapshot_id)}
                download={name}
                aria-label={t("downloads.downloadNamedFile", { name: file.relative_path })}
                onClick={() => onNativeDownload(name, "file")}
              >
                <Download aria-hidden="true" />
              </a>
            </Tooltip>
          )}
        </div>
      </li>
    );
  }

  function renderListing(response: TorrentDownloadDirectoriesV2, depth: number): ReactNode {
    const hasMoreFiles = response.files.length < response.direct_file_count;
    return (
      <>
        {response.directories.map((directory) => renderDirectory(directory, depth))}
        {response.files.map((file) => renderFile(file, depth))}
        {hasMoreFiles && (
          <li className="ready-tree-load-more">
            <Button
              variant="secondary"
              disabled={listings[response.path]?.loading === true}
              onClick={() => loadDirectories(response.path === "" ? null : response.path, response.files.length)}
            >
              {t("common.next")}
            </Button>
          </li>
        )}
      </>
    );
  }

  function renderDirectory(directory: TorrentDownloadDirectoryV2, depth: number): ReactNode {
    const path = directory.relative_path;
    const listing = listings[path];
    const open = openPaths.has(path);
    const detailsLabel = `${t("downloads.details")} — ${directory.name}`;
    const archiveLabel = `${t("downloads.archive")} — ${directory.name}`;
    return (
      <li key={path} className="ready-directory-item">
        <div className={`ready-directory-row${depth === 0 ? " is-root" : ""} ready-tree-depth-${Math.min(depth, 6)}`}>
          <button
            type="button"
            className={`ready-directory-toggle${depth === 0 ? " is-root" : ""}`}
            aria-expanded={open}
            aria-label={detailsLabel}
            onClick={() => toggleDirectory(directory)}
          >
            {depth === 0 ? (
              <>
                <span className="ready-directory-root-label">
                  {open ? <FolderOpen aria-hidden="true" /> : <Folder aria-hidden="true" />}
                  <span className="ready-directory-copy">
                    <Tooltip content={path} overflowOnly focusable={false} className="ready-directory-name">
                      <strong>{directory.name}</strong>
                    </Tooltip>
                  </span>
                </span>
                {open ? <ChevronDown className="ready-directory-chevron" aria-hidden="true" /> : <ChevronRight className="ready-directory-chevron" aria-hidden="true" />}
              </>
            ) : (
              <>
                {open ? <ChevronDown aria-hidden="true" /> : <ChevronRight aria-hidden="true" />}
                {open ? <FolderOpen aria-hidden="true" /> : <Folder aria-hidden="true" />}
                <span className="ready-directory-copy">
                  <Tooltip content={path} overflowOnly focusable={false} className="ready-directory-name">
                    <strong>{directory.name}</strong>
                  </Tooltip>
                  <small>{t(directory.file_count === 1 ? "downloads.contentSummaryOne" : "downloads.contentSummaryMany", {
                    count: directory.file_count,
                    size: formatBytes(directory.total_size),
                  })}</small>
                </span>
              </>
            )}
          </button>
          {directory.archive_available ? (
            <Tooltip content={archiveLabel}>
              <a
                className="ready-folder-download-button"
                href={api.torrentFolderArchiveDownloadUrlV2(torrentId, path, snapshot.snapshot_id)}
                download={`${directory.name}.zip`}
                aria-label={archiveLabel}
                onClick={() => onNativeDownload(`${directory.name}.zip`, "archive")}
              >
                <Archive aria-hidden="true" />
                <span>ZIP</span>
              </a>
            </Tooltip>
          ) : null}
        </div>
        {open && (
          <div className="ready-directory-children">
            {listing?.loading === true && <span className="ready-directory-loading">{t("common.loading")}</span>}
            {listing?.error !== "" && listing?.error !== undefined && (
              <div className="ready-directory-error">
                <span>{listing.error}</span>
                <Button variant="secondary" onClick={() => loadDirectories(path)}>{t("common.retry")}</Button>
              </div>
            )}
            {listing?.response !== null && listing?.response !== undefined && (
              <ul>{renderListing(listing.response, depth + 1)}</ul>
            )}
          </div>
        )}
      </li>
    );
  }

  const root = listings[""];
  if (root?.loading === true && root.response === null) {
    return <StateMessage tone="loading" className="ready-directories-state">{t("downloads.manifestLoading")}</StateMessage>;
  }
  const rootDirectories = root?.response?.directories ?? [];
  const rootFiles = root?.response?.files ?? [];
  const fallbackActive = root?.error !== "" && root?.error !== undefined;
  const fallbackFiles = fallbackActive ? snapshot.items : [];
  const fallbackPageSize = Math.max(1, snapshot.limit);
  if (rootDirectories.length === 0 && rootFiles.length === 0 && fallbackFiles.length === 0) return null;
  return (
    <section className="ready-directory-browser" aria-label={t("downloads.content")}>
      {root?.error !== "" && root?.error !== undefined && root.response === null && (
        <div className="ready-directories-state ready-directories-error">
          <span>{root.error}</span>
          <Button variant="secondary" onClick={() => loadDirectories(null)}>{t("common.retry")}</Button>
        </div>
      )}
      <ul className="ready-directory-list">
        {root?.response !== null && root?.response !== undefined
          ? renderListing(root.response, 0)
          : fallbackFiles.map((file) => renderFile(file, 0))}
      </ul>
      {fallbackActive && snapshot.file_count > fallbackPageSize && (
        <nav className="ready-manifest-pagination" aria-label={t("downloads.compatPagination")}>
          <Button
            variant="secondary"
            disabled={fallbackLoading || snapshot.offset === 0}
            onClick={() => onLoadFallbackPage(Math.max(0, snapshot.offset - fallbackPageSize))}
          >
            {t("common.previous")}
          </Button>
          <span>
            {Math.floor(snapshot.offset / fallbackPageSize) + 1} / {Math.ceil(snapshot.file_count / fallbackPageSize)}
          </span>
          <Button
            variant="secondary"
            disabled={fallbackLoading || snapshot.offset + snapshot.items.length >= snapshot.file_count}
            onClick={() => onLoadFallbackPage(snapshot.offset + snapshot.items.length)}
          >
            {t("common.next")}
          </Button>
        </nav>
      )}
    </section>
  );
}
