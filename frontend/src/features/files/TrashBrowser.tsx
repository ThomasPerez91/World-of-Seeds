import { useEffect, useRef, useState } from "react";

import { api, ApiError, type TrashEntry, type TrashListing } from "../../api/client";
import { useFeedback } from "../../components/Feedback";
import { FileIcon, FolderIcon } from "../../components/icons";
import { useI18n, type MessageKey } from "../../i18n";

function trashListingError(error: unknown, t: (key: MessageKey) => string): string {
  if (
    error instanceof ApiError &&
    (error.code === "trash_storage_unavailable" || error.status === 503)
  ) {
    return t("trash.temporarilyUnavailable");
  }
  return t("trash.loadFailed");
}

function trashActionError(error: unknown, action: "purge" | "restore", t: (key: MessageKey) => string): string {
  if (!(error instanceof ApiError)) return t("trash.actionFailed");
  const codedErrors: Record<string, MessageKey> = {
    trash_entry_not_found: "trash.missing",
    trash_restore_target_unavailable: "trash.restoreConflict",
    trash_integrity_failed: "trash.integrityFailed",
    trash_operation_unverified: "trash.restoreRollbackFailed",
    trash_purge_incomplete: "trash.purgeFailed",
    trash_storage_unavailable: "trash.storageUnavailable",
  };
  if (error.code !== null && codedErrors[error.code] !== undefined) {
    return t(codedErrors[error.code]);
  }
  if (error.status === 404) return t("trash.missing");
  if (error.status === 409) {
    return action === "restore"
      ? t("trash.restoreConflict")
      : t("trash.integrityFailed");
  }
  if (error.status === 500) {
    return action === "restore"
      ? t("trash.restoreRollbackFailed")
      : t("trash.purgeFailed");
  }
  if (error.status === 503) {
    return action === "purge"
      ? t("trash.purgeDatabaseFailed")
      : t("trash.storageUnavailable");
  }
  return t("trash.actionFailed");
}

