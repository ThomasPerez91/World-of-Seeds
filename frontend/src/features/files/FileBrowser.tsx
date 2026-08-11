import { useEffect, useState } from "react";

import {
  api,
  ApiError,
  type DirectoryListing,
  type FileEntry,
  type FileEntryKind,
} from "../../api/client";
import {
  FileMutationDialog,
  type FileMutationAction,
} from "./FileMutationDialog";

const dateFormatter = new Intl.DateTimeFormat("fr-FR", {
  dateStyle: "medium",
  timeStyle: "short",
});

function initialPath(): string {
  return new URLSearchParams(window.location.search).get("path") ?? "";
}

function formatBytes(value: number | null): string {
  if (value === null) {
    return "—";
  }
  if (value === 0) {
    return "0 o";
  }
  const units = ["o", "Ko", "Mo", "Go", "To", "Po"];
  const exponent = Math.min(Math.floor(Math.log(value) / Math.log(1024)), units.length - 1);
  const amount = value / 1024 ** exponent;
  return `${new Intl.NumberFormat("fr-FR", {
    maximumFractionDigits: amount >= 10 || exponent === 0 ? 0 : 1,
  }).format(amount)} ${units[exponent]}`;
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

function FileIcon({ kind, mediaType }: { kind: FileEntryKind; mediaType: string | null }) {
  if (kind === "directory") {
    return (
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <path d="M3.5 7.5h6l1.7 2h9.3v8.8a2.2 2.2 0 0 1-2.2 2.2H5.7a2.2 2.2 0 0 1-2.2-2.2V7.5Z" />
        <path d="M3.5 8V5.7a2.2 2.2 0 0 1 2.2-2.2h3.1l2 2.2h7.5a2.2 2.2 0 0 1 2.2 2.2v1.6" />
      </svg>
    );
  }
  if (kind === "symlink" || kind === "other") {
    return (
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <rect x="4" y="10" width="16" height="11" rx="2" />
        <path d="M8 10V7a4 4 0 0 1 8 0v3" />
      </svg>
    );
  }
  const isVideo = mediaType?.startsWith("video/") ?? false;
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path d="M6 2.8h7l5 5V21H6V2.8Z" />
      <path d="M13 2.8V8h5" />
      {isVideo && <path d="m10 12.2 4.8 2.8-4.8 2.8v-5.6Z" />}
    </svg>
  );
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
  return !entry.path.includes("/") && ["downloads", "watch"].includes(entry.name);
}

