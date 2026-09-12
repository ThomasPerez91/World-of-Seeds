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
  const [reconciliationError, setReconciliationError] = useState("");
  const [loading, setLoading] = useState(true);
  const [revision, setRevision] = useState(0);

  useEffect(() => {
    let active = true;
    setLoading(true);
    setError("");
    setReconciliationError("");
    void Promise.allSettled([api.getAdminStorage(), api.getAdminReconciliation()])
      .then(([storageResult, reportResult]) => {
        if (!active) return;
        const authenticationFailed = [storageResult, reportResult].some(
          (result) => result.status === "rejected"
            && result.reason instanceof ApiError
            && result.reason.status === 401,
        );
        if (authenticationFailed) {
          onSessionExpired();
          return;
        }
        if (storageResult.status === "fulfilled") setOverview(storageResult.value);
        else setError(t("admin.loadFailed"));
        if (reportResult.status === "fulfilled") setReconciliation(reportResult.value);
        else setReconciliationError(t("admin.integrityLoadFailed"));
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
              <dl className="admin-storage-capacity">
                <div><dt>{t("admin.totalSpace")}</dt><dd>{formatBytes(overview.total)}</dd></div>
                <div><dt>{t("admin.usedSpace")}</dt><dd>{formatBytes(overview.used)}</dd></div>
                <div><dt>{t("admin.available")}</dt><dd>{formatBytes(overview.available)}</dd></div>
                <div><dt>{t("admin.utilization")}</dt><dd>{formatNumber(usagePercent, { minimumFractionDigits: 1, maximumFractionDigits: 1 })} %</dd></div>
              </dl>
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
              <p>{t("admin.integrityIntro")}</p>
            </div>
            <dl className="reconciliation-metrics">
              <div><dt>{t("admin.integrityDatabase")}</dt><dd>{formatNumber(reconciliation.database_scanned)}</dd></div>
              <div><dt>{t("admin.integrityClient")}</dt><dd>{formatNumber(reconciliation.qbittorrent_scanned)}</dd></div>
              <div><dt>{t("admin.integrityFiles")}</dt><dd>{formatNumber(reconciliation.storage_scanned)}</dd></div>
              <div><dt>{t("admin.integrityExternal")}</dt><dd>{formatNumber(reconciliation.external_torrents)}</dd></div>
            </dl>
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
        {reconciliationError !== "" && <StateMessage tone="error">{reconciliationError}</StateMessage>}
      </section>
    </AdminPageShell>
  );
}
