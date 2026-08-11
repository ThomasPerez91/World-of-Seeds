import { type FormEvent, useEffect, useState } from "react";

import { api, ApiError, type DirectoryListing, type FileEntry } from "../../api/client";
import { FileDialog } from "./FileDialog";

export type FileMutationAction = "move" | "rename" | "trash";

interface FileMutationDialogProps {
  action: FileMutationAction;
  currentDirectory: string;
  entry: FileEntry;
  onClose: () => void;
  onCompleted: (message: string) => void;
  onSessionExpired: () => void;
}

function mutationErrorMessage(error: unknown): string {
  if (!(error instanceof ApiError)) return "L’opération n’a pas pu être effectuée.";
  return (
    {
      400: "Cette destination ou ce nom n’est pas valide.",
      403: "Cet élément est protégé ou bloqué.",
      404: "L’élément ou le dossier de destination n’existe plus.",
      409: "Un élément portant ce nom existe déjà à destination.",
      500: "L’opération n’a pas pu être vérifiée. Actualise le dossier avant de réessayer.",
      503: "Le stockage est temporairement indisponible.",
    }[error.status] ?? "L’opération n’a pas pu être effectuée."
  );
}

function listingErrorMessage(error: unknown): string {
  if (!(error instanceof ApiError)) return "Impossible d’ouvrir ce dossier.";
  return (
    {
      400: "Ce chemin de destination est invalide.",
      403: "Ce dossier est bloqué.",
      404: "Ce dossier n’existe plus.",
    }[error.status] ?? "Impossible d’ouvrir ce dossier."
  );
}

function RenameDialog({
  entry,
  onClose,
  onCompleted,
  onSessionExpired,
}: Omit<FileMutationDialogProps, "action" | "currentDirectory">) {
  const [name, setName] = useState(entry.name);
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSubmitting(true);
    setError("");
    try {
      const result = await api.renameFile(entry.path, name);
      onCompleted(`« ${entry.name} » a été renommé en « ${result.name} ».`);
    } catch (caught) {
      if (caught instanceof ApiError && caught.status === 401) {
        onSessionExpired();
        return;
      }
      setError(mutationErrorMessage(caught));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <FileDialog
      title="Renommer l’élément"
      description="Le changement est immédiat et n’écrase jamais un élément existant."
      onClose={onClose}
    >
      <form className="mutation-form" onSubmit={(event) => void submit(event)}>
        <label htmlFor="mutation-name">Nouveau nom</label>
        <input
          id="mutation-name"
          value={name}
          onChange={(event) => setName(event.target.value)}
          maxLength={255}
          data-initial-focus
          required
        />
        <p className="mutation-warning">
          Si qBittorrent utilise encore cet élément, son téléchargement peut passer en erreur.
        </p>
        <p className="form-message error-message" role="alert">
          {error}
        </p>
        <div className="dialog-actions">
          <button type="button" className="secondary-button" onClick={onClose}>
            Annuler
          </button>
          <button type="submit" disabled={submitting || name === entry.name || name.length === 0}>
            {submitting ? "Renommage…" : "Confirmer le renommage"}
          </button>
        </div>
      </form>
    </FileDialog>
  );
}

