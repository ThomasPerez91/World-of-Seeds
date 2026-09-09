import { useEffect, useState } from "react";

import { api, ApiError, type GeneratedCredentials, type User } from "../../api/client";
import { Dialog } from "../../components/Dialog";
import { useFeedback } from "../../components/Feedback";
import { Badge, Button, Card, StateMessage } from "../../components/ui";
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
  const [loading, setLoading] = useState(true);
  const [generating, setGenerating] = useState(false);
  const [updatingUserId, setUpdatingUserId] = useState<string | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<User | null>(null);

  useEffect(() => {
    let active = true;
    setLoading(true);
    void api
      .listUsers()
      .then((result) => {
        if (active) setUsers(result);
      })
      .catch((caught: unknown) => {
        if (!active) return;
        if (caught instanceof ApiError && caught.status === 401) {
          onSessionExpired();
          return;
        }
        setLoadError(t("admin.usersLoadFailed"));
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
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

  async function deleteAccess(account: User): Promise<boolean> {
    if (updatingUserId !== null) return false;
    setUpdatingUserId(account.id);
    try {
      await api.deleteUser(account.id);
      setUsers((current) => current.filter((candidate) => candidate.id !== account.id));
      feedback.toast({
        tone: "success",
        message: t("admin.userDeleted", { name: account.username }),
      });
      return true;
    } catch (caught) {
      if (caught instanceof ApiError && caught.status === 401) {
        onSessionExpired();
        return false;
      }
      feedback.toast({ tone: "error", message: t("admin.userDeleteFailed") });
      return false;
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
    <AdminPageShell activeView="admin-users" onBack={onBack} onNavigate={onNavigate}>
      <section className="admin-section" aria-labelledby="admin-users-title" aria-busy={loading}>
        <div className="section-heading">
          <div>
            <p className="eyebrow">{t("admin.access")}</p>
            <h2 id="admin-users-title">{t("admin.userAccounts")}</h2>
            <p className="section-intro">{t("admin.userIntro")}</p>
          </div>
          <div className="generator-controls">
            <Button onClick={() => void generateUser()} disabled={generating}>
              {generating ? t("admin.generating") : t("admin.generateUser")}
            </Button>
          </div>
        </div>

        {credentials !== null && (
          <Card className="credential-reveal" role="status">
            <strong>{t("admin.credentialsWarning")}</strong>
            <div className="credential-row">
              <span>{t("admin.username")}</span>
              <code>{credentials.user.username}</code>
              <Button variant="ghost" className="text-button" onClick={() => void copy(credentials.user.username)}>
                {t("admin.copy")}
              </Button>
            </div>
            <div className="credential-row">
              <span>{t("admin.password")}</span>
              <code>{credentials.initial_password}</code>
              <Button variant="ghost" className="text-button" onClick={() => void copy(credentials.initial_password)}>
                {t("admin.copy")}
              </Button>
            </div>
          </Card>
        )}

        {loading && users.length === 0 ? (
          <StateMessage tone="loading">{t("common.loading")}</StateMessage>
        ) : loadError !== "" ? (
          <StateMessage tone="error">{loadError}</StateMessage>
        ) : (
          <div className="user-list">
            {users.map((account) => (
              <Card className="user-row" key={account.id}>
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
                  <Badge tone={account.is_active ? "success" : "warning"}>
                    {account.is_active ? t("admin.active") : t("admin.suspended")}
                  </Badge>
                  {!account.is_admin && (
                    <>
                      <Button
                        variant="secondary"
                        className="compact-button"
                        aria-label={t("admin.accountNamed", {
                          action: account.is_active ? t("admin.suspend") : t("admin.reactivate"),
                          name: account.username,
                        })}
                        disabled={updatingUserId === account.id}
                        onClick={() => void setActive(account, !account.is_active)}
                      >
                        {account.is_active ? t("admin.suspend") : t("admin.reactivate")}
                      </Button>
                      <Button
                        variant="danger"
                        className="compact-button"
                        aria-label={t("admin.deleteAccessNamed", { name: account.username })}
                        disabled={updatingUserId === account.id}
                        onClick={() => setDeleteTarget(account)}
                      >
                        {updatingUserId === account.id ? t("admin.deleting") : t("admin.deleteAccess")}
                      </Button>
                    </>
                  )}
                </div>
              </Card>
            ))}
          </div>
        )}
      </section>
      {deleteTarget !== null && (
        <Dialog
          eyebrow={t("admin.access")}
          title={t("admin.deleteTitle", { name: deleteTarget.username })}
          description={t("admin.deleteDescription")}
          closeDisabled={updatingUserId === deleteTarget.id}
          onClose={() => setDeleteTarget(null)}
        >
          <p className="dialog-warning">{t("admin.deleteWarning")}</p>
          <div className="dialog-actions">
            <Button
              variant="secondary"
              data-initial-focus
              disabled={updatingUserId === deleteTarget.id}
              onClick={() => setDeleteTarget(null)}
            >
              {t("common.cancel")}
            </Button>
            <Button
              variant="danger"
              disabled={updatingUserId === deleteTarget.id}
              onClick={() => {
                const account = deleteTarget;
                void deleteAccess(account).then((deleted) => {
                  if (deleted) setDeleteTarget(null);
                });
              }}
            >
              {updatingUserId === deleteTarget.id
                ? t("admin.deleting")
                : t("admin.confirmDelete")}
            </Button>
          </div>
        </Dialog>
      )}
    </AdminPageShell>
  );
}
