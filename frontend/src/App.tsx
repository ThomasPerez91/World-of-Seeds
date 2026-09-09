import {
  type FormEvent,
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";

import { api, ApiError, type User } from "./api/client";
import { type AdminView } from "./features/admin/AdminPageShell";
import { AdminStoragePage } from "./features/admin/AdminStoragePage";
import { AdminServicesPage } from "./features/admin/AdminServicesPage";
import { AdminSettingsPage } from "./features/admin/AdminSettingsPage";
import { AdminUsersPage } from "./features/admin/AdminUsersPage";
import { UserDashboardPage } from "./features/dashboard/UserDashboardPage";
import { AccountMenuIcon, BackIcon, BrandIcon } from "./components/icons";
import { LanguageSelector } from "./components/LanguageSelector";
import {
  LegalLinks,
  LegalPage,
  type LegalDocument,
} from "./components/LegalPage";
import { ThemeProvider } from "./theme";
import { ThemeSelector } from "./components/ThemeSelector";
import { Button, Card, Badge, StateMessage } from "./components/ui";
import { APP_VERSION } from "./version";
import { FeedbackProvider } from "./components/Feedback";
import { useFeedback } from "./components/Feedback";
import { I18nProvider, useI18n, type Locale } from "./i18n";

type AuthState =
  | { status: "loading" }
  | { status: "anonymous" }
  | { status: "unavailable" }
  | { status: "authenticated"; user: User };

function clearLegacyFilePathFromUrl() {
  const url = new URL(window.location.href);
  url.searchParams.delete("path");
  window.history.replaceState({}, "", `${url.pathname}${url.search}${url.hash}`);
}

function BrandMark() {
  return (
    <div className="brand-mark" aria-hidden="true">
      <BrandIcon />
    </div>
  );
}

function ServiceHealth() {
  const { t } = useI18n();
  const [health, setHealth] = useState<"healthy" | "unavailable">("healthy");
  const [checking, setChecking] = useState(false);
  const mounted = useRef(true);

  const check = useCallback(async () => {
    setChecking(true);
    try {
      const status = await api.health();
      if (mounted.current) setHealth(status.status === "ok" ? "healthy" : "unavailable");
    } catch {
      if (mounted.current) setHealth("unavailable");
    } finally {
      if (mounted.current) setChecking(false);
    }
  }, []);

  useEffect(() => {
    mounted.current = true;
    void check();
    const interval = window.setInterval(() => void check(), 30_000);
    return () => {
      mounted.current = false;
      window.clearInterval(interval);
    };
  }, [check]);

  return (
    <Button
      type="button"
      className={`service-health ${health}`}
      onClick={() => void check()}
      disabled={checking}
      aria-label={t("health.check")}
    >
      <span className="service-health-dot" aria-hidden="true" />
      <span aria-live="polite">
        {checking
          ? t("health.checking")
          : health === "healthy"
            ? t("health.healthy")
            : t("health.unavailable")}
      </span>
    </Button>
  );
}

function LoginScreen({
  onLogin,
  onOpenLegal,
}: {
  onLogin: (user: User) => void;
  onOpenLegal: (document: LegalDocument) => void;
}) {
  const { apiError, locale, t } = useI18n();
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    setSubmitting(true);
    setError("");
    try {
      let user = await api.login(String(form.get("username")), String(form.get("password")));
      if ((user.preferred_locale ?? "fr") !== locale) {
        try {
          user = await api.changeLocale(locale);
        } catch {
          // Authentication succeeded: a preference write must never invalidate the session.
        }
      }
      onLogin(user);
    } catch (caught) {
      setError(apiError(caught, "error.temporary"));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main className="login-page">
      <section className="brand-panel" aria-labelledby="brand-title">
        <BrandMark />
        <p className="eyebrow">{t("login.private")}</p>
        <h1 id="brand-title">World of Seeds</h1>
        <p className="brand-copy">
          {t("login.tagline")}
        </p>
        <ServiceHealth />
      </section>

      <section className="form-panel" aria-labelledby="login-title">
        <LanguageSelector />
        <Card className="form-card">
          <p className="eyebrow">{t("login.title")}</p>
          <h2 id="login-title">{t("login.welcome")}</h2>
          <p className="form-intro">{t("login.instructions")}</p>

          <form onSubmit={handleSubmit}>
            <label htmlFor="username">{t("login.username")}</label>
            <input
              id="username"
              name="username"
              autoComplete="username"
              aria-describedby="login-error"
              aria-invalid={error !== ""}
              required
            />

            <label htmlFor="password">{t("login.password")}</label>
            <input
              id="password"
              name="password"
              type="password"
              autoComplete="current-password"
              aria-describedby="login-error"
              aria-invalid={error !== ""}
              required
            />

            <Button type="submit" disabled={submitting}>
              {submitting ? t("login.submitting") : t("login.submit")}
            </Button>
            <p id="login-error" className="form-message error-message" role="alert">
              {error}
            </p>
          </form>
        </Card>
        <LegalLinks onOpen={onOpenLegal} />
      </section>
    </main>
  );
}

function CredentialChangeScreen({
  user,
  onChanged,
  onOpenLegal,
}: {
  user: User;
  onChanged: (user: User) => void;
  onOpenLegal: (document: LegalDocument) => void;
}) {
  const { apiError, locale, setLocale, t } = useI18n();
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);

  async function changeLocale(nextLocale: typeof locale) {
    setError("");
    try {
      onChanged(await api.changeLocale(nextLocale));
    } catch (caught) {
      setLocale(user.preferred_locale ?? "fr");
      setError(apiError(caught, "language.saveFailed"));
    }
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const newPassword = String(form.get("new-password"));
    if (newPassword !== String(form.get("password-confirmation"))) {
      setError(t("credentials.mismatch"));
      return;
    }
    setSubmitting(true);
    setError("");
    try {
      const updatedUser = await api.changeCredentials(
        String(form.get("current-password")),
        String(form.get("username")),
        newPassword,
      );
      onChanged(updatedUser);
    } catch (caught) {
      setError(apiError(caught, "error.temporary"));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main className="centered-page">
      <section className="credential-card" aria-labelledby="credential-title">
        <BrandMark />
        <LanguageSelector onChange={(nextLocale) => void changeLocale(nextLocale)} />
        <p className="eyebrow">{t("credentials.eyebrow")}</p>
        <h1 id="credential-title" className="compact-title">
          {t("credentials.title")}
        </h1>
        <p className="form-intro">
          {t("credentials.intro")}
        </p>
        <form onSubmit={handleSubmit}>
          <label htmlFor="current-password">{t("credentials.initialPassword")}</label>
          <input
            id="current-password"
            name="current-password"
            type="password"
            autoComplete="current-password"
            aria-describedby="credential-error"
            aria-invalid={error !== ""}
            required
          />
          <label htmlFor="new-username">{t("account.username")}</label>
          <input
            id="new-username"
            name="username"
            defaultValue={user.username}
            pattern="[a-zA-Z0-9][a-zA-Z0-9_-]{2,31}"
            autoComplete="username"
            aria-describedby="new-username-hint credential-error"
            aria-invalid={error !== ""}
            required
          />
          <p id="new-username-hint" className="field-hint">
            {t("account.usernameHint")}
          </p>
          <label htmlFor="new-password">{t("credentials.newPassword")}</label>
          <input
            id="new-password"
            name="new-password"
            type="password"
            minLength={12}
            autoComplete="new-password"
            aria-describedby="credential-error"
            aria-invalid={error !== ""}
            required
          />
          <label htmlFor="password-confirmation">{t("credentials.confirmPassword")}</label>
          <input
            id="password-confirmation"
            name="password-confirmation"
            type="password"
            minLength={12}
            autoComplete="new-password"
            aria-describedby="credential-error"
            aria-invalid={error !== ""}
            required
          />
          <Button type="submit" disabled={submitting}>
            {submitting ? t("credentials.submitting") : t("credentials.submit")}
          </Button>
          <p id="credential-error" className="form-message error-message" role="alert">
            {error}
          </p>
        </form>
        <LegalLinks onOpen={onOpenLegal} />
      </section>
    </main>
  );
}

function AccountMenu({
  user,
  onOpenAdmin,
  onOpenSettings,
  onLogout,
  onSessionExpired,
}: {
  user: User;
  onOpenAdmin: () => void;
  onOpenSettings: () => void;
  onLogout: () => Promise<void>;
  onSessionExpired: () => void;
}) {
  const feedback = useFeedback();
  const { t } = useI18n();
  const [open, setOpen] = useState(false);
  const [loggingOut, setLoggingOut] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!open) return;

    function closeOnOutsideClick(event: MouseEvent) {
      if (!containerRef.current?.contains(event.target as Node)) setOpen(false);
    }

    function closeOnEscape(event: KeyboardEvent) {
      if (event.key === "Escape") {
        setOpen(false);
        triggerRef.current?.focus();
      }
    }

    document.addEventListener("mousedown", closeOnOutsideClick);
    document.addEventListener("keydown", closeOnEscape);
    return () => {
      document.removeEventListener("mousedown", closeOnOutsideClick);
      document.removeEventListener("keydown", closeOnEscape);
    };
  }, [open]);

  async function handleLogout() {
    setLoggingOut(true);
    try {
      await onLogout();
    } catch (caught) {
      if (caught instanceof ApiError && caught.status === 401) {
        onSessionExpired();
        return;
      }
      feedback.toast({ tone: "error", message: t("dashboard.logoutFailed") });
    } finally {
      setLoggingOut(false);
    }
  }

  return (
    <div className="account-menu" ref={containerRef} onBlur={(event) => {
      if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setOpen(false);
    }}>
      <Button
        type="button"
        ref={triggerRef}
        className="account-trigger"
        aria-label={t("dashboard.accountMenu")}
        aria-expanded={open}
        aria-controls="account-dropdown"
        onClick={() => setOpen((current) => !current)}
      >
        <span className="account-avatar" aria-hidden="true">
          {user.username.slice(0, 1).toUpperCase()}
        </span>
        <strong>{user.username}</strong>
        <AccountMenuIcon className="account-trigger-icon" />
      </Button>
      {open && (
        <div id="account-dropdown" className="account-dropdown">
          <p className="account-name">{user.username}</p>
          {user.is_admin && (
            <Button
              type="button"
              className="account-dropdown-item"
              onClick={() => {
                setOpen(false);
                onOpenAdmin();
              }}
            >
              {t("dashboard.admin")}
            </Button>
          )}
          <Button
            type="button"
            className="account-dropdown-item"
            onClick={() => {
              setOpen(false);
              onOpenSettings();
            }}
          >
            {t("dashboard.account")}
          </Button>
          <div className="account-dropdown-separator" />
          <ThemeSelector />
          <div className="account-dropdown-separator" />
          <Button
            type="button"
            className="account-dropdown-item logout-item"
            onClick={() => void handleLogout()}
            disabled={loggingOut}
          >
            {loggingOut ? t("dashboard.loggingOut") : t("dashboard.logout")}
          </Button>
        </div>
      )}
    </div>
  );
}

