import { useEffect, useState } from "react";

import {
  api,
  ApiError,
  type AdminReconciliationReport,
  type AdminStorageOverview,
} from "../../api/client";
import { useI18n } from "../../i18n";
import { AdminPageShell, type AdminView } from "./AdminPageShell";

export function AdminStoragePage({
  onBack,
  onNavigate,
  onSessionExpired,
}: {
  onBack: () => void;
  onNavigate: (view: AdminView) => void;
  onSessionExpired: () => void;
}) {
  const { formatBytes, formatNumber, t } = useI18n();
  const [overview, setOverview] = useState<AdminStorageOverview | null>(null);
  const [reconciliation, setReconciliation] = useState<AdminReconciliationReport | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [revision, setRevision] = useState(0);

  useEffect(() => {
    let active = true;
    setLoading(true);
    setError("");
    void Promise.all([api.getAdminStorage(), api.getAdminReconciliation()])
      .then(([storage, report]) => {
        if (active) {
          setOverview(storage);
          setReconciliation(report);
        }
      })
      .catch((caught: unknown) => {
        if (!active) return;
        if (caught instanceof ApiError && caught.status === 401) {
          onSessionExpired();
          return;
        }
        setError(t("admin.loadFailed"));
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [onSessionExpired, revision, t]);

  const usagePercent =
    overview === null || overview.total === 0
      ? 0
      : Math.min((overview.used / overview.total) * 100, 100);

  return (
    <AdminPageShell
      activeView="admin-storage"
      onBack={onBack}
      onNavigate={onNavigate}
    >
      <section className="admin-section" aria-labelledby="admin-storage-title" aria-busy={loading}>
        <div className="section-heading">
          <div>
            <p className="eyebrow">{t("admin.capacity")}</p>
            <h2 id="admin-storage-title">{t("admin.seedboxStorage")}</h2>
          </div>
          <button
            type="button"
            className="refresh-button"
            disabled={loading}
            onClick={() => setRevision((current) => current + 1)}
          >
            {t("common.refresh")}
          </button>
        </div>

        {loading && overview === null && (
          <p className="admin-loading" role="status">
            {t("admin.readingStorage")}
          </p>
        )}
        <p className="form-message error-message" role="alert">
          {error}
        </p>
        {overview !== null && (
          <>
            <div className="admin-storage-summary">
              <div className="admin-storage-usage">
                <div>
                  <span>{t("admin.usedSpace")}</span>
                  <strong>{formatBytes(overview.used)}</strong>
                </div>
                <div className="storage-copy-right">
                  <span>{t("admin.available")}</span>
                  <strong>{formatBytes(overview.available)}</strong>
                </div>
                <progress
                  max={100}
                  value={usagePercent}
                  aria-label={t("admin.storageUsage", { value: formatNumber(usagePercent, { maximumFractionDigits: 0 }) })}
                >
                  {formatNumber(usagePercent, { maximumFractionDigits: 0 })} %
                </progress>
                <p>
                  {t("admin.storageSummary", {
                    value: formatNumber(usagePercent, { minimumFractionDigits: 1, maximumFractionDigits: 1 }),
                    total: formatBytes(overview.total),
                  })}
                </p>
              </div>
              <div className="admin-metric-card">
                <span>{t("admin.activeAccounts")}</span>
                <strong>{formatNumber(overview.active_users)}</strong>
              </div>
              <div className="admin-metric-card">
                <span>{t("admin.suspendedAccounts")}</span>
                <strong>{formatNumber(overview.suspended_users)}</strong>
              </div>
              <div className="admin-metric-card">
                <span>{t("admin.trashItems")}</span>
                <strong>{formatNumber(overview.trash_entries)}</strong>
              </div>
              <div className="admin-metric-card">
                <span>{t("admin.knownTrashSize")}</span>
                <strong>{formatBytes(overview.known_trash_bytes)}</strong>
              </div>
            </div>
            <p className="admin-data-note">
              {t("admin.knownTrashNote")}
            </p>
          </>
        )}
        {reconciliation !== null && (
          <section className="reconciliation-panel" aria-labelledby="reconciliation-title">
            <div>
              <h3 id="reconciliation-title">{t("admin.reconciliation")}</h3>
              <p>
                {t("admin.reconciliationScanned", {
                  database: formatNumber(reconciliation.database_scanned),
                  qbittorrent: formatNumber(reconciliation.qbittorrent_scanned),
                  storage: formatNumber(reconciliation.storage_scanned),
                })}
              </p>
            </div>
            <p>
              {t(
                reconciliation.external_torrents === 1
                  ? "admin.externalTorrentOne"
                  : "admin.externalTorrentMany",
                { count: formatNumber(reconciliation.external_torrents) },
              )}
            </p>
            {reconciliation.anomalies.length === 0 ? (
              <strong className="reconciliation-ok">{t("admin.noAnomaly")}</strong>
            ) : (
              <ul>
                {reconciliation.anomalies.map((anomaly, index) => (
                  <li className={anomaly.severity} key={`${anomaly.code}-${anomaly.resource_id}-${index}`}>
                    <strong>{anomaly.code}</strong>
                    <span>{anomaly.action === "none" ? t("admin.noAction") : anomaly.action}</span>
                  </li>
                ))}
              </ul>
            )}
            {reconciliation.truncated && (
              <p className="truncated-notice">{t("admin.inventoryTruncated")}</p>
            )}
          </section>
        )}
      </section>
    </AdminPageShell>
  );
}
