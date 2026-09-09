import { type ReactNode, useCallback, useEffect, useRef, useState } from "react";

import {
  api,
  ApiError,
  type AdminServicesHealth,
  type ExternalServiceHealth,
} from "../../api/client";
import {
  NewGreedyServiceIcon,
  QBittorrentServiceIcon,
  RefreshIcon,
} from "../../components/icons";
import { type MessageKey, useI18n } from "../../i18n";
import { AdminPageShell, type AdminView } from "./AdminPageShell";
import { NewGreedyControlPanel } from "./NewGreedyControlPanel";
import { TorrentMonitoringPanel } from "./TorrentMonitoringPanel";

const statusCopy: Record<ExternalServiceHealth["status"], MessageKey> = {
  healthy: "admin.serviceHealthy",
  unavailable: "admin.serviceUnavailable",
  unconfigured: "admin.serviceUnconfigured",
} as const;

function serviceMessage(service: ExternalServiceHealth): MessageKey {
  if (service.status === "healthy") return "admin.serviceHealthyDescription";
  if (service.status === "unconfigured") return "admin.serviceUnconfiguredDescription";
  if (service.error_code === "authentication_failed") {
    return "admin.serviceUnauthorizedDescription";
  }
  return "admin.serviceUnavailableDescription";
}

function ServiceCard({
  children,
  description,
  health,
  name,
}: {
  children: ReactNode;
  description: string;
  health: ExternalServiceHealth;
  name: string;
}) {
  const { formatNumber, t } = useI18n();
  return (
    <article
      className={`integration-card ${health.status}`}
      aria-label={`${name} : ${t(statusCopy[health.status])}`}
    >
      <div className="integration-card-heading">
        <span className="integration-card-icon" aria-hidden="true">
          {children}
        </span>
        <div>
          <h3>{name}</h3>
          <p>{description}</p>
        </div>
        <span className={`integration-status ${health.status}`}>
          <span aria-hidden="true" />
          {t(statusCopy[health.status])}
        </span>
      </div>
      <p className="integration-service-message">{t(serviceMessage(health))}</p>
      <dl className="integration-metadata">
        <div>
          <dt>{t("admin.responseTime")}</dt>
          <dd>{health.latency_ms === null ? "—" : `${formatNumber(health.latency_ms)} ms`}</dd>
        </div>
        <div>
          <dt>{t("admin.version")}</dt>
          <dd>{health.version ?? "—"}</dd>
        </div>
      </dl>
    </article>
  );
}

export function AdminServicesPage({
  onBack,
  onNavigate,
  onSessionExpired,
}: {
  onBack: () => void;
  onNavigate: (view: AdminView) => void;
  onSessionExpired: () => void;
}) {
  const { formatDate, t } = useI18n();
  const [health, setHealth] = useState<AdminServicesHealth | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const mounted = useRef(true);

  const load = useCallback(async () => {
    if (mounted.current) {
      setLoading(true);
      setError("");
    }
    try {
      const result = await api.getAdminServicesHealth();
      if (mounted.current) setHealth(result);
    } catch (caught) {
      if (!mounted.current) return;
      if (caught instanceof ApiError && caught.status === 401) {
        onSessionExpired();
        return;
      }
      setError(t("admin.loadFailed"));
    } finally {
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

  return (
    <AdminPageShell activeView="admin-services" onBack={onBack} onNavigate={onNavigate}>
      <section className="admin-section services-section" aria-labelledby="admin-services-title">
        <div className="section-heading">
          <div>
            <p className="eyebrow">{t("admin.supervision")}</p>
            <h2 id="admin-services-title">{t("admin.torrentServices")}</h2>
            <p className="section-intro">{t("admin.servicesIntro")}</p>
          </div>
          <button
            type="button"
            className="refresh-button services-refresh-button"
            disabled={loading}
            onClick={() => void load()}
          >
            <RefreshIcon className={loading ? "rotating" : undefined} />
            {loading ? t("admin.servicesChecking") : t("common.refresh")}
          </button>
        </div>

        <p className="form-message error-message" role="alert">
          {error}
        </p>
        {health === null ? (
          <div className="integration-grid integration-grid-loading" role="status">
            <span>{t("admin.servicesLoading")}</span>
          </div>
        ) : (
          <div className="integration-status-region">
            <div className="integration-grid">
              <ServiceCard
                name="NewGreedy"
                description={t("admin.newgreedyDescription")}
                health={health.newgreedy}
              >
                <NewGreedyServiceIcon />
              </ServiceCard>
              <ServiceCard
                name="qBittorrent"
                description={t("admin.qbittorrentDescription")}
                health={health.qbittorrent}
              >
                <QBittorrentServiceIcon />
              </ServiceCard>
            </div>
            <p className="services-last-check">
              {t("admin.lastCheck", { date: formatDate(health.checked_at, {
                  dateStyle: "short",
                  timeStyle: "medium",
                }) })}
            </p>
          </div>
        )}

        {health?.service_controls_available === true && (
          <>
            <TorrentMonitoringPanel onSessionExpired={onSessionExpired} />
            <NewGreedyControlPanel onSessionExpired={onSessionExpired} />
          </>
        )}
      </section>
    </AdminPageShell>
  );
}