function AccountSettingsPage({
  user,
  onBack,
  onChanged,
  onPasswordChanged,
  onSessionExpired,
}: {
  user: User;
  onBack: () => void;
  onChanged: (user: User) => void;
  onPasswordChanged: () => void;
  onSessionExpired: () => void;
}) {
  const feedback = useFeedback();
  const { apiError, setLocale, t } = useI18n();
  const [localeSaving, setLocaleSaving] = useState(false);
  async function changeLocale(locale: Locale) {
    setLocaleSaving(true);
    try {
      onChanged(await api.changeLocale(locale));
    } catch {
      setLocale(user.preferred_locale ?? "fr");
      feedback.toast({ tone: "error", message: t("language.saveFailed") });
    } finally {
      setLocaleSaving(false);
    }
  }

  const [usernameSubmitting, setUsernameSubmitting] = useState(false);
  const [passwordError, setPasswordError] = useState("");
  const [passwordSubmitting, setPasswordSubmitting] = useState(false);
  const [username, setUsername] = useState(user.username);

  async function submitUsername(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setUsernameSubmitting(true);
    try {
      const updated = await api.changeUsername(username);
      onChanged(updated);
      setUsername(updated.username);
      feedback.toast({ tone: "success", message: t("account.nameUpdated") });
    } catch (caught) {
      if (caught instanceof ApiError && caught.status === 401) {
        onSessionExpired();
        return;
      }
      feedback.toast({ tone: "error", message: apiError(caught, "error.temporary") });
    } finally {
      setUsernameSubmitting(false);
    }
  }

  async function submitPassword(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const newPassword = String(form.get("new-password"));
    if (newPassword !== String(form.get("password-confirmation"))) {
      setPasswordError(t("account.passwordMismatch"));
      return;
    }

    setPasswordSubmitting(true);
    setPasswordError("");
    try {
      await api.changePassword(String(form.get("current-password")), newPassword);
      onPasswordChanged();
    } catch (caught) {
      if (
        caught instanceof ApiError &&
        caught.status === 401 &&
        caught.code === "not_authenticated"
      ) {
        onSessionExpired();
        return;
      }
      feedback.toast({ tone: "error", message: apiError(caught, "error.temporary") });
    } finally {
      setPasswordSubmitting(false);
    }
  }

  return (
    <section className="settings-page" aria-labelledby="account-settings-title">
      <Button type="button" className="back-button" onClick={onBack}>
        <BackIcon /> {t("common.backDashboard")}
      </Button>
      <div className="settings-header">
        <p className="eyebrow">{t("account.eyebrow")}</p>
        <h1 id="account-settings-title">{t("account.title")}</h1>
        <p className="settings-intro">
          {t("account.intro")}
        </p>
      </div>
      <Card className="preferences-card" aria-labelledby="preferences-title">
        <h2 id="preferences-title">{t("preferences.title")}</h2>
        <p className="settings-section-intro">{t("preferences.intro")}</p>
        <div className="preferences-grid">
          <LanguageSelector disabled={localeSaving} onChange={changeLocale} />
          <div><p className="preference-label">{t("theme.label")}</p><ThemeSelector /></div>
        </div>
      </Card>
      <div className="settings-grid">
        <Card className="settings-card" aria-labelledby="username-settings-title">
          <h2 id="username-settings-title">{t("account.username")}</h2>
          <form onSubmit={(event) => void submitUsername(event)}>
            <label htmlFor="settings-username">{t("account.username")}</label>
            <input
              id="settings-username"
              name="username"
              value={username}
              onChange={(event) => setUsername(event.target.value)}
              pattern="[a-zA-Z0-9][a-zA-Z0-9_-]{2,31}"
              autoComplete="username"
              required
            />
            <p className="field-hint">
              {t("account.usernameHint")}
            </p>
            <Button type="submit" disabled={usernameSubmitting || username === user.username}>
              {usernameSubmitting ? t("credentials.submitting") : t("account.updateName")}
            </Button>
          </form>
        </Card>

        <Card className="settings-card" aria-labelledby="password-settings-title">
          <h2 id="password-settings-title">{t("account.passwordTitle")}</h2>
          <p className="settings-section-intro">
            {t("account.passwordHint")}
          </p>
          <form onSubmit={(event) => void submitPassword(event)}>
            <label htmlFor="settings-current-password">{t("account.currentPassword")}</label>
            <input
              id="settings-current-password"
              name="current-password"
              type="password"
              autoComplete="current-password"
              required
            />
            <label htmlFor="settings-new-password">{t("credentials.newPassword")}</label>
            <input
              id="settings-new-password"
              name="new-password"
              type="password"
              minLength={12}
              autoComplete="new-password"
              required
            />
            <label htmlFor="settings-password-confirmation">{t("credentials.confirmPassword")}</label>
            <input
              id="settings-password-confirmation"
              name="password-confirmation"
              type="password"
              minLength={12}
              autoComplete="new-password"
              required
            />
            <p className="form-message error-message" role="alert">
              {passwordError}
            </p>
            <Button type="submit" disabled={passwordSubmitting}>
              {passwordSubmitting ? t("common.processing") : t("account.updatePassword")}
            </Button>
          </form>
        </Card>
      </div>
    </section>
  );
}

