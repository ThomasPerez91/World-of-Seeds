import { type FormEvent, useEffect, useState } from "react";

import {
  api,
  ApiError,
  type DirectoryListing,
  type FileEntry,
  type FileEntryKind,
  type StorageUsage,
} from "../../api/client";
import {
  DeleteIcon,
  DownloadIcon,
  FileIcon as FileGlyph,
  FolderIcon,
  LockedEntryIcon,
  MoveIcon,
  OpenIcon,
  RenameIcon,
  VideoFileIcon,
} from "../../components/icons";
import { Notice } from "../../components/Notice";
import { formatBytes } from "../../utils/format";
import { splitDisplayName } from "../../utils/files";
import {
  FileDialog,
} from "./FileDialog";
import {
  FileMutationDialog,
  type FileMutationAction,
} from "./FileMutationDialog";

function CreateFolderDialog({
  parent,
  onClose,
  onCompleted,
  onSessionExpired,
}: {
  parent: string;
  onClose: () => void;
  onCompleted: (message: string) => void;
  onSessionExpired: () => void;
}) {
  const [name, setName] = useState("");
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSubmitting(true);
    setError("");
    try {
      const result = await api.createDirectory(parent, name);
      onCompleted(`Le dossier « ${result.name} » a été créé.`);
    } catch (caught) {
      if (caught instanceof ApiError && caught.status === 401) {
        onSessionExpired();
        return;
      }
      setError(
        caught instanceof ApiError && caught.status === 409
          ? "Un élément porte déjà ce nom dans ce dossier."
          : "Le dossier n’a pas pu être créé.",
      );
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <FileDialog
      title="Créer un dossier"
      description="Le dossier sera créé uniquement à l’emplacement actuel."
      onClose={onClose}
      closeDisabled={submitting}
    >
      <form className="mutation-form" onSubmit={(event) => void submit(event)}>
        <label htmlFor="new-folder-name">Nom du dossier</label>
        <input
          id="new-folder-name"
          value={name}
          onChange={(event) => setName(event.target.value)}
          maxLength={255}
          data-initial-focus
          required
        />
        <p className="form-message error-message" role="alert">{error}</p>
        <div className="dialog-actions">
          <button type="button" className="secondary-button" onClick={onClose} disabled={submitting}>Annuler</button>
          <button type="submit" disabled={submitting || name.length === 0}>
            {submitting ? "Création…" : "Créer le dossier"}
          </button>
        </div>
      </form>
    </FileDialog>
  );
}

const dateFormatter = new Intl.DateTimeFormat("fr-FR", {
  dateStyle: "medium",
  timeStyle: "short",
});

function initialPath(): string {
  return new URLSearchParams(window.location.search).get("path") ?? "";
}

function typeLabel(entry: FileEntry): string {
  if (entry.kind === "directory") return "Dossier";
  if (entry.kind === "symlink") return "Lien bloqué";
  if (entry.kind === "other") return "Élément bloqué";
  const family = entry.media_type?.split("/", 1)[0];
  return {
    video: "Vidéo",
    audio: "Audio",
    image: "Image",
    text: "Document",
    application: "Fichier",
  }[family ?? ""] ?? "Fichier";
}

function listingErrorMessage(error: unknown): string {
  if (!(error instanceof ApiError)) {
    return "Impossible d’ouvrir ce dossier.";
  }
  return (
    {
      400: "Ce chemin est invalide.",
      403: "Ce chemin est bloqué pour protéger ton espace.",
      404: "Ce dossier n’existe plus.",
    }[error.status] ?? "Impossible d’ouvrir ce dossier."
  );
}

function EntryIcon({ kind, mediaType }: { kind: FileEntryKind; mediaType: string | null }) {
  if (kind === "directory") {
    return <FolderIcon />;
  }
  if (kind === "symlink" || kind === "other") {
    return <LockedEntryIcon />;
  }
  const isVideo = mediaType?.startsWith("video/") ?? false;
  return isVideo ? <VideoFileIcon /> : <FileGlyph />;
}

function LoadingRows() {
  return (
    <div className="browser-loading" role="status" aria-label="Chargement du dossier">
      {[0, 1, 2].map((row) => (
        <div className="loading-row" key={row}>
          <span />
          <span />
        </div>
      ))}
    </div>
  );
}

function isProtectedRootEntry(entry: FileEntry): boolean {
  return !entry.path.includes("/") && entry.name === "downloads";
}

