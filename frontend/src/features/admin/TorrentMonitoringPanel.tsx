import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  api,
  ApiError,
  type NewGreedyTorrent,
  type QBittorrentTorrent,
  type QBittorrentTorrentListing,
} from "../../api/client";
import { RefreshIcon } from "../../components/icons";
import { type MessageKey, useI18n } from "../../i18n";

const PAGE_SIZE = 50;

type TorrentStateKind = "active" | "complete" | "error" | "paused" | "pending";

function statePresentation(state: string): { kind: TorrentStateKind; label: MessageKey | null } {
  if (["downloading", "forcedDL"].includes(state)) {
    return { kind: "active", label: "admin.stateDownloading" };
  }
  if (["uploading", "forcedUP", "stalledUP"].includes(state)) {
    return { kind: "complete", label: "admin.stateSeeding" };
  }
  if (["pausedDL", "pausedUP", "stoppedDL", "stoppedUP"].includes(state)) {
    return { kind: "paused", label: "admin.statePaused" };
  }
  if (["error", "missingFiles", "unknown"].includes(state)) {
    return { kind: "error", label: "admin.stateError" };
  }
  if (state === "stalledDL") {
    return { kind: "pending", label: "admin.stateStalled" };
  }
  if (
    [
      "allocating",
      "checkingDL",
      "checkingUP",
      "checkingResumeData",
      "metaDL",
      "moving",
      "queuedDL",
      "queuedUP",
    ].includes(state)
  ) {
    return { kind: "pending", label: "admin.statePreparing" };
  }
  return { kind: "pending", label: null };
}

function formatEta(
  seconds: number | null,
  t: (key: MessageKey, params?: Record<string, string | number>) => string,
): string {
  if (seconds === null) return "—";
  if (seconds <= 0) return t("admin.completed");
  const days = Math.floor(seconds / 86_400);
  const hours = Math.floor((seconds % 86_400) / 3_600);
  const minutes = Math.floor((seconds % 3_600) / 60);
  if (days > 0) return t("admin.daysHours", { days, hours });
  if (hours > 0) return t("admin.hoursMinutes", { hours, minutes });
  return t("admin.minutes", { minutes: Math.max(1, minutes) });
}

export function correlateNewGreedyTorrents(
  qbittorrent: QBittorrentTorrent[],
  newgreedy: NewGreedyTorrent[],
): { byQBHash: Map<string, NewGreedyTorrent>; unmatched: number } {
  const byQBHash = new Map<string, NewGreedyTorrent>();
  const matchedNewGreedy = new Set<string>();
  for (const entry of newgreedy) {
    const normalized = entry.id.toLowerCase();
    const candidates = qbittorrent.filter((torrent) => {
      const hash = torrent.id.toLowerCase();
      return normalized.length === 40 ? hash === normalized : hash.startsWith(normalized);
    });
    if (candidates.length === 1) {
      byQBHash.set(candidates[0].id, entry);
      matchedNewGreedy.add(entry.id);
    }
  }
  return { byQBHash, unmatched: newgreedy.length - matchedNewGreedy.size };
}

function NewGreedyTorrentStatus({
  available,
  torrent,
}: {
  available: boolean;
  torrent: NewGreedyTorrent | undefined;
}) {
  const { formatBytes, t } = useI18n();
  if (!available) return <span className="torrent-secondary-state unavailable">{t("admin.serviceUnavailable")}</span>;
  if (torrent === undefined) {
    return <span className="torrent-secondary-state">{t("admin.notTracked")}</span>;
  }
  if (torrent.stalled) {
    return <span className="torrent-secondary-state warning">{t("admin.blocked")}</span>;
  }
  if (torrent.target_reached) {
    return <span className="torrent-secondary-state complete">{t("admin.targetReached")}</span>;
  }
  return (
    <span className="torrent-secondary-state active">
      {torrent.mode === "down" ? t("admin.active") : t("admin.seed")} · {formatBytes(torrent.fake_uploaded_bytes)}
    </span>
  );
}

