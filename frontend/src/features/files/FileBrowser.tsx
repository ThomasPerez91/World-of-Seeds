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
import { useFeedback } from "../../components/Feedback";
import { useI18n, type MessageKey } from "../../i18n";
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
  const feedback = useFeedback();
  const { t } = useI18n();
  const [name, setName] = useState("");
  const [submitting, setSubmitting] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSubmitting(true);
    try {
      const result = await api.createDirectory(parent, name);
      onCompleted(t("files.created", { name: result.name }));
    } catch (caught) {
      if (caught instanceof ApiError && caught.status === 401) {
        onSessionExpired();
        return;
      }
      feedback.toast({
        tone: "error",
        message:
          caught instanceof ApiError && caught.status === 409
            ? t("files.exists")
            : t("files.createFailed"),
      });
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <FileDialog
      title={t("files.createFolder")}
      description={t("files.createFolderDescription")}
      onClose={onClose}
      closeDisabled={submitting}
    >
      <form className="mutation-form" onSubmit={(event) => void submit(event)}>
        <label htmlFor="new-folder-name">{t("files.folderName")}</label>
        <input
          id="new-folder-name"
          value={name}
          onChange={(event) => setName(event.target.value)}
          maxLength={255}
          data-initial-focus
          required
        />
        <div className="dialog-actions">
          <button type="button" className="secondary-button" onClick={onClose} disabled={submitting}>{t("common.cancel")}</button>
          <button type="submit" disabled={submitting || name.length === 0}>
            {submitting ? t("files.creating") : t("files.create")}
          </button>
        </div>
      </form>
    </FileDialog>
  );
}

function initialPath(): string {
  return new URLSearchParams(window.location.search).get("path") ?? "";
}

function typeLabel(entry: FileEntry, t: (key: MessageKey) => string): string {
  if (entry.kind === "directory") return t("files.folder");
  if (entry.kind === "symlink") return t("files.blockedLink");
  if (entry.kind === "other") return t("files.blockedItem");
  const family = entry.media_type?.split("/", 1)[0];
  return {
    video: t("files.video"),
    audio: t("files.audio"),
    image: t("files.image"),
    text: t("files.document"),
    application: t("files.file"),
  }[family ?? ""] ?? t("files.file");
}

