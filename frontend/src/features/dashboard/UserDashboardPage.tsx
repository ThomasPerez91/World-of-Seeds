import { type ReactNode, useCallback, useEffect, useRef, useState } from "react";

import {
  api,
  ApiError,
  type SharedStorageCapacity,
  type TorrentRequestV2,
} from "../../api/client";
import {
  ActivityIcon,
  LocalDownloadIcon,
  StorageIcon,
} from "../../components/icons";
import { ArrowRight, Check, Clock3, Download, HardDrive } from "lucide-react";
import { Badge, Button, Card, Progress, StateMessage, Tooltip } from "../../components/ui";
import { useI18n, type MessageKey } from "../../i18n";
import {
  type LocalDownloadSummary,
  UserDownloadsPage,
} from "../torrents/UserDownloadsPage";
import { DEFAULT_RECURSIVE_DOWNLOAD_CONCURRENCY } from "../torrents/recursiveDownload";

const ACTIVITY_PAGE_SIZE = 100;
const localStatusLabels: Record<Exclude<LocalDownloadSummary["status"], "idle">, MessageKey> = {
  queued: "dashboard.localStatus.queued",
  running: "dashboard.localStatus.running",
  paused: "dashboard.localStatus.paused",
  completed: "dashboard.localStatus.completed",
  error: "dashboard.localStatus.error",
  cancelled: "dashboard.localStatus.cancelled",
};

export interface TorrentActivitySummary {
  active: number;
  ready: number;
  waiting: number;
}

export function classifyTorrentActivity(
  summary: TorrentActivitySummary,
  torrent: TorrentRequestV2,
): void {
  if (torrent.state === "ready") {
    summary.ready += 1;
  } else if (torrent.state === "requested" || torrent.queue_status === "waiting") {
    summary.waiting += 1;
  } else if (torrent.state === "active") {
    summary.active += 1;
  }
}

export async function loadTorrentActivity(signal: AbortSignal): Promise<TorrentActivitySummary> {
  const summary = { active: 0, ready: 0, waiting: 0 };
  let offset = 0;
  let expectedTotal: number | null = null;

  while (expectedTotal === null || offset < expectedTotal) {
    const page = await api.listTorrentRequestsV2(offset, ACTIVITY_PAGE_SIZE, signal);
    if (page.offset !== offset || page.items.length > ACTIVITY_PAGE_SIZE) {
      throw new Error("torrent_activity_page_invalid");
    }
    if (expectedTotal === null) expectedTotal = page.total;
    if (page.items.length === 0) {
      if (offset < expectedTotal) throw new Error("torrent_activity_page_incomplete");
      break;
    }
    page.items.forEach((torrent) => classifyTorrentActivity(summary, torrent));
    offset += page.items.length;
  }
  return summary;
}

function SummaryHeading({
  icon,
  title,
  titleId,
}: {
  icon: ReactNode;
  title: string;
  titleId: string;
}) {
  return (
    <div className="dashboard-summary-heading">
      <div className="dashboard-summary-title">
        <span className="summary-icon" aria-hidden="true">{icon}</span>
        <h2 id={titleId}>{title}</h2>
      </div>
    </div>
  );
}