export function TorrentMonitoringPanel({
  onSessionExpired,
}: {
  onSessionExpired: () => void;
}) {
  const { formatBytes, formatDate, formatNumber, t } = useI18n();
  const [listing, setListing] = useState<QBittorrentTorrentListing | null>(null);
  const [newgreedy, setNewgreedy] = useState<NewGreedyTorrent[]>([]);
  const [newgreedyAvailable, setNewgreedyAvailable] = useState(false);
  const [error, setError] = useState("");
  const [newgreedyError, setNewgreedyError] = useState("");
  const [loading, setLoading] = useState(true);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);
  const [visibleCount, setVisibleCount] = useState(PAGE_SIZE);
  const mounted = useRef(true);
  const inFlight = useRef(false);

  const load = useCallback(async () => {
    if (inFlight.current) return;
    inFlight.current = true;
    if (mounted.current) setLoading(true);
    try {
      const [qbittorrentResult, newgreedyResult] = await Promise.allSettled([
        api.listQBittorrentTorrents(),
        api.listNewGreedyTorrents(),
      ]);
      if (!mounted.current) return;

      const authenticationFailure = [qbittorrentResult, newgreedyResult].some(
        (result) =>
          result.status === "rejected" &&
          result.reason instanceof ApiError &&
          result.reason.status === 401,
      );
      if (authenticationFailure) {
        onSessionExpired();
        return;
      }

      if (qbittorrentResult.status === "fulfilled") {
        setListing(qbittorrentResult.value);
        setError("");
      } else {
        setError(t("admin.qbListUnavailable"));
      }
      if (newgreedyResult.status === "fulfilled") {
        setNewgreedy(newgreedyResult.value.torrents);
        setNewgreedyAvailable(true);
        setNewgreedyError("");
      } else {
        setNewgreedyAvailable(false);
        setNewgreedyError(t("admin.newgreedyCorrelationUnavailable"));
      }
      setLastUpdated(new Date());
    } finally {
      inFlight.current = false;
      if (mounted.current) setLoading(false);
    }
  }, [onSessionExpired, t]);

  useEffect(() => {
    mounted.current = true;
    void load();
    const interval = window.setInterval(() => void load(), 15_000);
    return () => {
      mounted.current = false;
      window.clearInterval(interval);
    };
  }, [load]);

  const torrents = listing?.torrents ?? [];
  const correlation = useMemo(
    () => correlateNewGreedyTorrents(torrents, newgreedy),
    [newgreedy, torrents],
  );
  const visibleTorrents = torrents.slice(0, visibleCount);
  const unmatchedNewGreedy = correlation.unmatched;

  return (
    <section className="torrent-monitoring" aria-labelledby="torrent-monitoring-title">
      <div className="torrent-monitoring-heading">
        <div>
          <p className="eyebrow">{t("admin.activity")}</p>
          <h3 id="torrent-monitoring-title">{t("admin.qbTorrents")}</h3>
          <p>
            {t(torrents.length === 1 ? "admin.torrentOne" : "admin.torrentMany", { count: formatNumber(torrents.length) })}
            {unmatchedNewGreedy > 0
              ? t("admin.unmatchedNewgreedy", { count: formatNumber(unmatchedNewGreedy) })
              : ""}
          </p>
        </div>
        <button
          type="button"
          className="refresh-button torrent-refresh-button"
          disabled={loading}
          onClick={() => void load()}
        >
          <RefreshIcon className={loading ? "rotating" : undefined} />
          {loading ? t("downloads.refreshing") : t("common.refresh")}
        </button>
      </div>

      <p className="form-message error-message" role="alert">
        {[error, newgreedyError].filter(Boolean).join(" ")}
      </p>
      {loading && listing === null ? (
        <div className="torrent-list-state" role="status">
          {t("admin.readingTorrents")}
        </div>
      ) : listing === null ? (
        <div className="torrent-list-state error">{t("admin.noQbState")}</div>
      ) : torrents.length === 0 ? (
        <div className="torrent-list-state">{t("admin.noQbTorrent")}</div>
      ) : (
        <>
          <ol className="torrent-monitoring-list">
            {visibleTorrents.map((torrent) => {
              const presentation = statePresentation(torrent.state);
              const linkedNewGreedy = correlation.byQBHash.get(torrent.id);
              return (
                <li key={torrent.id}>
                  <article className="torrent-monitoring-card">
                    <header>
                      <div className="torrent-monitoring-name">
                        <h4 title={torrent.name}>{torrent.name}</h4>
                        <p>
                          {[torrent.category, torrent.tracker_host].filter(Boolean).join(" · ") ||
                            t("admin.noCategoryTracker")}
                        </p>
                      </div>
                      <span className={`torrent-primary-state ${presentation.kind}`}>
                        {presentation.label === null ? torrent.state : t(presentation.label)}
                      </span>
                    </header>
                    <div className="torrent-progress">
                      <progress value={torrent.progress} max={1}>
                        {formatNumber(Math.round(torrent.progress * 100))} %
                      </progress>
                      <strong>{formatNumber(Math.round(torrent.progress * 100))} %</strong>
                    </div>
                    <dl className="torrent-monitoring-metrics">
                      <div>
                        <dt>{t("admin.size")}</dt>
                        <dd>{formatBytes(torrent.size_bytes)}</dd>
                      </div>
                      <div>
                        <dt>{t("admin.download")}</dt>
                        <dd>{formatBytes(torrent.download_speed_bytes)}/s</dd>
                      </div>
                      <div>
                        <dt>{t("admin.upload")}</dt>
                        <dd>{formatBytes(torrent.upload_speed_bytes)}/s</dd>
                      </div>
                      <div>
                        <dt>{t("admin.ratio")}</dt>
                        <dd>{formatNumber(torrent.ratio, { maximumFractionDigits: 2 })}</dd>
                      </div>
                      <div>
                        <dt>{t("admin.remainingTime")}</dt>
                        <dd>{formatEta(torrent.eta_seconds, t)}</dd>
                      </div>
                      <div>
                        <dt>NewGreedy</dt>
                        <dd>
                          <NewGreedyTorrentStatus
                            available={newgreedyAvailable}
                            torrent={linkedNewGreedy}
                          />
                        </dd>
                      </div>
                    </dl>
                  </article>
                </li>
              );
            })}
          </ol>
          {visibleCount < torrents.length && (
            <button
              type="button"
              className="secondary-button torrent-show-more"
              onClick={() => setVisibleCount((current) => current + PAGE_SIZE)}
            >
              {t("admin.showNext", { count: formatNumber(Math.min(PAGE_SIZE, torrents.length - visibleCount)) })}
            </button>
          )}
          {listing.truncated && (
            <p className="torrent-truncated-notice">
              {t("admin.torrentsTruncated")}
            </p>
          )}
        </>
      )}
      {lastUpdated !== null && (
        <p className="services-last-check">
          {t("admin.activityUpdated", { time: formatDate(lastUpdated, { timeStyle: "medium" }) })}
        </p>
      )}
    </section>
  );
}