function Dashboard({
  user,
  onUserChanged,
  onLogout,
  onOpenLegal,
  onSessionExpired,
}: {
  user: User;
  onUserChanged: (user: User) => void;
  onLogout: () => Promise<void>;
  onOpenLegal: (document: LegalDocument) => void;
  onSessionExpired: () => void;
}) {
  const { t } = useI18n();
  const [view, setView] = useState<"dashboard" | "settings" | AdminView>("dashboard");

  useEffect(() => {
    clearLegacyFilePathFromUrl();
  }, []);

  function openDashboard() {
    clearLegacyFilePathFromUrl();
    setView("dashboard");
  }

  return (
    <main className="app-shell">
      <a className="skip-link" href="#dashboard-content">
        {t("dashboard.skip")}
      </a>
      <header className="app-header">
        <Button
          type="button"
          className="wordmark"
          onClick={openDashboard}
          aria-label={t("dashboard.openDashboard")}
        >
          <BrandMark />
          <span>World of Seeds</span>
          <Badge className="version-badge">v{APP_VERSION}</Badge>
        </Button>
        <nav className="user-navigation" aria-label={t("dashboard.navigation")}>
          <Button
            variant="ghost"
            aria-current={view === "dashboard" ? "page" : undefined}
            onClick={openDashboard}
          >
            {t("dashboard.title")}
          </Button>
        </nav>
        <div className="header-actions">
          <AccountMenu
            user={user}
            onOpenAdmin={() => setView("admin-users")}
            onOpenSettings={() => setView("settings")}
            onLogout={onLogout}
            onSessionExpired={onSessionExpired}
          />
        </div>
      </header>
      <div id="dashboard-content" className="dashboard-content" tabIndex={-1}>
        {view === "dashboard" ? (
          <UserDashboardPage onSessionExpired={onSessionExpired} />
        ) : view === "settings" ? (
          <AccountSettingsPage
            user={user}
            onBack={openDashboard}
            onChanged={onUserChanged}
            onPasswordChanged={onSessionExpired}
            onSessionExpired={onSessionExpired}
          />
        ) : view === "admin-users" && user.is_admin ? (
          <AdminUsersPage
            onBack={openDashboard}
            onNavigate={setView}
            onSessionExpired={onSessionExpired}
          />
        ) : view === "admin-storage" && user.is_admin ? (
          <AdminStoragePage
            onBack={openDashboard}
            onNavigate={setView}
            onSessionExpired={onSessionExpired}
          />
        ) : view === "admin-services" && user.is_admin ? (
          <AdminServicesPage
            onBack={openDashboard}
            onNavigate={setView}
            onSessionExpired={onSessionExpired}
          />
        ) : view === "admin-settings" && user.is_admin ? (
          <AdminSettingsPage
            onBack={openDashboard}
            onNavigate={setView}
            onSessionExpired={onSessionExpired}
          />
        ) : (
          <UserDashboardPage onSessionExpired={onSessionExpired} />
        )}
      </div>
      <footer className="app-footer">
        <span>World of Seeds · v{APP_VERSION}</span>
        <LegalLinks onOpen={onOpenLegal} />
      </footer>
    </main>
  );
}

