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
import { Badge, Button, Card, StateMessage } from "../../components/ui";
import { type MessageKey, useI18n } from "../../i18n";
import { AdminPageShell, type AdminView } from "./AdminPageShell";
import { NewGreedyControlPanel } from "./NewGreedyControlPanel";
import { TorrentMonitoringPanel } from "./TorrentMonitoringPanel";

const statusCopy: Record<ExternalServiceHealth["status"], MessageKey> = {
  healthy: "admin.serviceHealthy",
  unavailable: "admin.serviceUnavailable",
  unconfigured: "admin.serviceUnconfigured",
} as const;

export const EXPECTED_SERVICE_VERSIONS = {
  newgreedy: "1.7.5",
  qbittorrent: "5.2.3",
} as const;

function serviceMessage(service: ExternalServiceHealth): MessageKey {
  if (service.status === "healthy") return "admin.serviceHealthyDescription";
  if (service.status === "unconfigured") return "admin.serviceUnconfiguredDescription";
  if (service.error_code === "authentication_failed") {
    return "admin.serviceUnauthorizedDescription";
  }
  return "admin.serviceUnavailableDescription";
}

function statusTone(status: ExternalServiceHealth["status"]): "success" | "warning" | "danger" {
  if (status === "healthy") return "success";
  if (status === "unconfigured") return "warning";
  return "danger";
}

function ServiceCard({
  children,
  description,
  health,
  name,
  fallbackVersion,
}: {
  children: ReactNode;
  description: string;
  health: ExternalServiceHealth;
  name: string;
  fallbackVersion: string;
}) {
  const { formatNumber, t } = useI18n();
  return (
    <Card
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
        <Badge tone={statusTone(health.status)} className="integration-status">
          {t(statusCopy[health.status])}
        </Badge>
      </div>
      <p className="integration-service-message">{t(serviceMessage(health))}</p>
      <dl className="integration-metadata">
        <div>
          <dt>{t("admin.responseTime")}</dt>
          <dd>{health.latency_ms === null ? "—" : `${formatNumber(health.latency_ms)} ms`}</dd>
        </div>
        <div>
          <dt>{t("admin.version")}</dt>
          <dd>{health.version ?? fallbackVersion}</dd>
        </div>
      </dl>
    </Card>
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
      <section className="admin-section services-section" aria-labelledby="admin-services-title" aria-busy={loading}>
        <div className="section-heading">
          <div>
            <p className="eyebrow">{t("admin.supervision")}</p>
            <h2 id="admin-services-title">{t("admin.torrentServices")}</h2>
            <p className="section-intro">{t("admin.servicesIntro")}</p>
          </div>
          <Button
            variant="secondary"
            className="refresh-button services-refresh-button"
            disabled={loading}
            onClick={() => void load()}
          >
            <RefreshIcon className={loading ? "rotating" : undefined} />
            {loading ? t("admin.servicesChecking") : t("common.refresh")}
          </Button>
        </div>

        {error !== "" && health === null ? (
          <StateMessage tone="error">{error}</StateMessage>
        ) : health === null ? (
          <StateMessage tone="loading">{t("admin.servicesLoading")}</StateMessage>
        ) : (
          <div className="integration-status-region">
            {error !== "" && <StateMessage tone="error">{error}</StateMessage>}
            <div className="integration-grid">
              <ServiceCard
                name="NewGreedy"
                description={t("admin.newgreedyDescription")}
                health={health.newgreedy}
                fallbackVersion={EXPECTED_SERVICE_VERSIONS.newgreedy}
              >
                <NewGreedyServiceIcon />
              </ServiceCard>
              <ServiceCard
                name="qBittorrent"
                description={t("admin.qbittorrentDescription")}
                health={health.qbittorrent}
                fallbackVersion={EXPECTED_SERVICE_VERSIONS.qbittorrent}
              >
                <QBittorrentServiceIcon />
              </ServiceCard>
            </div>
            <p className="services-last-check">
              {t("admin.lastCheck", {
                date: formatDate(health.checked_at, {
                  dateStyle: "short",
                  timeStyle: "medium",
                }),
              })}
            </p>
          </div>
        )}

        {health?.service_controls_available === true && (
          <div className="admin-service-controls">
            <TorrentMonitoringPanel onSessionExpired={onSessionExpired} />
            <NewGreedyControlPanel onSessionExpired={onSessionExpired} />
          </div>
        )}
      </section>
    </AdminPageShell>
  );
}
