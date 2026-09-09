import {
  type ChangeEvent,
  type DragEvent,
  type ReactNode,
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";

import {
  api,
  ApiError,
  parseTorrentRealtimeMessage,
  type TorrentRequestV2,
  type TorrentRequestV2State,
  type TorrentDownloadManifestPageV2,
} from "../../api/client";
import { useFeedback } from "../../components/Feedback";
import {
  DeleteIcon,
  DownloadIcon,
  InfoIcon,
  QueueIcon,
  RefreshIcon,
} from "../../components/icons";
import { Accordion, Badge, Button, Progress, StateMessage } from "../../components/ui";
import { useI18n, type MessageKey } from "../../i18n";
import {
  pickDownloadDirectory,
  DEFAULT_RECURSIVE_DOWNLOAD_CONCURRENCY,
  RecursiveDownloadController,
  type RecursiveTransferErrorCode,
  type RecursiveTransferProgress,
  type LocalTransferQueueItem,
  supportsRecursiveDirectoryDownload,
} from "./recursiveDownload";
import { RetentionWarning } from "./RetentionWarning";

const PAGE_SIZE = 10;
const FALLBACK_PAGE_SIZE = 50;
export const MAX_TORRENT_BATCH_FILES = 50;
export const TORRENT_UPLOAD_CONCURRENCY = 3;

export interface LocalDownloadSummary {
  active: number;
  maximum: number;
  status: "idle" | RecursiveTransferProgress["status"];
  waiting: number;
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
  };
}

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

interface ReadyManifestState {
  error: string;
  firstPage: TorrentDownloadManifestPageV2 | null;
  loading: boolean;
  requestedOffset: number;
  snapshot: TorrentDownloadManifestPageV2 | null;
}

type LocalTransferState = RecursiveTransferProgress & {
  fileCount: number;
  name: string;
  torrentId: string;
  totalBytes: number;
};

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

function TorrentQueueVisibility({ torrent }: { torrent: TorrentRequestV2 }) {
  const { t } = useI18n();
  if (torrent.queue_status === null) return null;
  let label: string;
  if (torrent.queue_status === "downloading") {
    label = t("downloads.queueDownloading");
  } else if (torrent.queue_status === "stalled") {
    label = t("downloads.queueStalled");
  } else if (torrent.queue_status === "cooldown") {
    label = t("downloads.queueCooldown");
  } else if (torrent.queue_position_estimate === 1) {
    label = t("downloads.queueSoon");
  } else if (torrent.queue_position_estimate !== null) {
    label = t("downloads.queuePosition", { position: torrent.queue_position_estimate });
  } else {
    label = t("downloads.queueEstimating");
  }
  return (
    <span className={`torrent-queue-status ${torrent.queue_status}`}>
      <QueueIcon />
      <span>{label}</span>
    </span>
  );
}

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

