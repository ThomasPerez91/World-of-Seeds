import { useEffect, useState } from "react";

import { api, ApiError, type TrashEntry, type TrashListing } from "../../api/client";
import { FileDialog } from "./FileDialog";

const deletedAtFormatter = new Intl.DateTimeFormat("fr-FR", {
  dateStyle: "medium",
  timeStyle: "short",
});

function formatBytes(value: number | null): string {
  if (value === null) return "Taille du dossier non calculée";
  if (value === 0) return "0 o";
  const units = ["o", "Ko", "Mo", "Go", "To", "Po"];
  const exponent = Math.min(Math.floor(Math.log(value) / Math.log(1024)), units.length - 1);
  const amount = value / 1024 ** exponent;
  return `${new Intl.NumberFormat("fr-FR", {
    maximumFractionDigits: amount >= 10 || exponent === 0 ? 0 : 1,
  }).format(amount)} ${units[exponent]}`;
}

function trashListingError(error: unknown): string {
  if (error instanceof ApiError && error.status === 503) {
    return "La corbeille est temporairement indisponible.";
  }
  return "Impossible de charger la corbeille.";
}

function trashActionError(error: unknown, action: "purge" | "restore"): string {
  if (!(error instanceof ApiError)) return "L’opération n’a pas pu être effectuée.";
  if (error.status === 404) return "Cet élément n’est plus présent dans la corbeille.";
  if (error.status === 409) {
    return action === "restore"
      ? "L’emplacement d’origine n’existe plus ou contient déjà un élément portant ce nom."
      : "L’intégrité de cet élément n’a pas pu être confirmée.";
  }
  if (error.status === 500) {
    return action === "restore"
      ? "La restauration n’a pas pu être annulée en toute sécurité."
      : "La suppression définitive n’a pas pu être terminée.";
  }
  if (error.status === 503) {
    return action === "purge"
      ? "Le fichier a peut-être été supprimé, mais la base n’a pas pu être mise à jour. Réessayer est sans danger."
      : "Le stockage est temporairement indisponible.";
  }
  return "L’opération n’a pas pu être effectuée.";
}

function TrashIcon({ kind }: { kind: TrashEntry["kind"] }) {
  return (
    <span className={`trash-icon ${kind}`} aria-hidden="true">
      <svg viewBox="0 0 24 24">
        {kind === "directory" ? (
          <>
            <path d="M3.5 7.5h6l1.7 2h9.3v8.8a2.2 2.2 0 0 1-2.2 2.2H5.7a2.2 2.2 0 0 1-2.2-2.2V7.5Z" />
            <path d="M3.5 8V5.7a2.2 2.2 0 0 1 2.2-2.2h3.1l2 2.2h7.5a2.2 2.2 0 0 1 2.2 2.2v1.6" />
          </>
        ) : (
          <>
            <path d="M6 2.8h7l5 5V21H6V2.8Z" />
            <path d="M13 2.8V8h5" />
          </>
        )}
      </svg>
    </span>
  );
}

