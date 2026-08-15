import {
  type FormEvent,
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";

import { api, ApiError, type StorageUsage, type User } from "./api/client";
import { type AdminView } from "./features/admin/AdminPageShell";
import { AdminStoragePage } from "./features/admin/AdminStoragePage";
import { AdminServicesPage } from "./features/admin/AdminServicesPage";
import { AdminTrashPage } from "./features/admin/AdminTrashPage";
import { AdminUsersPage } from "./features/admin/AdminUsersPage";
import { FileBrowser } from "./features/files/FileBrowser";
import { TrashBrowser } from "./features/files/TrashBrowser";
import { AccountMenuIcon, BackIcon, BrandIcon } from "./components/icons";
import {
  LegalLinks,
  LegalPage,
  type LegalDocument,
} from "./components/LegalPage";
import { formatBytes } from "./utils/format";
import { APP_VERSION } from "./version";

type AuthState =
  | { status: "loading" }
  | { status: "anonymous" }
  | { status: "unavailable" }
  | { status: "authenticated"; user: User };

function clearFilePathFromUrl() {
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
    <button
      type="button"
      className={`service-health ${health}`}
      onClick={() => void check()}
      disabled={checking}
      aria-label="Vérifier l’état du service"
    >
      <span className="service-health-dot" aria-hidden="true" />
      <span aria-live="polite">
        {checking
          ? "Vérification en cours…"
          : health === "healthy"
            ? "Tous les services fonctionnent normalement."
            : "Le service est momentanément interrompu."}
      </span>
    </button>
  );
}

