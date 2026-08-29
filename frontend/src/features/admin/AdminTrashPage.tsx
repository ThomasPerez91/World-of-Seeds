import { useEffect, useState } from "react";

import { api, ApiError, type AdminTrashEntry, type AdminTrashListing } from "../../api/client";
import { useI18n } from "../../i18n";
import { Notice } from "../../components/Notice";
import { FileDialog } from "../files/FileDialog";
import { AdminPageShell, type AdminView } from "./AdminPageShell";

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
  const { t } = useI18n();
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const all = target.kind === "all";

  async function purge() {
    setSubmitting(true);
    setError("");
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
      setError(
        caught instanceof ApiError && caught.status === 409
          ? t("admin.purgeIntegrityFailed")
          : t("admin.purgeFailed"),
      );
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <FileDialog
      eyebrow={t("admin.adminEyebrow")}
      title={all ? t("admin.emptyAllTrash") : t("admin.deletePermanently")}
      description={
        all
          ? t("admin.emptyTrashDescription")
          : t("admin.deleteTrashDescription", { name: target.entry.name, username: target.entry.username })
      }
      onClose={onClose}
      closeDisabled={submitting}
    >
      <div className="confirmation-content">
        <p className="permanent-delete-warning">
          {t("admin.permanentWarning")}
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
            {t("common.cancel")}
          </button>
          <button
            type="button"
            className="danger-button"
            onClick={() => void purge()}
            disabled={submitting}
          >
            {submitting ? t("admin.deletingTrash") : all ? t("admin.deleteAll") : t("common.delete")}
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
  const { formatBytes, formatDate, t } = useI18n();
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
        setError(t("admin.trashLoadFailed"));
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [onSessionExpired, revision, t]);

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
              disabled={loading || (listing?.entries.length ?? 0) === 0}
              onClick={() => setTarget({ kind: "all" })}
            >
              {t("admin.emptyAllTrash")}
            </button>
          </div>
        </div>

        <Notice message={notice} onDismiss={() => setNotice("")} />
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
                  onClick={() => setTarget({ kind: "entry", entry })}
                >
                  {t("common.delete")}
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
