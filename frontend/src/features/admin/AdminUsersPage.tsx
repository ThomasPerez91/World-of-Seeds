import { useEffect, useMemo, useState } from "react";

import {
  api,
  ApiError,
  type AdminUser,
  type GeneratedCredentials,
  type UserQuota,
} from "../../api/client";
import { Dialog } from "../../components/Dialog";
import { useFeedback } from "../../components/Feedback";
import { Badge, Button, Card, StateMessage } from "../../components/ui";
import { useI18n } from "../../i18n";
import { AdminPageShell, type AdminView } from "./AdminPageShell";

type LastLoginFilter = "all" | "day" | "week" | "month" | "never";

export interface LastLoginAge {
  days: number;
  hours: number;
  minutes: number;
}

export function getLastLoginAge(value: string, now = Date.now()): LastLoginAge {
  const elapsedSeconds = Math.max(0, Math.floor((now - new Date(value).getTime()) / 1_000));
  return {
    days: Math.floor(elapsedSeconds / 86_400),
    hours: Math.floor((elapsedSeconds % 86_400) / 3_600),
    minutes: Math.floor((elapsedSeconds % 3_600) / 60),
  };
}

export function matchesLastLoginFilter(
  account: AdminUser,
  filter: LastLoginFilter,
  now = Date.now(),
): boolean {
  if (filter === "all") return true;
  if (filter === "never") return account.last_login_at === null;
  if (account.last_login_at === null) return false;
  const maximumAge = {
    day: 86_400_000,
    week: 7 * 86_400_000,
    month: 30 * 86_400_000,
  }[filter];
  const elapsed = now - new Date(account.last_login_at).getTime();
  return elapsed >= 0 && elapsed <= maximumAge;
}

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
  const { t, formatDate } = useI18n();
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [quota, setQuota] = useState<UserQuota | null>(null);
  const [credentials, setCredentials] = useState<GeneratedCredentials | null>(null);
  const [loadError, setLoadError] = useState("");
  const [loading, setLoading] = useState(true);
  const [generating, setGenerating] = useState(false);
  const [updatingUserId, setUpdatingUserId] = useState<string | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<AdminUser | null>(null);
  const [lastLoginFilter, setLastLoginFilter] = useState<LastLoginFilter>("all");
  const [clockNow, setClockNow] = useState(() => Date.now());

  const filteredUsers = useMemo(
    () => users.filter((account) => matchesLastLoginFilter(account, lastLoginFilter, clockNow)),
    [clockNow, lastLoginFilter, users],
  );

  function describeLastLogin(value: string | null): string {
    if (value === null) return t("admin.lastLoginNever");
    const age = getLastLoginAge(value, clockNow);
    const duration = age.days > 0
      ? [
          t(age.days === 1 ? "admin.durationDay" : "admin.durationDays", { count: age.days }),
          age.hours > 0
            ? t(age.hours === 1 ? "admin.durationHour" : "admin.durationHours", {
                count: age.hours,
              })
            : "",
        ].filter(Boolean).join(" ")
      : age.hours > 0
        ? t(age.hours === 1 ? "admin.durationHour" : "admin.durationHours", {
            count: age.hours,
          })
        : age.minutes > 0
          ? t(age.minutes === 1 ? "admin.durationMinute" : "admin.durationMinutes", {
              count: age.minutes,
            })
          : "";
    if (duration !== "") return t("admin.lastLoginAgo", { duration });
    return t("admin.lastLoginJustNow");
  }

  function absoluteDate(value: string): string {
    return formatDate(value, { dateStyle: "medium", timeStyle: "short" });
  }

  useEffect(() => {
    let active = true;
    setLoading(true);
    void Promise.all([api.listUsers(), api.getUserQuota()])
      .then(([result, quotaResult]) => {
        if (active) {
          setUsers(result);
          setQuota(quotaResult);
        }
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

  useEffect(() => {
    const refreshClock = () => setClockNow(Date.now());
    const intervalId = window.setInterval(refreshClock, 60_000);
    window.addEventListener("focus", refreshClock);
    document.addEventListener("visibilitychange", refreshClock);
    return () => {
      window.clearInterval(intervalId);
      window.removeEventListener("focus", refreshClock);
      document.removeEventListener("visibilitychange", refreshClock);
    };
  }, []);

  async function generateUser() {
    setGenerating(true);
    setCredentials(null);
    try {
      const generated = await api.createUser();
      setCredentials(generated);
      setUsers((current) => [generated.user, ...current]);
      setQuota((current) =>
        current === null
          ? current
          : { ...current, used: current.used + 1, reached: current.used + 1 >= current.maximum },
      );
      feedback.toast({ tone: "success", message: t("admin.userCreated") });
    } catch (caught) {
      if (caught instanceof ApiError && caught.status === 401) {
        onSessionExpired();
        return;
      }
      feedback.toast({
        tone: "error",
        message:
          caught instanceof ApiError && caught.code === "account_quota_reached"
            ? t("admin.accountQuotaReached")
            : t("admin.userCreateFailed"),
      });
    } finally {
      setGenerating(false);
    }
  }

  async function setActive(account: AdminUser, isActive: boolean) {
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

  async function deleteAccess(account: AdminUser): Promise<boolean> {
    if (updatingUserId !== null) return false;
    setUpdatingUserId(account.id);
    try {
      await api.deleteUser(account.id);
      setUsers((current) => current.filter((candidate) => candidate.id !== account.id));
      setQuota((current) =>
        current === null
          ? current
          : {
              ...current,
              used: Math.max(0, current.used - 1),
              reached: Math.max(0, current.used - 1) >= current.maximum,
            },
      );
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
            {quota !== null && (
              <p className="account-quota" role="status">
                {t("admin.accountsUsed", { used: quota.used, maximum: quota.maximum })}
              </p>
            )}
          </div>
          <div className="generator-controls">
            <Button
              onClick={() => void generateUser()}
              disabled={generating || quota?.reached === true}
              title={quota?.reached === true ? t("admin.accountQuotaReached") : undefined}
            >
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
              <span>{t("account.authSeed")}</span>
              <code>{credentials.auth_seed}</code>
              <Button variant="ghost" className="text-button" onClick={() => void copy(credentials.auth_seed)}>
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

        <div className="user-login-filter">
          <label htmlFor="last-login-filter">{t("admin.lastLoginFilter")}</label>
          <select
            id="last-login-filter"
            value={lastLoginFilter}
            onChange={(event) => setLastLoginFilter(event.target.value as LastLoginFilter)}
          >
            <option value="all">{t("admin.lastLoginFilterAll")}</option>
            <option value="day">{t("admin.lastLoginFilterDay")}</option>
            <option value="week">{t("admin.lastLoginFilterWeek")}</option>
            <option value="month">{t("admin.lastLoginFilterMonth")}</option>
            <option value="never">{t("admin.lastLoginFilterNever")}</option>
          </select>
        </div>

        {loading && users.length === 0 ? (
          <StateMessage tone="loading">{t("common.loading")}</StateMessage>
        ) : loadError !== "" ? (
          <StateMessage tone="error">{loadError}</StateMessage>
        ) : filteredUsers.length === 0 ? (
          <StateMessage tone="empty">{t("admin.usersNoFilterResults")}</StateMessage>
        ) : (
          <div className="user-list">
            {filteredUsers.map((account) => (
              <Card className="user-row" key={account.id}>
                <div className="user-row-heading">
                  <div className="user-row-identity">
                    <span className="account-avatar admin-user-avatar" aria-hidden="true">
                      {account.username.slice(0, 1).toUpperCase()}
                    </span>
                    <div className="user-row-identity-copy">
                      <strong>{account.username}</strong>
                      <span>
                        {account.is_admin
                          ? t("admin.administrator")
                          : account.must_change_credentials
                            ? t("admin.personalizationPending")
                            : t("admin.configuredUser")}
                      </span>
                    </div>
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
                </div>
                <dl className="user-activity-dates">
                  <div>
                    <dt>{t("admin.registeredAt")}</dt>
                    <dd>
                      <time dateTime={account.created_at}>{absoluteDate(account.created_at)}</time>
                    </dd>
                  </div>
                  <div>
                    <dt>{t("admin.lastLoginAt")}</dt>
                    <dd>
                      {account.last_login_at === null ? (
                        <span>{t("admin.lastLoginNever")}</span>
                      ) : (
                        <time dateTime={account.last_login_at}>
                          {absoluteDate(account.last_login_at)}
                        </time>
                      )}
                      <small>{describeLastLogin(account.last_login_at)}</small>
                    </dd>
                  </div>
                </dl>
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