function UnavailableScreen({ onRetry }: { onRetry: () => void }) {
  const { t } = useI18n();
  return (
    <main className="centered-page">
      <section className="credential-card" aria-labelledby="unavailable-title">
        <BrandMark />
        <p className="eyebrow">{t("unavailable.eyebrow")}</p>
        <h1 id="unavailable-title" className="compact-title">
          {t("unavailable.title")}
        </h1>
        <p className="form-intro">
          {t("unavailable.message")}
        </p>
        <Button type="button" onClick={onRetry}>
          {t("common.retry")}
        </Button>
      </section>
    </main>
  );
}

function AppContent() {
  const { setLocale, t } = useI18n();
  const [auth, setAuth] = useState<AuthState>({ status: "loading" });
  const [legalDocument, setLegalDocument] = useState<LegalDocument | null>(null);
  const handleSessionExpired = useCallback(() => {
    clearLegacyFilePathFromUrl();
    setAuth({ status: "anonymous" });
  }, []);

  const loadSession = useCallback(() => {
    setAuth({ status: "loading" });
    void api
      .me()
      .then((user) => setAuth({ status: "authenticated", user }))
      .catch((caught: unknown) => {
        if (caught instanceof ApiError && caught.status === 401) {
          handleSessionExpired();
          return;
        }
        setAuth({ status: "unavailable" });
      });
  }, [handleSessionExpired]);

  useEffect(() => {
    loadSession();
  }, [loadSession]);

  useEffect(() => {
    if (auth.status === "authenticated") setLocale(auth.user.preferred_locale ?? "fr");
  }, [auth, setLocale]);

  async function logout() {
    await api.logout();
    clearLegacyFilePathFromUrl();
    setAuth({ status: "anonymous" });
  }

  function renderScreen() {
    if (legalDocument !== null) {
      return (
        <LegalPage
          document={legalDocument}
          onBack={() => setLegalDocument(null)}
          onOpen={setLegalDocument}
        />
      );
    }

    if (auth.status === "loading") {
      return (
        <main className="loading-page" aria-live="polite" aria-busy="true">
          <StateMessage tone="loading">{t("app.opening")}</StateMessage>
        </main>
      );
    }
    if (auth.status === "unavailable") {
      return <UnavailableScreen onRetry={loadSession} />;
    }
    if (auth.status === "anonymous") {
      return (
        <LoginScreen
          onLogin={(user) => setAuth({ status: "authenticated", user })}
          onOpenLegal={setLegalDocument}
        />
      );
    }
    if (auth.user.must_change_credentials) {
      return (
        <CredentialChangeScreen
          user={auth.user}
          onChanged={(user) => setAuth({ status: "authenticated", user })}
          onOpenLegal={setLegalDocument}
        />
      );
    }
    return (
      <Dashboard
        user={auth.user}
        onUserChanged={(user) => setAuth({ status: "authenticated", user })}
        onLogout={logout}
        onOpenLegal={setLegalDocument}
        onSessionExpired={handleSessionExpired}
      />
    );
  }
  return <ThemeProvider user={auth.status === "authenticated" ? auth.user : null}>{renderScreen()}</ThemeProvider>;
}

export function App() {
  return (
    <I18nProvider>
      <FeedbackProvider>
        <AppContent />
      </FeedbackProvider>
    </I18nProvider>
  );
}