export function LocalDownloadCard({ local }: { local: LocalDownloadSummary }) {
  const { formatBytes, t } = useI18n();
  const content = local.status === "idle"
    ? <p className="dashboard-summary-empty">{t("dashboard.localIdle")}</p>
    : local.name !== null ? (
          <div className="local-download-progress">
            <div className="local-download-primary">
              <Tooltip content={local.name} overflowOnly><strong>{local.name}</strong></Tooltip>
              <Badge tone={local.status === "error" ? "danger" : local.status === "completed" ? "success" : "neutral"}>
                {t(localStatusLabels[local.status])}
              </Badge>
            </div>
            <Progress
              label={t("downloads.localProgress", { value: local.percent.toFixed(0) })}
              value={local.percent}
            />
            <span>{formatBytes(local.downloadedBytes)} / {formatBytes(local.totalBytes)} · {local.percent.toFixed(0)} %</span>
            <span>{t("dashboard.localActive", { active: local.active, maximum: local.maximum })} · {t("dashboard.localWaiting", { waiting: local.waiting })}</span>
            {local.kind === "folder" && local.fileCount > 0 && (
              <span>{t("dashboard.localFiles", { completed: local.completedFiles, total: local.fileCount })}</span>
            )}
            {local.otherJobs > 0 && <span>{t("dashboard.localOthers", { count: local.otherJobs })}</span>}
          </div>
        ) : (
          <>
            <p className="dashboard-summary-value">
              {t("dashboard.localActive", { active: local.active, maximum: local.maximum })}
            </p>
            <p>{t("dashboard.localWaiting", { waiting: local.waiting })}</p>
          </>
        );
  return (
    <Card className="dashboard-summary-card" aria-labelledby="local-card-title">
      <SummaryHeading
        icon={<LocalDownloadIcon />}
        title={t("dashboard.local")}
        titleId="local-card-title"
      />
      <a className="summary-link" href="#user-downloads-title">{t("dashboard.open")} <ArrowRight aria-hidden="true" /></a>
      {content}
      <p className="dashboard-summary-note">{t("dashboard.localNote")}</p>
    </Card>
  );
}

const idleLocalSummary: LocalDownloadSummary = {
  active: 0,
  completedFiles: 0,
  fileCount: 0,
  jobCount: 0,
  kind: null,
  maximum: DEFAULT_RECURSIVE_DOWNLOAD_CONCURRENCY,
  status: "idle",
  waiting: 0,
  name: null,
  otherJobs: 0,
  downloadedBytes: 0,
  totalBytes: 0,
  percent: 0,
};

