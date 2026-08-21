import { type FormEvent, useEffect, useState } from "react";

import { api, ApiError, type DirectoryListing, type FileEntry } from "../../api/client";
import { confirmOperation, showOperationError } from "../../components/alerts";
import { FolderIcon } from "../../components/icons";
import { splitDisplayName } from "../../utils/files";
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
  const displayed = splitDisplayName(entry);
  const [name, setName] = useState(displayed.basename);
  const [submitting, setSubmitting] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSubmitting(true);
    try {
      const result = await api.renameFile(entry.path, name);
      onCompleted(`« ${entry.name} » a été renommé en « ${result.name} ».`);
    } catch (caught) {
      if (caught instanceof ApiError && caught.status === 401) {
        onSessionExpired();
        return;
      }
      await showOperationError(mutationErrorMessage(caught));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <FileDialog
      title="Renommer l’élément"
      description="Le changement est immédiat et n’écrase jamais un élément existant."
      onClose={onClose}
      closeDisabled={submitting}
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
        {displayed.extension !== "" && (
          <p className="field-hint">
            L’extension <strong>{displayed.extension}</strong> sera conservée automatiquement.
          </p>
        )}
        <p className="mutation-warning">
          Si qBittorrent utilise encore cet élément, son téléchargement peut passer en erreur.
        </p>
        <div className="dialog-actions">
          <button
            type="button"
            className="secondary-button"
            onClick={onClose}
            disabled={submitting}
          >
            Annuler
          </button>
          <button
            type="submit"
            disabled={submitting || name === displayed.basename || name.length === 0}
          >
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

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setListingError("");
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
    try {
      const result = await api.moveFile(entry.path, destination);
      onCompleted(`« ${entry.name} » a été déplacé vers « ${result.path} ».`);
    } catch (caught) {
      if (caught instanceof ApiError && caught.status === 401) {
        onSessionExpired();
        return;
      }
      await showOperationError(mutationErrorMessage(caught));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <FileDialog
      title="Déplacer l’élément"
      description={`Choisis le nouveau dossier de « ${entry.name} ».`}
      onClose={onClose}
      closeDisabled={submitting}
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
                      <FolderIcon />
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
        <div className="dialog-actions">
          <button
            type="button"
            className="secondary-button"
            onClick={onClose}
            disabled={submitting}
          >
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
  useEffect(() => {
    let active = true;
    void (async () => {
      const confirmed = await confirmOperation({
        title: "Placer dans la corbeille ?",
        message: `« ${entry.name} » pourra être restauré. Un torrent actif peut passer en erreur.`,
        confirmText: "Placer dans la corbeille",
        destructive: true,
      });
      if (!active) return;
      if (!confirmed) {
        onClose();
        return;
      }
      try {
        await api.trashFile(entry.path);
        if (active) onCompleted(`« ${entry.name} » a été placé dans la corbeille.`);
      } catch (caught) {
        if (caught instanceof ApiError && caught.status === 401) {
          onSessionExpired();
          return;
        }
        const message =
          caught instanceof ApiError
            ? {
                400: "Ce chemin n’est pas valide.",
                403: "Cet élément est protégé ou bloqué.",
                404: "L’élément n’existe plus.",
                409: "L’intégrité de cet élément n’a pas pu être confirmée.",
                500: "L’opération n’a pas pu être annulée en toute sécurité.",
                503: "La corbeille est temporairement indisponible.",
              }[caught.status] ?? "L’élément n’a pas pu être placé dans la corbeille."
            : "L’élément n’a pas pu être placé dans la corbeille.";
        await showOperationError(message);
        if (active) onClose();
      }
    })();
    return () => {
      active = false;
    };
  }, [entry, onClose, onCompleted, onSessionExpired]);

  return null;
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
