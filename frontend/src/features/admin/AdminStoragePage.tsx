import { useEffect, useState } from "react";

import { api, ApiError, type AdminStorageOverview } from "../../api/client";
import { formatBytes } from "../../utils/format";
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
  const [overview, setOverview] = useState<AdminStorageOverview | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [revision, setRevision] = useState(0);

  useEffect(() => {
    let active = true;
    setLoading(true);
    setError("");
    void api
      .getAdminStorage()
      .then((result) => {
        if (active) setOverview(result);
      })
      .catch((caught: unknown) => {
        if (!active) return;
        if (caught instanceof ApiError && caught.status === 401) {
          onSessionExpired();
          return;
        }
        setError("Impossible de charger l’état du stockage.");
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [onSessionExpired, revision]);

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
            <p className="eyebrow">Capacité</p>
            <h2 id="admin-storage-title">Stockage de la seedbox</h2>
          </div>
          <button
            type="button"
            className="refresh-button"
            disabled={loading}
            onClick={() => setRevision((current) => current + 1)}
          >
            Actualiser
          </button>
        </div>

        {loading && overview === null && (
          <p className="admin-loading" role="status">
            Lecture du stockage…
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
                  <span>Espace utilisé</span>
                  <strong>{formatBytes(overview.used)}</strong>
                </div>
                <div className="storage-copy-right">
                  <span>Disponible</span>
                  <strong>{formatBytes(overview.available)}</strong>
                </div>
                <progress
                  max={100}
                  value={usagePercent}
                  aria-label={`${usagePercent.toFixed(0)} % du stockage utilisé`}
                >
                  {usagePercent.toFixed(0)} %
                </progress>
                <p>
                  {usagePercent.toFixed(1)} % utilisés sur {formatBytes(overview.total)}
                </p>
              </div>
              <div className="admin-metric-card">
                <span>Comptes actifs</span>
                <strong>{overview.active_users}</strong>
              </div>
              <div className="admin-metric-card">
                <span>Comptes suspendus</span>
                <strong>{overview.suspended_users}</strong>
              </div>
              <div className="admin-metric-card">
                <span>Éléments en corbeille</span>
                <strong>{overview.trash_entries}</strong>
              </div>
              <div className="admin-metric-card">
                <span>Taille connue en corbeille</span>
                <strong>{formatBytes(overview.known_trash_bytes)}</strong>
              </div>
            </div>
            <p className="admin-data-note">
              La taille connue additionne uniquement les fichiers. Les dossiers ne sont pas parcourus
              récursivement afin de préserver les performances du serveur.
            </p>
          </>
        )}
      </section>
    </AdminPageShell>
  );
}
