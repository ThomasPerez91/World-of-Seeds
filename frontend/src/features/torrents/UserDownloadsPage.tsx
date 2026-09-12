import {
  type CSSProperties,
  type ChangeEvent,
  type DragEvent,
  type ReactNode,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import {
  api,
  ApiError,
  parseTorrentRealtimeMessage,
  type TorrentDownloadDirectoriesV2,
  type TorrentDownloadDirectoryV2,
  type TorrentDownloadFileV2,
  type TorrentDownloadManifestPageV2,
  type TorrentRequestV2,
  type TorrentRequestV2State,
} from "../../api/client";
import { useFeedback } from "../../components/Feedback";
import {
  DeleteIcon,
  DownloadIcon,
  InfoIcon,
  QueueIcon,
  RefreshIcon,
} from "../../components/icons";
import {
  Archive,
  Check,
  ChevronDown,
  ChevronRight,
  Clock3,
  Download,
  File,
  Folder,
  FolderOpen,
  ListTree,
  Search,
} from "lucide-react";
import { Accordion, Badge, Button, Progress, StateMessage, Tooltip } from "../../components/ui";
import { useI18n, type MessageKey } from "../../i18n";
import {
  BrowserDownloadManager,
  DownloadPolicyRequestError,
  loadBrowserDownloadPolicy,
  pickManagedDownloadFile,
  supportsManagedFileDownload,
  type BrowserDownloadJobSnapshot,
  type BrowserDownloadManagerSnapshot,
} from "./downloadManager";
import {
  DEFAULT_RECURSIVE_DOWNLOAD_CONCURRENCY,
  pickDownloadDirectory,
  type LocalTransferQueueItem,
  type RecursiveTransferErrorCode,
  type RecursiveTransferProgress,
  supportsRecursiveDirectoryDownload,
} from "./recursiveDownload";
import { SubscriptionExpiryIndicator } from "./SubscriptionExpiryIndicator";

export const PAGE_SIZE = 25;
const AUTO_REFRESH_MS = 4_000;
const FALLBACK_PAGE_SIZE = 50;
export const MAX_TORRENT_BATCH_FILES = 50;
export const TORRENT_UPLOAD_CONCURRENCY = 3;

export interface LocalDownloadSummary {
  active: number;
  maximum: number;
  status: "idle" | RecursiveTransferProgress["status"];
  waiting: number;
  name: string | null;
  downloadedBytes: number;
  totalBytes: number;
  percent: number;
}

interface UserDownloadsPageProps {
  onActivityChanged?: () => void;
  onLocalTransferChanged?: (summary: LocalDownloadSummary) => void;
  onSessionExpired: () => void;
}

export function summarizeLocalTransfer(
  transfer: RecursiveTransferProgress | null,
): LocalDownloadSummary {
  return {
    active: transfer?.queue.filter((item) => item.status === "active").length ?? 0,
    maximum: DEFAULT_RECURSIVE_DOWNLOAD_CONCURRENCY,
    status: transfer?.status ?? "idle",
    waiting: transfer?.queue.filter((item) => item.status === "waiting").length ?? 0,
    name: null,
    downloadedBytes: transfer?.downloadedBytes ?? 0,
    totalBytes: 0,
    percent: 0,
  };
}

export function summarizeDownloadManager(
  manager: BrowserDownloadManagerSnapshot,
): LocalDownloadSummary {
  const jobs = manager.jobs;
  const waitingStreams = jobs.reduce(
    (count, job) => count + job.queue.filter(
      (item) => item.status === "waiting" || (job.status === "queued" && item.status === "active"),
    ).length,
    0,
  );
  const waiting = Math.max(manager.waitingJobs, waitingStreams);
  const status: LocalDownloadSummary["status"] = manager.activeStreams > 0 || waiting > 0
    ? "running"
    : jobs.some((job) => job.status === "error")
      ? "error"
      : jobs.some((job) => job.status === "paused")
        ? "paused"
        : jobs.length > 0 && jobs.every((job) => job.status === "completed")
          ? "completed"
          : jobs.length > 0 && jobs.every((job) => job.status === "cancelled")
            ? "cancelled"
            : "idle";
  const current = jobs.find((job) => job.status === "running" || job.status === "paused" || job.status === "queued") ?? null;
  const percent = current === null || current.totalBytes <= 0
    ? 0
    : Math.min(100, Math.max(0, (current.downloadedBytes / current.totalBytes) * 100));
  return {
    active: manager.activeStreams,
    maximum: manager.maxConcurrentStreams,
    status,
    waiting,
    name: current?.name ?? null,
    downloadedBytes: current?.downloadedBytes ?? 0,
    totalBytes: current?.totalBytes ?? 0,
    percent,
  };
}

type UploadResultStatus = "queued" | "uploading" | "added" | "duplicate" | "invalid" | "failed";

interface UploadFileResult {
  error?: string;
  file: File;
  name: string;
  status: UploadResultStatus;
}

interface ReadyManifestState {
  error: string;
  firstPage: TorrentDownloadManifestPageV2 | null;
  loading: boolean;
  requestedOffset: number;
  snapshot: TorrentDownloadManifestPageV2 | null;
}

const EMPTY_MANAGER_SNAPSHOT: BrowserDownloadManagerSnapshot = {
  activeStreams: 0,
  maxConcurrentStreams: DEFAULT_RECURSIVE_DOWNLOAD_CONCURRENCY,
  waitingJobs: 0,
  jobs: [],
};

type TorrentStatusFilter = "all" | "active" | "ready" | "waiting";

export function matchesTorrentFilter(torrent: TorrentRequestV2, filter: TorrentStatusFilter): boolean {
  if (!isVisibleTorrentRequest(torrent)) return false;
  if (filter === "all") return true;
  if (filter === "ready") return torrent.state === "ready";
  if (filter === "waiting") return torrentRowStatus(torrent) === "waiting";
  return torrentRowStatus(torrent) === "downloading";
}

export function isVisibleTorrentRequest(torrent: TorrentRequestV2): boolean {
  return torrent.state !== "cancelled" && torrent.state !== "expired";
}

export type TorrentRowStatus = "downloading" | "ready" | "waiting" | "error";

export function torrentRowStatus(torrent: TorrentRequestV2): TorrentRowStatus {
  if (torrent.state === "ready") return "ready";
  if (torrent.state === "error") return "error";
  if (
    torrent.state === "requested"
    || torrent.queue_status === "waiting"
    || torrent.queue_status === "cooldown"
  ) return "waiting";
  return "downloading";
}

export function torrentQueueLabel(torrent: TorrentRequestV2): string {
  const status = torrentRowStatus(torrent);
  if (status === "ready" || status === "error" || torrent.queue_position_estimate === null) return "-";
  return `#${torrent.queue_position_estimate}`;
}

const rowStatusLabels: Record<TorrentRowStatus, MessageKey> = {
  downloading: "downloads.statusDownloading",
  ready: "downloads.statusReady",
  waiting: "downloads.statusWaiting",
  error: "downloads.statusError",
};

const transferErrorKeys: Record<RecursiveTransferErrorCode, MessageKey> = {
  manifest_incomplete: "downloads.manifestIncomplete",
  manifest_changed: "downloads.manifestChanged",
  received_file_too_large: "downloads.receivedFileInvalid",
  received_file_incomplete: "downloads.receivedFileInvalid",
  local_file_size_invalid: "downloads.localFileInvalid",
  manifest_path_invalid: "downloads.manifestPathInvalid",
  local_disk_full: "downloads.localDiskFull",
  local_write_denied: "downloads.localWriteDenied",
  local_destination_missing: "downloads.localDestinationMissing",
  download_interrupted: "downloads.interrupted",
  local_transfer_failed: "downloads.failed",
};

function LocalQueueLabel({ item }: { item: LocalTransferQueueItem }) {
  const { t } = useI18n();
  if (item.status === "waiting" && item.position !== null) {
    return t(
      item.position === 1 ? "downloads.localWaitingFirst" : "downloads.localWaiting",
      { position: item.position },
    );
  }
  return t({
    active: "downloads.queueDownloading",
    paused: "downloads.localPaused",
    completed: "downloads.localCompleted",
    error: "downloads.localError",
    cancelled: "downloads.localCancelled",
    waiting: "downloads.localWaitingUnknown",
  }[item.status] as MessageKey);
}

function jobStatusLabel(job: BrowserDownloadJobSnapshot, t: (key: MessageKey, params?: Record<string, string | number>) => string) {
  if (job.status === "queued") return t("downloads.batchQueued");
  return t(`downloads.localStatus.${job.status}` as MessageKey);
}

function LocalTransferPanel({
  transfer,
  onCancel,
  onClose,
  onPause,
  onResume,
}: {
  transfer: BrowserDownloadJobSnapshot;
  onCancel: () => void;
  onClose: () => void;
  onPause: () => void;
  onResume: () => void;
}) {
  const { formatBytes, t } = useI18n();
  const error = transfer.error === null ? null : t(transferErrorKeys[transfer.error]);
  return (
    <section className={`recursive-transfer ${transfer.status}`} aria-label={t("downloads.localNamed", { name: transfer.name })}>
      <div className="local-transfer-heading">
        <strong title={transfer.name}>{t("downloads.localRecovery")}</strong>
        <span>
          {t("downloads.files", {
            completed: transfer.completedFiles,
            total: transfer.fileCount,
            downloaded: formatBytes(transfer.downloadedBytes),
            size: formatBytes(transfer.totalBytes),
          })}
        </span>
      </div>
      <Progress
        value={transfer.totalBytes === 0 ? 0 : (transfer.downloadedBytes / transfer.totalBytes) * 100}
        label={t("downloads.localNamed", { name: transfer.name })}
      />
      <div className="local-transfer-metrics">
        <Badge tone={transfer.status === "error" ? "danger" : transfer.status === "completed" ? "success" : "neutral"}>
          {jobStatusLabel(transfer, t)}
        </Badge>
      </div>
      {transfer.status === "queued" && transfer.queuePosition !== null && (
        <p>{t("downloads.localWaiting", { position: transfer.queuePosition })}</p>
      )}
      {error !== null && <p className="local-transfer-error" role="alert">{error}</p>}
      <p className="local-transfer-volatility">{t("downloads.localQueueVolatility")}</p>
      {transfer.queue.length > 0 && (
        <ul className="local-transfer-queue" aria-label={t("downloads.localQueue")}>
          {transfer.queue.map((item) => (
            <li key={`${item.id}-${item.status}`} className={item.status}>
              <span title={item.relativePath}>{item.relativePath}</span>
              <strong><LocalQueueLabel item={item} /></strong>
            </li>
          ))}
        </ul>
      )}
      <div className="recursive-transfer-actions">
        {(transfer.status === "running" || transfer.status === "queued") && (
          <Button variant="secondary" onClick={onPause}>{t("downloads.pauseRecovery")}</Button>
        )}
        {(transfer.status === "paused" || transfer.status === "error") && (
          <Button onClick={onResume}>{t("downloads.resumeRecovery")}</Button>
        )}
        {transfer.status === "completed" || transfer.status === "cancelled" ? (
          <Button variant="secondary" onClick={onClose}>{t("common.close")}</Button>
        ) : (
          <Button variant="secondary" onClick={onCancel}>{t("downloads.cancelRecovery")}</Button>
        )}
      </div>
    </section>
  );
}

interface DirectoryListingState {
  error: string;
  loading: boolean;
  response: TorrentDownloadDirectoriesV2 | null;
}

function TorrentDirectoryBrowser({
  snapshotId,
  torrentId,
}: {
  snapshotId: string;
  torrentId: string;
}) {
  const { apiError, formatBytes, t } = useI18n();
  const [listings, setListings] = useState<Record<string, DirectoryListingState>>({});
  const [openPaths, setOpenPaths] = useState<Set<string>>(() => new Set());

  const loadDirectories = useCallback((parent: string | null) => {
    const key = parent ?? "";
    setListings((current) => ({
      ...current,
      [key]: { error: "", loading: true, response: current[key]?.response ?? null },
    }));
    const controller = new AbortController();
    void api.getTorrentDownloadDirectoriesV2(torrentId, parent, controller.signal)
      .then((response) => {
        if (
          response.snapshot_id !== snapshotId
          || response.path !== key
          || !Array.isArray(response.directories)
        ) {
          throw new Error("manifest_changed");
        }
        setListings((current) => ({
          ...current,
          [key]: { error: "", loading: false, response },
        }));
      })
      .catch((caught: unknown) => {
        if (caught instanceof DOMException && caught.name === "AbortError") return;
        setListings((current) => ({
          ...current,
          [key]: {
            error: apiError(caught, "downloads.directoriesFailed"),
            loading: false,
            response: current[key]?.response ?? null,
          },
        }));
      });
    return () => controller.abort();
  }, [apiError, snapshotId, torrentId]);

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

  function renderDirectory(directory: TorrentDownloadDirectoryV2, depth: number): ReactNode {
    const path = directory.relative_path;
    const listing = listings[path];
    const open = openPaths.has(path);
    const childDirectories = listing?.response?.directories ?? [];
    return (
      <li key={path} className="ready-directory-item">
        <div
          className="ready-directory-row"
          style={{ "--directory-indent": `${Math.min(depth, 6)}rem` } as CSSProperties}
        >
          <button
            type="button"
            className="ready-directory-toggle"
            aria-expanded={open}
            aria-label={t(open ? "downloads.collapseFolder" : "downloads.expandFolder", { name: directory.name })}
            onClick={() => toggleDirectory(directory)}
          >
            {open ? <ChevronDown aria-hidden="true" /> : <ChevronRight aria-hidden="true" />}
            {open ? <FolderOpen aria-hidden="true" /> : <Folder aria-hidden="true" />}
            <span>
              <strong>{directory.name}</strong>
              <small>{t(directory.file_count === 1 ? "downloads.folderSummaryOne" : "downloads.folderSummaryMany", {
                count: directory.file_count,
                size: formatBytes(directory.total_size),
              })}</small>
            </span>
          </button>
          {directory.archive_available && (
            <Tooltip content={t("downloads.downloadFolderZip", { name: directory.name })}>
              <a
                className="ready-folder-download-button"
                href={api.torrentFolderArchiveDownloadUrlV2(torrentId, path, snapshotId)}
                download={`${directory.name}.zip`}
                aria-label={t("downloads.downloadFolderZip", { name: directory.name })}
              >
                <Archive aria-hidden="true" />
                <span>ZIP</span>
              </a>
            </Tooltip>
          )}
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
            {listing?.loading === false && listing.error === "" && childDirectories.length === 0 && (
              <span className="ready-directory-empty">{t("downloads.noSubfolders")}</span>
            )}
            {childDirectories.length > 0 && (
              <ul>{childDirectories.map((child) => renderDirectory(child, depth + 1))}</ul>
            )}
          </div>
        )}
      </li>
    );
  }

  const root = listings[""];
  if (root?.loading === true && root.response === null) {
    return <StateMessage tone="loading" className="ready-directories-state">{t("downloads.directoriesLoading")}</StateMessage>;
  }
  if (root?.error !== "" && root?.error !== undefined && root.response === null) {
    return (
      <div className="ready-directories-state ready-directories-error">
        <span>{root.error}</span>
        <Button variant="secondary" onClick={() => loadDirectories(null)}>{t("common.retry")}</Button>
      </div>
    );
  }
  const rootDirectories = root?.response?.directories ?? [];
  if (rootDirectories.length === 0) return null;
  return (
    <section className="ready-directory-browser" aria-labelledby={`torrent-folders-${torrentId}`}>
      <header>
        <Folder aria-hidden="true" />
        <div>
          <h4 id={`torrent-folders-${torrentId}`}>{t("downloads.folders")}</h4>
          <span>{t("downloads.foldersHint")}</span>
        </div>
      </header>
      <ul className="ready-directory-list">
        {rootDirectories.map((directory) => renderDirectory(directory, 0))}
      </ul>
    </section>
  );
}

