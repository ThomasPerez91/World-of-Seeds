import { type FormEvent, useState } from "react";

export function App() {
  const [message, setMessage] = useState("");

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setMessage("L’authentification sera activée dans la prochaine étape.");
  }

  return (
    <main className="login-page">
      <section className="brand-panel" aria-labelledby="brand-title">
        <div className="brand-mark" aria-hidden="true">
          <svg viewBox="0 0 48 48" role="img">
            <path d="M24 40V21" />
            <path d="M24 27C14 27 9 20 9 10c10 0 15 7 15 17Z" />
            <path d="M24 22c0-9 6-14 15-14 0 9-6 14-15 14Z" />
          </svg>
        </div>
        <p className="eyebrow">Espace privé</p>
        <h1 id="brand-title">World of Seeds</h1>
        <p className="brand-copy">
          Vos fichiers, vos téléchargements et votre espace seedbox dans une interface unique.
        </p>
        <div className="privacy-note">
          <span className="privacy-dot" aria-hidden="true" />
          Accès restreint et connexion chiffrée par tunnel
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
              type="text"
              autoComplete="username"
              required
            />

            <label htmlFor="password">Mot de passe</label>
            <input
              id="password"
              name="password"
              type="password"
              autoComplete="current-password"
              required
            />

            <button type="submit">Se connecter</button>
            <p className="form-message" role="status" aria-live="polite">
              {message}
            </p>
          </form>
        </div>
      </section>
    </main>
  );
}