function listingErrorMessage(error: unknown, t: (key: MessageKey) => string): string {
  if (!(error instanceof ApiError)) {
    return t("files.openFailed");
  }
  if (error.code === "file_path_invalid") return t("files.invalidPath");
  if (error.code === "file_path_blocked") return t("files.blockedPath");
  if (error.code === "directory_not_found") return t("files.missingFolder");
  return (
    {
      400: t("files.invalidPath"),
      403: t("files.blockedPath"),
      404: t("files.missingFolder"),
    }[error.status] ?? t("files.openFailed")
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
  const { t } = useI18n();
  return (
    <div className="browser-loading" role="status" aria-label={t("files.loading")}>
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
  const feedback = useFeedback();
  const { formatBytes, formatDate, t } = useI18n();
  const [path, setPath] = useState(initialPath);
  const [listing, setListing] = useState<DirectoryListing | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [reloadKey, setReloadKey] = useState(0);
  const [creatingFolder, setCreatingFolder] = useState(false);
  const [trashingPath, setTrashingPath] = useState<string | null>(null);
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
        setError(listingErrorMessage(caught, t));
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [onSessionExpired, onStorageChanged, path, reloadKey, revision, t]);

  function navigate(nextPath: string) {
    const url = new URL(window.location.href);
    if (nextPath === "") url.searchParams.delete("path");
    else url.searchParams.set("path", nextPath);
    window.history.pushState({}, "", `${url.pathname}${url.search}${url.hash}`);
    setPath(nextPath);
  }

  function completeMutation(message: string) {
    setMutation(null);
    feedback.toast({ tone: "success", message });
    setReloadKey((value) => value + 1);
    onFilesChanged();
  }

  async function trash(entry: FileEntry) {
    if (trashingPath !== null) return;
    setTrashingPath(entry.path);
    try {
      await api.trashFile(entry.path);
      feedback.toast({ tone: "success", message: t("files.trashed", { name: entry.name }) });
      setReloadKey((value) => value + 1);
      onFilesChanged();
    } catch (caught) {
      if (caught instanceof ApiError && caught.status === 401) {
        onSessionExpired();
        return;
      }
      const message = caught instanceof ApiError
        ? {
            400: t("files.invalidPath"),
            403: t("files.protected"),
            404: t("files.targetMissing"),
            409: t("trash.integrityFailed"),
            500: t("trash.restoreRollbackFailed"),
            503: t("trash.temporarilyUnavailable"),
          }[caught.status] ?? t("files.trashFailed")
        : t("files.trashFailed");
      feedback.toast({ tone: "error", message });
    } finally {
      setTrashingPath(null);
    }
  }

  return (
    <section
      className="file-browser"
      aria-labelledby="file-browser-title"
      aria-busy={loading}
    >
      <h2 id="file-browser-title" className="sr-only">{t("files.explorer")}</h2>

      <div className="browser-navigation">
        <nav className="breadcrumbs" aria-label={t("files.breadcrumbs")}>
          {(listing?.breadcrumbs ?? [{ label: t("dashboard.files"), path: "" }]).map(
            (breadcrumb, index, all) => (
              <span className="breadcrumb" key={breadcrumb.path || "root"}>
                <button
                  type="button"
                  onClick={() => navigate(breadcrumb.path)}
                  aria-current={index === all.length - 1 ? "page" : undefined}
                >
                  {breadcrumb.path === "" ? t("dashboard.files") : breadcrumb.label}
                </button>
                {index < all.length - 1 && <span aria-hidden="true">/</span>}
              </span>
            ),
          )}
        </nav>
        <div className="browser-navigation-actions">
          <button type="button" className="refresh-button" onClick={() => setCreatingFolder(true)}>
            {t("files.newFolder")}
          </button>
          <button
            type="button"
            className="refresh-button"
            onClick={() => setReloadKey((value) => value + 1)}
            disabled={loading}
          >
            {t("common.refresh")}
          </button>
        </div>
      </div>

      {loading && <LoadingRows />}

      {!loading && error !== "" && (
        <div className="browser-state" role="alert">
          <strong>{t("files.inaccessible")}</strong>
          <p>{error}</p>
          <div className="state-actions">
            {path !== "" && (
              <button type="button" className="secondary-button" onClick={() => navigate("")}>
                {t("files.backRoot")}
              </button>
            )}
            <button type="button" className="compact-button" onClick={() => setReloadKey((v) => v + 1)}>
              {t("common.retry")}
            </button>
          </div>
        </div>
      )}

      {!loading && listing !== null && listing.entries.length === 0 && (
        <div className="browser-state empty-state">
          <strong>{t("files.empty")}</strong>
          <p>{t("files.emptyHint")}</p>
        </div>
      )}

      {!loading && listing !== null && listing.entries.length > 0 && (
        <div className="file-table-wrap">
          <table className="file-table">
            <caption className="sr-only">
              {t("files.contents", { path: listing.path === "" ? t("dashboard.files") : listing.path })}
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
                <th scope="col">{t("files.name")}</th>
                <th scope="col">{t("files.extension")}</th>
                <th scope="col">{t("files.type")}</th>
                <th scope="col">{t("files.size")}</th>
                <th scope="col">{t("files.modification")}</th>
                <th scope="col">
                  <span className="sr-only">{t("files.action")}</span>
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
                          {typeLabel(entry, t)} · {formatBytes(entry.size)}
                        </span>
                        <time
                          className="mobile-file-date"
                          dateTime={entry.modified_at}
                        >
                          {t("files.modifiedOn", { date: formatDate(entry.modified_at, { dateStyle: "medium", timeStyle: "short" }) })}
                        </time>
                      </span>
                    </div>
                  </td>
                  <td className="file-extension-cell">{displayed.extension || "—"}</td>
                  <td>{typeLabel(entry, t)}</td>
                  <td>{formatBytes(entry.size)}</td>
                  <td>
                    <time dateTime={entry.modified_at}>
                      {formatDate(entry.modified_at, { dateStyle: "medium", timeStyle: "short" })}
                    </time>
                  </td>
                  <td className="file-action-cell">
                    <div
                      className="file-actions"
                      role="group"
                      aria-label={t("files.actionsFor", { name: entry.name })}
                    >
                      {entry.kind === "directory" && !entry.blocked ? (
                        <>
                          <button
                            type="button"
                            className="open-folder-button"
                            onClick={() => navigate(entry.path)}
                            aria-label={t("files.open", { name: entry.name })}
                            title={t("files.openTitle")}
                          >
                            <OpenIcon />
                          </button>
                          <a
                            className="download-link"
                            href={api.folderDownloadUrl(entry.path)}
                            download={`${entry.name}.zip`}
                            aria-label={t("files.downloadFolderNamed", { name: entry.name })}
                            title={t("files.downloadFolder")}
                          >
                            <DownloadIcon />
                            <span className="download-label">{t("files.downloadFolder")}</span>
                          </a>
                        </>
                      ) : entry.blocked ? (
                        <span className="blocked-badge">{t("files.blocked")}</span>
                      ) : (
                        <a
                          className="download-link"
                          href={api.fileDownloadUrl(entry.path)}
                          download={entry.name}
                          aria-label={t("files.downloadNamed", { name: entry.name })}
                          title={t("common.download")}
                        >
                          <DownloadIcon />
                          <span className="download-label">{t("common.download")}</span>
                        </a>
                      )}
                      {!entry.blocked && !isProtectedRootEntry(entry) && (
                        <>
                          <button
                            type="button"
                            className="file-mutation-button"
                            onClick={() => setMutation({ action: "rename", entry })}
                            aria-label={t("files.renameNamed", { name: entry.name })}
                            title={t("common.rename")}
                          >
                            <RenameIcon />
                            <span>{t("common.rename")}</span>
                          </button>
                          <button
                            type="button"
                            className="file-mutation-button"
                            onClick={() => setMutation({ action: "move", entry })}
                            aria-label={t("files.moveNamed", { name: entry.name })}
                            title={t("common.move")}
                          >
                            <MoveIcon />
                            <span>{t("common.move")}</span>
                          </button>
                          <button
                            type="button"
                            className="file-mutation-button destructive-file-action"
                            disabled={trashingPath !== null}
                            onClick={() => void trash(entry)}
                            aria-label={t("files.trashNamed", { name: entry.name })}
                            title={t("files.moveToTrash")}
                          >
                            <DeleteIcon />
                            <span>{trashingPath === entry.path ? t("common.processing") : t("files.trash")}</span>
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
          {t("files.truncated")}
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