function splitFilePath(relativePath: string): { directory: string; name: string } {
  const parts = relativePath.split("/");
  return {
    directory: parts.slice(0, -1).join(" / "),
    name: parts.at(-1) ?? relativePath,
  };
}

function ReadyTorrentContent({
  manifest,
  transfers,
  onCancelTransfer,
  onCloseTransfer,
  onDownloadAll,
  onDownloadFile,
  onLoadPage,
  onPauseTransfer,
  onResumeTransfer,
  onRetry,
  torrent,
}: {
  manifest: ReadyManifestState | undefined;
  transfers: readonly BrowserDownloadJobSnapshot[];
  onCancelTransfer: (jobId: string) => void;
  onCloseTransfer: (jobId: string) => void;
  onDownloadAll: () => void;
  onDownloadFile: (file: TorrentDownloadFileV2, snapshot: TorrentDownloadManifestPageV2) => void;
  onLoadPage: (offset: number) => void;
  onPauseTransfer: (jobId: string) => void;
  onResumeTransfer: (jobId: string) => void;
  onRetry: () => void;
  torrent: TorrentRequestV2;
}) {
  const { formatBytes, t } = useI18n();
  const snapshot = manifest?.snapshot ?? null;
  const compatible = !supportsRecursiveDirectoryDownload();
  const managedFiles = supportsManagedFileDownload();
  const folderBusy = transfers.some(
    (transfer) => transfer.kind === "folder" && !["completed", "cancelled"].includes(transfer.status),
  );
  return (
    <section className="ready-content" aria-label={t("downloads.contentNamed", { name: torrent.name })}>
      {transfers.map((transfer) => (
        <LocalTransferPanel
          key={transfer.id}
          transfer={transfer}
          onCancel={() => onCancelTransfer(transfer.id)}
          onClose={() => onCloseTransfer(transfer.id)}
          onPause={() => onPauseTransfer(transfer.id)}
          onResume={() => onResumeTransfer(transfer.id)}
        />
      ))}
      {manifest === undefined || (manifest.loading && snapshot === null) ? (
        <StateMessage tone="loading">{t("downloads.manifestLoading")}</StateMessage>
      ) : manifest.error !== "" && snapshot === null ? (
        <StateMessage tone="error">
          <span>{manifest.error}</span>
          <Button variant="secondary" onClick={onRetry}>{t("common.retry")}</Button>
        </StateMessage>
      ) : snapshot !== null ? (
        <>
          <header className="ready-content-heading">
            <div>
              <h3>{t("downloads.content")}</h3>
              <span>{t(snapshot.file_count === 1 ? "downloads.contentSummaryOne" : "downloads.contentSummaryMany", { count: snapshot.file_count, size: formatBytes(snapshot.total_size) })}</span>
            </div>
            {snapshot.file_count > 1 && !compatible && (
              <Button disabled={folderBusy} onClick={onDownloadAll}>
                <DownloadIcon /> {t("downloads.downloadAll")}
              </Button>
            )}
            {snapshot.file_count > 1 && compatible && snapshot.archive_available && (
              <a
                className="download-fallback-archive"
                href={api.torrentArchiveDownloadUrlV2(torrent.id, snapshot.snapshot_id)}
                download={`${torrent.name}.zip`}
              >
                <Archive aria-hidden="true" /> {t("downloads.archive")}
              </a>
            )}
          </header>
          {compatible && snapshot.file_count > 1 && (
            <p className="ready-compatibility-note">{t("downloads.compatHint")}</p>
          )}
          {manifest.error !== "" && (
            <StateMessage tone="error" className="ready-manifest-error">
              <span>{manifest.error}</span>
              <Button variant="secondary" onClick={onRetry}>{t("common.retry")}</Button>
            </StateMessage>
          )}
          {snapshot.file_count > 1 && (
            <TorrentDirectoryBrowser torrentId={torrent.id} snapshotId={snapshot.snapshot_id} />
          )}
          <h4 className="ready-files-heading"><File aria-hidden="true" /> {t("downloads.filesHeading")}</h4>
          <ul className="ready-file-list">
            {snapshot.items.map((file) => {
              const path = splitFilePath(file.relative_path);
              return (
              <li key={file.id}>
                <Tooltip content={file.relative_path} overflowOnly className="ready-file-path">
                  <span className="ready-file-label">
                    <File aria-hidden="true" />
                    <span>
                      <strong>{path.name}</strong>
                      {path.directory !== "" && <small>{path.directory}</small>}
                    </span>
                  </span>
                </Tooltip>
                <span>{formatBytes(file.size)}</span>
                {managedFiles ? (
                  <Tooltip content={t("common.download")}>
                    <button
                      type="button"
                      className="ready-file-download-button"
                      aria-label={t("downloads.downloadNamedFile", { name: file.relative_path })}
                      onClick={() => onDownloadFile(file, snapshot)}
                    >
                      <Download aria-hidden="true" />
                    </button>
                  </Tooltip>
                ) : (
                  <Tooltip content={t("common.download")}>
                    <a
                      className="ready-file-download-button"
                      href={api.torrentFileDownloadUrlV2(torrent.id, file.id, snapshot.snapshot_id)}
                      download={file.relative_path.split("/").at(-1)}
                      aria-label={t("downloads.downloadNamedFile", { name: file.relative_path })}
                    >
                      <Download aria-hidden="true" />
                    </a>
                  </Tooltip>
                )}
              </li>
              );
            })}
          </ul>
          {snapshot.file_count > FALLBACK_PAGE_SIZE && (
            <nav className="ready-manifest-pagination" aria-label={t("downloads.compatPagination")}>
              <Button
                variant="secondary"
                disabled={manifest.loading || snapshot.offset === 0}
                onClick={() => onLoadPage(Math.max(0, snapshot.offset - FALLBACK_PAGE_SIZE))}
              >
                {t("common.previous")}
              </Button>
              <span>{Math.floor(snapshot.offset / FALLBACK_PAGE_SIZE) + 1} / {Math.ceil(snapshot.file_count / FALLBACK_PAGE_SIZE)}</span>
              <Button
                variant="secondary"
                disabled={manifest.loading || snapshot.offset + snapshot.items.length >= snapshot.file_count}
                onClick={() => onLoadPage(snapshot.offset + snapshot.items.length)}
              >
                {t("common.next")}
              </Button>
            </nav>
          )}
        </>
      ) : null}
    </section>
  );
}

