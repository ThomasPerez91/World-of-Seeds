import { useEffect, useState } from "react";

import { api, ApiError, type GeneratedCredentials, type User } from "../../api/client";
import { useFeedback } from "../../components/Feedback";
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
  const feedback = useFeedback();
  const { t } = useI18n();
  const [users, setUsers] = useState<User[]>([]);
  const [credentials, setCredentials] = useState<GeneratedCredentials | null>(null);
  const [loadError, setLoadError] = useState("");
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
        setLoadError(t("admin.usersLoadFailed"));
      });
  }, [onSessionExpired, t]);

  async function generateUser() {
    setGenerating(true);
    setCredentials(null);
    try {
      const generated = await api.createUser();
      setCredentials(generated);
      setUsers((current) => [generated.user, ...current]);
      feedback.toast({ tone: "success", message: t("admin.userCreated") });
    } catch (caught) {
      if (caught instanceof ApiError && caught.status === 401) {
        onSessionExpired();
        return;
      }
      feedback.toast({ tone: "error", message: t("admin.userCreateFailed") });
    } finally {
      setGenerating(false);
    }
  }

  async function setActive(account: User, isActive: boolean) {
    setUpdatingUserId(account.id);
    try {
      const updated = await api.setUserActive(account.id, isActive);
      setUsers((current) =>
        current.map((candidate) => (candidate.id === updated.id ? updated : candidate)),
      );
      feedback.toast({
        tone: "success",
        message: t(isActive ? "admin.userReactivated" : "admin.userSuspended", {
          name: account.username,
        }),
      });
    } catch (caught) {
      if (caught instanceof ApiError && caught.status === 401) {
        onSessionExpired();
        return;
      }
      feedback.toast({ tone: "error", message: t("admin.userUpdateFailed") });
    } finally {
      setUpdatingUserId(null);
    }
  }

  async function deleteAccess(account: User) {
    if (updatingUserId !== null) return;
    setUpdatingUserId(account.id);
    try {
      await api.deleteUser(account.id);
      setUsers((current) => current.filter((candidate) => candidate.id !== account.id));
      feedback.toast({
        tone: "success",
        message: t("admin.userDeleted", { name: account.username }),
      });
    } catch (caught) {
      if (caught instanceof ApiError && caught.status === 401) {
        onSessionExpired();
        return;
      }
      feedback.toast({ tone: "error", message: t("admin.userDeleteFailed") });
    } finally {
      setUpdatingUserId(null);
    }
  }

  async function copy(value: string) {
    try {
      if (navigator.clipboard === undefined) throw new Error("Clipboard API unavailable");
      await navigator.clipboard.writeText(value);
      feedback.toast({ tone: "info", message: t("admin.copied") });
    } catch {
      feedback.toast({ tone: "error", message: t("admin.copyFailed") });
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

        {loadError !== "" && (
          <p className="form-message error-message" role="alert">{loadError}</p>
        )}
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
                      onClick={() => void deleteAccess(account)}
                    >
                      {updatingUserId === account.id ? t("admin.deleting") : t("admin.deleteAccess")}
                    </button>
                  </>
                )}
              </div>
            </div>
          ))}
        </div>
      </section>
    </AdminPageShell>
  );
}
