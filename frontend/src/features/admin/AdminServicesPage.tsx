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
import { AdminPageShell, type AdminView } from "./AdminPageShell";

const statusCopy = {
  healthy: "Opérationnel",
  unavailable: "Indisponible",
  unconfigured: "Non configuré",
} as const;

function serviceMessage(service: ExternalServiceHealth): string {
  if (service.status === "healthy") return "Le service répond normalement.";
  if (service.status === "unconfigured") return "La connexion n’est pas encore configurée.";
  if (service.error_code === "authentication_failed") {
    return "Les identifiants configurés ont été refusés.";
  }
  return "Le service ne répond pas pour le moment.";
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
  return (
    <article
      className={`integration-card ${health.status}`}
      aria-label={`${name} : ${statusCopy[health.status]}`}
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
          {statusCopy[health.status]}
        </span>
      </div>
      <p className="integration-service-message">{serviceMessage(health)}</p>
      <dl className="integration-metadata">
        <div>
          <dt>Temps de réponse</dt>
          <dd>{health.latency_ms === null ? "—" : `${health.latency_ms} ms`}</dd>
        </div>
        <div>
          <dt>Version</dt>
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
      setError("Impossible de vérifier les services pour le moment.");
    } finally {
      if (mounted.current) setLoading(false);
    }
  }, [onSessionExpired]);

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
            <p className="eyebrow">Supervision</p>
            <h2 id="admin-services-title">Services torrent</h2>
            <p className="section-intro">État des moteurs connectés à World of Seeds.</p>
          </div>
          <button
            type="button"
            className="refresh-button services-refresh-button"
            disabled={loading}
            onClick={() => void load()}
          >
            <RefreshIcon className={loading ? "rotating" : undefined} />
            {loading ? "Vérification…" : "Actualiser"}
          </button>
        </div>

        <p className="form-message error-message" role="alert">
          {error}
        </p>
        {health === null ? (
          <div className="integration-grid integration-grid-loading" role="status">
            <span>Vérification de NewGreedy et qBittorrent…</span>
          </div>
        ) : (
          <div className="integration-status-region">
            <div className="integration-grid">
              <ServiceCard
                name="NewGreedy"
                description="Proxy et suivi des annonces"
                health={health.newgreedy}
              >
                <NewGreedyServiceIcon />
              </ServiceCard>
              <ServiceCard
                name="qBittorrent"
                description="Moteur de téléchargement"
                health={health.qbittorrent}
              >
                <QBittorrentServiceIcon />
              </ServiceCard>
            </div>
            <p className="services-last-check">
              Dernière vérification :{" "}
              <time dateTime={health.checked_at}>
                {new Intl.DateTimeFormat("fr-FR", {
                  dateStyle: "short",
                  timeStyle: "medium",
                }).format(new Date(health.checked_at))}
              </time>
            </p>
          </div>
        )}
      </section>
    </AdminPageShell>
  );
}