export function FileBrowser({
  onFilesChanged,
  onSessionExpired,
  revision,
}: {
  onFilesChanged: () => void;
  onSessionExpired: () => void;
  revision: number;
}) {
  const [path, setPath] = useState(initialPath);
  const [listing, setListing] = useState<DirectoryListing | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [reloadKey, setReloadKey] = useState(0);
  const [notice, setNotice] = useState("");
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
      .then(setListing)
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
  }, [onSessionExpired, path, reloadKey, revision]);

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

  const storagePercent =
    listing === null || listing.storage.total === 0
      ? 0
      : Math.min((listing.storage.used / listing.storage.total) * 100, 100);

  return (
    <section
      className="file-browser"
      aria-labelledby="file-browser-title"
      aria-busy={loading}
    >
      <header className="browser-header">
        <div>
          <p className="eyebrow">Espace personnel</p>
          <h2 id="file-browser-title">Mes fichiers</h2>
          <p className="browser-subtitle">Ton espace privé sur la seedbox.</p>
        </div>
        {listing !== null && (
          <div className="storage-card" role="group" aria-label="Utilisation du stockage">
            <div className="storage-copy">
              <span>{formatBytes(listing.storage.used)} utilisés</span>
              <strong>{formatBytes(listing.storage.available)} disponibles</strong>
            </div>
            <progress
              className="storage-track"
              max={100}
              value={storagePercent}
              aria-label={`${storagePercent.toFixed(0)} % du stockage utilisé`}
            >
              {storagePercent.toFixed(0)} %
            </progress>
            <span className="storage-total">Capacité totale : {formatBytes(listing.storage.total)}</span>
          </div>
        )}
      </header>

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
        <button
          type="button"
          className="refresh-button"
          onClick={() => setReloadKey((value) => value + 1)}
          disabled={loading}
        >
          Actualiser
        </button>
      </div>

      {notice !== "" && (
        <p className="operation-notice" role="status">
          {notice}
        </p>
      )}

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
            <thead>
              <tr>
                <th scope="col">Nom</th>
                <th scope="col">Type</th>
                <th scope="col">Taille</th>
                <th scope="col">Modification</th>
                <th scope="col">
                  <span className="sr-only">Action</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {listing.entries.map((entry) => (
                <tr className={entry.blocked ? "blocked-row" : undefined} key={entry.name}>
                  <td className="file-name-cell">
                    <span className={`file-icon ${entry.kind}`}>
                      <FileIcon kind={entry.kind} mediaType={entry.media_type} />
                    </span>
                    <span className="file-name-copy" title={entry.name}>
                      {entry.kind === "directory" && !entry.blocked ? (
                        <button type="button" onClick={() => navigate(entry.path)}>
                          {entry.name}
                        </button>
                      ) : (
                        <strong>{entry.name}</strong>
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
                  </td>
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
                        <button
                          type="button"
                          className="open-folder-button"
                          onClick={() => navigate(entry.path)}
                          aria-label={`Ouvrir ${entry.name}`}
                        >
                          <span aria-hidden="true">›</span>
                        </button>
                      ) : entry.blocked ? (
                        <span className="blocked-badge">Bloqué</span>
                      ) : (
                        <a
                          className="download-link"
                          href={api.fileDownloadUrl(entry.path)}
                          download={entry.name}
                          aria-label={`Télécharger ${entry.name}`}
                        >
                          <svg viewBox="0 0 24 24" aria-hidden="true">
                            <path d="M12 3v12" />
                            <path d="m7.5 10.5 4.5 4.5 4.5-4.5" />
                            <path d="M5 20h14" />
                          </svg>
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
                          >
                            <svg viewBox="0 0 24 24" aria-hidden="true">
                              <path d="m4 16-.8 4 4-.8L18.5 7.9l-3.2-3.2L4 16Z" />
                              <path d="m13.8 6.2 3.2 3.2" />
                            </svg>
                            <span>Renommer</span>
                          </button>
                          <button
                            type="button"
                            className="file-mutation-button"
                            onClick={() => setMutation({ action: "move", entry })}
                            aria-label={`Déplacer ${entry.name}`}
                          >
                            <svg viewBox="0 0 24 24" aria-hidden="true">
                              <path d="M3.5 7.5h6l1.7 2h10.3v8.8a2.2 2.2 0 0 1-2.2 2.2H5.7a2.2 2.2 0 0 1-2.2-2.2V7.5Z" />
                              <path d="m13 13 2-2 2 2M15 11v5" />
                            </svg>
                            <span>Déplacer</span>
                          </button>
                          <button
                            type="button"
                            className="file-mutation-button destructive-file-action"
                            onClick={() => setMutation({ action: "trash", entry })}
                            aria-label={`Placer ${entry.name} dans la corbeille`}
                          >
                            <svg viewBox="0 0 24 24" aria-hidden="true">
                              <path d="M4.5 7h15" />
                              <path d="m9 7 .7-2h4.6l.7 2" />
                              <path d="m6.5 7 .8 13h9.4l.8-13" />
                              <path d="M10 11v5M14 11v5" />
                            </svg>
                            <span>Corbeille</span>
                          </button>
                        </>
                      )}
                    </div>
                  </td>
                </tr>
              ))}
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
    </section>
  );
}
