import {
  type FormEvent,
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";

import { api, ApiError, type TemporaryCredentials, type User } from "./api/client";
import { FileBrowser } from "./features/files/FileBrowser";
import { TrashBrowser } from "./features/files/TrashBrowser";

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
      <svg viewBox="0 0 48 48" role="img">
        <path d="M24 40V21" />
        <path d="M24 27C14 27 9 20 9 10c10 0 15 7 15 17Z" />
        <path d="M24 22c0-9 6-14 15-14 0 9-6 14-15 14Z" />
      </svg>
    </div>
  );
}

function LoginScreen({ onLogin }: { onLogin: (user: User) => void }) {
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
          Vos fichiers, vos téléchargements et votre espace seedbox dans une interface unique.
        </p>
        <div className="privacy-note">
          <span className="privacy-dot" aria-hidden="true" />
          Accès restreint et connexion par tunnel privé
        </div>
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
      </section>
    </main>
  );
}

function CredentialChangeScreen({ user, onChanged }: { user: User; onChanged: (user: User) => void }) {
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
              401: "Le mot de passe temporaire est incorrect.",
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
          Les identifiants temporaires ne seront plus valables après cette étape.
        </p>
        <form onSubmit={handleSubmit}>
          <label htmlFor="current-password">Mot de passe temporaire</label>
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
      </section>
    </main>
  );
}

function AdminPanel({ onSessionExpired }: { onSessionExpired: () => void }) {
  const [users, setUsers] = useState<User[]>([]);
  const [credentials, setCredentials] = useState<TemporaryCredentials | null>(null);
  const [expiresInDays, setExpiresInDays] = useState(7);
  const [error, setError] = useState("");
  const [generating, setGenerating] = useState(false);

  useEffect(() => {
    void api
      .listUsers()
      .then(setUsers)
      .catch((caught: unknown) => {
        if (caught instanceof ApiError && caught.status === 401) {
          onSessionExpired();
          return;
        }
        setError("Impossible de charger les comptes.");
      });
  }, [onSessionExpired]);

  async function generateUser() {
    setGenerating(true);
    setError("");
    setCredentials(null);
    try {
      const generated = await api.createTemporaryUser(expiresInDays);
      setCredentials(generated);
      setUsers((current) => [generated.user, ...current]);
    } catch (caught) {
      if (caught instanceof ApiError && caught.status === 401) {
        onSessionExpired();
        return;
      }
      setError("Impossible de générer le compte temporaire.");
    } finally {
      setGenerating(false);
    }
  }

  async function copy(value: string) {
    try {
      if (navigator.clipboard === undefined) {
        throw new Error("Clipboard API unavailable");
      }
      await navigator.clipboard.writeText(value);
    } catch {
      setError("Copie automatique indisponible. Sélectionne la valeur manuellement.");
    }
  }

  return (
    <section className="admin-section" aria-labelledby="admin-title">
      <div className="section-heading">
        <div>
          <p className="eyebrow">Administration</p>
          <h2 id="admin-title">Accès temporaires</h2>
        </div>
        <div className="generator-controls">
          <label htmlFor="expiry">Validité</label>
          <select
            id="expiry"
            value={expiresInDays}
            onChange={(event) => setExpiresInDays(Number(event.target.value))}
          >
            <option value={1}>1 jour</option>
            <option value={3}>3 jours</option>
            <option value={7}>7 jours</option>
            <option value={14}>14 jours</option>
            <option value={30}>30 jours</option>
          </select>
          <button
            type="button"
            className="compact-button"
            onClick={generateUser}
            disabled={generating}
          >
            {generating ? "Génération…" : "Générer un utilisateur"}
          </button>
        </div>
      </div>

      {credentials !== null && (
        <div className="credential-reveal" role="status">
          <strong>À transmettre maintenant — le mot de passe ne sera plus affiché.</strong>
          <div className="credential-row">
            <span>Utilisateur</span>
            <code>{credentials.user.username}</code>
            <button
              type="button"
              className="text-button"
              onClick={() => void copy(credentials.user.username)}
            >
              Copier
            </button>
          </div>
          <div className="credential-row">
            <span>Mot de passe</span>
            <code>{credentials.temporary_password}</code>
            <button
              type="button"
              className="text-button"
              onClick={() => void copy(credentials.temporary_password)}
            >
              Copier
            </button>
          </div>
        </div>
      )}

      <p className="form-message error-message" role="alert">
        {error}
      </p>
      <div className="user-list">
        {users.map((account) => {
          const expired =
            account.expires_at !== null && new Date(account.expires_at).getTime() <= Date.now();
          const available = account.is_active && !expired;
          return (
            <div className="user-row" key={account.id}>
              <div className="avatar" aria-hidden="true">
                {account.username.slice(0, 1).toUpperCase()}
              </div>
              <div>
                <strong>{account.username}</strong>
                <span>
                  {account.is_admin
                    ? "Administrateur"
                    : account.must_change_credentials
                      ? "Invitation en attente"
                      : "Utilisateur actif"}
                </span>
              </div>
              <span className={available ? "status-pill" : "status-pill inactive"}>
                {expired ? "Expiré" : account.is_active ? "Actif" : "Désactivé"}
              </span>
            </div>
          );
        })}
      </div>
    </section>
  );
}