function LocalTransferPanel({
  transfer,
  onCancel,
  onClose,
  onPause,
  onResume,
}: {
  transfer: LocalTransferState;
  onCancel: () => void;
  onClose: () => void;
  onPause: () => void;
  onResume: () => void;
}) {
  const { formatBytes, t } = useI18n();
  const summary = summarizeLocalTransfer(transfer);
  const error = transfer.error === null ? null : t(transferErrorKeys[transfer.error]);
  return (
    <section className={`recursive-transfer ${transfer.status}`} aria-label={t("downloads.localNamed", { name: transfer.name })}>
      <div className="local-transfer-heading">
        <strong title={transfer.name}>{t("downloads.localRecovery")}</strong>
        <span>
          {t("downloads.files", { completed: transfer.completedFiles, total: transfer.fileCount, downloaded: formatBytes(transfer.downloadedBytes), size: formatBytes(transfer.totalBytes) })}
        </span>
      </div>
      <Progress
        value={transfer.totalBytes === 0 ? 0 : (transfer.downloadedBytes / transfer.totalBytes) * 100}
        label={t("downloads.localNamed", { name: transfer.name })}
      />
      <div className="local-transfer-metrics">
        <span>{t("downloads.localActive", { active: summary.active, maximum: summary.maximum })}</span>
        <span>{t("downloads.localWaitingCount", { waiting: summary.waiting })}</span>
        <Badge tone={transfer.status === "error" ? "danger" : transfer.status === "completed" ? "success" : "neutral"}>
          {t(`downloads.localStatus.${transfer.status}` as MessageKey)}
        </Badge>
      </div>
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
        {transfer.status === "running" && (
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

function ReadyTorrentContent({
  manifest,
  transfer,
  completeTransferBusy,
  onCancelTransfer,
  onCloseTransfer,
  onDownloadAll,
  onLoadPage,
  onPauseTransfer,
  onResumeTransfer,
  onRetry,
  torrent,
}: {
  manifest: ReadyManifestState | undefined;
  transfer: LocalTransferState | null;
  completeTransferBusy: boolean;
  onCancelTransfer: () => void;
  onCloseTransfer: () => void;
  onDownloadAll: () => void;
  onLoadPage: (offset: number) => void;
  onPauseTransfer: () => void;
  onResumeTransfer: () => void;
  onRetry: () => void;
  torrent: TorrentRequestV2;
}) {
  const { formatBytes, t } = useI18n();
  const snapshot = manifest?.snapshot ?? null;
  const compatible = !supportsRecursiveDirectoryDownload();
  return (
    <section className="ready-content" aria-label={t("downloads.contentNamed", { name: torrent.name })}>
      {transfer !== null && (
        <LocalTransferPanel
          transfer={transfer}
          onCancel={onCancelTransfer}
          onClose={onCloseTransfer}
          onPause={onPauseTransfer}
          onResume={onResumeTransfer}
        />
      )}
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
              <Button disabled={completeTransferBusy} onClick={onDownloadAll}>
                <DownloadIcon /> {t("downloads.downloadAll")}
              </Button>
            )}
            {snapshot.file_count > 1 && compatible && snapshot.archive_available && (
              <a
                className="download-fallback-archive"
                href={api.torrentArchiveDownloadUrlV2(torrent.id, snapshot.snapshot_id)}
                download={`${torrent.name}.zip`}
              >
                <DownloadIcon /> {t("downloads.archive")}
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
          <ul className="ready-file-list">
            {snapshot.items.map((file) => (
              <li key={file.id}>
                <span title={file.relative_path}>{file.relative_path}</span>
                <span>{formatBytes(file.size)}</span>
                <a
                  href={api.torrentFileDownloadUrlV2(torrent.id, file.id, snapshot.snapshot_id)}
                  download={file.relative_path.split("/").at(-1)}
                  aria-label={t("downloads.downloadNamedFile", { name: file.relative_path })}
                >
                  {t("common.download")}
                </a>
              </li>
            ))}
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
}: {
  torrent: TorrentRequestV2;
  onRefresh: () => void;
  onDownload: () => void;
  onCancel: () => void;
  cancelBusy: boolean;
  downloadBusy: boolean;
  details?: ReactNode;
  onOpen?: () => void;
}) {
  const { formatBytes, formatDate, t } = useI18n();
  const detailsRef = useRef<HTMLDetailsElement>(null);
  const percent = Math.round(torrent.progress * 100);
  const error = torrent.error_code === null
    ? null
    : t(torrent.error_code === "torrent_failed" ? "downloads.needsAttention" : "downloads.stateError");
  return (
    <li className="torrent-accordion-item">
      <article className="torrent-accordion-card" aria-label={torrent.name}>
        <Accordion
          ref={detailsRef}
          className="torrent-accordion"
          summaryClassName="torrent-accordion-toggle"
          onToggle={(event) => { if (event.currentTarget.open) onOpen?.(); }}
          title={(
            <span className="torrent-accordion-summary">
              <span className="torrent-summary-heading">
                <strong title={torrent.name}>{torrent.name}</strong>
                <span>{formatBytes(torrent.total_size)}</span>
              </span>
              <span className="torrent-summary-status">
                <Badge
                  tone={torrent.state === "ready" ? "success" : torrent.state === "error" ? "danger" : "neutral"}
                  className={`torrent-primary-state ${torrent.state}`}
                >
                  {t(stateLabels[torrent.state])}
                </Badge>
                <TorrentQueueVisibility torrent={torrent} />
              </span>
              <span className="torrent-summary-progress">
                <Progress
                  label={t("downloads.progressFor", { name: torrent.name })}
                  value={percent}
                />
                <strong>{percent} %</strong>
              </span>
              <span className="torrent-details-cue" aria-hidden="true">{t("downloads.details")}</span>
            </span>
          )}
          contentClassName="torrent-accordion-content"
        >
          <dl className="torrent-detail-grid">
            <div><dt>{t("downloads.created")}</dt><dd>{formatDate(torrent.created_at, { dateStyle: "short", timeStyle: "short" })}</dd></div>
            <div><dt>{t("downloads.updated")}</dt><dd>{formatDate(torrent.updated_at, { dateStyle: "short", timeStyle: "short" })}</dd></div>
          </dl>
          {error !== null && <p className="torrent-detail-error" role="alert">{error}</p>}
          {details}
        </Accordion>
        {torrent.state === "ready" && (
          <RetentionWarning retentionExpiresAt={torrent.retention_expires_at} compact />
        )}
        <div className="torrent-card-actions">
          {torrent.state === "ready" ? (
            <Button type="button" disabled={downloadBusy} onClick={() => {
              if (detailsRef.current !== null) detailsRef.current.open = true;
              onDownload();
            }}>
              <DownloadIcon />
              <span>{t("common.download")}</span>
            </Button>
          ) : (
            <Button type="button" variant="secondary" onClick={onRefresh}>
              <RefreshIcon />
              <span>{t("common.refresh")}</span>
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
              <span>{cancelBusy ? t("downloads.cancelling") : t(torrent.state === "ready" ? "common.delete" : "common.cancel")}</span>
            </Button>
          )}
        </div>
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
  const [transfer, setTransfer] = useState<LocalTransferState | null>(null);
  const [readyManifests, setReadyManifests] = useState<Record<string, ReadyManifestState>>({});
  const readyManifestsRef = useRef(readyManifests);
  readyManifestsRef.current = readyManifests;
  const manifestRequestsRef = useRef(new Map<string, Promise<TorrentDownloadManifestPageV2 | null>>());

  useEffect(() => () => controllerRef.current?.cancel(), []);

  useEffect(() => {
    onLocalTransferChanged?.(summarizeLocalTransfer(transfer));
  }, [onLocalTransferChanged, transfer]);

  const load = useCallback(async (requestedOffset: number, signal?: AbortSignal) => {
    const generation = ++loadGenerationRef.current;
    setRefreshing(true);
    try {
      const result = await api.listTorrentRequestsV2(requestedOffset, PAGE_SIZE, signal);
      if (generation !== loadGenerationRef.current) return;
      setTorrents(result.items);
      setTotal(result.total);
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
        refreshFromEvent();
        if (message.type === "torrent.retention_extended") {
          refreshOpenManifest(message.request_id);
        }
      };
      socket.onerror = () => socket?.close();
      socket.onclose = () => {
        if (!active) return;
        reconnectTimer = window.setTimeout(connect, 1_000);
      };
    };

    runRefresh(controller.signal);
    connect();
    return () => {
      active = false;
      controller.abort();
      if (reconnectTimer !== null) window.clearTimeout(reconnectTimer);
      socket?.close();
    };
  }, [load, loadReadyManifest, offset]);

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

  async function startRecursiveDownload(
    torrent: TorrentRequestV2,
    snapshot: TorrentDownloadManifestPageV2,
  ) {
    if (!supportsRecursiveDirectoryDownload() || snapshot.offset !== 0) return;
    try {
      const directory = await pickDownloadDirectory();
      const update = (progress: RecursiveTransferProgress) => {
        setTransfer({
          ...progress,
          torrentId: torrent.id,
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

  async function openReadyTorrent(torrent: TorrentRequestV2, directSingleFile = false) {
    const existing = readyManifestsRef.current[torrent.id];
    const snapshot = existing?.firstPage ?? await loadReadyManifest(torrent.id);
    if (directSingleFile && snapshot?.file_count === 1 && snapshot.items.length === 1) {
      const file = snapshot.items[0];
      const link = document.createElement("a");
      link.href = api.torrentFileDownloadUrlV2(torrent.id, file.id, snapshot.snapshot_id);
      link.download = file.relative_path.split("/").at(-1) ?? file.relative_path;
      link.click();
    }
  }

  function cancelTransfer() {
    controllerRef.current?.cancel();
  }

  function closeTransfer() {
    controllerRef.current = null;
    setTransfer(null);
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
  const queueTotal = torrents.find(
    (torrent) => torrent.queue_total_estimate !== null,
  )?.queue_total_estimate ?? null;

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
      <p className="torrent-atomic-note">
        <InfoIcon />
        <span>{t("downloads.atomicTorrentHint")}</span>
      </p>

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
        <StateMessage tone="error" className="browser-state torrent-page-error">
          <strong>{t("downloads.trackingUnavailable")}</strong>
          <p>{pageError}</p>
          <Button type="button" onClick={() => void load(offset)}>
            {t("common.retry")}
          </Button>
        </StateMessage>
      )}

      {loading ? (
        <StateMessage tone="loading" className="torrent-list-state">{t("downloads.reading")}</StateMessage>
      ) : torrents.length === 0 ? (
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
          <ul className="torrent-accordion-list" aria-label={t("downloads.requests")} aria-busy={refreshing}>
            {torrents.map((torrent) => {
              const manifest = readyManifests[torrent.id];
              const localTransfer = transfer?.torrentId === torrent.id ? transfer : null;
              const completeTransferBusy = transfer !== null
                && (transfer.status === "running" || transfer.status === "paused" || transfer.status === "error");
              return (
                <TorrentItem
                  key={torrent.id}
                  torrent={torrent}
                  onRefresh={() => void load(offset)}
                  onOpen={torrent.state === "ready" && manifest === undefined
                    ? () => void openReadyTorrent(torrent)
                    : undefined}
                  onDownload={() => void openReadyTorrent(torrent, true)}
                  onCancel={() => void cancelTorrentRequest(torrent)}
                  cancelBusy={cancellingId === torrent.id}
                  downloadBusy={torrent.state === "ready" && manifest?.loading === true && manifest.snapshot === null}
                  details={torrent.state === "ready" ? (
                    <ReadyTorrentContent
                      torrent={torrent}
                      manifest={manifest}
                      transfer={localTransfer}
                      completeTransferBusy={completeTransferBusy}
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
                        if (firstPage !== null && firstPage !== undefined && !completeTransferBusy) {
                          void startRecursiveDownload(torrent, firstPage);
                        }
                      }}
                      onPauseTransfer={() => controllerRef.current?.pause()}
                      onResumeTransfer={() => void controllerRef.current?.resume()}
                      onCancelTransfer={cancelTransfer}
                      onCloseTransfer={closeTransfer}
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