export function UserDashboardPage({ onSessionExpired }: { onSessionExpired: () => void }) {
  const { apiError, formatBytes, t } = useI18n();
  const [activity, setActivity] = useState<TorrentActivitySummary | null>(null);
  const [activityError, setActivityError] = useState("");
  const [storage, setStorage] = useState<SharedStorageCapacity | null>(null);
  const [storageError, setStorageError] = useState("");
  const [local, setLocal] = useState<LocalDownloadSummary>(idleLocalSummary);
  const activityController = useRef<AbortController | null>(null);
  const storageController = useRef<AbortController | null>(null);
  const activityRunning = useRef(false);
  const activityQueued = useRef(false);
  const mounted = useRef(true);

  const refreshActivity = useCallback(() => {
    if (!mounted.current) return;
    if (activityRunning.current) {
      activityQueued.current = true;
      return;
    }
    activityRunning.current = true;
    const controller = new AbortController();
    activityController.current = controller;
    void loadTorrentActivity(controller.signal)
      .then((next) => {
        setActivity(next);
        setActivityError("");
      })
      .catch((caught: unknown) => {
        if (caught instanceof DOMException && caught.name === "AbortError") return;
        if (caught instanceof ApiError && caught.status === 401) {
          onSessionExpired();
          return;
        }
        setActivityError(apiError(caught, "dashboard.activityError"));
      })
      .finally(() => {
        activityRunning.current = false;
        if (!mounted.current || !activityQueued.current) return;
        activityQueued.current = false;
        refreshActivity();
      });
  }, [apiError, onSessionExpired]);

  const refreshStorage = useCallback(() => {
    storageController.current?.abort();
    const controller = new AbortController();
    storageController.current = controller;
    setStorageError("");
    void api.getSharedStorageCapacity(controller.signal)
      .then((next) => setStorage(next))
      .catch((caught: unknown) => {
        if (caught instanceof DOMException && caught.name === "AbortError") return;
        if (caught instanceof ApiError && caught.status === 401) {
          onSessionExpired();
          return;
        }
        setStorageError(apiError(caught, "dashboard.storageError"));
      })
      .finally(() => {
        if (storageController.current === controller) storageController.current = null;
      });
  }, [apiError, onSessionExpired]);

  useEffect(() => {
    mounted.current = true;
    refreshActivity();
    refreshStorage();
    return () => {
      mounted.current = false;
      activityQueued.current = false;
      activityController.current?.abort();
      storageController.current?.abort();
    };
  }, [refreshActivity, refreshStorage]);

  const usedPercent = storage === null || storage.total_bytes === 0
    ? 0
    : Math.min(100, Math.max(0, (storage.used_bytes / storage.total_bytes) * 100));

  return (
    <section className="user-dashboard" aria-labelledby="user-dashboard-title">
      <header className="user-dashboard-header">
        <h1 id="user-dashboard-title">{t("dashboard.title")}</h1>
      </header>

      <div className="dashboard-summary-grid">
        <Card className="dashboard-summary-card" aria-labelledby="activity-card-title">
          <SummaryHeading
            icon={<ActivityIcon />}
            title={t("dashboard.activity")}
            titleId="activity-card-title"
          />
          <a className="summary-link" href="#user-downloads-title">{t("dashboard.viewAll")} <ArrowRight aria-hidden="true" /></a>
          {activityError !== "" ? (
            <StateMessage tone="error">
              <span>{activityError}</span>
              <Button variant="secondary" onClick={refreshActivity}>{t("common.retry")}</Button>
            </StateMessage>
          ) : activity === null ? (
            <StateMessage tone="loading">{t("dashboard.activityLoading")}</StateMessage>
          ) : (
            <dl className="activity-metrics">
              <div className="active"><Download aria-hidden="true" /><dt>{t("dashboard.active")}</dt><dd>{activity.active}<span aria-hidden="true" /></dd></div>
              <div className="ready"><Check aria-hidden="true" /><dt>{t("dashboard.ready")}</dt><dd>{activity.ready}<span aria-hidden="true" /></dd></div>
              <div className="waiting"><Clock3 aria-hidden="true" /><dt>{t("dashboard.waiting")}</dt><dd>{activity.waiting}<span aria-hidden="true" /></dd></div>
            </dl>
          )}
        </Card>

        <LocalDownloadCard local={local} />

        <Card className="dashboard-summary-card" aria-labelledby="storage-card-title">
          <SummaryHeading
            icon={<StorageIcon />}
            title={t("dashboard.storage")}
            titleId="storage-card-title"
          />
          <span className="summary-link summary-link-static">{t("dashboard.viewDetails")} <ArrowRight aria-hidden="true" /></span>
          {storageError !== "" ? (
            <StateMessage tone="error">
              <span>{storageError}</span>
              <Button variant="secondary" onClick={refreshStorage}>{t("common.retry")}</Button>
            </StateMessage>
          ) : storage === null ? (
            <StateMessage tone="loading">{t("dashboard.storageLoading")}</StateMessage>
          ) : (
            <>
              <p className="dashboard-summary-value">
                {t("dashboard.storageAvailable", { value: formatBytes(storage.available_bytes) })}
              </p>
              <div className="storage-summary-line">
                <p>{t("dashboard.storageTotal", { value: formatBytes(storage.total_bytes) })}</p>
                <strong>{usedPercent.toFixed(0)} % {t("dashboard.used")}</strong>
              </div>
              <Progress
                label={t("dashboard.storageProgress", { value: usedPercent.toFixed(0) })}
                value={usedPercent}
              />
              <div className="storage-legend">
                <span className="used"><HardDrive aria-hidden="true" />{formatBytes(storage.used_bytes)} {t("dashboard.used")}</span>
                <span>{formatBytes(storage.available_bytes)} {t("dashboard.available")}</span>
              </div>
            </>
          )}
        </Card>
      </div>

      <UserDownloadsPage
        onActivityChanged={refreshActivity}
        onLocalTransferChanged={setLocal}
        onSessionExpired={onSessionExpired}
      />
    </section>
  );
}
