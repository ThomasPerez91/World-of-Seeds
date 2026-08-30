import { useEffect, useRef, useState } from "react";

import { api, ApiError, type AdminTrashEntry, type AdminTrashListing } from "../../api/client";
import { useFeedback } from "../../components/Feedback";
import { useI18n } from "../../i18n";
import { AdminPageShell, type AdminView } from "./AdminPageShell";

type PurgeTarget = { kind: "all" } | { kind: "entry"; entry: AdminTrashEntry };

function InlinePurgeConfirmation({
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
  const feedback = useFeedback();
  const { t } = useI18n();
  const [submitting, setSubmitting] = useState(false);
  const all = target.kind === "all";

  async function purge() {
    setSubmitting(true);
    try {
      if (target.kind === "entry") {
        await api.purgeAdminTrash(target.entry.id);
        onCompleted(t("admin.trashPurged", { name: target.entry.name }));
      } else {
        const result = await api.purgeAllAdminTrash();
        const suffix =
          result.remaining === 0
            ? t("admin.purgeAllComplete")
            : t("admin.purgeAllRemaining", { count: result.remaining });
        onCompleted(t("admin.purgeAllResult", { count: result.purged, suffix }));
      }
    } catch (caught) {
      if (caught instanceof ApiError && caught.status === 401) {
        onSessionExpired();
        return;
      }
      feedback.toast({
        tone: "error",
        message: caught instanceof ApiError && caught.status === 409
          ? t("admin.purgeIntegrityFailed")
          : t("admin.purgeFailed"),
      });
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div
      className="inline-danger-confirmation admin-inline-confirmation"
      role="group"
      aria-labelledby="admin-purge-confirmation-title"
    >
      <div>
        <strong id="admin-purge-confirmation-title">
          {all ? t("admin.emptyAllTrash") : t("admin.deletePermanently")}
        </strong>
        <span>
          {all
            ? t("admin.emptyTrashDescription")
            : t("admin.deleteTrashDescription", { name: target.entry.name, username: target.entry.username })}
        </span>
        <small>{t("admin.permanentWarning")}</small>
      </div>
      <button type="button" className="secondary-button" onClick={onClose} disabled={submitting}>
        {t("common.cancel")}
      </button>
      <button
        type="button"
        className="danger-button"
        onClick={() => void purge()}
        disabled={submitting}
        autoFocus
      >
        {submitting ? t("admin.deletingTrash") : all ? t("admin.deleteAll") : t("admin.confirmPurge")}
      </button>
    </div>
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
  const feedback = useFeedback();
  const { formatBytes, formatDate, t } = useI18n();
  const [listing, setListing] = useState<AdminTrashListing | null>(null);
  const [target, setTarget] = useState<PurgeTarget | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [revision, setRevision] = useState(0);
  const purgeOpenerRef = useRef<HTMLButtonElement | null>(null);
  const restorePurgeFocusRef = useRef(false);

  useEffect(() => {
    if (target !== null || !restorePurgeFocusRef.current) return;
    restorePurgeFocusRef.current = false;
    purgeOpenerRef.current?.focus();
  }, [target]);

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
        setError(t("admin.trashLoadFailed"));
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [onSessionExpired, revision, t]);

  function completed(message: string) {
    purgeOpenerRef.current = null;
    setTarget(null);
    feedback.toast({ tone: "success", message });
    setRevision((current) => current + 1);
  }

  function closePurgeConfirmation() {
    restorePurgeFocusRef.current = true;
    setTarget(null);
  }

  return (
    <AdminPageShell activeView="admin-trash" onBack={onBack} onNavigate={onNavigate}>
      <section className="admin-section" aria-labelledby="admin-trash-title" aria-busy={loading}>
        <div className="section-heading">
          <div>
            <p className="eyebrow">{t("admin.globalCleanup")}</p>
            <h2 id="admin-trash-title">{t("admin.userTrash")}</h2>
          </div>
          <div className="admin-trash-actions">
            <button
              type="button"
              className="refresh-button"
              disabled={loading}
              onClick={() => setRevision((current) => current + 1)}
            >
              {t("common.refresh")}
            </button>
            <button
              type="button"
              className="danger-outline-button"
              disabled={loading || target !== null || (listing?.entries.length ?? 0) === 0}
              onClick={(event) => {
                purgeOpenerRef.current = event.currentTarget;
                setTarget({ kind: "all" });
              }}
            >
              {t("admin.emptyAllTrash")}
            </button>
          </div>
        </div>

        {target !== null && (
          <InlinePurgeConfirmation
            target={target}
            onClose={closePurgeConfirmation}
            onCompleted={completed}
            onSessionExpired={onSessionExpired}
          />
        )}
        <p className="form-message error-message" role="alert">
          {error}
        </p>
        {listing?.truncated && (
          <p className="truncation-notice" role="status">
            {t("admin.trashTruncated")}
          </p>
        )}
        {!loading && listing?.entries.length === 0 && (
          <div className="admin-empty-state">
            <strong>{t("admin.allTrashEmpty")}</strong>
            <span>{t("admin.noTrashCleanup")}</span>
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
                    {t("admin.deletedOn", {
                      size: formatBytes(entry.size, t("trash.folderSizeUnknown")),
                      date: formatDate(entry.deleted_at, { dateStyle: "medium", timeStyle: "short" }),
                    })}
                  </span>
                </div>
                <button
                  type="button"
                  className="danger-outline-button compact-button"
                  aria-label={t("admin.deleteTrashNamed", { name: entry.name, username: entry.username })}
                  disabled={target !== null}
                  onClick={(event) => {
                    purgeOpenerRef.current = event.currentTarget;
                    setTarget({ kind: "entry", entry });
                  }}
                >
                  {t("common.delete")}
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>
    </AdminPageShell>
  );
}