function TorrentItem({
  torrent,
  onRefresh,
  onDownload,
  onCancel,
  cancelBusy,
  downloadBusy,
  details,
  onOpen,
  expanded,
  onToggleDetails,
}: {
  torrent: TorrentRequestV2;
  onRefresh: () => void;
  onDownload: () => void;
  onCancel: () => void;
  cancelBusy: boolean;
  downloadBusy: boolean;
  details?: ReactNode;
  onOpen?: () => void;
  expanded: boolean;
  onToggleDetails: () => void;
}) {
  const { formatBytes, formatDate, t } = useI18n();
  const rowStatus = torrentRowStatus(torrent);
  const detailsId = `torrent-details-${torrent.id}`;
  const percent = rowStatus === "ready" ? 100 : Math.round(torrent.progress * 100);
  const error = torrent.error_code === null
    ? null
    : t(torrent.error_code === "torrent_failed" ? "downloads.needsAttention" : "downloads.stateError");
  return (
    <li className="torrent-accordion-item">
      <article className="torrent-accordion-card" aria-label={torrent.name}>
        <Accordion
          externalTrigger
          open={expanded}
          contentId={detailsId}
          contentClassName="torrent-accordion-content"
          title={(
            <div className="torrent-accordion-summary">
              <div
                className={`torrent-row-grid${expanded ? " is-expanded" : ""}`}
                onClick={(event) => {
                  const target = event.target;
                  if (
                    target instanceof Element &&
                    target.closest("button, a, input, select, textarea, [role='button'], .torrent-card-actions") !== null
                  ) return;
                  if (!expanded) onOpen?.();
                  onToggleDetails();
                }}
              >
                <Tooltip content={torrent.name} overflowOnly className="torrent-summary-heading">
                  <strong>{torrent.name}</strong>
                </Tooltip>
                <span className="torrent-summary-status">
                  <Badge
                    tone={rowStatus === "ready" ? "success" : rowStatus === "error" ? "danger" : rowStatus === "waiting" ? "warning" : "neutral"}
                    className={`torrent-primary-state ${rowStatus}`}
                  >
                    {t(rowStatusLabels[rowStatus])}
                  </Badge>
                  {torrent.state === "ready" && (
                    <SubscriptionExpiryIndicator
                      readyAt={torrent.ready_at}
                      unsubscribeAt={torrent.unsubscribe_at}
                    />
                  )}
                </span>
                <span className="torrent-summary-queue">{torrentQueueLabel(torrent)}</span>
                <span className={`torrent-summary-progress ${rowStatus}`}>
                  <Progress className="torrent-row-progress" label={t("downloads.progressFor", { name: torrent.name })} value={percent} />
                  <strong>{percent} %</strong>
                </span>
                <span className="torrent-summary-size">{formatBytes(torrent.total_size)}</span>
                <div className="torrent-card-actions">
                  <Tooltip content={t("downloads.details")}>
                    <Button
                      type="button"
                      variant="secondary"
                      className="torrent-action-details"
                      aria-label={t(expanded ? "downloads.hideDetailsNamed" : "downloads.showDetailsNamed", { name: torrent.name })}
                      aria-expanded={expanded}
                      aria-controls={detailsId}
                      onClick={() => {
                        if (!expanded) onOpen?.();
                        onToggleDetails();
                      }}
                    >
                      <ListTree aria-hidden="true" />
                    </Button>
                  </Tooltip>
                  {torrent.state === "ready" ? (
                    <Button
                      type="button"
                      className="torrent-action-download"
                      aria-label={t("common.download")}
                      disabled={downloadBusy}
                      onClick={onDownload}
                    >
                      <DownloadIcon />
                    </Button>
                  ) : (
                    <Button type="button" variant="secondary" aria-label={t("downloads.refreshNamed", { name: torrent.name })} onClick={onRefresh}>
                      <RefreshIcon />
                    </Button>
                  )}
                  {!(["cancelled", "expired"] as TorrentRequestV2State[]).includes(torrent.state) && (
                    <Button
                      type="button"
                      variant="danger"
                      disabled={cancelBusy}
                      onClick={onCancel}
                      aria-label={t(torrent.state === "ready" ? "downloads.deleteNamed" : "downloads.cancelNamed", { name: torrent.name })}
                    >
                      <DeleteIcon />
                    </Button>
                  )}
                </div>
              </div>
            </div>
          )}
        >
          <dl className="torrent-detail-grid">
            <div><dt>{t("downloads.created")}</dt><dd>{formatDate(torrent.created_at, { dateStyle: "short", timeStyle: "short" })}</dd></div>
            <div><dt>{t("downloads.updated")}</dt><dd>{formatDate(torrent.updated_at, { dateStyle: "short", timeStyle: "short" })}</dd></div>
          </dl>
          {error !== null && <p className="torrent-detail-error" role="alert">{error}</p>}
          {details}
        </Accordion>
      </article>
    </li>
  );
}

