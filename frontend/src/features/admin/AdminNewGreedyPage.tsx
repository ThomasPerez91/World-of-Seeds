import { useCallback, useEffect, useMemo, useState } from "react";

import { api, ApiError, type AdminNewGreedyRuntime } from "../../api/client";
import { RefreshIcon } from "../../components/icons";
import { Badge, IconButton, StateMessage, Tooltip } from "../../components/ui";
import { type MessageKey, useI18n } from "../../i18n";
import { AdminPageShell, type AdminView } from "./AdminPageShell";
import { AdminSortableHeader, type AdminSortOrder } from "./AdminSortableHeader";

type NewGreedyTorrent = AdminNewGreedyRuntime["torrents"][number];
type SortKey = "name" | "status" | "downloaded" | "uploaded" | "ratio";

function compareTorrents(left: NewGreedyTorrent, right: NewGreedyTorrent, key: SortKey): number {
  if (key === "name") return (left.name ?? "").localeCompare(right.name ?? "", undefined, { sensitivity: "base" });
  if (key === "status") return left.status.localeCompare(right.status);
  if (key === "downloaded") return left.downloaded_bytes - right.downloaded_bytes;
  if (key === "uploaded") return left.uploaded_bytes - right.uploaded_bytes;
  return (left.ratio ?? -1) - (right.ratio ?? -1);
}

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
  const [sortKey, setSortKey] = useState<SortKey>("name");
  const [sortOrder, setSortOrder] = useState<AdminSortOrder>("asc");
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

  const sortedTorrents = useMemo(() => [...(runtime?.torrents ?? [])].sort((left, right) => {
    const compared = compareTorrents(left, right, sortKey);
    return sortOrder === "asc" ? compared : -compared;
  }), [runtime, sortKey, sortOrder]);

  function changeSort(key: SortKey) {
    if (sortKey === key) {
      setSortOrder((current) => current === "asc" ? "desc" : "asc");
      return;
    }
    setSortKey(key);
    setSortOrder("asc");
  }

  const sortableHeader = (key: SortKey, label: string, align: "left" | "center" | "right" = "left") => (
    <AdminSortableHeader
      active={sortKey === key}
      align={align}
      direction={sortOrder}
      label={label}
      onSort={() => changeSort(key)}
      sortLabel={t("admin.cleanupSort", { column: label })}
    />
  );

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
        <thead><tr>
          <th>{t("admin.hash")}</th>
          {sortableHeader("name", t("admin.torrentName"))}
          {sortableHeader("status", t("admin.status"))}
          {sortableHeader("downloaded", t("admin.downloadShort"))}
          {sortableHeader("uploaded", t("admin.uploadShort"), "center")}
          {sortableHeader("ratio", t("admin.ratio"), "right")}
        </tr></thead>
        <tbody>{sortedTorrents.map((torrent) => <tr key={torrent.hash}>
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
