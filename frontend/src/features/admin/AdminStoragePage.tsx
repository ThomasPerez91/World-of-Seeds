import { useEffect, useState } from "react";

import {
  api,
  ApiError,
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
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [revision, setRevision] = useState(0);

  useEffect(() => {
    let active = true;
    setLoading(true);
    setError("");
    void api.getAdminStorage()
      .then((result) => {
        if (!active) return;
        setOverview(result);
      })
      .catch((caught) => {
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
      </section>
    </AdminPageShell>
  );
}
