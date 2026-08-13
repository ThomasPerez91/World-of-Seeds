import { useEffect, useState } from "react";

import { api, ApiError, type GeneratedCredentials, type User } from "../../api/client";
import { FileDialog } from "../files/FileDialog";
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
        setError("Impossible de charger les comptes.");
      });
  }, [onSessionExpired]);

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
      setError("Impossible de générer le compte.");
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
      setError("Impossible de modifier l’accès de cet utilisateur.");
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
      setDeletionError("Impossible de supprimer l’accès de cet utilisateur.");
    } finally {
      setUpdatingUserId(null);
    }
  }

  async function copy(value: string) {
    try {
      if (navigator.clipboard === undefined) throw new Error("Clipboard API unavailable");
      await navigator.clipboard.writeText(value);
    } catch {
      setError("Copie automatique indisponible. Sélectionne la valeur manuellement.");
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
            <p className="eyebrow">Accès</p>
            <h2 id="admin-users-title">Comptes utilisateurs</h2>
            <p className="section-intro">
              Les identifiants générés restent valables jusqu’à leur personnalisation.
            </p>
          </div>
          <div className="generator-controls">
            <button
              type="button"
              className="compact-button"
              onClick={() => void generateUser()}
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
              <code>{credentials.initial_password}</code>
              <button
                type="button"
                className="text-button"
                onClick={() => void copy(credentials.initial_password)}
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
                      ? "Personnalisation en attente"
                      : "Utilisateur configuré"}
                </span>
              </div>
              <div className="user-row-actions">
                <span className={account.is_active ? "status-pill" : "status-pill inactive"}>
                  {account.is_active ? "Actif" : "Suspendu"}
                </span>
                {!account.is_admin && (
                  <>
                    <button
                      type="button"
                      className="secondary-button compact-button"
                      aria-label={`${account.is_active ? "Suspendre" : "Réactiver"} ${account.username}`}
                      disabled={updatingUserId === account.id}
                      onClick={() => void setActive(account, !account.is_active)}
                    >
                      {account.is_active ? "Suspendre" : "Réactiver"}
                    </button>
                    <button
                      type="button"
                      className="danger-outline-button compact-button"
                      aria-label={`Supprimer l’accès de ${account.username}`}
                      disabled={updatingUserId === account.id}
                      onClick={() => {
                        setDeletionError("");
                        setPendingDeletion(account);
                      }}
                    >
                      Supprimer l’accès
                    </button>
                  </>
                )}
              </div>
            </div>
          ))}
        </div>
        {pendingDeletion !== null && (
          <FileDialog
            eyebrow="Administration"
            title={`Supprimer l’accès de ${pendingDeletion.username} ?`}
            description="Le compte sera désactivé, ses sessions fermées et son dossier sera conservé."
            onClose={() => {
              setDeletionError("");
              setPendingDeletion(null);
            }}
            closeDisabled={updatingUserId === pendingDeletion.id}
          >
            <div className="confirmation-content">
              <p className="permanent-delete-warning">
                Les fichiers ne seront pas supprimés, mais cet utilisateur ne pourra plus se connecter.
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
                  Annuler
                </button>
                <button
                  type="button"
                  className="danger-button"
                  disabled={updatingUserId === pendingDeletion.id}
                  onClick={() => void confirmDeletion()}
                >
                  {updatingUserId === pendingDeletion.id
                    ? "Suppression…"
                    : "Confirmer la suppression"}
                </button>
              </div>
            </div>
          </FileDialog>
        )}
      </section>
    </AdminPageShell>
  );
}