export function FileBrowser({
  onFilesChanged,
  onSessionExpired,
  onStorageChanged,
  revision,
}: {
  onFilesChanged: () => void;
  onSessionExpired: () => void;
  onStorageChanged: (storage: StorageUsage) => void;
  revision: number;
}) {
  const [path, setPath] = useState(initialPath);
  const [listing, setListing] = useState<DirectoryListing | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [reloadKey, setReloadKey] = useState(0);
  const [notice, setNotice] = useState("");
  const [creatingFolder, setCreatingFolder] = useState(false);
  const [mutation, setMutation] = useState<{
    action: FileMutationAction;
    entry: FileEntry;
  } | null>(null);

  useEffect(() => {
    const handleBack = () => setPath(initialPath());
    window.addEventListener("popstate", handleBack);
    return () => window.removeEventListener("popstate", handleBack);
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError("");
    void api
      .listFiles(path, controller.signal)
      .then((result) => {
        setListing(result);
        onStorageChanged(result.storage);
      })
      .catch((caught: unknown) => {
        if (caught instanceof DOMException && caught.name === "AbortError") return;
        if (caught instanceof ApiError && caught.status === 401) {
          onSessionExpired();
          return;
        }
        setListing(null);
        setError(listingErrorMessage(caught));
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [onSessionExpired, onStorageChanged, path, reloadKey, revision]);

  function navigate(nextPath: string) {
    const url = new URL(window.location.href);
    if (nextPath === "") url.searchParams.delete("path");
    else url.searchParams.set("path", nextPath);
    window.history.pushState({}, "", `${url.pathname}${url.search}${url.hash}`);
    setPath(nextPath);
    setNotice("");
  }

  function completeMutation(message: string) {
    setMutation(null);
    setNotice(message);
    setReloadKey((value) => value + 1);
    onFilesChanged();
  }

  return (
    <section
      className="file-browser"
      aria-labelledby="file-browser-title"
      aria-busy={loading}
    >
      <h2 id="file-browser-title" className="sr-only">Explorateur de fichiers</h2>

      <div className="browser-navigation">
        <nav className="breadcrumbs" aria-label="Fil d’Ariane">
          {(listing?.breadcrumbs ?? [{ label: "Mes fichiers", path: "" }]).map(
            (breadcrumb, index, all) => (
              <span className="breadcrumb" key={breadcrumb.path || "root"}>
                <button
                  type="button"
                  onClick={() => navigate(breadcrumb.path)}
                  aria-current={index === all.length - 1 ? "page" : undefined}
                >
                  {breadcrumb.label}
                </button>
                {index < all.length - 1 && <span aria-hidden="true">/</span>}
              </span>
            ),
          )}
        </nav>
        <div className="browser-navigation-actions">
          <button type="button" className="refresh-button" onClick={() => setCreatingFolder(true)}>
            Nouveau dossier
          </button>
          <button
            type="button"
            className="refresh-button"
            onClick={() => setReloadKey((value) => value + 1)}
            disabled={loading}
          >
            Actualiser
          </button>
        </div>
      </div>

      <Notice message={notice} onDismiss={() => setNotice("")} />

      {loading && <LoadingRows />}

      {!loading && error !== "" && (
        <div className="browser-state" role="alert">
          <strong>Dossier inaccessible</strong>
          <p>{error}</p>
          <div className="state-actions">
            {path !== "" && (
              <button type="button" className="secondary-button" onClick={() => navigate("")}>
                Revenir à la racine
              </button>
            )}
            <button type="button" className="compact-button" onClick={() => setReloadKey((v) => v + 1)}>
              Réessayer
            </button>
          </div>
        </div>
      )}

      {!loading && listing !== null && listing.entries.length === 0 && (
        <div className="browser-state empty-state">
          <strong>Ce dossier est vide</strong>
          <p>Les fichiers ajoutés ici apparaîtront automatiquement.</p>
        </div>
      )}

      {!loading && listing !== null && listing.entries.length > 0 && (
        <div className="file-table-wrap">
          <table className="file-table">
            <caption className="sr-only">
              Contenu du dossier {listing.path === "" ? "Mes fichiers" : listing.path}
            </caption>
            <colgroup>
              <col className="file-name-column" />
              <col className="file-extension-column" />
              <col className="file-type-column" />
              <col className="file-size-column" />
              <col className="file-date-column" />
              <col className="file-actions-column" />
            </colgroup>
            <thead>
              <tr>
                <th scope="col">Nom</th>
                <th scope="col">Extension</th>
                <th scope="col">Type</th>
                <th scope="col">Taille</th>
                <th scope="col">Modification</th>
                <th scope="col">
                  <span className="sr-only">Action</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {listing.entries.map((entry) => {
                const displayed = splitDisplayName(entry);
                return (
                <tr className={entry.blocked ? "blocked-row" : undefined} key={entry.name}>
                  <td className="file-name-cell">
                    <div className="file-name-content">
                      <span className={`file-icon ${entry.kind}`}>
                        <EntryIcon kind={entry.kind} mediaType={entry.media_type} />
                      </span>
                      <span className="file-name-copy" title={entry.name}>
                        {entry.kind === "directory" && !entry.blocked ? (
                          <button type="button" onClick={() => navigate(entry.path)}>
                            {displayed.basename}
                          </button>
                        ) : (
                          <strong>{displayed.basename}</strong>
                        )}
                        <span className="mobile-file-meta">
                          {typeLabel(entry)} · {formatBytes(entry.size)}
                        </span>
                        <time
                          className="mobile-file-date"
                          dateTime={entry.modified_at}
                        >
                          Modifié le {dateFormatter.format(new Date(entry.modified_at))}
                        </time>
                      </span>
                    </div>
                  </td>
                  <td className="file-extension-cell">{displayed.extension || "—"}</td>
                  <td>{typeLabel(entry)}</td>
                  <td>{formatBytes(entry.size)}</td>
                  <td>
                    <time dateTime={entry.modified_at}>
                      {dateFormatter.format(new Date(entry.modified_at))}
                    </time>
                  </td>
                  <td className="file-action-cell">
                    <div
                      className="file-actions"
                      role="group"
                      aria-label={`Actions pour ${entry.name}`}
                    >
                      {entry.kind === "directory" && !entry.blocked ? (
                        <>
                          <button
                            type="button"
                            className="open-folder-button"
                            onClick={() => navigate(entry.path)}
                            aria-label={`Ouvrir ${entry.name}`}
                            title="Ouvrir"
                          >
                            <OpenIcon />
                          </button>
                          <a
                            className="download-link"
                            href={api.folderDownloadUrl(entry.path)}
                            download={`${entry.name}.zip`}
                            aria-label={`Télécharger le dossier ${entry.name}`}
                            title="Télécharger le dossier"
                          >
                            <DownloadIcon />
                            <span className="download-label">Télécharger le dossier</span>
                          </a>
                        </>
                      ) : entry.blocked ? (
                        <span className="blocked-badge">Bloqué</span>
                      ) : (
                        <a
                          className="download-link"
                          href={api.fileDownloadUrl(entry.path)}
                          download={entry.name}
                          aria-label={`Télécharger ${entry.name}`}
                          title="Télécharger"
                        >
                          <DownloadIcon />
                          <span className="download-label">Télécharger</span>
                        </a>
                      )}
                      {!entry.blocked && !isProtectedRootEntry(entry) && (
                        <>
                          <button
                            type="button"
                            className="file-mutation-button"
                            onClick={() => setMutation({ action: "rename", entry })}
                            aria-label={`Renommer ${entry.name}`}
                            title="Renommer"
                          >
                            <RenameIcon />
                            <span>Renommer</span>
                          </button>
                          <button
                            type="button"
                            className="file-mutation-button"
                            onClick={() => setMutation({ action: "move", entry })}
                            aria-label={`Déplacer ${entry.name}`}
                            title="Déplacer"
                          >
                            <MoveIcon />
                            <span>Déplacer</span>
                          </button>
                          <button
                            type="button"
                            className="file-mutation-button destructive-file-action"
                            onClick={() => setMutation({ action: "trash", entry })}
                            aria-label={`Placer ${entry.name} dans la corbeille`}
                            title="Placer dans la corbeille"
                          >
                            <DeleteIcon />
                            <span>Corbeille</span>
                          </button>
                        </>
                      )}
                    </div>
                  </td>
                </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {!loading && listing?.truncated === true && (
        <p className="truncated-notice" role="status">
          Ce dossier contient plus de 5 000 éléments. Seuls les premiers sont affichés.
        </p>
      )}
      {mutation !== null && (
        <FileMutationDialog
          action={mutation.action}
          currentDirectory={listing?.path ?? path}
          entry={mutation.entry}
          onClose={() => setMutation(null)}
          onCompleted={completeMutation}
          onSessionExpired={onSessionExpired}
        />
      )}
      {creatingFolder && (
        <CreateFolderDialog
          parent={listing?.path ?? path}
          onClose={() => setCreatingFolder(false)}
          onCompleted={(message) => {
            setCreatingFolder(false);
            completeMutation(message);
          }}
          onSessionExpired={onSessionExpired}
        />
      )}
    </section>
  );
}