function TrashIcon({ kind }: { kind: TrashEntry["kind"] }) {
  return (
    <span className={`trash-icon ${kind}`} aria-hidden="true">
      {kind === "directory" ? <FolderIcon /> : <FileIcon />}
    </span>
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
  const feedback = useFeedback();
  const { formatBytes, formatDate, t } = useI18n();
  const [listing, setListing] = useState<TrashListing | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [reloadKey, setReloadKey] = useState(0);
  const [pendingPurge, setPendingPurge] = useState<TrashEntry | null>(null);
  const [actionBusyId, setActionBusyId] = useState<string | null>(null);
  const returnFocusRef = useRef<HTMLButtonElement | null>(null);
  const confirmButtonRef = useRef<HTMLButtonElement | null>(null);
  const shouldReturnFocusRef = useRef(false);

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
        setError(trashListingError(caught, t));
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [onSessionExpired, reloadKey, revision, t]);

  useEffect(() => {
    if (pendingPurge !== null) {
      confirmButtonRef.current?.focus();
      return;
    }
    if (shouldReturnFocusRef.current) {
      shouldReturnFocusRef.current = false;
      returnFocusRef.current?.focus();
    }
  }, [pendingPurge]);

  function completeAction(message: string) {
    setPendingPurge(null);
    feedback.toast({ tone: "success", message });
    setReloadKey((value) => value + 1);
    onFilesChanged();
  }

  async function restore(entry: TrashEntry) {
    if (actionBusyId !== null) return;
    setActionBusyId(entry.id);
    try {
      await api.restoreTrash(entry.id);
      completeAction(t("trash.restored", { name: entry.name }));
    } catch (caught) {
      if (caught instanceof ApiError && caught.status === 401) {
        onSessionExpired();
        return;
      }
      feedback.toast({ tone: "error", message: trashActionError(caught, "restore", t) });
    } finally {
      setActionBusyId(null);
    }
  }

  async function purge(entry: TrashEntry) {
    if (actionBusyId !== null) return;
    setActionBusyId(entry.id);
    try {
      await api.purgeTrash(entry.id);
      completeAction(t("trash.purged", { name: entry.name }));
    } catch (caught) {
      if (caught instanceof ApiError && caught.status === 401) {
        onSessionExpired();
        return;
      }
      feedback.toast({ tone: "error", message: trashActionError(caught, "purge", t) });
    } finally {
      setActionBusyId(null);
    }
  }

  function cancelPurge() {
    shouldReturnFocusRef.current = true;
    setPendingPurge(null);
  }

  return (
    <section
      className="trash-browser"
      aria-labelledby="trash-title"
      aria-busy={loading}
    >
      <h2 id="trash-title" className="sr-only">{t("files.trash")}</h2>
      <div className="trash-toolbar">
        <span>{t("trash.deletedItems")}</span>
        <button
          type="button"
          className="refresh-button"
          onClick={() => setReloadKey((value) => value + 1)}
          disabled={loading}
        >
          {t("common.refresh")}
        </button>
      </div>

      {loading && (
        <div
          className="trash-loading"
          role="status"
          aria-label={t("trash.loading")}
        >
          <span />
          <span />
        </div>
      )}

      {!loading && error !== "" && (
        <div className="browser-state" role="alert">
          <strong>{t("trash.unavailable")}</strong>
          <p>{error}</p>
          <button
            type="button"
            className="compact-button"
            onClick={() => setReloadKey((value) => value + 1)}
          >
            {t("common.retry")}
          </button>
        </div>
      )}

      {!loading && listing?.entries.length === 0 && (
        <div className="browser-state empty-state">
          <strong>{t("trash.empty")}</strong>
          <p>{t("trash.emptyHint")}</p>
        </div>
      )}

      {!loading && listing !== null && listing.entries.length > 0 && (
        <ul className="trash-list">
          {listing.entries.map((entry) => (
            <li className="trash-row" key={entry.id}>
              <TrashIcon kind={entry.kind} />
              <div className="trash-copy">
                <h3 title={entry.name}>{entry.name}</h3>
                <code title={entry.original_path}>{entry.original_path}</code>
                <span>
                  {entry.kind === "directory"
                    ? formatBytes(entry.size, t("trash.folderSizeUnknown"))
                    : formatBytes(entry.size)}{" · "}
                  <time dateTime={entry.deleted_at}>
                    {t("trash.deletedOn", { size: "", date: formatDate(entry.deleted_at, { dateStyle: "medium", timeStyle: "short" }) }).replace(/^ · /, "")}
                  </time>
                </span>
              </div>
              <div
                className="trash-actions"
                role="group"
                aria-label={t("files.actionsFor", { name: entry.name })}
              >
                {pendingPurge?.id === entry.id ? (
                  <div className="inline-danger-confirmation" role="group" aria-label={t("trash.purgeTitle")}>
                    <span>{t("trash.purgeMessage", { name: entry.name })}</span>
                    <button
                      type="button"
                      className="secondary-button"
                      disabled={actionBusyId !== null}
                      onClick={cancelPurge}
                    >
                      {t("common.cancel")}
                    </button>
                    <button
                      ref={confirmButtonRef}
                      type="button"
                      className="trash-purge-button"
                      disabled={actionBusyId !== null}
                      onClick={() => void purge(entry)}
                    >
                      {actionBusyId === entry.id ? t("common.processing") : t("trash.confirmPurge")}
                    </button>
                  </div>
                ) : (
                  <>
                    <button
                      type="button"
                      className="secondary-button"
                      disabled={actionBusyId !== null}
                      onClick={() => void restore(entry)}
                    >
                      {actionBusyId === entry.id ? t("common.processing") : t("trash.restore")}
                    </button>
                    <button
                      ref={pendingPurge === null ? returnFocusRef : undefined}
                      type="button"
                      className="trash-purge-button"
                      disabled={actionBusyId !== null}
                      onClick={(event) => {
                        returnFocusRef.current = event.currentTarget;
                        setPendingPurge(entry);
                      }}
                      aria-label={t("trash.purgeNamed", { name: entry.name })}
                    >
                      {t("trash.purge")}
                    </button>
                  </>
                )}
              </div>
            </li>
          ))}
        </ul>
      )}

      {!loading && listing?.truncated === true && (
        <p className="truncated-notice" role="status">
          {t("trash.truncated")}
        </p>
      )}
    </section>
  );
}