function LoginScreen({
  onLogin,
  onOpenLegal,
}: {
  onLogin: (user: User) => void;
  onOpenLegal: (document: LegalDocument) => void;
}) {
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    setSubmitting(true);
    setError("");
    try {
      const user = await api.login(String(form.get("username")), String(form.get("password")));
      onLogin(user);
    } catch (caught) {
      setError(
        caught instanceof ApiError
          ? {
              401: "Identifiants incorrects ou compte indisponible.",
              429: "Trop de tentatives. Réessaie dans quelques minutes.",
            }[caught.status] ?? "Le service est temporairement indisponible."
          : "Le service est temporairement indisponible.",
      );
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main className="login-page">
      <section className="brand-panel" aria-labelledby="brand-title">
        <BrandMark />
        <p className="eyebrow">Espace privé</p>
        <h1 id="brand-title">World of Seeds</h1>
        <p className="brand-copy">
          Un espace discret où chaque graine déposée trouve sa place, grandit puis rejoint ta
          collection.
        </p>
        <ServiceHealth />
      </section>

      <section className="form-panel" aria-labelledby="login-title">
        <div className="form-card">
          <p className="eyebrow">Connexion</p>
          <h2 id="login-title">Bienvenue</h2>
          <p className="form-intro">Saisissez les identifiants fournis par l’administrateur.</p>

          <form onSubmit={handleSubmit}>
            <label htmlFor="username">Nom d’utilisateur</label>
            <input
              id="username"
              name="username"
              autoComplete="username"
              aria-describedby="login-error"
              aria-invalid={error !== ""}
              required
            />

            <label htmlFor="password">Mot de passe</label>
            <input
              id="password"
              name="password"
              type="password"
              autoComplete="current-password"
              aria-describedby="login-error"
              aria-invalid={error !== ""}
              required
            />

            <button type="submit" disabled={submitting}>
              {submitting ? "Connexion…" : "Se connecter"}
            </button>
            <p id="login-error" className="form-message error-message" role="alert">
              {error}
            </p>
          </form>
        </div>
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
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const newPassword = String(form.get("new-password"));
    if (newPassword !== String(form.get("password-confirmation"))) {
      setError("Les deux nouveaux mots de passe ne correspondent pas.");
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
      setError(
        caught instanceof ApiError
          ? {
              401: "Le mot de passe initial est incorrect.",
              409: "Ce nom d’utilisateur est indisponible ou l’espace ne peut pas être renommé.",
            }[caught.status] ?? "Impossible de modifier le compte pour le moment."
          : "Impossible de modifier le compte pour le moment.",
      );
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main className="centered-page">
      <section className="credential-card" aria-labelledby="credential-title">
        <BrandMark />
        <p className="eyebrow">Première connexion</p>
        <h1 id="credential-title" className="compact-title">
          Personnalise ton accès
        </h1>
        <p className="form-intro">
          Remplace les identifiants générés par ceux que tu souhaites utiliser.
        </p>
        <form onSubmit={handleSubmit}>
          <label htmlFor="current-password">Mot de passe initial</label>
          <input
            id="current-password"
            name="current-password"
            type="password"
            autoComplete="current-password"
            aria-describedby="credential-error"
            aria-invalid={error !== ""}
            required
          />
          <label htmlFor="new-username">Nouveau nom d’utilisateur</label>
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
            3–32 caractères : lettres, chiffres, _ ou -.
          </p>
          <label htmlFor="new-password">Nouveau mot de passe</label>
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
          <label htmlFor="password-confirmation">Confirmer le mot de passe</label>
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
          <button type="submit" disabled={submitting}>
            {submitting ? "Enregistrement…" : "Enregistrer mes identifiants"}
          </button>
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
  const [open, setOpen] = useState(false);
  const [logoutError, setLogoutError] = useState("");
  const [loggingOut, setLoggingOut] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;

    function closeOnOutsideClick(event: MouseEvent) {
      if (!containerRef.current?.contains(event.target as Node)) setOpen(false);
    }

    function closeOnEscape(event: KeyboardEvent) {
      if (event.key === "Escape") setOpen(false);
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
    setLogoutError("");
    try {
      await onLogout();
    } catch (caught) {
      if (caught instanceof ApiError && caught.status === 401) {
        onSessionExpired();
        return;
      }
      setLogoutError("La déconnexion a échoué. Ta session est toujours active.");
    } finally {
      setLoggingOut(false);
    }
  }

  return (
    <div className="account-menu" ref={containerRef}>
      <button
        type="button"
        className="account-trigger"
        aria-label="Ouvrir le menu du compte"
        aria-expanded={open}
        aria-controls="account-dropdown"
        onClick={() => setOpen((current) => !current)}
      >
        <span className="account-avatar" aria-hidden="true">
          {user.username.slice(0, 1).toUpperCase()}
        </span>
        <strong>{user.username}</strong>
        <AccountMenuIcon className="account-trigger-icon" />
      </button>
      {open && (
        <div id="account-dropdown" className="account-dropdown">
          {user.is_admin && (
            <button
              type="button"
              className="account-dropdown-item"
              onClick={() => {
                setOpen(false);
                onOpenAdmin();
              }}
            >
              Administration
            </button>
          )}
          <button
            type="button"
            className="account-dropdown-item"
            onClick={() => {
              setOpen(false);
              onOpenSettings();
            }}
          >
            Paramètres du compte
          </button>
          <div className="account-dropdown-separator" />
          <button
            type="button"
            className="account-dropdown-item logout-item"
            onClick={() => void handleLogout()}
            disabled={loggingOut}
          >
            {loggingOut ? "Déconnexion…" : "Déconnexion"}
          </button>
          {logoutError !== "" && (
            <p className="logout-error" role="alert">
              {logoutError}
            </p>
          )}
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
  const [usernameError, setUsernameError] = useState("");
  const [usernameNotice, setUsernameNotice] = useState("");
  const [usernameSubmitting, setUsernameSubmitting] = useState(false);
  const [passwordError, setPasswordError] = useState("");
  const [passwordSubmitting, setPasswordSubmitting] = useState(false);
  const [username, setUsername] = useState(user.username);

  async function submitUsername(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setUsernameSubmitting(true);
    setUsernameError("");
    setUsernameNotice("");
    try {
      const updated = await api.changeUsername(username);
      onChanged(updated);
      setUsername(updated.username);
      setUsernameNotice("Ton nom d’utilisateur a été mis à jour.");
    } catch (caught) {
      if (caught instanceof ApiError && caught.status === 401) {
        onSessionExpired();
        return;
      }
      setUsernameError(
        caught instanceof ApiError && caught.status === 409
          ? "Ce nom d’utilisateur est indisponible ou ton espace ne peut pas être renommé."
          : "Impossible de modifier le nom d’utilisateur pour le moment.",
      );
    } finally {
      setUsernameSubmitting(false);
    }
  }

  async function submitPassword(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const newPassword = String(form.get("new-password"));
    if (newPassword !== String(form.get("password-confirmation"))) {
      setPasswordError("Les deux nouveaux mots de passe ne correspondent pas.");
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
        caught.message === "Not authenticated"
      ) {
        onSessionExpired();
        return;
      }
      setPasswordError(
        caught instanceof ApiError && caught.status === 401
          ? "Le mot de passe actuel est incorrect."
          : "Impossible de modifier le mot de passe pour le moment.",
      );
    } finally {
      setPasswordSubmitting(false);
    }
  }

  return (
    <section className="settings-page" aria-labelledby="account-settings-title">
      <button type="button" className="back-button" onClick={onBack}>
        <BackIcon /> Retour aux fichiers
      </button>
      <div className="settings-header">
        <p className="eyebrow">Compte</p>
        <h1 id="account-settings-title">Paramètres du compte</h1>
        <p className="settings-intro">
          Mets à jour ton identité et sécurise ton accès depuis deux formulaires indépendants.
        </p>
      </div>
      <div className="settings-grid">
        <section className="settings-card" aria-labelledby="username-settings-title">
          <h2 id="username-settings-title">Nom d’utilisateur</h2>
          <p className="settings-section-intro">
            Le dossier personnel sera renommé en même temps, sans déplacer son contenu.
          </p>
          <form onSubmit={(event) => void submitUsername(event)}>
            <label htmlFor="settings-username">Nom d’utilisateur</label>
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
              3–32 caractères : lettres, chiffres, tiret ou tiret bas.
            </p>
            <p className="form-message error-message" role="alert">
              {usernameError}
            </p>
            {usernameNotice !== "" && (
              <p className="settings-notice" role="status">
                {usernameNotice}
              </p>
            )}
            <button type="submit" disabled={usernameSubmitting || username === user.username}>
              {usernameSubmitting ? "Enregistrement…" : "Mettre à jour le nom"}
            </button>
          </form>
        </section>

        <section className="settings-card" aria-labelledby="password-settings-title">
          <h2 id="password-settings-title">Mot de passe</h2>
          <p className="settings-section-intro">
            Toutes tes sessions seront fermées après la modification.
          </p>
          <form onSubmit={(event) => void submitPassword(event)}>
            <label htmlFor="settings-current-password">Mot de passe actuel</label>
            <input
              id="settings-current-password"
              name="current-password"
              type="password"
              autoComplete="current-password"
              required
            />
            <label htmlFor="settings-new-password">Nouveau mot de passe</label>
            <input
              id="settings-new-password"
              name="new-password"
              type="password"
              minLength={12}
              autoComplete="new-password"
              required
            />
            <label htmlFor="settings-password-confirmation">Confirmer le mot de passe</label>
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
            <button type="submit" disabled={passwordSubmitting}>
              {passwordSubmitting ? "Modification…" : "Modifier le mot de passe"}
            </button>
          </form>
        </section>
      </div>
    </section>
  );
}

function StorageCard({ storage }: { storage: StorageUsage | null }) {
  if (storage === null) {
    return <div className="storage-card storage-card-loading" aria-hidden="true" />;
  }
  const percent =
    storage.total === 0 ? 0 : Math.min((storage.used / storage.total) * 100, 100);
  return (
    <div className="storage-card" role="group" aria-label="Utilisation du stockage">
      <div className="storage-copy">
        <span>{formatBytes(storage.used)} utilisés</span>
        <strong>{formatBytes(storage.available)} disponibles</strong>
      </div>
      <progress
        className="storage-track"
        max={100}
        value={percent}
        aria-label={`${percent.toFixed(0)} % du stockage utilisé`}
      >
        {percent.toFixed(0)} %
      </progress>
      <span className="storage-total">Capacité totale : {formatBytes(storage.total)}</span>
    </div>
  );
}

function FilesWorkspace({
  onFilesChanged,
  onSessionExpired,
  revision,
}: {
  onFilesChanged: () => void;
  onSessionExpired: () => void;
  revision: number;
}) {
  const [activeView, setActiveView] = useState<"files" | "trash">("files");
  const [storage, setStorage] = useState<StorageUsage | null>(null);

  return (
    <section className="files-workspace" aria-labelledby="files-page-title">
      <header className="files-page-header">
        <div>
          <p className="eyebrow">Espace personnel</p>
          <h1 id="files-page-title">Mes fichiers</h1>
        </div>
        <StorageCard storage={storage} />
      </header>
      <div className="file-view-tabs" role="group" aria-label="Vues de l’espace personnel">
        <button
          type="button"
          aria-pressed={activeView === "files"}
          onClick={() => setActiveView("files")}
        >
          Mes fichiers
        </button>
        <button
          type="button"
          aria-pressed={activeView === "trash"}
          onClick={() => setActiveView("trash")}
        >
          Corbeille
        </button>
      </div>
      {activeView === "files" ? (
        <div>
          <FileBrowser
            onFilesChanged={onFilesChanged}
            onSessionExpired={onSessionExpired}
            onStorageChanged={setStorage}
            revision={revision}
          />
        </div>
      ) : (
        <div>
          <TrashBrowser
            onFilesChanged={onFilesChanged}
            onSessionExpired={onSessionExpired}
            revision={revision}
          />
        </div>
      )}
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
  const [view, setView] = useState<"files" | "settings" | AdminView>("files");
  const [filesRevision, setFilesRevision] = useState(0);
  const [filesHomeKey, setFilesHomeKey] = useState(0);
  const handleFilesChanged = useCallback(() => {
    setFilesRevision((value) => value + 1);
  }, []);

  function openFilesHome() {
    clearFilePathFromUrl();
    setView("files");
    setFilesHomeKey((value) => value + 1);
  }

  return (
    <main className="app-shell">
      <a className="skip-link" href="#dashboard-content">
        Aller au contenu principal
      </a>
      <header className="app-header">
        <button
          type="button"
          className="wordmark"
          onClick={openFilesHome}
          aria-label="Ouvrir Mes fichiers"
        >
          <BrandMark />
          <span>World of Seeds</span>
          <span className="version-badge">v{APP_VERSION}</span>
        </button>
        <AccountMenu
          user={user}
          onOpenAdmin={() => setView("admin-users")}
          onOpenSettings={() => setView("settings")}
          onLogout={onLogout}
          onSessionExpired={onSessionExpired}
        />
      </header>
      <div id="dashboard-content" className="dashboard-content" tabIndex={-1}>
        {view === "settings" ? (
          <AccountSettingsPage
            user={user}
            onBack={openFilesHome}
            onChanged={onUserChanged}
            onPasswordChanged={onSessionExpired}
            onSessionExpired={onSessionExpired}
          />
        ) : view === "admin-users" && user.is_admin ? (
          <AdminUsersPage
            onBack={openFilesHome}
            onNavigate={setView}
            onSessionExpired={onSessionExpired}
          />
        ) : view === "admin-storage" && user.is_admin ? (
          <AdminStoragePage
            onBack={openFilesHome}
            onNavigate={setView}
            onSessionExpired={onSessionExpired}
          />
        ) : view === "admin-services" && user.is_admin ? (
          <AdminServicesPage
            onBack={openFilesHome}
            onNavigate={setView}
            onSessionExpired={onSessionExpired}
          />
        ) : view === "admin-trash" && user.is_admin ? (
          <AdminTrashPage
            onBack={openFilesHome}
            onNavigate={setView}
            onSessionExpired={onSessionExpired}
          />
        ) : (
          <FilesWorkspace
            key={filesHomeKey}
            onFilesChanged={handleFilesChanged}
            onSessionExpired={onSessionExpired}
            revision={filesRevision}
          />
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
  return (
    <main className="centered-page">
      <section className="credential-card" aria-labelledby="unavailable-title">
        <BrandMark />
        <p className="eyebrow">Service indisponible</p>
        <h1 id="unavailable-title" className="compact-title">
          Connexion impossible
        </h1>
        <p className="form-intro">
          Le serveur ou la base de données ne répond pas. Aucun identifiant n’a été refusé.
        </p>
        <button type="button" onClick={onRetry}>
          Réessayer
        </button>
      </section>
    </main>
  );
}

export function App() {
  const [auth, setAuth] = useState<AuthState>({ status: "loading" });
  const [legalDocument, setLegalDocument] = useState<LegalDocument | null>(null);
  const handleSessionExpired = useCallback(() => {
    clearFilePathFromUrl();
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

  async function logout() {
    await api.logout();
    clearFilePathFromUrl();
    setAuth({ status: "anonymous" });
  }

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
        Ouverture de l’espace privé…
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