function MoveDialog({
  currentDirectory,
  entry,
  onClose,
  onCompleted,
  onSessionExpired,
}: Omit<FileMutationDialogProps, "action">) {
  const [destination, setDestination] = useState(currentDirectory);
  const [listing, setListing] = useState<DirectoryListing | null>(null);
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [listingError, setListingError] = useState("");
  const [mutationError, setMutationError] = useState("");

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setListingError("");
    setMutationError("");
    setListing(null);
    void api
      .listFiles(destination, controller.signal)
      .then(setListing)
      .catch((caught: unknown) => {
        if (caught instanceof DOMException && caught.name === "AbortError") return;
        if (caught instanceof ApiError && caught.status === 401) {
          onSessionExpired();
          return;
        }
        setListingError(listingErrorMessage(caught));
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [destination, onSessionExpired]);

  const destinationIsSource =
    entry.kind === "directory" &&
    (destination === entry.path || destination.startsWith(`${entry.path}/`));
  const destinationIsCurrent = destination === currentDirectory;

  async function move() {
    setSubmitting(true);
    setMutationError("");
    try {
      const result = await api.moveFile(entry.path, destination);
      onCompleted(`« ${entry.name} » a été déplacé vers « ${result.path} ».`);
    } catch (caught) {
      if (caught instanceof ApiError && caught.status === 401) {
        onSessionExpired();
        return;
      }
      setMutationError(mutationErrorMessage(caught));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <FileDialog
      title="Déplacer l’élément"
      description={`Choisis le nouveau dossier de « ${entry.name} ».`}
      onClose={onClose}
    >
      <div className="destination-picker">
        <nav className="picker-breadcrumbs" aria-label="Dossier de destination">
          {(listing?.breadcrumbs ?? [{ label: "Mes fichiers", path: "" }]).map(
            (breadcrumb, index, all) => (
              <span key={breadcrumb.path || "root"}>
                <button
                  type="button"
                  onClick={() => setDestination(breadcrumb.path)}
                  aria-current={index === all.length - 1 ? "page" : undefined}
                >
                  {breadcrumb.label}
                </button>
                {index < all.length - 1 && <span aria-hidden="true">/</span>}
              </span>
            ),
          )}
        </nav>

        <div className="destination-list" aria-busy={loading}>
          {loading && <p className="picker-state">Chargement des dossiers…</p>}
          {!loading && listingError !== "" && (
            <p className="picker-state error-message">{listingError}</p>
          )}
          {!loading && listing !== null && (
            <>
              {listing.entries.filter(
                (candidate) => candidate.kind === "directory" && !candidate.blocked,
              ).length === 0 && <p className="picker-state">Aucun sous-dossier ici.</p>}
              {listing.entries
                .filter((candidate) => candidate.kind === "directory" && !candidate.blocked)
                .map((candidate) => {
                  const forbidden =
                    entry.kind === "directory" &&
                    (candidate.path === entry.path || candidate.path.startsWith(`${entry.path}/`));
                  return (
                    <button
                      type="button"
                      className="destination-row"
                      key={candidate.path}
                      onClick={() => setDestination(candidate.path)}
                      disabled={forbidden}
                    >
                      <span aria-hidden="true">▰</span>
                      <strong>{candidate.name}</strong>
                      <span>{forbidden ? "Dossier source" : "Ouvrir"}</span>
                    </button>
                  );
                })}
            </>
          )}
        </div>

        <div className="selected-destination">
          <span>Destination sélectionnée</span>
          <strong>{destination === "" ? "Mes fichiers" : destination}</strong>
        </div>
        <p className="mutation-warning">
          Si qBittorrent utilise encore cet élément, son téléchargement peut passer en erreur.
        </p>
        {destinationIsCurrent && (
          <p className="picker-hint">Cet élément se trouve déjà dans ce dossier.</p>
        )}
        {destinationIsSource && (
          <p className="picker-hint error-message">Un dossier ne peut pas être déplacé en lui-même.</p>
        )}
        <p className="form-message error-message" role="alert">
          {mutationError}
        </p>
        <div className="dialog-actions">
          <button type="button" className="secondary-button" onClick={onClose}>
            Annuler
          </button>
          <button
            type="button"
            onClick={() => void move()}
            disabled={
              loading ||
              submitting ||
              listing === null ||
              destinationIsCurrent ||
              destinationIsSource
            }
          >
            {submitting ? "Déplacement…" : "Déplacer ici"}
          </button>
        </div>
      </div>
    </FileDialog>
  );
}

function TrashDialog({
  entry,
  onClose,
  onCompleted,
  onSessionExpired,
}: Omit<FileMutationDialogProps, "action" | "currentDirectory">) {
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);

  async function moveToTrash() {
    setSubmitting(true);
    setError("");
    try {
      await api.trashFile(entry.path);
      onCompleted(`« ${entry.name} » a été placé dans la corbeille.`);
    } catch (caught) {
      if (caught instanceof ApiError && caught.status === 401) {
        onSessionExpired();
        return;
      }
      setError(
        caught instanceof ApiError
          ? {
              400: "Ce chemin n’est pas valide.",
              403: "Cet élément est protégé ou bloqué.",
              404: "L’élément n’existe plus.",
              409: "L’intégrité de cet élément n’a pas pu être confirmée.",
              500: "L’opération n’a pas pu être annulée en toute sécurité.",
              503: "La corbeille est temporairement indisponible.",
            }[caught.status] ?? "L’élément n’a pas pu être placé dans la corbeille."
          : "L’élément n’a pas pu être placé dans la corbeille.",
      );
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <FileDialog
      title="Placer dans la corbeille"
      description={`« ${entry.name} » pourra être restauré à son emplacement actuel.`}
      onClose={submitting ? () => undefined : onClose}
    >
      <div className="confirmation-content">
        <p className="mutation-warning">
          Un torrent encore actif dans qBittorrent peut passer en erreur après cette opération.
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
            onClick={() => void moveToTrash()}
            disabled={submitting}
          >
            {submitting ? "Déplacement…" : "Placer dans la corbeille"}
          </button>
        </div>
      </div>
    </FileDialog>
  );
}

export function FileMutationDialog(props: FileMutationDialogProps) {
  if (props.action === "rename") {
    return <RenameDialog {...props} />;
  }
  if (props.action === "trash") {
    return <TrashDialog {...props} />;
  }
  return <MoveDialog {...props} />;
}
