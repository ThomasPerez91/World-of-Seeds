import { type ReactNode, useCallback, useEffect, useRef, useState } from "react";

import {
  api,
  ApiError,
  type NetworkThroughput,
  type NetworkThroughputSample,
  type SharedStorageCapacity,
  type TorrentRequestV2,
} from "../../api/client";
import {
  ActivityIcon,
  StorageIcon,
} from "../../components/icons";
import { ArrowDown, ArrowRight, ArrowUp, Check, Clock3, Download, Gauge, HardDrive } from "lucide-react";
import { Button, Card, Progress, StateMessage } from "../../components/ui";
import { useI18n } from "../../i18n";
import { UserDownloadsPage } from "../torrents/UserDownloadsPage";

const ACTIVITY_PAGE_SIZE = 100;
export const NETWORK_REFRESH_MS = 15_000;

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

function Sparkline({
  label,
  samples,
  tone,
}: {
  label: string;
  samples: NetworkThroughputSample[];
  tone: "download" | "upload";
}) {
  const values = samples.map((sample) => sample.value_bytes_per_second);
  const maximum = Math.max(...values, 1);
  const points = values.map((value, index) => {
    const x = values.length <= 1 ? 50 : (index / (values.length - 1)) * 100;
    const y = 29 - (Math.max(0, value) / maximum) * 25;
    return `${x.toFixed(2)},${y.toFixed(2)}`;
  }).join(" ");
  return (
    <svg
      className={`network-sparkline ${tone}`}
      viewBox="0 0 100 32"
      preserveAspectRatio="none"
      role="img"
      aria-label={label}
    >
      <polyline points={points} vectorEffect="non-scaling-stroke" />
    </svg>
  );
}

export function NetworkThroughputCard({ onSessionExpired }: { onSessionExpired: () => void }) {
  const { formatBytes, t } = useI18n();
  const [network, setNetwork] = useState<NetworkThroughput | null>(null);
  const [networkError, setNetworkError] = useState(false);
  const controller = useRef<AbortController | null>(null);

  const refresh = useCallback(() => {
    controller.current?.abort();
    const nextController = new AbortController();
    controller.current = nextController;
    void api.getNetworkThroughput(nextController.signal)
      .then((next) => {
        setNetwork(next);
        setNetworkError(false);
      })
      .catch((caught: unknown) => {
        if (caught instanceof DOMException && caught.name === "AbortError") return;
        if (caught instanceof ApiError && caught.status === 401) {
          onSessionExpired();
          return;
        }
        setNetworkError(true);
      });
  }, [onSessionExpired]);

  useEffect(() => {
    refresh();
    const interval = window.setInterval(refresh, NETWORK_REFRESH_MS);
    return () => {
      window.clearInterval(interval);
      controller.current?.abort();
    };
  }, [refresh]);

  const ready = network?.status === "ok" && network.download !== null && network.upload !== null;
  return (
    <Card className="dashboard-summary-card network-throughput-card" aria-labelledby="network-card-title">
      <SummaryHeading icon={<Gauge />} title={t("dashboard.network")} titleId="network-card-title" />
      <span className="network-period-control">{t("dashboard.networkRealtime")}</span>
      {networkError || network?.status === "unavailable" ? (
        <p className="network-throughput-state is-error" role="status">{t("dashboard.networkUnavailable")}</p>
      ) : network?.status === "no_data" ? (
        <p className="network-throughput-state" role="status">{t("dashboard.networkNoData")}</p>
      ) : !ready ? (
        <StateMessage tone="loading">{t("dashboard.networkLoading")}</StateMessage>
      ) : (
        <div className="network-throughput-grid">
          <section className="network-throughput-direction download" aria-label={t("dashboard.networkDownload")}>
            <div className="network-throughput-label"><ArrowDown aria-hidden="true" /><span>{t("dashboard.networkDownload")}</span></div>
            <strong>{formatBytes(network.download!.current_bytes_per_second)}/s</strong>
            <Sparkline label={t("dashboard.networkDownloadChart")} samples={network.download!.samples} tone="download" />
          </section>
          <section className="network-throughput-direction upload" aria-label={t("dashboard.networkUpload")}>
            <div className="network-throughput-label"><ArrowUp aria-hidden="true" /><span>{t("dashboard.networkUpload")}</span></div>
            <strong>{formatBytes(network.upload!.current_bytes_per_second)}/s</strong>
            <Sparkline label={t("dashboard.networkUploadChart")} samples={network.upload!.samples} tone="upload" />
          </section>
        </div>
      )}
    </Card>
  );
}

export function UserDashboardPage({
  isAdmin = false,
  onSessionExpired,
}: {
  isAdmin?: boolean;
  onSessionExpired: () => void;
}) {
  const { apiError, formatBytes, t } = useI18n();
  const [activity, setActivity] = useState<TorrentActivitySummary | null>(null);
  const [activityError, setActivityError] = useState("");
  const [storage, setStorage] = useState<SharedStorageCapacity | null>(null);
  const [storageError, setStorageError] = useState("");
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

        <NetworkThroughputCard onSessionExpired={onSessionExpired} />

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
        isAdmin={isAdmin}
        onActivityChanged={refreshActivity}
        onSessionExpired={onSessionExpired}
      />
    </section>
  );
}