export function UserDownloadsPage({
  onActivityChanged,
  onLocalTransferChanged,
  onSessionExpired,
}: UserDownloadsPageProps) {
  const feedback = useFeedback();
  const { apiError, t } = useI18n();
  const inputRef = useRef<HTMLInputElement>(null);
  const [torrents, setTorrents] = useState<TorrentRequestV2[]>([]);
  const [offset, setOffset] = useState(0);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [cancellingId, setCancellingId] = useState<string | null>(null);
  const [pageError, setPageError] = useState("");
  const [searchQuery, setSearchQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState<TorrentStatusFilter>("all");
  const [openTorrentIds, setOpenTorrentIds] = useState<Set<string>>(() => new Set());
  const [managerSnapshot, setManagerSnapshot] = useState<BrowserDownloadManagerSnapshot>(EMPTY_MANAGER_SNAPSHOT);
  const loadGenerationRef = useRef(0);
  const managerRef = useRef<BrowserDownloadManager | null>(null);
  const completedDownloadNotificationsRef = useRef(new Set<string>());
  if (managerRef.current === null) {
    managerRef.current = new BrowserDownloadManager(
      DEFAULT_RECURSIVE_DOWNLOAD_CONCURRENCY,
      setManagerSnapshot,
    );
  }
  const [readyManifests, setReadyManifests] = useState<Record<string, ReadyManifestState>>({});
  const readyManifestsRef = useRef(readyManifests);
  readyManifestsRef.current = readyManifests;
  const manifestRequestsRef = useRef(new Map<string, Promise<TorrentDownloadManifestPageV2 | null>>());

  useEffect(() => () => managerRef.current?.dispose(), []);

  useEffect(() => {
    onLocalTransferChanged?.(summarizeDownloadManager(managerSnapshot));
  }, [managerSnapshot, onLocalTransferChanged]);

  useEffect(() => {
    for (const job of managerSnapshot.jobs) {
      if (job.status !== "completed" || completedDownloadNotificationsRef.current.has(job.id)) continue;
      completedDownloadNotificationsRef.current.add(job.id);
      feedback.toast({ tone: "success", message: t("downloads.completed", { name: job.name }) });
    }
  }, [feedback, managerSnapshot.jobs, t]);

  const refreshDownloadPolicy = useCallback(() => {
    const controller = new AbortController();
    void loadBrowserDownloadPolicy(controller.signal)
      .then((policy) => managerRef.current?.setMaxConcurrentStreams(policy.max_concurrent_streams))
      .catch((caught: unknown) => {
        if (caught instanceof DOMException && caught.name === "AbortError") return;
        if (caught instanceof DownloadPolicyRequestError && caught.status === 401) onSessionExpired();
      });
  }, [onSessionExpired]);

  const load = useCallback(async (_requestedOffset: number, signal?: AbortSignal) => {
    const generation = ++loadGenerationRef.current;
    setRefreshing(true);
    try {
      const items: TorrentRequestV2[] = [];
      let apiOffset = 0;
      let expectedTotal: number | null = null;
      while (expectedTotal === null || apiOffset < expectedTotal) {
        const result = await api.listTorrentRequestsV2(apiOffset, PAGE_SIZE, signal);
        if (expectedTotal === null) expectedTotal = result.total;
        items.push(...result.items.filter(isVisibleTorrentRequest));
        if (result.items.length < PAGE_SIZE) break;
        apiOffset += result.items.length;
      }
      if (generation !== loadGenerationRef.current) return;
      setTorrents(items);
      setPageError("");
      onActivityChanged?.();
    } catch (caught) {
      if (caught instanceof DOMException && caught.name === "AbortError") return;
      if (caught instanceof ApiError && caught.status === 401) {
        onSessionExpired();
        return;
      }
      if (generation !== loadGenerationRef.current) return;
      setPageError(apiError(caught, "downloads.trackingFailed"));
    } finally {
      if (generation === loadGenerationRef.current) {
        setLoading(false);
        setRefreshing(false);
      }
    }
  }, [apiError, onActivityChanged, onSessionExpired]);

  const loadReadyManifest = useCallback((
    torrentId: string,
    requestedOffset = 0,
    requestedSnapshot: string | null = null,
  ): Promise<TorrentDownloadManifestPageV2 | null> => {
    const current = readyManifestsRef.current[torrentId];
    const snapshotId = requestedOffset === 0
      ? null
      : requestedSnapshot ?? current?.firstPage?.snapshot_id ?? current?.snapshot?.snapshot_id ?? null;
    const key = `${torrentId}:${requestedOffset}:${snapshotId ?? "fresh"}`;
    const pending = manifestRequestsRef.current.get(key);
    if (pending !== undefined) return pending;
    setReadyManifests((states) => ({
      ...states,
      [torrentId]: {
        error: "",
        firstPage: states[torrentId]?.firstPage ?? null,
        loading: true,
        requestedOffset,
        snapshot: states[torrentId]?.snapshot ?? null,
      },
    }));
    const request = api.getTorrentDownloadManifestPageV2(
      torrentId,
      requestedOffset,
      snapshotId,
      undefined,
      FALLBACK_PAGE_SIZE,
    ).then((page) => {
      if (page.offset !== requestedOffset || (snapshotId !== null && page.snapshot_id !== snapshotId)) {
        throw new Error("manifest_changed");
      }
      setReadyManifests((states) => ({
        ...states,
        [torrentId]: {
          error: "",
          firstPage: requestedOffset === 0 ? page : states[torrentId]?.firstPage ?? null,
          loading: false,
          requestedOffset,
          snapshot: page,
        },
      }));
      return page;
    }).catch((caught: unknown) => {
      if (caught instanceof ApiError && caught.status === 401) {
        onSessionExpired();
        return null;
      }
      setReadyManifests((states) => ({
        ...states,
        [torrentId]: {
          error: apiError(caught, "downloads.manifestFailed"),
          firstPage: states[torrentId]?.firstPage ?? null,
          loading: false,
          requestedOffset,
          snapshot: states[torrentId]?.snapshot ?? null,
        },
      }));
      return null;
    }).finally(() => manifestRequestsRef.current.delete(key));
    manifestRequestsRef.current.set(key, request);
    return request;
  }, [apiError, onSessionExpired]);

  useEffect(() => {
    const controller = new AbortController();
    let active = true;
    let socket: WebSocket | null = null;
    let reconnectTimer: number | null = null;
    let pollingTimer: number | null = null;
    let hasConnected = false;
    let refreshPending = false;
    let refreshQueued = false;
    const manifestRefreshPending = new Set<string>();
    const manifestRefreshQueued = new Set<string>();

    const runRefresh = (signal?: AbortSignal) => {
      if (!active) return;
      refreshPending = true;
      void load(offset, signal).finally(() => {
        refreshPending = false;
        if (!refreshQueued) return;
        refreshQueued = false;
        runRefresh();
      });
    };
    const refreshFromEvent = () => {
      if (!active) return;
      if (refreshPending) {
        refreshQueued = true;
        return;
      }
      runRefresh();
    };
    const refreshOpenManifest = (torrentId: string) => {
      const current = readyManifestsRef.current[torrentId];
      if (!active || current?.snapshot === null || current === undefined) return;
      if (manifestRefreshPending.has(torrentId)) {
        manifestRefreshQueued.add(torrentId);
        return;
      }
      manifestRefreshPending.add(torrentId);
      void loadReadyManifest(
        torrentId,
        current.snapshot.offset,
        current.firstPage?.snapshot_id ?? current.snapshot.snapshot_id,
      ).finally(() => {
        manifestRefreshPending.delete(torrentId);
        if (!manifestRefreshQueued.delete(torrentId)) return;
        refreshOpenManifest(torrentId);
      });
    };
    const connect = () => {
      if (!active || typeof WebSocket === "undefined") return;
      socket = api.openTorrentEventsV2();
      socket.onopen = () => {
        if (hasConnected) refreshFromEvent();
        hasConnected = true;
      };
      socket.onmessage = (event) => {
        const message = parseTorrentRealtimeMessage(event.data);
        if (message === null || message.type === "heartbeat") return;
        if (message.type === "torrent.cancelled" || message.type === "torrent.expired") {
          setTorrents((current) => current.filter((torrent) => torrent.id !== message.request_id));
        }
        refreshFromEvent();
        if (message.type === "torrent.retention_extended") refreshOpenManifest(message.request_id);
      };
      socket.onerror = () => socket?.close();
      socket.onclose = () => {
        if (!active) return;
        reconnectTimer = window.setTimeout(connect, 1_000);
      };
    };

    const poll = () => {
      if (document.visibilityState === "visible") refreshFromEvent();
    };

    runRefresh(controller.signal);
    connect();
    pollingTimer = window.setInterval(poll, AUTO_REFRESH_MS);
    return () => {
      active = false;
      controller.abort();
      if (reconnectTimer !== null) window.clearTimeout(reconnectTimer);
      if (pollingTimer !== null) window.clearInterval(pollingTimer);
      socket?.close();
    };
  }, [load, loadReadyManifest, offset]);

  async function submitBatch(fileList: FileList | File[]) {
    if (uploading) return;
    const files = Array.from(fileList);
    if (files.length === 0) return;
    if (files.length > MAX_TORRENT_BATCH_FILES) {
      feedback.toast({ tone: "error", message: t("downloads.batchTooLarge", { count: MAX_TORRENT_BATCH_FILES }) });
      if (inputRef.current !== null) inputRef.current.value = "";
      return;
    }

    const seen = new Set<string>();
    const results: UploadFileResult[] = files.map((file) => {
      const fingerprint = `${file.name.toLocaleLowerCase()}\u0000${file.size}\u0000${file.lastModified}`;
      if (!file.name.toLowerCase().endsWith(".torrent")) {
        return { file, name: file.name, status: "invalid", error: t("downloads.invalidFile") };
      }
      if (file.size === 0) {
        return { file, name: file.name, status: "invalid", error: t("downloads.emptyTorrent") };
      }
      if (seen.has(fingerprint)) return { file, name: file.name, status: "duplicate" };
      seen.add(fingerprint);
      return { file, name: file.name, status: "queued" };
    });
    const queuedIndexes = results.flatMap((result, index) => result.status === "queued" ? [index] : []);
    let nextIndex = 0;
    let sessionExpired = false;

    setUploading(true);
    if (inputRef.current !== null) inputRef.current.value = "";

    const notifyResult = (result: UploadFileResult) => {
      if (result.status === "added") {
        feedback.toast({ tone: "success", title: t("downloads.uploadSuccessTitle"), message: result.name });
      } else if (result.status === "duplicate") {
        feedback.toast({ tone: "warning", title: t("downloads.uploadDuplicateTitle"), message: result.name });
      } else if (result.status === "invalid") {
        feedback.toast({
          tone: "error",
          title: t("downloads.uploadErrorTitle"),
          message: `${result.name}\n${result.error ?? t("downloads.invalidFile")}`,
        });
      } else if (result.status === "failed") {
        feedback.toast({
          tone: "error",
          title: t("downloads.uploadErrorTitle"),
          message: `${result.name}\n${result.error ?? t("downloads.uploadRetry")}`,
        });
      }
    };

    for (const result of results) {
      if (result.status === "invalid" || result.status === "duplicate") notifyResult(result);
    }

    const updateResult = (index: number, status: UploadResultStatus, error?: string) => {
      results[index] = { ...results[index], status, error };
      if (status !== "uploading") notifyResult(results[index]);
    };

    const worker = async () => {
      while (!sessionExpired) {
        const queuePosition = nextIndex;
        nextIndex += 1;
        if (queuePosition >= queuedIndexes.length) return;
        const resultIndex = queuedIndexes[queuePosition];
        const file = results[resultIndex].file;
        updateResult(resultIndex, "uploading");
        try {
          const created = await api.createTorrentRequestV2(file);
          updateResult(resultIndex, created.created ? "added" : "duplicate");
          if (created.storage_pressure !== "normal") {
            feedback.toast({
              tone: "warning",
              title: t("downloads.storagePressureTitle"),
              message: `${file.name}\n${t("downloads.storagePressureWarning")}`,
            });
          }
        } catch (caught) {
          if (caught instanceof ApiError && caught.status === 401) {
            sessionExpired = true;
            updateResult(resultIndex, "failed", apiError(caught, "downloads.uploadFailed"));
            onSessionExpired();
            return;
          }
          updateResult(
            resultIndex,
            caught instanceof ApiError && (caught.status === 413 || caught.status === 422) ? "invalid" : "failed",
            apiError(caught, "downloads.uploadFailed"),
          );
        }
      }
    };

    await Promise.all(Array.from(
      { length: Math.min(TORRENT_UPLOAD_CONCURRENCY, queuedIndexes.length) },
      () => worker(),
    ));
    setUploading(false);
    if (!sessionExpired) {
      setOffset(0);
      await load(0);
    }
  }

  function drop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    setDragging(false);
    void submitBatch(event.dataTransfer.files);
  }

  function select(event: ChangeEvent<HTMLInputElement>) {
    void submitBatch(event.target.files ?? []);
  }

  async function startRecursiveDownload(torrent: TorrentRequestV2, snapshot: TorrentDownloadManifestPageV2) {
    if (!supportsRecursiveDirectoryDownload() || snapshot.offset !== 0) return;
    try {
      const directory = await pickDownloadDirectory();
      managerRef.current?.enqueueFolder({
        torrentId: torrent.id,
        name: torrent.name,
        snapshot,
        directory,
        loadManifestPage: (requestedOffset, snapshotId, signal) => api.getTorrentDownloadManifestPageV2(
          torrent.id,
          requestedOffset,
          snapshotId,
          signal,
          snapshot.limit,
        ),
      });
      refreshDownloadPolicy();
    } catch (caught) {
      if (caught instanceof DOMException && caught.name === "AbortError") return;
      if (caught instanceof ApiError && caught.status === 401) {
        onSessionExpired();
        return;
      }
      feedback.toast({ tone: "error", message: apiError(caught, "downloads.failed") });
    }
  }

  async function startManagedFileDownload(
    torrent: TorrentRequestV2,
    snapshot: TorrentDownloadManifestPageV2,
    file: TorrentDownloadFileV2,
  ) {
    if (!supportsManagedFileDownload()) {
      const link = document.createElement("a");
      link.href = api.torrentFileDownloadUrlV2(torrent.id, file.id, snapshot.snapshot_id);
      link.download = file.relative_path.split("/").at(-1) ?? file.relative_path;
      link.click();
      return;
    }
    try {
      const name = file.relative_path.split("/").at(-1) ?? file.relative_path;
      const target = await pickManagedDownloadFile(name);
      managerRef.current?.enqueueFile({
        torrentId: torrent.id,
        name,
        snapshot,
        file,
        target,
      });
      refreshDownloadPolicy();
    } catch (caught) {
      if (caught instanceof DOMException && caught.name === "AbortError") return;
      feedback.toast({ tone: "error", message: apiError(caught, "downloads.failed") });
    }
  }

  async function openReadyTorrent(torrent: TorrentRequestV2, directSingleFile = false) {
    const existing = readyManifestsRef.current[torrent.id];
    const snapshot = existing?.firstPage ?? await loadReadyManifest(torrent.id);
    if (directSingleFile && snapshot?.file_count === 1 && snapshot.items.length === 1) {
      await startManagedFileDownload(torrent, snapshot, snapshot.items[0]);
    }
  }

  async function cancelTorrentRequest(torrent: TorrentRequestV2) {
    if (cancellingId !== null) return;
    setCancellingId(torrent.id);
    try {
      await api.cancelTorrentRequestV2(torrent.id);
      feedback.toast({ tone: "success", message: t("downloads.cancelledNamed", { name: torrent.name }) });
      setReadyManifests((states) => {
        const next = { ...states };
        delete next[torrent.id];
        return next;
      });
      await load(offset);
    } catch (caught) {
      if (caught instanceof ApiError && caught.status === 401) {
        onSessionExpired();
        return;
      }
      feedback.toast({ tone: "error", message: apiError(caught, "downloads.cancelFailed") });
    } finally {
      setCancellingId(null);
    }
  }

  const normalizedSearch = searchQuery.trim().toLocaleLowerCase();
  const counts = useMemo(() => ({
    all: torrents.length,
    active: torrents.filter((torrent) => matchesTorrentFilter(torrent, "active")).length,
    ready: torrents.filter((torrent) => matchesTorrentFilter(torrent, "ready")).length,
    waiting: torrents.filter((torrent) => matchesTorrentFilter(torrent, "waiting")).length,
  }), [torrents]);
  const filteredTorrents = useMemo(() => torrents.filter((torrent) => (
    matchesTorrentFilter(torrent, statusFilter)
    && (normalizedSearch === "" || torrent.name.toLocaleLowerCase().includes(normalizedSearch))
  )), [normalizedSearch, statusFilter, torrents]);
  const visibleTorrents = filteredTorrents.slice(offset, offset + PAGE_SIZE);
  const page = Math.floor(offset / PAGE_SIZE) + 1;
  const pageCount = Math.max(1, Math.ceil(filteredTorrents.length / PAGE_SIZE));
  const queueTotal = torrents.find((torrent) => torrent.queue_total_estimate !== null)?.queue_total_estimate ?? null;
  const localDownloadSummary = summarizeDownloadManager(managerSnapshot);

  useEffect(() => {
    const torrentIds = new Set(torrents.map((torrent) => torrent.id));
    setOpenTorrentIds((current) => {
      const next = new Set([...current].filter((id) => torrentIds.has(id)));
      return next.size === current.size ? current : next;
    });
  }, [torrents]);

  useEffect(() => {
    if (offset < filteredTorrents.length || offset === 0) return;
    setOffset(Math.max(0, Math.floor((filteredTorrents.length - 1) / PAGE_SIZE) * PAGE_SIZE));
  }, [filteredTorrents.length, offset]);

  return (
    <section className="user-downloads" aria-labelledby="user-downloads-title">
      <header className="user-downloads-header">
        <div>
          <h2 id="user-downloads-title">{t("downloads.title")}</h2>
          <p>{t("downloads.intro")}</p>
        </div>
      </header>

      <input
        ref={inputRef}
        className="sr-only"
        type="file"
        multiple
        aria-label={t("downloads.fileLabel")}
        accept=".torrent,application/x-bittorrent"
        onChange={select}
        disabled={uploading}
        tabIndex={-1}
      />
      <div
        className={`torrent-drop-zone${dragging ? " dragging" : ""}${uploading ? " disabled" : ""}`}
        data-testid="torrent-drop-zone"
        aria-label={t("downloads.upload")}
        aria-disabled={uploading}
        role="button"
        tabIndex={uploading ? -1 : 0}
        onClick={() => inputRef.current?.click()}
        onKeyDown={(event) => {
          if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            inputRef.current?.click();
          }
        }}
        onDragEnter={(event) => { event.preventDefault(); setDragging(true); }}
        onDragOver={(event) => event.preventDefault()}
        onDragLeave={(event) => { if (!event.currentTarget.contains(event.relatedTarget as Node)) setDragging(false); }}
        onDrop={drop}
      >
        <DownloadIcon />
        <strong>{t("downloads.dropTitle")}</strong>
        <span>{t("downloads.dropHint")}</span>
      </div>
      <p className="torrent-atomic-note">
        <InfoIcon />
        <span>{t("downloads.atomicTorrentHint")}</span>
      </p>

      <div className="torrent-toolbar">
        <label className="torrent-search">
          <span className="sr-only">{t("downloads.search")}</span>
          <Search aria-hidden="true" />
          <input
            type="search"
            value={searchQuery}
            placeholder={t("downloads.searchPlaceholder")}
            onChange={(event) => { setSearchQuery(event.target.value); setOffset(0); }}
          />
        </label>
        <div className="torrent-filters" aria-label={t("downloads.filters")}>
          {([
            ["all", t("downloads.filterAll"), null],
            ["active", t("downloads.filterActive"), <Download aria-hidden="true" />],
            ["ready", t("downloads.filterReady"), <Check aria-hidden="true" />],
            ["waiting", t("downloads.filterWaiting"), <Clock3 aria-hidden="true" />],
          ] as const).map(([filter, label, icon]) => (
            <button
              key={filter}
              type="button"
              className={`torrent-filter torrent-filter-${filter}${statusFilter === filter ? " active" : ""}`}
              aria-pressed={statusFilter === filter}
              onClick={() => { setStatusFilter(filter); setOffset(0); }}
            >
              {icon}{label}<span className="torrent-filter-count">{counts[filter]}</span>
            </button>
          ))}
        </div>
      </div>

      {managerSnapshot.jobs.length > 0 && (
        <aside className="torrent-queue-summary local-download-manager-summary" aria-live="polite">
          <QueueIcon />
          <div>
            <strong>{t("downloads.localActive", { active: localDownloadSummary.active, maximum: localDownloadSummary.maximum })}</strong>
            <span>{t("downloads.localWaitingCount", { waiting: localDownloadSummary.waiting })}</span>
          </div>
        </aside>
      )}

      {pageError !== "" && (
        <StateMessage tone="error" className="browser-state torrent-page-error">
          <strong>{t("downloads.trackingUnavailable")}</strong>
          <p>{pageError}</p>
          <Button type="button" onClick={() => void load(offset)}>{t("common.retry")}</Button>
        </StateMessage>
      )}

      {loading ? (
        <StateMessage tone="loading" className="torrent-list-state">{t("downloads.reading")}</StateMessage>
      ) : filteredTorrents.length === 0 ? (
        <StateMessage tone="empty" className="torrent-list-state">{t("downloads.empty")}</StateMessage>
      ) : (
        <>
          {queueTotal !== null && (
            <aside className="torrent-queue-summary">
              <InfoIcon />
              <div>
                <strong>{t("downloads.queueTotal", { total: queueTotal })}</strong>
                <span>{t("downloads.queueDisclaimer")}</span>
              </div>
            </aside>
          )}
          <div className="torrent-list-heading" aria-hidden="true">
            <span>{t("downloads.name")}</span>
            <span>{t("downloads.status")}</span>
            <span>{t("downloads.queue")}</span>
            <span>{t("downloads.progress")}</span>
            <span>{t("downloads.size")}</span>
            <span />
          </div>
          <ul className="torrent-accordion-list" aria-label={t("downloads.requests")} aria-busy={refreshing}>
            {visibleTorrents.map((torrent) => {
              const manifest = readyManifests[torrent.id];
              const transfers = managerSnapshot.jobs.filter((job) => job.torrentId === torrent.id);
              return (
                <TorrentItem
                  key={torrent.id}
                  torrent={torrent}
                  expanded={openTorrentIds.has(torrent.id)}
                  onToggleDetails={() => setOpenTorrentIds((current) => {
                    const next = new Set(current);
                    if (next.has(torrent.id)) next.delete(torrent.id);
                    else next.add(torrent.id);
                    return next;
                  })}
                  onRefresh={() => void load(offset)}
                  onOpen={torrent.state === "ready" && manifest === undefined ? () => void openReadyTorrent(torrent) : undefined}
                  onDownload={() => {
                    if (!openTorrentIds.has(torrent.id)) {
                      setOpenTorrentIds((current) => new Set(current).add(torrent.id));
                    }
                    void openReadyTorrent(torrent, true);
                  }}
                  onCancel={() => void cancelTorrentRequest(torrent)}
                  cancelBusy={cancellingId === torrent.id}
                  downloadBusy={torrent.state === "ready" && manifest?.loading === true && manifest.snapshot === null}
                  details={torrent.state === "ready" ? (
                    <ReadyTorrentContent
                      torrent={torrent}
                      manifest={manifest}
                      transfers={transfers}
                      onLoadPage={(requestedOffset) => void loadReadyManifest(
                        torrent.id,
                        requestedOffset,
                        manifest?.firstPage?.snapshot_id ?? manifest?.snapshot?.snapshot_id ?? null,
                      )}
                      onRetry={() => void loadReadyManifest(
                        torrent.id,
                        manifest?.requestedOffset ?? 0,
                        manifest?.firstPage?.snapshot_id ?? manifest?.snapshot?.snapshot_id ?? null,
                      )}
                      onDownloadAll={() => {
                        const firstPage = readyManifestsRef.current[torrent.id]?.firstPage;
                        if (firstPage !== null && firstPage !== undefined) void startRecursiveDownload(torrent, firstPage);
                      }}
                      onDownloadFile={(file, snapshot) => void startManagedFileDownload(torrent, snapshot, file)}
                      onPauseTransfer={(jobId) => managerRef.current?.pause(jobId)}
                      onResumeTransfer={(jobId) => managerRef.current?.resume(jobId)}
                      onCancelTransfer={(jobId) => managerRef.current?.cancel(jobId)}
                      onCloseTransfer={(jobId) => managerRef.current?.remove(jobId)}
                    />
                  ) : undefined}
                />
              );
            })}
          </ul>
          <nav className="torrent-pagination" aria-label={t("downloads.pagination")}>
            <button
              type="button"
              className="secondary-button"
              disabled={offset === 0 || refreshing}
              onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
            >
              {t("common.previous")}
            </button>
            <span aria-live="polite">{t(filteredTorrents.length === 1 ? "downloads.pageOne" : "downloads.pageMany", { page, pages: pageCount, total: filteredTorrents.length })}</span>
            <button
              type="button"
              className="secondary-button"
              disabled={offset + PAGE_SIZE >= filteredTorrents.length || refreshing}
              onClick={() => setOffset(offset + PAGE_SIZE)}
            >
              {t("common.next")}
            </button>
          </nav>
        </>
      )}
    </section>
  );
}
