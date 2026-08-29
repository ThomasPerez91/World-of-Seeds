import { type FormEvent, useEffect, useState } from "react";

import { api, ApiError, type DirectoryListing, type FileEntry } from "../../api/client";
import { ConfirmDialog, useFeedback } from "../../components/Feedback";
import { FolderIcon } from "../../components/icons";
import { useI18n, type MessageKey } from "../../i18n";
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

function mutationErrorMessage(error: unknown, t: (key: MessageKey) => string): string {
  if (!(error instanceof ApiError)) return t("files.operationFailed");
  return (
    {
      400: t("files.invalidDestination"),
      403: t("files.protected"),
      404: t("files.targetMissing"),
      409: t("files.destinationExists"),
      500: t("files.operationUnverified"),
      503: t("files.storageUnavailable"),
    }[error.status] ?? t("files.operationFailed")
  );
}

function listingErrorMessage(error: unknown, t: (key: MessageKey) => string): string {
  if (!(error instanceof ApiError)) return t("files.openFailed");
  return (
    {
      400: t("files.destinationPathInvalid"),
      403: t("files.destinationBlocked"),
      404: t("files.missingFolder"),
    }[error.status] ?? t("files.openFailed")
  );
}

function RenameDialog({
  entry,
  onClose,
  onCompleted,
  onSessionExpired,
}: Omit<FileMutationDialogProps, "action" | "currentDirectory">) {
  const feedback = useFeedback();
  const { t } = useI18n();
  const displayed = splitDisplayName(entry);
  const [name, setName] = useState(displayed.basename);
  const [submitting, setSubmitting] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSubmitting(true);
    try {
      const result = await api.renameFile(entry.path, name);
      onCompleted(t("files.renamed", { oldName: entry.name, name: result.name }));
    } catch (caught) {
      if (caught instanceof ApiError && caught.status === 401) {
        onSessionExpired();
        return;
      }
      feedback.toast({ tone: "error", message: mutationErrorMessage(caught, t) });
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <FileDialog
      title={t("files.renameTitle")}
      description={t("files.renameDescription")}
      onClose={onClose}
      closeDisabled={submitting}
    >
      <form className="mutation-form" onSubmit={(event) => void submit(event)}>
        <label htmlFor="mutation-name">{t("files.newName")}</label>
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
            {t("files.extensionPreserved", { extension: displayed.extension })}
          </p>
        )}
        <p className="mutation-warning">
          {t("files.torrentWarning")}
        </p>
        <div className="dialog-actions">
          <button
            type="button"
            className="secondary-button"
            onClick={onClose}
            disabled={submitting}
          >
            {t("common.cancel")}
          </button>
          <button
            type="submit"
            disabled={submitting || name === displayed.basename || name.length === 0}
          >
            {submitting ? t("files.renaming") : t("files.confirmRename")}
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
  const feedback = useFeedback();
  const { t } = useI18n();
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
        setListingError(listingErrorMessage(caught, t));
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [destination, onSessionExpired, t]);

  const destinationIsSource =
    entry.kind === "directory" &&
    (destination === entry.path || destination.startsWith(`${entry.path}/`));
  const destinationIsCurrent = destination === currentDirectory;

  async function move() {
    setSubmitting(true);
    try {
      const result = await api.moveFile(entry.path, destination);
      onCompleted(t("files.moved", { name: entry.name, path: result.path }));
    } catch (caught) {
      if (caught instanceof ApiError && caught.status === 401) {
        onSessionExpired();
        return;
      }
      feedback.toast({ tone: "error", message: mutationErrorMessage(caught, t) });
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <FileDialog
      title={t("files.moveTitle")}
      description={t("files.moveDescription", { name: entry.name })}
      onClose={onClose}
      closeDisabled={submitting}
    >
      <div className="destination-picker">
        <nav className="picker-breadcrumbs" aria-label={t("files.destinationFolder")}>
          {(listing?.breadcrumbs ?? [{ label: "Mes fichiers", path: "" }]).map(
            (breadcrumb, index, all) => (
              <span key={breadcrumb.path || "root"}>
                <button
                  type="button"
                  onClick={() => setDestination(breadcrumb.path)}
                  aria-current={index === all.length - 1 ? "page" : undefined}
                >
                  {breadcrumb.path === "" ? t("dashboard.files") : breadcrumb.label}
                </button>
                {index < all.length - 1 && <span aria-hidden="true">/</span>}
              </span>
            ),
          )}
        </nav>

        <div className="destination-list" aria-busy={loading}>
          {loading && <p className="picker-state">{t("files.loadingFolders")}</p>}
          {!loading && listingError !== "" && (
            <p className="picker-state error-message">{listingError}</p>
          )}
          {!loading && listing !== null && (
            <>
              {listing.entries.filter(
                (candidate) => candidate.kind === "directory" && !candidate.blocked,
              ).length === 0 && <p className="picker-state">{t("files.noSubfolder")}</p>}
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
                      <span>{forbidden ? t("files.sourceFolder") : t("files.openTitle")}</span>
                    </button>
                  );
                })}
            </>
          )}
        </div>

        <div className="selected-destination">
          <span>{t("files.selectedDestination")}</span>
          <strong>{destination === "" ? t("dashboard.files") : destination}</strong>
        </div>
        <p className="mutation-warning">
          {t("files.torrentWarning")}
        </p>
        {destinationIsCurrent && (
          <p className="picker-hint">{t("files.alreadyHere")}</p>
        )}
        {destinationIsSource && (
          <p className="picker-hint error-message">{t("files.moveIntoSelf")}</p>
        )}
        <div className="dialog-actions">
          <button
            type="button"
            className="secondary-button"
            onClick={onClose}
            disabled={submitting}
          >
            {t("common.cancel")}
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
            {submitting ? t("files.moving") : t("files.moveHere")}
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
  const feedback = useFeedback();
  const { t } = useI18n();
  const [submitting, setSubmitting] = useState(false);

  async function trash() {
    setSubmitting(true);
    try {
      await api.trashFile(entry.path);
      onCompleted(t("files.trashed", { name: entry.name }));
    } catch (caught) {
      if (caught instanceof ApiError && caught.status === 401) {
        onSessionExpired();
        return;
      }
      const message =
        caught instanceof ApiError
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
      onClose();
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <ConfirmDialog
      options={{
        title: t("files.trashTitle"),
        message: t("files.trashMessage", { name: entry.name }),
        confirmText: t("files.moveToTrash"),
        destructive: true,
      }}
      closeDisabled={submitting}
      onClose={(confirmed) => {
        if (confirmed) void trash();
        else onClose();
      }}
    />
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
