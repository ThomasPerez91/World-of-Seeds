import { useEffect, useState } from "react";

import { api, ApiError, type GeneratedCredentials, type User } from "../../api/client";
import { FileDialog } from "../files/FileDialog";
import { useI18n } from "../../i18n";
import { AdminPageShell, type AdminView } from "./AdminPageShell";

export function AdminUsersPage({
  onBack,
  onNavigate,
  onSessionExpired,
}: {
  onBack: () => void;
  onNavigate: (view: AdminView) => void;
  onSessionExpired: () => void;
}) {
  const { t } = useI18n();
  const [users, setUsers] = useState<User[]>([]);
  const [credentials, setCredentials] = useState<GeneratedCredentials | null>(null);
  const [pendingDeletion, setPendingDeletion] = useState<User | null>(null);
  const [deletionError, setDeletionError] = useState("");
  const [error, setError] = useState("");
  const [generating, setGenerating] = useState(false);
  const [updatingUserId, setUpdatingUserId] = useState<string | null>(null);

  useEffect(() => {
    void api
      .listUsers()
      .then(setUsers)
      .catch((caught: unknown) => {
        if (caught instanceof ApiError && caught.status === 401) {
          onSessionExpired();
          return;
        }
        setError(t("admin.usersLoadFailed"));
      });
  }, [onSessionExpired, t]);

  async function generateUser() {
    setGenerating(true);
    setError("");
    setCredentials(null);
    try {
      const generated = await api.createUser();
      setCredentials(generated);
      setUsers((current) => [generated.user, ...current]);
    } catch (caught) {
      if (caught instanceof ApiError && caught.status === 401) {
        onSessionExpired();
        return;
      }
      setError(t("admin.userCreateFailed"));
    } finally {
      setGenerating(false);
    }
  }

  async function setActive(account: User, isActive: boolean) {
    setUpdatingUserId(account.id);
    setError("");
    try {
      const updated = await api.setUserActive(account.id, isActive);
      setUsers((current) =>
        current.map((candidate) => (candidate.id === updated.id ? updated : candidate)),
      );
    } catch (caught) {
      if (caught instanceof ApiError && caught.status === 401) {
        onSessionExpired();
        return;
      }
      setError(t("admin.userUpdateFailed"));
    } finally {
      setUpdatingUserId(null);
    }
  }

  async function confirmDeletion() {
    if (pendingDeletion === null) return;
    setUpdatingUserId(pendingDeletion.id);
    setDeletionError("");
    try {
      await api.deleteUser(pendingDeletion.id);
      setUsers((current) => current.filter((candidate) => candidate.id !== pendingDeletion.id));
      setPendingDeletion(null);
    } catch (caught) {
      if (caught instanceof ApiError && caught.status === 401) {
        onSessionExpired();
        return;
      }
      setDeletionError(t("admin.userDeleteFailed"));
    } finally {
      setUpdatingUserId(null);
    }
  }

  async function copy(value: string) {
    try {
      if (navigator.clipboard === undefined) throw new Error("Clipboard API unavailable");
      await navigator.clipboard.writeText(value);
    } catch {
      setError(t("admin.copyFailed"));
    }
  }

  return (
    <AdminPageShell
      activeView="admin-users"
      onBack={onBack}
      onNavigate={onNavigate}
    >
      <section className="admin-section" aria-labelledby="admin-users-title">
        <div className="section-heading">
          <div>
            <p className="eyebrow">{t("admin.access")}</p>
            <h2 id="admin-users-title">{t("admin.userAccounts")}</h2>
            <p className="section-intro">
              {t("admin.userIntro")}
            </p>
          </div>
          <div className="generator-controls">
            <button
              type="button"
              className="compact-button"
              onClick={() => void generateUser()}
              disabled={generating}
            >
              {generating ? t("admin.generating") : t("admin.generateUser")}
            </button>
          </div>
        </div>

        {credentials !== null && (
          <div className="credential-reveal" role="status">
            <strong>{t("admin.credentialsWarning")}</strong>
            <div className="credential-row">
              <span>{t("admin.username")}</span>
              <code>{credentials.user.username}</code>
              <button
                type="button"
                className="text-button"
                onClick={() => void copy(credentials.user.username)}
              >
                {t("admin.copy")}
              </button>
            </div>
            <div className="credential-row">
              <span>{t("admin.password")}</span>
              <code>{credentials.initial_password}</code>
              <button
                type="button"
                className="text-button"
                onClick={() => void copy(credentials.initial_password)}
              >
                {t("admin.copy")}
              </button>
            </div>
          </div>
        )}

        <p className="form-message error-message" role="alert">
          {error}
        </p>
        <div className="user-list">
          {users.map((account) => (
            <div className="user-row" key={account.id}>
              <div className="avatar" aria-hidden="true">
                {account.username.slice(0, 1).toUpperCase()}
              </div>
              <div>
                <strong>{account.username}</strong>
                <span>
                  {account.is_admin
                    ? t("admin.administrator")
                    : account.must_change_credentials
                      ? t("admin.personalizationPending")
                      : t("admin.configuredUser")}
                </span>
              </div>
              <div className="user-row-actions">
                <span className={account.is_active ? "status-pill" : "status-pill inactive"}>
                  {account.is_active ? t("admin.active") : t("admin.suspended")}
                </span>
                {!account.is_admin && (
                  <>
                    <button
                      type="button"
                      className="secondary-button compact-button"
                      aria-label={t("admin.accountNamed", { action: account.is_active ? t("admin.suspend") : t("admin.reactivate"), name: account.username })}
                      disabled={updatingUserId === account.id}
                      onClick={() => void setActive(account, !account.is_active)}
                    >
                      {account.is_active ? t("admin.suspend") : t("admin.reactivate")}
                    </button>
                    <button
                      type="button"
                      className="danger-outline-button compact-button"
                      aria-label={t("admin.deleteAccessNamed", { name: account.username })}
                      disabled={updatingUserId === account.id}
                      onClick={() => {
                        setDeletionError("");
                        setPendingDeletion(account);
                      }}
                    >
                      {t("admin.deleteAccess")}
                    </button>
                  </>
                )}
              </div>
            </div>
          ))}
        </div>
        {pendingDeletion !== null && (
          <FileDialog
            eyebrow={t("admin.title")}
            title={t("admin.deleteTitle", { name: pendingDeletion.username })}
            description={t("admin.deleteDescription")}
            onClose={() => {
              setDeletionError("");
              setPendingDeletion(null);
            }}
            closeDisabled={updatingUserId === pendingDeletion.id}
          >
            <div className="confirmation-content">
              <p className="permanent-delete-warning">
                {t("admin.deleteWarning")}
              </p>
              <p className="form-message error-message" role="alert">
                {deletionError}
              </p>
              <div className="dialog-actions">
                <button
                  type="button"
                  className="secondary-button"
                  onClick={() => {
                    setDeletionError("");
                    setPendingDeletion(null);
                  }}
                  disabled={updatingUserId === pendingDeletion.id}
                  data-initial-focus
                >
                  {t("common.cancel")}
                </button>
                <button
                  type="button"
                  className="danger-button"
                  disabled={updatingUserId === pendingDeletion.id}
                  onClick={() => void confirmDeletion()}
                >
                  {updatingUserId === pendingDeletion.id
                    ? t("admin.deleting")
                    : t("admin.confirmDelete")}
                </button>
              </div>
            </div>
          </FileDialog>
        )}
      </section>
    </AdminPageShell>
  );
}
