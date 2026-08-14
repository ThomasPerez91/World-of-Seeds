import { useEffect, useState } from "react";

import { api, ApiError, type AdminTrashEntry, type AdminTrashListing } from "../../api/client";
import { formatBytes } from "../../utils/format";
import { Notice } from "../../components/Notice";
import { FileDialog } from "../files/FileDialog";
import { AdminPageShell, type AdminView } from "./AdminPageShell";

const dateFormatter = new Intl.DateTimeFormat("fr-FR", {
  dateStyle: "medium",
  timeStyle: "short",
});

type PurgeTarget = { kind: "all" } | { kind: "entry"; entry: AdminTrashEntry };

function PurgeDialog({
  onClose,
  onCompleted,
  onSessionExpired,
  target,
}: {
  onClose: () => void;
  onCompleted: (message: string) => void;
  onSessionExpired: () => void;
  target: PurgeTarget;
}) {
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const all = target.kind === "all";

  async function purge() {
    setSubmitting(true);
    setError("");
    try {
      if (target.kind === "entry") {
        await api.purgeAdminTrash(target.entry.id);
        onCompleted(`« ${target.entry.name} » a été supprimé définitivement.`);
      } else {
        const result = await api.purgeAllAdminTrash();
        const suffix =
          result.remaining === 0
            ? "Toutes les corbeilles sont vides."
            : `${result.remaining} éléments restent à traiter. Relance le nettoyage.`;
        onCompleted(`${result.purged} éléments supprimés. ${suffix}`);
      }
    } catch (caught) {
      if (caught instanceof ApiError && caught.status === 401) {
        onSessionExpired();
        return;
      }
      setError(
        caught instanceof ApiError && caught.status === 409
          ? "L’intégrité d’un élément n’a pas pu être confirmée."
          : "Le nettoyage n’a pas pu être terminé. Actualise la page avant de réessayer.",
      );
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <FileDialog
      eyebrow="Administration"
      title={all ? "Vider toutes les corbeilles" : "Supprimer définitivement"}
      description={
        all
          ? "Tous les éléments de toutes les corbeilles seront supprimés par lots sécurisés."
          : `« ${target.entry.name} » sera supprimé de la corbeille de ${target.entry.username}.`
      }
      onClose={onClose}
      closeDisabled={submitting}
    >
      <div className="confirmation-content">
        <p className="permanent-delete-warning">
          Cette action détruit les fichiers et ne peut pas être annulée.
        </p>
        <p className="form-message error-message" role="alert">
          {error}
        </p>
        <div className="dialog-actions">
          <button
            type="button"
            className="secondary-button"
            onClick={onClose}
            disabled={submitting}
            data-initial-focus
          >
            Annuler
          </button>
          <button
            type="button"
            className="danger-button"
            onClick={() => void purge()}
            disabled={submitting}
          >
            {submitting ? "Suppression…" : all ? "Tout supprimer" : "Supprimer"}
          </button>
        </div>
      </div>
    </FileDialog>
  );
}

export function AdminTrashPage({
  onBack,
  onNavigate,
  onSessionExpired,
}: {
  onBack: () => void;
  onNavigate: (view: AdminView) => void;
  onSessionExpired: () => void;
}) {
  const [listing, setListing] = useState<AdminTrashListing | null>(null);
  const [target, setTarget] = useState<PurgeTarget | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [loading, setLoading] = useState(true);
  const [revision, setRevision] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError("");
    void api
      .listAdminTrash(controller.signal)
      .then(setListing)
      .catch((caught: unknown) => {
        if (caught instanceof DOMException && caught.name === "AbortError") return;
        if (caught instanceof ApiError && caught.status === 401) {
          onSessionExpired();
          return;
        }
        setError("Impossible de charger les corbeilles.");
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [onSessionExpired, revision]);

  function completed(message: string) {
    setTarget(null);
    setNotice(message);
    setRevision((current) => current + 1);
  }

  return (
    <AdminPageShell activeView="admin-trash" onBack={onBack} onNavigate={onNavigate}>
      <section className="admin-section" aria-labelledby="admin-trash-title" aria-busy={loading}>
        <div className="section-heading">
          <div>
            <p className="eyebrow">Nettoyage global</p>
            <h2 id="admin-trash-title">Corbeilles utilisateurs</h2>
            <p className="section-intro">Contrôle et purge les éléments de tous les comptes.</p>
          </div>
          <div className="admin-trash-actions">
            <button
              type="button"
              className="refresh-button"
              disabled={loading}
              onClick={() => setRevision((current) => current + 1)}
            >
              Actualiser
            </button>
            <button
              type="button"
              className="danger-outline-button"
              disabled={loading || (listing?.entries.length ?? 0) === 0}
              onClick={() => setTarget({ kind: "all" })}
            >
              Vider toutes les corbeilles
            </button>
          </div>
        </div>

        <Notice message={notice} onDismiss={() => setNotice("")} />
        <p className="form-message error-message" role="alert">
          {error}
        </p>
        {listing?.truncated && (
          <p className="truncation-notice" role="status">
            La liste est limitée aux 5 000 éléments les plus récents.
          </p>
        )}
        {!loading && listing?.entries.length === 0 && (
          <div className="admin-empty-state">
            <strong>Toutes les corbeilles sont vides</strong>
            <span>Aucun élément ne nécessite de nettoyage.</span>
          </div>
        )}
        {listing !== null && listing.entries.length > 0 && (
          <ul className="admin-trash-list">
            {listing.entries.map((entry) => (
              <li key={entry.id}>
                <span className="account-avatar" aria-hidden="true">
                  {entry.username.slice(0, 1).toUpperCase()}
                </span>
                <div className="admin-trash-copy">
                  <div>
                    <strong>{entry.name}</strong>
                    <span className="admin-user-badge">{entry.username}</span>
                  </div>
                  <span className="admin-trash-path">{entry.original_path}</span>
                  <span>
                    {formatBytes(entry.size, "Taille du dossier non calculée")} · supprimé le{" "}
                    <time dateTime={entry.deleted_at}>
                      {dateFormatter.format(new Date(entry.deleted_at))}
                    </time>
                  </span>
                </div>
                <button
                  type="button"
                  className="danger-outline-button compact-button"
                  aria-label={`Supprimer définitivement ${entry.name} de la corbeille de ${entry.username}`}
                  onClick={() => setTarget({ kind: "entry", entry })}
                >
                  Supprimer
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>
      {target !== null && (
        <PurgeDialog
          target={target}
          onClose={() => setTarget(null)}
          onCompleted={completed}
          onSessionExpired={onSessionExpired}
        />
      )}
    </AdminPageShell>
  );
}
