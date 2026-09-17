import { useCallback, useEffect, useMemo, useState } from "react";
import { ArrowDown, ArrowUp, ChevronsUpDown, Trash2 } from "lucide-react";

import {
  api,
  ApiError,
  type AdminCleanupItem,
  type AdminCleanupListing,
} from "../../api/client";
import { Dialog } from "../../components/Dialog";
import { RefreshIcon } from "../../components/icons";
import { Button, IconButton, StateMessage, Tooltip } from "../../components/ui";
import { useI18n } from "../../i18n";
import { AdminPageShell, type AdminView } from "./AdminPageShell";

type SortKey = "name" | "size" | "subscribers" | "deletion";
type SortOrder = "asc" | "desc";
type SubscriberFilter = "all" | "none" | "active";

function compareItems(left: AdminCleanupItem, right: AdminCleanupItem, key: SortKey): number {
  if (key === "name") return left.name.localeCompare(right.name, undefined, { sensitivity: "base" });
  if (key === "size") return left.size_bytes - right.size_bytes;
  if (key === "subscribers") return left.subscriber_count - right.subscriber_count;
  if (left.deletion_at === null && right.deletion_at === null) return 0;
  if (left.deletion_at === null) return 1;
  if (right.deletion_at === null) return -1;
  return Date.parse(left.deletion_at) - Date.parse(right.deletion_at);
}

