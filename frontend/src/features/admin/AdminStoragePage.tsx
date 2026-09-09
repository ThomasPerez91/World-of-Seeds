import { useEffect, useState } from "react";

import {
  api,
  ApiError,
  type AdminReconciliationReport,
  type AdminStorageOverview,
} from "../../api/client";
import { Button, Card, Progress, StateMessage } from "../../components/ui";
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
  const usageLabel = t("admin.storageUsage", {
    value: formatNumber(usagePercent, { maximumFractionDigits: 0 }),
  });

  return (
    <AdminPageShell activeView="admin-storage" onBack={onBack} onNavigate={onNavigate}>
      <section className="admin-section" aria-labelledby="admin-storage-title" aria-busy={loading}>
        <div className="section-heading">
          <div>
            <p className="eyebrow">{t("admin.capacity")}</p>
            <h2 id="admin-storage-title">{t("admin.seedboxStorage")}</h2>
          </div>
          <Button
            variant="secondary"
            className="refresh-button"
            disabled={loading}
            onClick={() => setRevision((current) => current + 1)}
          >
            {t("common.refresh")}
          </Button>
        </div>

        {loading && overview === null ? (
          <StateMessage tone="loading">{t("admin.readingStorage")}</StateMessage>
        ) : error !== "" && overview === null ? (
          <StateMessage tone="error">{error}</StateMessage>
        ) : null}

        {overview !== null && (
          <div className="admin-storage-summary">
            <Card className="admin-storage-usage">
              <div className="admin-storage-usage-heading">
                <div>
                  <span>{t("admin.usedSpace")}</span>
                  <strong>{formatBytes(overview.used)}</strong>
                </div>
                <div className="storage-copy-right">
                  <span>{t("admin.available")}</span>
                  <strong>{formatBytes(overview.available)}</strong>
                </div>
              </div>
              <Progress value={usagePercent} label={usageLabel} />
              <p>
                {t("admin.storageSummary", {
                  value: formatNumber(usagePercent, {
                    minimumFractionDigits: 1,
                    maximumFractionDigits: 1,
                  }),
                  total: formatBytes(overview.total),
                })}
              </p>
            </Card>
            <Card className="admin-metric-card">
              <span>{t("admin.activeAccounts")}</span>
              <strong>{formatNumber(overview.active_users)}</strong>
            </Card>
            <Card className="admin-metric-card">
              <span>{t("admin.suspendedAccounts")}</span>
              <strong>{formatNumber(overview.suspended_users)}</strong>
            </Card>
          </div>
        )}

        {error !== "" && overview !== null && <StateMessage tone="error">{error}</StateMessage>}

        {reconciliation !== null && (
          <Card className="reconciliation-panel" aria-labelledby="reconciliation-title">
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
          </Card>
        )}
      </section>
    </AdminPageShell>
  );
}
