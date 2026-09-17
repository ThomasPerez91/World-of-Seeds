import { useCallback, useEffect, useState } from "react";

import { api, ApiError, type AdminQBittorrentRuntime } from "../../api/client";
import { RefreshIcon } from "../../components/icons";
import { Badge, Card, IconButton, Progress, StateMessage, Tooltip } from "../../components/ui";
import { type MessageKey, useI18n } from "../../i18n";
import { AdminPageShell, type AdminView } from "./AdminPageShell";

function statePresentation(state: string): { label: MessageKey | null; tone: "neutral" | "success" | "warning" | "danger" } {
  if (["downloading", "forcedDL"].includes(state)) return { label: "admin.stateDownloading", tone: "success" };
  if (["uploading", "forcedUP", "stalledUP"].includes(state)) return { label: "admin.stateSeeding", tone: "success" };
  if (["pausedDL", "pausedUP", "stoppedDL", "stoppedUP"].includes(state)) return { label: "admin.statePaused", tone: "neutral" };
  if (["error", "missingFiles", "unknown"].includes(state)) return { label: "admin.stateError", tone: "danger" };
  if (state === "stalledDL") return { label: "admin.stateStalled", tone: "warning" };
  return { label: "admin.statePreparing", tone: "warning" };
}

export function AdminQBittorrentPage({ onBack, onNavigate, onSessionExpired }: {
  onBack: () => void;
  onNavigate: (view: AdminView) => void;
  onSessionExpired: () => void;
}) {
  const { formatBytes, formatDate, formatNumber, t } = useI18n();
  const [runtime, setRuntime] = useState<AdminQBittorrentRuntime | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      setRuntime(await api.getAdminQBittorrentRuntime());
    } catch (caught) {
      if (caught instanceof ApiError && caught.status === 401) return onSessionExpired();
      setError(t("admin.qbListUnavailable"));
    } finally {
      setLoading(false);
    }
  }, [onSessionExpired, t]);

  useEffect(() => { void load(); }, [load]);

  return (
    <AdminPageShell activeView="admin-qbittorrent" onBack={onBack} onNavigate={onNavigate}>
      <section className="admin-section admin-runtime" aria-labelledby="admin-qb-runtime-title" aria-busy={loading}>
        <div className="section-heading admin-runtime-heading">
          <div>
            <p className="eyebrow">{t("admin.activity")}</p>
            <h2 id="admin-qb-runtime-title">{t("admin.qbittorrentControl")}</h2>
            <p className="section-intro">{t("admin.qbittorrentRuntimeIntro")}</p>
          </div>
          <Tooltip content={loading ? t("downloads.refreshing") : t("common.refresh")}>
            <IconButton className="admin-runtime-refresh" label={t("common.refresh")} disabled={loading} onClick={() => void load()}>
              <RefreshIcon className={loading ? "rotating" : undefined} />
            </IconButton>
          </Tooltip>
        </div>

        {runtime !== null && <div className="admin-runtime-summary">
          <Card className="admin-metric-card"><span>{t("admin.download")}</span><strong>{formatBytes(runtime.download_speed_bytes)}/s</strong></Card>
          <Card className="admin-metric-card"><span>{t("admin.upload")}</span><strong>{formatBytes(runtime.upload_speed_bytes)}/s</strong></Card>
        </div>}
        {loading && runtime === null ? <StateMessage tone="loading">{t("admin.readingTorrents")}</StateMessage> : null}
        {error !== "" ? <StateMessage tone="error">{error}</StateMessage> : null}
        {runtime !== null && runtime.torrents.length === 0 ? <StateMessage tone="empty">{t("admin.noQbTorrent")}</StateMessage> : null}
        {runtime !== null && runtime.torrents.length > 0 ? <div className="admin-runtime-table-wrap">
          <table className="admin-runtime-table admin-qb-table">
            <thead><tr><th>{t("admin.torrentName")}</th><th>{t("admin.size")}</th><th>{t("admin.status")}</th><th>{t("admin.progress")}</th></tr></thead>
            <tbody>{runtime.torrents.map((torrent) => {
              const presentation = statePresentation(torrent.state);
              const percent = Math.round(torrent.progress * 100);
              return <tr key={torrent.hash}>
                <td data-label={t("admin.torrentName")}><Tooltip content={torrent.name} overflowOnly className="admin-runtime-name"><span>{torrent.name}</span></Tooltip></td>
                <td data-label={t("admin.size")}>{formatBytes(torrent.size_bytes)}</td>
                <td data-label={t("admin.status")}><Badge tone={presentation.tone}>{presentation.label === null ? torrent.state : t(presentation.label)}</Badge></td>
                <td data-label={t("admin.progress")}><div className="admin-runtime-progress"><Progress value={percent} label={`${percent} %`} /><span>{formatNumber(percent)} %</span></div></td>
              </tr>;
            })}</tbody>
          </table>
        </div> : null}
        {runtime?.truncated ? <p className="admin-runtime-note">{t("admin.torrentsTruncated")}</p> : null}
        {runtime !== null ? <p className="admin-runtime-updated">{t("admin.lastCheck", { date: formatDate(new Date(runtime.checked_at)) })}</p> : null}
      </section>
    </AdminPageShell>
  );
}
