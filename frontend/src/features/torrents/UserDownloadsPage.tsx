import { type ChangeEvent, type DragEvent, useCallback, useEffect, useRef, useState } from "react";

import {
  api,
  ApiError,
  parseTorrentRealtimeMessage,
  type TorrentRequestV2,
  type TorrentRequestV2State,
  type TorrentDownloadSnapshotV2,
} from "../../api/client";
import { useFeedback } from "../../components/Feedback";
import { DeleteIcon, DownloadIcon, RefreshIcon } from "../../components/icons";
import { Notice, type NoticeTone } from "../../components/Notice";
import { useI18n, type MessageKey } from "../../i18n";
import {
  pickDownloadDirectory,
  RecursiveDownloadController,
  type RecursiveTransferErrorCode,
  type RecursiveTransferProgress,
  supportsRecursiveDirectoryDownload,
} from "./recursiveDownload";

const PAGE_SIZE = 10;
const FALLBACK_PAGE_SIZE = 50;

const stateLabels: Record<TorrentRequestV2State, MessageKey> = {
  requested: "downloads.requested",
  active: "downloads.active",
  ready: "downloads.ready",
  cancelled: "downloads.cancelled",
  expired: "downloads.expired",
  error: "downloads.error",
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

function TorrentRow({
  torrent,
  onRefresh,
  onDownload,
  onCancel,
  cancelBusy,
  downloadBusy,
}: {
  torrent: TorrentRequestV2;
  onRefresh: () => void;
  onDownload: () => void;
  onCancel: () => void;
  cancelBusy: boolean;
  downloadBusy: boolean;
}) {
  const { formatBytes, formatDate, t } = useI18n();
  const percent = Math.round(torrent.progress * 100);
  const error = torrent.error_code === null
    ? null
    : t(torrent.error_code === "torrent_failed" ? "downloads.needsAttention" : "downloads.stateError");
  return (
    <tr>
      <td className="torrent-name-cell" data-label={t("downloads.name")}>
        <span title={torrent.name}>{torrent.name}</span>
        {error !== null && <small role="alert">{error}</small>}
      </td>
      <td data-label={t("downloads.status")}>
        <span className={`torrent-primary-state ${torrent.state}`}>
          {t(stateLabels[torrent.state])}
        </span>
      </td>
      <td className="torrent-size-cell" data-label={t("files.size")}>{formatBytes(torrent.total_size)}</td>
      <td className="torrent-progress-cell" data-label={t("downloads.progress")}>
        <div>
          <progress value={torrent.progress} max={1} aria-label={t("downloads.progressFor", { name: torrent.name })}>
            {percent} %
          </progress>
          <strong>{percent} %</strong>
        </div>
      </td>
      <td className="torrent-date-cell" data-label={t("downloads.updated")}>{formatDate(torrent.updated_at, { dateStyle: "short", timeStyle: "short" })}</td>
      <td className="torrent-row-actions" data-label={t("files.actions")}>
        <div className="torrent-row-action-group">
          {torrent.state === "ready" ? (
            <button type="button" disabled={downloadBusy} onClick={onDownload}>
              <DownloadIcon />
              <span>{t("common.download")}</span>
            </button>
          ) : (
            <button type="button" className="secondary-button" onClick={onRefresh}>
              <RefreshIcon />
              <span>{t("common.refresh")}</span>
            </button>
          )}
          {!(["cancelled", "expired"] as TorrentRequestV2State[]).includes(torrent.state) && (
            <button
              type="button"
              className="danger-button"
              disabled={cancelBusy}
              onClick={onCancel}
              aria-label={t("downloads.cancelNamed", { name: torrent.name })}
            >
              <DeleteIcon />
              <span>{cancelBusy ? t("downloads.cancelling") : t("common.cancel")}</span>
            </button>
          )}
        </div>
      </td>
    </tr>
  );
}

export function UserDownloadsPage({ onSessionExpired }: { onSessionExpired: () => void }) {
  const feedback = useFeedback();
  const { apiError, formatBytes, t } = useI18n();
  const inputRef = useRef<HTMLInputElement>(null);
  const [torrents, setTorrents] = useState<TorrentRequestV2[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [cancellingId, setCancellingId] = useState<string | null>(null);
  const [notice, setNotice] = useState<{ message: string; tone: NoticeTone } | null>(null);
  const controllerRef = useRef<RecursiveDownloadController | null>(null);
  const [transfer, setTransfer] = useState<(
    RecursiveTransferProgress & {
      name: string;
      totalBytes: number;
      fileCount: number;
    }
  ) | null>(null);
  const [fallback, setFallback] = useState<{
    torrentId: string;
    name: string;
    snapshot: TorrentDownloadSnapshotV2;
  } | null>(null);
  const [fallbackOffset, setFallbackOffset] = useState(0);

  useEffect(() => () => controllerRef.current?.cancel(), []);

  const load = useCallback(async (requestedOffset: number, signal?: AbortSignal) => {
    setRefreshing(true);
    try {
      const result = await api.listTorrentRequestsV2(requestedOffset, PAGE_SIZE, signal);
      setTorrents(result.items);
      setTotal(result.total);
      setNotice((current) => current?.tone === "error" ? null : current);
    } catch (caught) {
      if (caught instanceof DOMException && caught.name === "AbortError") return;
      if (caught instanceof ApiError && caught.status === 401) {
        onSessionExpired();
        return;
      }
      setNotice({
        tone: "error",
        message: apiError(caught, "downloads.trackingFailed"),
      });
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [apiError, onSessionExpired]);

  useEffect(() => {
    const controller = new AbortController();
    let active = true;
    let socket: WebSocket | null = null;
    let reconnectTimer: number | null = null;
    let hasConnected = false;
    let refreshPending = false;

    const refreshFromEvent = () => {
      if (refreshPending || !active) return;
      refreshPending = true;
      void load(offset).finally(() => { refreshPending = false; });
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
        if (message !== null && message.type !== "heartbeat") refreshFromEvent();
      };
      socket.onerror = () => socket?.close();
      socket.onclose = () => {
        if (!active) return;
        reconnectTimer = window.setTimeout(connect, 1_000);
      };
    };

    void load(offset, controller.signal);
    connect();
    return () => {
      active = false;
      controller.abort();
      if (reconnectTimer !== null) window.clearTimeout(reconnectTimer);
      socket?.close();
    };
  }, [load, offset]);

  async function submit(file: File | undefined) {
    if (file === undefined) return;
    if (!file.name.toLowerCase().endsWith(".torrent")) {
      setNotice({ tone: "warning", message: t("downloads.invalidFile") });
      return;
    }
    setUploading(true);
    setNotice({ tone: "progress", message: t("downloads.validating") });
    try {
      const result = await api.createTorrentRequestV2(file);
      setNotice({
        tone: result.storage_pressure === "warning" ? "warning" : "success",
        message: t(result.created ? "downloads.added" : "downloads.duplicate", { name: result.name }),
      });
      setOffset(0);
      await load(0);
    } catch (caught) {
      if (caught instanceof ApiError && caught.status === 401) {
        onSessionExpired();
        return;
      }
      setNotice({ tone: "error", message: apiError(caught, "downloads.uploadRetry") });
    } finally {
      setUploading(false);
      if (inputRef.current !== null) inputRef.current.value = "";
    }
  }

  function drop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    setDragging(false);
    void submit(event.dataTransfer.files.item(0) ?? undefined);
  }

  function select(event: ChangeEvent<HTMLInputElement>) {
    void submit(event.target.files?.item(0) ?? undefined);
  }

  async function startRecursiveDownload(torrent: TorrentRequestV2) {
    if (!supportsRecursiveDirectoryDownload()) {
      setNotice({ tone: "progress", message: t("downloads.compatPreparing") });
      try {
        const snapshot = await api.getTorrentDownloadSnapshotV2(torrent.id);
        setFallback({ torrentId: torrent.id, name: torrent.name, snapshot });
        setFallbackOffset(0);
        setNotice({
          tone: "warning",
          message: t("downloads.compatHint"),
        });
      } catch (caught) {
        if (caught instanceof ApiError && caught.status === 401) {
          onSessionExpired();
          return;
        }
        setNotice({
          tone: "error",
          message: apiError(caught, "downloads.manifestFailed"),
        });
      }
      return;
    }
    setNotice({ tone: "progress", message: t("downloads.preparing", { name: torrent.name }) });
    try {
      const snapshotPromise = api.getTorrentDownloadManifestPageV2(torrent.id);
      const directoryPromise = pickDownloadDirectory();
      const [snapshot, directory] = await Promise.all([snapshotPromise, directoryPromise]);
      const update = (progress: RecursiveTransferProgress) => {
        setTransfer({
          ...progress,
          name: torrent.name,
          totalBytes: snapshot.total_size,
          fileCount: snapshot.file_count,
        });
        if (progress.status === "completed") {
          setNotice({ tone: "success", message: t("downloads.completed", { name: torrent.name }) });
        } else if (progress.status === "error") {
          setNotice({
            tone: "error",
            message: progress.error === null ? t("downloads.failed") : t(transferErrorKeys[progress.error]),
          });
        }
      };
      const controller = new RecursiveDownloadController({
        torrentRequestId: torrent.id,
        firstPage: snapshot,
        directory,
        loadManifestPage: (requestedOffset, snapshotId, signal) =>
          api.getTorrentDownloadManifestPageV2(
            torrent.id,
            requestedOffset,
            snapshotId,
            signal,
            snapshot.limit,
          ),
        onProgress: update,
      });
      controllerRef.current = controller;
      setNotice(null);
      await controller.start();
    } catch (caught) {
      if (caught instanceof DOMException && caught.name === "AbortError") {
        setNotice(null);
        return;
      }
      if (caught instanceof ApiError && caught.status === 401) {
        onSessionExpired();
        return;
      }
      setNotice({
        tone: "error",
        message: apiError(caught, "downloads.failed"),
      });
    }
  }

  function cancelTransfer() {
    controllerRef.current?.cancel();
    controllerRef.current = null;
  }

  async function cancelTorrentRequest(torrent: TorrentRequestV2) {
    const confirmed = await feedback.confirm({
      title: t("downloads.cancelTitle"),
      message: t("downloads.cancelMessage", { name: torrent.name }),
      confirmText: t("downloads.cancel"),
      destructive: true,
    });
    if (!confirmed) return;
    setCancellingId(torrent.id);
    try {
      await api.cancelTorrentRequestV2(torrent.id);
      feedback.toast({
        tone: "success",
        message: t("downloads.cancelledNamed", { name: torrent.name }),
      });
      if (fallback?.torrentId === torrent.id) setFallback(null);
      await load(offset);
    } catch (caught) {
      if (caught instanceof ApiError && caught.status === 401) {
        onSessionExpired();
        return;
      }
      feedback.toast({
        tone: "error",
        message: apiError(caught, "downloads.cancelFailed"),
      });
    } finally {
      setCancellingId(null);
    }
  }

  const page = Math.floor(offset / PAGE_SIZE) + 1;
  const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE));

  return (
    <section className="user-downloads" aria-labelledby="user-downloads-title">
      <header className="user-downloads-header">
        <div>
          <p className="eyebrow">{t("files.personalSpace")}</p>
          <h2 id="user-downloads-title">{t("downloads.title")}</h2>
          <p>{t("downloads.intro")}</p>
        </div>
        <div className="torrent-page-actions">
          <button type="button" disabled={uploading} onClick={() => inputRef.current?.click()}>
            <DownloadIcon />
            <span>{uploading ? t("downloads.adding") : t("downloads.upload")}</span>
          </button>
          <button
            type="button"
            className="secondary-button"
            disabled={refreshing}
            onClick={() => void load(offset)}
          >
            <RefreshIcon className={refreshing ? "rotating" : undefined} />
            <span>{refreshing ? t("downloads.refreshing") : t("common.refresh")}</span>
          </button>
        </div>
      </header>

      <input
        ref={inputRef}
        className="sr-only"
        type="file"
        aria-label={t("downloads.fileLabel")}
        accept=".torrent,application/x-bittorrent"
        onChange={select}
        disabled={uploading}
        tabIndex={-1}
      />
      <div
        className={`torrent-drop-zone${dragging ? " dragging" : ""}${uploading ? " disabled" : ""}`}
        data-testid="torrent-drop-zone"
        aria-disabled={uploading}
        onDragEnter={(event) => { event.preventDefault(); setDragging(true); }}
        onDragOver={(event) => event.preventDefault()}
        onDragLeave={(event) => { if (!event.currentTarget.contains(event.relatedTarget as Node)) setDragging(false); }}
        onDrop={drop}
      >
        <DownloadIcon />
        <strong>{t("downloads.dropTitle")}</strong>
        <span>{t("downloads.dropHint")}</span>
      </div>

      {notice !== null && (
        <Notice
          message={notice.message}
          tone={notice.tone}
          onDismiss={() => setNotice(null)}
          onRetry={notice.tone === "error" ? () => void load(offset) : undefined}
        />
      )}

      {transfer !== null && (
        <section className={`recursive-transfer ${transfer.status}`} aria-label={t("downloads.local")}>
          <div>
            <strong title={transfer.name}>{transfer.name}</strong>
            <span aria-live="polite">
              {t("downloads.files", { completed: transfer.completedFiles, total: transfer.fileCount, downloaded: formatBytes(transfer.downloadedBytes), size: formatBytes(transfer.totalBytes) })}
            </span>
          </div>
          <progress
            value={transfer.downloadedBytes}
            max={Math.max(1, transfer.totalBytes)}
            aria-label={t("downloads.localNamed", { name: transfer.name })}
          />
          <div className="recursive-transfer-actions">
            {transfer.status === "running" && (
              <button type="button" className="secondary-button" onClick={() => controllerRef.current?.pause()}>
                {t("common.pause")}
              </button>
            )}
            {(transfer.status === "paused" || transfer.status === "error") && (
              <button type="button" onClick={() => void controllerRef.current?.resume()}>
                {t("common.resume")}
              </button>
            )}
            {transfer.status === "completed" || transfer.status === "cancelled" ? (
              <button type="button" className="secondary-button" onClick={() => setTransfer(null)}>
                {t("common.close")}
              </button>
            ) : (
              <button type="button" className="danger-button" onClick={cancelTransfer}>
                {t("common.cancel")}
              </button>
            )}
          </div>
        </section>
      )}

      {fallback !== null && (
        <section className="download-fallback" aria-labelledby="download-fallback-title">
          <header>
            <div>
              <strong id="download-fallback-title" title={fallback.name}>{fallback.name}</strong>
              <span>{t("downloads.files", { completed: 0, total: fallback.snapshot.file_count, downloaded: formatBytes(0), size: formatBytes(fallback.snapshot.total_size) })}</span>
            </div>
            <button type="button" className="secondary-button" onClick={() => setFallback(null)}>
              {t("common.close")}
            </button>
          </header>
          {fallback.snapshot.archive_available && (
            <a
              className="download-fallback-archive"
              href={api.torrentArchiveDownloadUrlV2(fallback.torrentId, fallback.snapshot.snapshot_id)}
              download={`${fallback.name}.zip`}
            >
              <DownloadIcon />
              {t("downloads.archive")}
            </a>
          )}
          <ul>
            {fallback.snapshot.items
              .slice(fallbackOffset, fallbackOffset + FALLBACK_PAGE_SIZE)
              .map((file) => (
                <li key={file.id}>
                  <span title={file.relative_path}>{file.relative_path}</span>
                  <span>{formatBytes(file.size)}</span>
                  <a
                    href={api.torrentFileDownloadUrlV2(
                      fallback.torrentId,
                      file.id,
                      fallback.snapshot.snapshot_id,
                    )}
                    download={file.relative_path.split("/").at(-1)}
                  >
                    {t("common.download")}
                  </a>
                </li>
              ))}
          </ul>
          {fallback.snapshot.file_count > FALLBACK_PAGE_SIZE && (
            <nav aria-label={t("downloads.compatPagination")}>
              <button
                type="button"
                className="secondary-button"
                disabled={fallbackOffset === 0}
                onClick={() => setFallbackOffset(Math.max(0, fallbackOffset - FALLBACK_PAGE_SIZE))}
              >
                {t("common.previous")}
              </button>
              <span>{Math.floor(fallbackOffset / FALLBACK_PAGE_SIZE) + 1} / {Math.ceil(fallback.snapshot.file_count / FALLBACK_PAGE_SIZE)}</span>
              <button
                type="button"
                className="secondary-button"
                disabled={fallbackOffset + FALLBACK_PAGE_SIZE >= fallback.snapshot.file_count}
                onClick={() => setFallbackOffset(fallbackOffset + FALLBACK_PAGE_SIZE)}
              >
                {t("common.next")}
              </button>
            </nav>
          )}
        </section>
      )}

      {loading ? (
        <p className="torrent-list-state" role="status">{t("downloads.reading")}</p>
      ) : torrents.length === 0 ? (
        <p className="torrent-list-state">{t("downloads.empty")}</p>
      ) : (
        <>
          <div className="torrent-table-wrap" aria-busy={refreshing}>
            <table className="torrent-table">
              <caption className="sr-only">{t("downloads.requests")}</caption>
              <thead>
                <tr>
                  <th scope="col">{t("downloads.name")}</th>
                  <th scope="col">{t("downloads.status")}</th>
                  <th scope="col">{t("files.size")}</th>
                  <th scope="col">{t("downloads.progress")}</th>
                  <th scope="col">{t("downloads.updated")}</th>
                  <th scope="col">{t("files.actions")}</th>
                </tr>
              </thead>
              <tbody>
                {torrents.map((torrent) => (
                  <TorrentRow
                    key={torrent.id}
                    torrent={torrent}
                    onRefresh={() => void load(offset)}
                    onDownload={() => void startRecursiveDownload(torrent)}
                    onCancel={() => void cancelTorrentRequest(torrent)}
                    cancelBusy={cancellingId === torrent.id}
                    downloadBusy={transfer?.status === "running" || transfer?.status === "paused"}
                  />
                ))}
              </tbody>
            </table>
          </div>
          <nav className="torrent-pagination" aria-label={t("downloads.pagination")}>
            <button
              type="button"
              className="secondary-button"
              disabled={offset === 0 || refreshing}
              onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
            >
              {t("common.previous")}
            </button>
            <span aria-live="polite">{t(total === 1 ? "downloads.pageOne" : "downloads.pageMany", { page, pages: pageCount, total })}</span>
            <button
              type="button"
              className="secondary-button"
              disabled={offset + PAGE_SIZE >= total || refreshing}
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
