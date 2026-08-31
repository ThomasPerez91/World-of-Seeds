import { type ChangeEvent, type DragEvent, useCallback, useEffect, useRef, useState } from "react";

import {
  api,
  ApiError,
  parseTorrentRealtimeMessage,
  type TorrentRequestV2,
  type TorrentRequestV2State,
  type TorrentDownloadManifestPageV2,
} from "../../api/client";
import { useFeedback } from "../../components/Feedback";
import { DeleteIcon, DownloadIcon, RefreshIcon } from "../../components/icons";
import { useI18n, type MessageKey } from "../../i18n";
import {
  pickDownloadDirectory,
  RecursiveDownloadController,
  type RecursiveTransferErrorCode,
  type RecursiveTransferProgress,
  supportsRecursiveDirectoryDownload,
} from "./recursiveDownload";
import { RetentionWarning } from "./RetentionWarning";

const PAGE_SIZE = 10;
const FALLBACK_PAGE_SIZE = 50;
export const MAX_TORRENT_BATCH_FILES = 50;
export const TORRENT_UPLOAD_CONCURRENCY = 3;

type UploadResultStatus = "queued" | "uploading" | "added" | "duplicate" | "invalid" | "failed";

interface UploadFileResult {
  file: File;
  name: string;
  status: UploadResultStatus;
}

interface UploadBatchState {
  active: number;
  completed: number;
  done: boolean;
  errors: number;
  results: UploadFileResult[];
  total: number;
}

const stateLabels: Record<TorrentRequestV2State, MessageKey> = {
  requested: "downloads.requested",
  active: "downloads.active",
  ready: "downloads.ready",
  cancelled: "downloads.cancelled",
  expired: "downloads.expired",
  error: "downloads.error",
};

