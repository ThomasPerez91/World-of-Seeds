import { useCallback, useEffect, useMemo, useState } from "react";

import { api, ApiError, type AdminQBittorrentRuntime } from "../../api/client";
import { RefreshIcon } from "../../components/icons";
import { Badge, Card, IconButton, Progress, StateMessage, Tooltip } from "../../components/ui";
import { type MessageKey, useI18n } from "../../i18n";
import { AdminPageShell, type AdminView } from "./AdminPageShell";
import { AdminSortableHeader, type AdminSortOrder } from "./AdminSortableHeader";

type QBittorrentTorrent = AdminQBittorrentRuntime["torrents"][number];
type SortKey = "name" | "size" | "status" | "progress";

function compareTorrents(left: QBittorrentTorrent, right: QBittorrentTorrent, key: SortKey): number {
  if (key === "name") return left.name.localeCompare(right.name, undefined, { sensitivity: "base" });
  if (key === "size") return left.size_bytes - right.size_bytes;
  if (key === "status") return left.state.localeCompare(right.state);
  return left.progress - right.progress;
}

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
  const [search, setSearch] = useState("");
  const [sortKey, setSortKey] = useState<SortKey>("name");
  const [sortOrder, setSortOrder] = useState<AdminSortOrder>("asc");

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

  const visibleTorrents = useMemo(() => {
    const normalizedSearch = search.trim().toLocaleLowerCase();
    return [...(runtime?.torrents ?? [])]
      .filter((torrent) => normalizedSearch === "" || torrent.name.toLocaleLowerCase().includes(normalizedSearch))
      .sort((left, right) => {
        const compared = compareTorrents(left, right, sortKey);
        return sortOrder === "asc" ? compared : -compared;
      });
  }, [runtime, search, sortKey, sortOrder]);

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

        {runtime !== null && <div className="admin-qb-runtime-controls">
          <Card className="admin-qb-transfer-card">
            <div><span>{t("admin.download")}</span><strong>{formatBytes(runtime.download_speed_bytes)}/s</strong></div>
            <div><span>{t("admin.upload")}</span><strong>{formatBytes(runtime.upload_speed_bytes)}/s</strong></div>
          </Card>
          <label className="admin-qb-search-card">
            <span>{t("admin.qbSearch")}</span>
            <input
              type="search"
              value={search}
              placeholder={t("admin.qbSearchPlaceholder")}
              onChange={(event) => setSearch(event.target.value)}
            />
          </label>
        </div>}
        {loading && runtime === null ? <StateMessage tone="loading">{t("admin.readingTorrents")}</StateMessage> : null}
        {error !== "" ? <StateMessage tone="error">{error}</StateMessage> : null}
        {runtime !== null && runtime.torrents.length === 0 ? <StateMessage tone="empty">{t("admin.noQbTorrent")}</StateMessage> : null}
        {runtime !== null && runtime.torrents.length > 0 && visibleTorrents.length === 0 ? <StateMessage tone="empty">{t("admin.qbNoSearchResults")}</StateMessage> : null}
        {visibleTorrents.length > 0 ? <div className="admin-runtime-table-wrap">
          <table className="admin-runtime-table admin-qb-table">
            <thead><tr>
              {sortableHeader("name", t("admin.torrentName"))}
              {sortableHeader("size", t("admin.size"), "center")}
              {sortableHeader("status", t("admin.status"))}
              {sortableHeader("progress", t("admin.progress"))}
            </tr></thead>
            <tbody>{visibleTorrents.map((torrent) => {
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
