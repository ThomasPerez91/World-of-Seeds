import { useCallback, useEffect, useState } from "react";

import { api, ApiError, type AdminNewGreedyRuntime } from "../../api/client";
import { RefreshIcon } from "../../components/icons";
import { Badge, IconButton, StateMessage, Tooltip } from "../../components/ui";
import { type MessageKey, useI18n } from "../../i18n";
import { AdminPageShell, type AdminView } from "./AdminPageShell";

const statusKeys: Record<AdminNewGreedyRuntime["torrents"][number]["status"], MessageKey> = {
  downloading: "admin.stateDownloading",
  seeding: "admin.stateSeeding",
  stalled: "admin.stateStalled",
  target_reached: "admin.targetReached",
};

export function AdminNewGreedyPage({ onBack, onNavigate, onSessionExpired }: {
  onBack: () => void;
  onNavigate: (view: AdminView) => void;
  onSessionExpired: () => void;
}) {
  const { formatBytes, formatDate, formatNumber, t } = useI18n();
  const [runtime, setRuntime] = useState<AdminNewGreedyRuntime | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try { setRuntime(await api.getAdminNewGreedyRuntime()); }
    catch (caught) {
      if (caught instanceof ApiError && caught.status === 401) return onSessionExpired();
      setError(t("admin.newgreedyListUnavailable"));
    } finally { setLoading(false); }
  }, [onSessionExpired, t]);

  useEffect(() => { void load(); }, [load]);

  return <AdminPageShell activeView="admin-newgreedy" onBack={onBack} onNavigate={onNavigate}>
    <section className="admin-section admin-runtime" aria-labelledby="admin-newgreedy-runtime-title" aria-busy={loading}>
      <div className="section-heading admin-runtime-heading">
        <div><p className="eyebrow">{t("admin.activity")}</p><h2 id="admin-newgreedy-runtime-title">{t("admin.newgreedyControl")}</h2><p className="section-intro">{t("admin.newgreedyRuntimeIntro")}</p></div>
        <Tooltip content={loading ? t("downloads.refreshing") : t("common.refresh")}><IconButton className="admin-runtime-refresh" label={t("common.refresh")} disabled={loading} onClick={() => void load()}><RefreshIcon className={loading ? "rotating" : undefined} /></IconButton></Tooltip>
      </div>
      {loading && runtime === null ? <StateMessage tone="loading">{t("admin.readingTorrents")}</StateMessage> : null}
      {error !== "" ? <StateMessage tone="error">{error}</StateMessage> : null}
      {runtime !== null && runtime.torrents.length === 0 ? <StateMessage tone="empty">{t("admin.noNewgreedyTorrent")}</StateMessage> : null}
      {runtime !== null && runtime.torrents.length > 0 ? <div className="admin-runtime-table-wrap"><table className="admin-runtime-table admin-newgreedy-table">
        <thead><tr><th>{t("admin.hash")}</th><th>{t("admin.torrentName")}</th><th>{t("admin.status")}</th><th>{t("admin.downloadShort")}</th><th>{t("admin.uploadShort")}</th><th>{t("admin.ratio")}</th></tr></thead>
        <tbody>{runtime.torrents.map((torrent) => <tr key={torrent.hash}>
          <td data-label={t("admin.hash")}><code>{torrent.hash}</code></td>
          <td data-label={t("admin.torrentName")}>{torrent.name === null ? "—" : <Tooltip content={torrent.name} overflowOnly className="admin-runtime-name"><span>{torrent.name}</span></Tooltip>}</td>
          <td data-label={t("admin.status")}><Badge tone={torrent.status === "stalled" ? "warning" : "success"}>{t(statusKeys[torrent.status])}</Badge></td>
          <td data-label={t("admin.downloadShort")}>{formatBytes(torrent.downloaded_bytes)}</td>
          <td data-label={t("admin.uploadShort")}>{formatBytes(torrent.uploaded_bytes)}</td>
          <td data-label={t("admin.ratio")}>{torrent.ratio === null ? "—" : formatNumber(torrent.ratio, { maximumFractionDigits: 4 })}</td>
        </tr>)}</tbody>
      </table></div> : null}
      {runtime !== null ? <p className="admin-runtime-updated">{t("admin.lastCheck", { date: formatDate(new Date(runtime.checked_at)) })}</p> : null}
    </section>
  </AdminPageShell>;
}