const uploadStatusLabels: Record<UploadResultStatus, MessageKey> = {
  queued: "downloads.batchQueued",
  uploading: "downloads.batchUploading",
  added: "downloads.batchAdded",
  duplicate: "downloads.batchDuplicate",
  invalid: "downloads.batchInvalid",
  failed: "downloads.batchFailed",
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
        <div className="torrent-status-content">
          <span className={`torrent-primary-state ${torrent.state}`}>
            {t(stateLabels[torrent.state])}
          </span>
          {torrent.state === "ready" && (
            <RetentionWarning retentionExpiresAt={torrent.retention_expires_at} compact />
          )}
        </div>
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
  const [uploadBatch, setUploadBatch] = useState<UploadBatchState | null>(null);
  const [cancellingId, setCancellingId] = useState<string | null>(null);
  const [pageError, setPageError] = useState("");
  const loadGenerationRef = useRef(0);
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
    snapshot: TorrentDownloadManifestPageV2;
  } | null>(null);
  const [fallbackLoading, setFallbackLoading] = useState(false);

  useEffect(() => () => controllerRef.current?.cancel(), []);

  const load = useCallback(async (requestedOffset: number, signal?: AbortSignal) => {
    const generation = ++loadGenerationRef.current;
    setRefreshing(true);
    try {
      const result = await api.listTorrentRequestsV2(requestedOffset, PAGE_SIZE, signal);
      if (generation !== loadGenerationRef.current) return;
      setTorrents(result.items);
      setTotal(result.total);
      setPageError("");
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

  async function submitBatch(fileList: FileList | File[]) {
    if (uploading) return;
    const files = Array.from(fileList);
    if (files.length === 0) return;
    if (files.length > MAX_TORRENT_BATCH_FILES) {
      feedback.toast({
        tone: "error",
        message: t("downloads.batchTooLarge", { count: MAX_TORRENT_BATCH_FILES }),
      });
      if (inputRef.current !== null) inputRef.current.value = "";
      return;
    }

    const seen = new Set<string>();
    const results: UploadFileResult[] = files.map((file) => {
      const fingerprint = `${file.name.toLocaleLowerCase()}\u0000${file.size}\u0000${file.lastModified}`;
      const invalid = file.size === 0 || !file.name.toLowerCase().endsWith(".torrent");
      if (invalid) return { file, name: file.name, status: "invalid" };
      if (seen.has(fingerprint)) return { file, name: file.name, status: "duplicate" };
      seen.add(fingerprint);
      return { file, name: file.name, status: "queued" };
    });
    const queuedIndexes = results.flatMap((result, index) => result.status === "queued" ? [index] : []);
    const initiallyCompleted = results.length - queuedIndexes.length;
    let nextIndex = 0;
    let pressureWarning = false;
    let sessionExpired = false;

    setUploading(true);
    setUploadBatch({
      active: 0,
      completed: initiallyCompleted,
      done: queuedIndexes.length === 0,
      errors: results.filter((result) => result.status === "invalid").length,
      results: [...results],
      total: results.length,
    });
    if (inputRef.current !== null) inputRef.current.value = "";

    const updateResult = (index: number, status: UploadResultStatus) => {
      results[index] = { ...results[index], status };
      setUploadBatch((current) => current === null ? null : {
        ...current,
        active: current.active - (status === "uploading" ? -1 : 1),
        completed: current.completed + (status === "uploading" ? 0 : 1),
        errors: current.errors + (status === "invalid" || status === "failed" ? 1 : 0),
        results: [...results],
      });
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
          if (created.storage_pressure !== "normal") pressureWarning = true;
          updateResult(resultIndex, created.created ? "added" : "duplicate");
        } catch (caught) {
          if (caught instanceof ApiError && caught.status === 401) {
            sessionExpired = true;
            updateResult(resultIndex, "failed");
            onSessionExpired();
            return;
          }
          updateResult(
            resultIndex,
            caught instanceof ApiError && (caught.status === 413 || caught.status === 422)
                ? "invalid"
                : "failed",
          );
        }
      }
    };

    await Promise.all(
      Array.from(
        { length: Math.min(TORRENT_UPLOAD_CONCURRENCY, queuedIndexes.length) },
        () => worker(),
      ),
    );
    setUploading(false);
    setUploadBatch((current) => current === null ? null : { ...current, active: 0, done: true });

    const counts = {
      added: results.filter((result) => result.status === "added").length,
      duplicate: results.filter((result) => result.status === "duplicate").length,
      invalid: results.filter((result) => result.status === "invalid").length,
      failed: results.filter((result) => result.status === "failed").length,
    };
    if (!sessionExpired) {
      feedback.toast({
        tone: counts.failed > 0 || counts.invalid > 0 || pressureWarning ? "warning" : "success",
        title: t("downloads.batchComplete"),
        message: t("downloads.batchSummary", counts),
      });
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

  async function startRecursiveDownload(torrent: TorrentRequestV2) {
    if (!supportsRecursiveDirectoryDownload()) {
      try {
        const snapshot = await api.getTorrentDownloadManifestPageV2(
          torrent.id, 0, null, undefined, FALLBACK_PAGE_SIZE,
        );
        setFallback({ torrentId: torrent.id, name: torrent.name, snapshot });
        feedback.toast({
          tone: "warning",
          message: t("downloads.compatHint"),
        });
      } catch (caught) {
        if (caught instanceof ApiError && caught.status === 401) {
          onSessionExpired();
          return;
        }
        feedback.toast({
          tone: "error",
          message: apiError(caught, "downloads.manifestFailed"),
        });
      }
      return;
    }
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
          feedback.toast({ tone: "success", message: t("downloads.completed", { name: torrent.name }) });
        } else if (progress.status === "error") {
          feedback.toast({
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
      await controller.start();
    } catch (caught) {
      if (caught instanceof DOMException && caught.name === "AbortError") {
        return;
      }
      if (caught instanceof ApiError && caught.status === 401) {
        onSessionExpired();
        return;
      }
      feedback.toast({
        tone: "error",
        message: apiError(caught, "downloads.failed"),
      });
    }
  }

  async function loadFallbackPage(requestedOffset: number) {
    if (fallback === null || fallbackLoading) return;
    setFallbackLoading(true);
    try {
      const page = await api.getTorrentDownloadManifestPageV2(
        fallback.torrentId,
        requestedOffset,
        fallback.snapshot.snapshot_id,
        undefined,
        FALLBACK_PAGE_SIZE,
      );
      setFallback((current) => current === null ? null : { ...current, snapshot: page });
    } catch (caught) {
      feedback.toast({ tone: "error", message: apiError(caught, "downloads.manifestFailed") });
    } finally {
      setFallbackLoading(false);
    }
  }

  function cancelTransfer() {
    controllerRef.current?.cancel();
    controllerRef.current = null;
  }

  async function cancelTorrentRequest(torrent: TorrentRequestV2) {
    if (cancellingId !== null) return;
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
        aria-disabled={uploading}
        onDragEnter={(event) => { event.preventDefault(); setDragging(true); }}
        onDragOver={(event) => event.preventDefault()}
        onDragLeave={(event) => { if (!event.currentTarget.contains(event.relatedTarget as Node)) setDragging(false); }}
        onDrop={drop}
      >
        <DownloadIcon />
        <strong>{t("downloads.dropTitle")}</strong>
        <span>{t("downloads.dropHint")}</span>
        <button
          type="button"
          className="secondary-button compact-button"
          disabled={uploading}
          onClick={() => inputRef.current?.click()}
        >
          {t("downloads.upload")}
        </button>
      </div>

      {uploadBatch !== null && (
        <section className="torrent-upload-batch" aria-labelledby="torrent-batch-title" aria-busy={!uploadBatch.done}>
          <header>
            <div>
              <strong id="torrent-batch-title">{t("downloads.batchTitle")}</strong>
              <span aria-live="polite">
                {t("downloads.batchProgress", {
                  active: uploadBatch.active,
                  completed: uploadBatch.completed,
                  errors: uploadBatch.errors,
                  total: uploadBatch.total,
                })}
              </span>
            </div>
            {uploadBatch.done && (
              <button type="button" className="secondary-button compact-button" onClick={() => setUploadBatch(null)}>
                {t("common.close")}
              </button>
            )}
          </header>
          <progress value={uploadBatch.completed} max={uploadBatch.total} aria-label={t("downloads.batchTitle")} />
          <ul>
            {uploadBatch.results.map((result, index) => (
              <li key={`${result.name}-${result.file.lastModified}-${index}`}>
                <span title={result.name}>{result.name}</span>
                <strong className={`batch-result ${result.status}`}>
                  {t(uploadStatusLabels[result.status])}
                </strong>
              </li>
            ))}
          </ul>
        </section>
      )}

      {pageError !== "" && (
        <div className="browser-state torrent-page-error" role="alert">
          <strong>{t("downloads.trackingUnavailable")}</strong>
          <p>{pageError}</p>
          <button type="button" className="compact-button" onClick={() => void load(offset)}>
            {t("common.retry")}
          </button>
        </div>
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
          <RetentionWarning retentionExpiresAt={fallback.snapshot.retention_expires_at} />
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
            {fallback.snapshot.items.map((file) => (
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
                disabled={fallbackLoading || fallback.snapshot.offset === 0}
                onClick={() => void loadFallbackPage(Math.max(0, fallback.snapshot.offset - FALLBACK_PAGE_SIZE))}
              >
                {t("common.previous")}
              </button>
              <span>{Math.floor(fallback.snapshot.offset / FALLBACK_PAGE_SIZE) + 1} / {Math.ceil(fallback.snapshot.file_count / FALLBACK_PAGE_SIZE)}</span>
              <button
                type="button"
                className="secondary-button"
                disabled={fallbackLoading || fallback.snapshot.offset + fallback.snapshot.items.length >= fallback.snapshot.file_count}
                onClick={() => void loadFallbackPage(fallback.snapshot.offset + fallback.snapshot.items.length)}
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