function AccountMenu({
  user,
  onOpenSettings,
  onLogout,
  onSessionExpired,
}: {
  user: User;
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
      <div className="account-identity">
        <span className="account-avatar" aria-hidden="true">
          {user.username.slice(0, 1).toUpperCase()}
        </span>
        <strong>{user.username}</strong>
      </div>
      <button
        type="button"
        className="account-settings-trigger"
        aria-label="Ouvrir le menu du compte"
        aria-expanded={open}
        aria-controls="account-dropdown"
        onClick={() => setOpen((current) => !current)}
      >
        <svg viewBox="0 0 24 24" aria-hidden="true">
          <path d="M12 8.2a3.8 3.8 0 1 0 0 7.6 3.8 3.8 0 0 0 0-7.6Z" />
          <path d="M19.4 13.5c.1-.5.1-1 0-1.5l2-1.6-2-3.4-2.5 1a8 8 0 0 0-1.3-.8L15.2 4h-4l-.4 3.2c-.5.2-.9.5-1.3.8L7 7 5 10.4 7 12c-.1.5-.1 1 0 1.5l-2 1.6 2 3.4 2.5-1c.4.3.8.6 1.3.8l.4 3.2h4l.4-3.2c.5-.2.9-.5 1.3-.8l2.5 1 2-3.4-2-1.6Z" />
        </svg>
      </button>
      {open && (
        <div id="account-dropdown" className="account-dropdown">
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
  onSessionExpired,
}: {
  user: User;
  onBack: () => void;
  onChanged: (user: User) => void;
  onSessionExpired: () => void;
}) {
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [username, setUsername] = useState(user.username);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const formElement = event.currentTarget;
    const form = new FormData(formElement);
    const newPassword = String(form.get("new-password"));
    if (newPassword !== String(form.get("password-confirmation"))) {
      setError("Les deux nouveaux mots de passe ne correspondent pas.");
      return;
    }

    setSubmitting(true);
    setError("");
    setNotice("");
    try {
      const updated = await api.changeCredentials(
        String(form.get("current-password")),
        String(form.get("username")),
        newPassword,
      );
      onChanged(updated);
      setNotice("Tes identifiants ont été mis à jour.");
      formElement.reset();
      setUsername(updated.username);
    } catch (caught) {
      if (
        caught instanceof ApiError &&
        caught.status === 401 &&
        caught.message === "Not authenticated"
      ) {
        onSessionExpired();
        return;
      }
      if (caught instanceof ApiError && caught.status === 401) {
        setError("Le mot de passe actuel est incorrect.");
      } else if (caught instanceof ApiError && caught.status === 409) {
        setError("Ce nom d’utilisateur est indisponible.");
      } else {
        setError("Impossible de modifier le compte pour le moment.");
      }
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <section className="settings-page" aria-labelledby="account-settings-title">
      <button type="button" className="back-button" onClick={onBack}>
        <span aria-hidden="true">←</span> Retour aux fichiers
      </button>
      <div className="settings-card">
        <p className="eyebrow">Compte</p>
        <h1 id="account-settings-title">Paramètres du compte</h1>
        <p className="settings-intro">
          Le changement du nom d’utilisateur renomme également ton espace de stockage.
        </p>
        <form onSubmit={(event) => void submit(event)}>
          <label htmlFor="settings-current-password">Mot de passe actuel</label>
          <input
            id="settings-current-password"
            name="current-password"
            type="password"
            autoComplete="current-password"
            required
          />
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
            {error}
          </p>
          {notice !== "" && (
            <p className="settings-notice" role="status">
              {notice}
            </p>
          )}
          <button type="submit" disabled={submitting}>
            {submitting ? "Enregistrement…" : "Enregistrer les modifications"}
          </button>
        </form>
      </div>
    </section>
  );
}

function Dashboard({
  user,
  onUserChanged,
  onLogout,
  onSessionExpired,
}: {
  user: User;
  onUserChanged: (user: User) => void;
  onLogout: () => Promise<void>;
  onSessionExpired: () => void;
}) {
  const [view, setView] = useState<"files" | "settings">("files");
  const [filesRevision, setFilesRevision] = useState(0);
  const handleFilesChanged = useCallback(() => {
    setFilesRevision((value) => value + 1);
  }, []);

  return (
    <main className="app-shell">
      <a className="skip-link" href="#dashboard-content">
        Aller au contenu principal
      </a>
      <header className="app-header">
        <div className="wordmark">
          <BrandMark />
          <span>World of Seeds</span>
        </div>
        <AccountMenu
          user={user}
          onOpenSettings={() => setView("settings")}
          onLogout={onLogout}
          onSessionExpired={onSessionExpired}
        />
      </header>
      <div id="dashboard-content" className="dashboard-content" tabIndex={-1}>
        {view === "settings" ? (
          <AccountSettingsPage
            user={user}
            onBack={() => setView("files")}
            onChanged={onUserChanged}
            onSessionExpired={onSessionExpired}
          />
        ) : (
          <>
            <FileBrowser
              onFilesChanged={handleFilesChanged}
              onSessionExpired={onSessionExpired}
              revision={filesRevision}
            />
            <TrashBrowser
              onFilesChanged={handleFilesChanged}
              onSessionExpired={onSessionExpired}
              revision={filesRevision}
            />
            {user.is_admin && <AdminPanel onSessionExpired={onSessionExpired} />}
          </>
        )}
      </div>
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
    return <LoginScreen onLogin={(user) => setAuth({ status: "authenticated", user })} />;
  }
  if (auth.user.must_change_credentials) {
    return (
      <CredentialChangeScreen
        user={auth.user}
        onChanged={(user) => setAuth({ status: "authenticated", user })}
      />
    );
  }
  return (
    <Dashboard
      user={auth.user}
      onUserChanged={(user) => setAuth({ status: "authenticated", user })}
      onLogout={logout}
      onSessionExpired={handleSessionExpired}
    />
  );
}