function TrashActionDialog({
  action,
  entry,
  onClose,
  onCompleted,
  onSessionExpired,
}: {
  action: "purge" | "restore";
  entry: TrashEntry;
  onClose: () => void;
  onCompleted: (message: string) => void;
  onSessionExpired: () => void;
}) {
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const restoring = action === "restore";

  async function submit() {
    setSubmitting(true);
    setError("");
    try {
      if (restoring) {
        await api.restoreTrash(entry.id);
        onCompleted(`« ${entry.name} » a été restauré.`);
      } else {
        await api.purgeTrash(entry.id);
        onCompleted(`« ${entry.name} » a été supprimé définitivement.`);
      }
    } catch (caught) {
      if (caught instanceof ApiError && caught.status === 401) {
        onSessionExpired();
        return;
      }
      setError(trashActionError(caught, action));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <FileDialog
      eyebrow="Corbeille"
      title={restoring ? "Restaurer l’élément" : "Supprimer définitivement"}
      description={
        restoring
          ? `« ${entry.name} » sera replacé dans « ${entry.original_path} ».`
          : `« ${entry.name} » et tout son contenu seront irrécupérables.`
      }
      onClose={submitting ? () => undefined : onClose}
    >
      <div className="confirmation-content">
        {!restoring && (
          <p className="permanent-delete-warning">
            Cette action est définitive. Elle ne peut pas être annulée.
          </p>
        )}
        <p className="form-message error-message" role="alert">
          {error}
        </p>
        <div className="dialog-actions">
          <button
            type="button"
            className="secondary-button"
            onClick={onClose}
            disabled={submitting}
            data-initial-focus={!restoring ? true : undefined}
          >
            Annuler
          </button>
          <button
            type="button"
            className={restoring ? undefined : "danger-button"}
            onClick={() => void submit()}
            disabled={submitting}
            data-initial-focus={restoring ? true : undefined}
          >
            {submitting
              ? restoring
                ? "Restauration…"
                : "Suppression…"
              : restoring
                ? "Restaurer"
                : "Supprimer définitivement"}
          </button>
        </div>
      </div>
    </FileDialog>
  );
}

export function TrashBrowser({
  onFilesChanged,
  onSessionExpired,
  revision,
}: {
  onFilesChanged: () => void;
  onSessionExpired: () => void;
  revision: number;
}) {
  const [listing, setListing] = useState<TrashListing | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [reloadKey, setReloadKey] = useState(0);
  const [selectedAction, setSelectedAction] = useState<{
    action: "purge" | "restore";
    entry: TrashEntry;
  } | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError("");
    void api
      .listTrash(controller.signal)
      .then(setListing)
      .catch((caught: unknown) => {
        if (caught instanceof DOMException && caught.name === "AbortError") return;
        if (caught instanceof ApiError && caught.status === 401) {
          onSessionExpired();
          return;
        }
        setListing(null);
        setError(trashListingError(caught));
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [onSessionExpired, reloadKey, revision]);

  function completeAction(message: string) {
    setSelectedAction(null);
    setNotice(message);
    setReloadKey((value) => value + 1);
    onFilesChanged();
  }

  return (
    <section className="trash-browser" aria-labelledby="trash-title">
      <header className="trash-header">
        <div>
          <p className="eyebrow">Récupération</p>
          <h2 id="trash-title">Corbeille</h2>
          <p>Les éléments restent isolés par compte jusqu’à leur suppression définitive.</p>
        </div>
        <button
          type="button"
          className="refresh-button"
          onClick={() => setReloadKey((value) => value + 1)}
          disabled={loading}
        >
          Actualiser
        </button>
      </header>

      {notice !== "" && (
        <p className="operation-notice" role="status">
          {notice}
        </p>
      )}

      {loading && (
        <div className="trash-loading" aria-label="Chargement de la corbeille">
          <span />
          <span />
        </div>
      )}

      {!loading && error !== "" && (
        <div className="browser-state" role="alert">
          <strong>Corbeille inaccessible</strong>
          <p>{error}</p>
          <button
            type="button"
            className="compact-button"
            onClick={() => setReloadKey((value) => value + 1)}
          >
            Réessayer
          </button>
        </div>
      )}

      {!loading && listing?.entries.length === 0 && (
        <div className="browser-state empty-state">
          <strong>La corbeille est vide</strong>
          <p>Les éléments supprimés depuis le gestionnaire de fichiers apparaîtront ici.</p>
        </div>
      )}

      {!loading && listing !== null && listing.entries.length > 0 && (
        <div className="trash-list">
          {listing.entries.map((entry) => (
            <article className="trash-row" key={entry.id}>
              <TrashIcon kind={entry.kind} />
              <div className="trash-copy">
                <strong>{entry.name}</strong>
                <code>{entry.original_path}</code>
                <span>
                  {entry.kind === "directory" ? "Dossier" : formatBytes(entry.size)} · supprimé le{" "}
                  {deletedAtFormatter.format(new Date(entry.deleted_at))}
                </span>
              </div>
              <div className="trash-actions">
                <button
                  type="button"
                  className="secondary-button"
                  onClick={() => setSelectedAction({ action: "restore", entry })}
                >
                  Restaurer
                </button>
                <button
                  type="button"
                  className="trash-purge-button"
                  onClick={() => setSelectedAction({ action: "purge", entry })}
                  aria-label={`Supprimer définitivement ${entry.name}`}
                >
                  Supprimer définitivement
                </button>
              </div>
            </article>
          ))}
        </div>
      )}

      {!loading && listing?.truncated === true && (
        <p className="truncated-notice" role="status">
          La corbeille contient plus de 1 000 éléments. Seuls les plus récents sont affichés.
        </p>
      )}

      {selectedAction !== null && (
        <TrashActionDialog
          action={selectedAction.action}
          entry={selectedAction.entry}
          onClose={() => setSelectedAction(null)}
          onCompleted={completeAction}
          onSessionExpired={onSessionExpired}
        />
      )}
    </section>
  );
}
