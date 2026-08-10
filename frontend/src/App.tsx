import { type FormEvent, useEffect, useState } from "react";

import { api, ApiError, type TemporaryCredentials, type User } from "./api/client";

type AuthState =
  | { status: "loading" }
  | { status: "anonymous" }
  | { status: "authenticated"; user: User };

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
        caught instanceof ApiError && caught.status === 429
          ? "Trop de tentatives. Réessaie dans quelques minutes."
          : "Identifiants incorrects ou compte indisponible.",
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
            <input id="username" name="username" autoComplete="username" required />

            <label htmlFor="password">Mot de passe</label>
            <input
              id="password"
              name="password"
              type="password"
              autoComplete="current-password"
              required
            />

            <button type="submit" disabled={submitting}>
              {submitting ? "Connexion…" : "Se connecter"}
            </button>
            <p className="form-message error-message" role="alert">
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
      setError(caught instanceof ApiError ? caught.message : "Impossible de modifier le compte.");
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
            required
          />
          <label htmlFor="new-username">Nouveau nom d’utilisateur</label>
          <input
            id="new-username"
            name="username"
            defaultValue={user.username}
            pattern="[a-z0-9][a-z0-9_-]{2,31}"
            autoComplete="username"
            required
          />
          <p className="field-hint">3–32 caractères : lettres minuscules, chiffres, _ ou -.</p>
          <label htmlFor="new-password">Nouveau mot de passe</label>
          <input
            id="new-password"
            name="new-password"
            type="password"
            minLength={12}
            autoComplete="new-password"
            required
          />
          <label htmlFor="password-confirmation">Confirmer le mot de passe</label>
          <input
            id="password-confirmation"
            name="password-confirmation"
            type="password"
            minLength={12}
            autoComplete="new-password"
            required
          />
          <button type="submit" disabled={submitting}>
            {submitting ? "Enregistrement…" : "Enregistrer mes identifiants"}
          </button>
          <p className="form-message error-message" role="alert">
            {error}
          </p>
        </form>
      </section>
    </main>
  );
}

function AdminPanel() {
  const [users, setUsers] = useState<User[]>([]);
  const [credentials, setCredentials] = useState<TemporaryCredentials | null>(null);
  const [expiresInDays, setExpiresInDays] = useState(7);
  const [error, setError] = useState("");
  const [generating, setGenerating] = useState(false);

  useEffect(() => {
    void api.listUsers().then(setUsers).catch(() => setError("Impossible de charger les comptes."));
  }, []);

  async function generateUser() {
    setGenerating(true);
    setError("");
    setCredentials(null);
    try {
      const generated = await api.createTemporaryUser(expiresInDays);
      setCredentials(generated);
      setUsers((current) => [generated.user, ...current]);
    } catch {
      setError("Impossible de générer le compte temporaire.");
    } finally {
      setGenerating(false);
    }
  }

  async function copy(value: string) {
    await navigator.clipboard.writeText(value);
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
          <button className="compact-button" onClick={generateUser} disabled={generating}>
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
            <button className="text-button" onClick={() => copy(credentials.user.username)}>
              Copier
            </button>
          </div>
          <div className="credential-row">
            <span>Mot de passe</span>
            <code>{credentials.temporary_password}</code>
            <button className="text-button" onClick={() => copy(credentials.temporary_password)}>
              Copier
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
                  ? "Administrateur"
                  : account.must_change_credentials
                    ? "Invitation en attente"
                    : "Utilisateur actif"}
              </span>
            </div>
            <span className={account.is_active ? "status-pill" : "status-pill inactive"}>
              {account.is_active ? "Actif" : "Désactivé"}
            </span>
          </div>
        ))}
      </div>
    </section>
  );
}

function Dashboard({ user, onLogout }: { user: User; onLogout: () => Promise<void> }) {
  const [logoutError, setLogoutError] = useState("");
  const [loggingOut, setLoggingOut] = useState(false);

  async function handleLogout() {
    setLoggingOut(true);
    setLogoutError("");
    try {
      await onLogout();
    } catch {
      setLogoutError("La déconnexion a échoué. Ta session est toujours active.");
    } finally {
      setLoggingOut(false);
    }
  }

  return (
    <main className="app-shell">
      <header className="app-header">
        <div className="wordmark">
          <BrandMark />
          <span>World of Seeds</span>
        </div>
        <div className="account-menu">
          <span>{user.username}</span>
          <button
            className="secondary-button"
            onClick={() => void handleLogout()}
            disabled={loggingOut}
          >
            {loggingOut ? "Déconnexion…" : "Déconnexion"}
          </button>
          <p className="logout-error" role="alert">
            {logoutError}
          </p>
        </div>
      </header>
      <div className="dashboard-content">
        <section className="welcome-card">
          <p className="eyebrow">Tableau de bord</p>
          <h1 className="dashboard-title">Bonjour, {user.username}</h1>
          <p>La navigation dans les fichiers sera ajoutée dans la prochaine étape.</p>
        </section>
        {user.is_admin && <AdminPanel />}
      </div>
    </main>
  );
}

export function App() {
  const [auth, setAuth] = useState<AuthState>({ status: "loading" });

  useEffect(() => {
    void api
      .me()
      .then((user) => setAuth({ status: "authenticated", user }))
      .catch(() => setAuth({ status: "anonymous" }));
  }, []);

  async function logout() {
    await api.logout();
    setAuth({ status: "anonymous" });
  }

  if (auth.status === "loading") {
    return <main className="loading-page">Ouverture de l’espace privé…</main>;
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
  return <Dashboard user={auth.user} onLogout={logout} />;
}