export function AdminCleanupPage({ onBack, onNavigate, onSessionExpired }: {
  onBack: () => void;
  onNavigate: (view: AdminView) => void;
  onSessionExpired: () => void;
}) {
  const { formatBytes, formatDate, formatNumber, t } = useI18n();
  const [listing, setListing] = useState<AdminCleanupListing | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [search, setSearch] = useState("");
  const [subscriberFilter, setSubscriberFilter] = useState<SubscriberFilter>("all");
  const [sortKey, setSortKey] = useState<SortKey>("name");
  const [sortOrder, setSortOrder] = useState<SortOrder>("asc");
  const [purgeSelection, setPurgeSelection] = useState<AdminCleanupItem[] | null>(null);
  const [purging, setPurging] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      setListing(await api.getAdminCleanup());
    } catch (caught) {
      if (caught instanceof ApiError && caught.status === 401) return onSessionExpired();
      setError(t("admin.cleanupLoadFailed"));
    } finally {
      setLoading(false);
    }
  }, [onSessionExpired, t]);

  useEffect(() => { void load(); }, [load]);

  const visibleItems = useMemo(() => {
    const normalizedSearch = search.trim().toLocaleLowerCase();
    return [...(listing?.items ?? [])]
      .filter((item) => normalizedSearch === "" || item.name.toLocaleLowerCase().includes(normalizedSearch))
      .filter((item) => {
        if (subscriberFilter === "none") return item.subscriber_count === 0;
        if (subscriberFilter === "active") return item.subscriber_count > 0;
        return true;
      })
      .sort((left, right) => {
        const compared = compareItems(left, right, sortKey);
        return sortOrder === "asc" ? compared : -compared;
      });
  }, [listing, search, sortKey, sortOrder, subscriberFilter]);

  function changeSort(key: SortKey) {
    if (sortKey === key) {
      setSortOrder((current) => current === "asc" ? "desc" : "asc");
      return;
    }
    setSortKey(key);
    setSortOrder("asc");
  }

  function sortIcon(key: SortKey) {
    if (sortKey !== key) return <ChevronsUpDown aria-hidden="true" />;
    return sortOrder === "asc"
      ? <ArrowUp aria-hidden="true" />
      : <ArrowDown aria-hidden="true" />;
  }

  async function confirmPurge() {
    if (purgeSelection === null || purgeSelection.length === 0) return;
    setPurging(true);
    setError("");
    setMessage("");
    try {
      const result = await api.purgeAdminCleanup(purgeSelection.map((item) => item.id));
      setPurgeSelection(null);
      setMessage(t("admin.cleanupPurgeScheduled", { count: formatNumber(result.scheduled) }));
      await load();
    } catch (caught) {
      if (caught instanceof ApiError && caught.status === 401) return onSessionExpired();
      setError(t("admin.cleanupPurgeFailed"));
    } finally {
      setPurging(false);
    }
  }

  const sortableHeader = (key: SortKey, label: string) => (
    <th aria-sort={sortKey === key ? (sortOrder === "asc" ? "ascending" : "descending") : "none"}>
      <button
        type="button"
        className="admin-cleanup-sort"
        onClick={() => changeSort(key)}
        aria-label={t("admin.cleanupSort", { column: label })}
      >
        <span>{label}</span>
        {sortIcon(key)}
      </button>
    </th>
  );

  return (
    <AdminPageShell activeView="admin-cleanup" onBack={onBack} onNavigate={onNavigate}>
      <section className="admin-section admin-runtime admin-cleanup" aria-labelledby="admin-cleanup-title" aria-busy={loading}>
        <div className="section-heading admin-runtime-heading">
          <div>
            <p className="eyebrow">{t("admin.storage")}</p>
            <h2 id="admin-cleanup-title">{t("admin.cleanupTitle")}</h2>
            <p className="section-intro">{t("admin.cleanupIntro")}</p>
          </div>
          <Tooltip content={loading ? t("downloads.refreshing") : t("common.refresh")}>
            <IconButton className="admin-runtime-refresh" label={t("common.refresh")} disabled={loading || purging} onClick={() => void load()}>
              <RefreshIcon className={loading ? "rotating" : undefined} />
            </IconButton>
          </Tooltip>
        </div>

        <div className="admin-cleanup-toolbar">
          <label>
            <span>{t("admin.cleanupSearch")}</span>
            <input
              type="search"
              value={search}
              placeholder={t("admin.cleanupSearchPlaceholder")}
              onChange={(event) => setSearch(event.target.value)}
            />
          </label>
          <label>
            <span>{t("admin.cleanupSubscribersFilter")}</span>
            <select value={subscriberFilter} onChange={(event) => setSubscriberFilter(event.target.value as SubscriberFilter)}>
              <option value="all">{t("admin.cleanupSubscribersAll")}</option>
              <option value="none">{t("admin.cleanupSubscribersNone")}</option>
              <option value="active">{t("admin.cleanupSubscribersActive")}</option>
            </select>
          </label>
          <Button
            variant="danger"
            className="admin-cleanup-purge-filtered"
            disabled={loading || purging || visibleItems.length === 0}
            onClick={() => setPurgeSelection(visibleItems)}
          >
            <Trash2 aria-hidden="true" />
            {t("admin.cleanupPurgeFiltered", { count: formatNumber(visibleItems.length) })}
          </Button>
        </div>

        {loading && listing === null ? <StateMessage tone="loading">{t("admin.cleanupLoading")}</StateMessage> : null}
        {error !== "" ? <StateMessage tone="error">{error}</StateMessage> : null}
        {message !== "" ? <p className="admin-cleanup-message" role="status">{message}</p> : null}
        {listing !== null && listing.items.length === 0 ? <StateMessage tone="empty">{t("admin.cleanupEmpty")}</StateMessage> : null}
        {listing !== null && listing.items.length > 0 && visibleItems.length === 0 ? <StateMessage tone="empty">{t("admin.cleanupNoResults")}</StateMessage> : null}
        {visibleItems.length > 0 ? (
          <div className="admin-runtime-table-wrap">
            <table className="admin-runtime-table admin-cleanup-table">
              <thead>
                <tr>
                  {sortableHeader("name", t("admin.cleanupName"))}
                  {sortableHeader("size", t("admin.size"))}
                  {sortableHeader("subscribers", t("admin.cleanupSubscribers"))}
                  {sortableHeader("deletion", t("admin.cleanupDeletionAt"))}
                  <th>{t("admin.actions")}</th>
                </tr>
              </thead>
              <tbody>
                {visibleItems.map((item) => (
                  <tr key={item.id}>
                    <td data-label={t("admin.cleanupName")}>
                      <Tooltip content={item.name} overflowOnly className="admin-runtime-name">
                        <span>{item.name}</span>
                      </Tooltip>
                    </td>
                    <td data-label={t("admin.size")}>{formatBytes(item.size_bytes)}</td>
                    <td data-label={t("admin.cleanupSubscribers")}>{formatNumber(item.subscriber_count)}</td>
                    <td data-label={t("admin.cleanupDeletionAt")}>
                      {item.deletion_at === null ? t("admin.cleanupNotScheduled") : formatDate(new Date(item.deletion_at))}
                    </td>
                    <td data-label={t("admin.actions")}>
                      <Button
                        variant="danger"
                        className="admin-cleanup-row-purge"
                        disabled={purging}
                        aria-label={t("admin.cleanupPurgeNamed", { name: item.name })}
                        onClick={() => setPurgeSelection([item])}
                      >
                        <Trash2 aria-hidden="true" />
                        {t("admin.cleanupPurge")}
                      </Button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
        {listing !== null ? <p className="admin-runtime-updated">{t("admin.lastCheck", { date: formatDate(new Date(listing.checked_at)) })}</p> : null}
      </section>

      {purgeSelection !== null && (
        <Dialog
          eyebrow={t("admin.cleanup")}
          title={purgeSelection.length === 1 ? t("admin.cleanupPurgeOneTitle") : t("admin.cleanupPurgeManyTitle", { count: formatNumber(purgeSelection.length) })}
          description={t("admin.cleanupPurgeDescription")}
          closeDisabled={purging}
          onClose={() => setPurgeSelection(null)}
        >
          <p className="dialog-warning">{t("admin.cleanupPurgeWarning")}</p>
          <div className="dialog-actions">
            <Button variant="secondary" data-initial-focus disabled={purging} onClick={() => setPurgeSelection(null)}>
              {t("common.cancel")}
            </Button>
            <Button variant="danger" disabled={purging} onClick={() => void confirmPurge()}>
              {purging ? t("admin.cleanupPurging") : t("admin.cleanupConfirmPurge")}
            </Button>
          </div>
        </Dialog>
      )}
    </AdminPageShell>
  );
}
