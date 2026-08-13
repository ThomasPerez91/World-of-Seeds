import type { ReactNode } from "react";

export type AdminView = "admin-users" | "admin-storage" | "admin-trash";

const navigation: { view: AdminView; label: string }[] = [
  { view: "admin-users", label: "Utilisateurs" },
  { view: "admin-storage", label: "Stockage" },
  { view: "admin-trash", label: "Corbeilles" },
];

export function AdminPageShell({
  activeView,
  children,
  onBack,
  onNavigate,
}: {
  activeView: AdminView;
  children: ReactNode;
  onBack: () => void;
  onNavigate: (view: AdminView) => void;
}) {
  return (
    <section className="admin-page" aria-labelledby="administration-title">
      <button type="button" className="back-button" onClick={onBack}>
        <span aria-hidden="true">←</span> Retour aux fichiers
      </button>
      <header className="admin-page-header">
        <div>
          <p className="eyebrow">Espace sécurisé</p>
          <h1 id="administration-title">Administration</h1>
          <p>Gère les accès et le stockage de la seedbox depuis un espace dédié.</p>
        </div>
        <nav className="admin-navigation" aria-label="Sections d’administration">
          {navigation.map((item) => (
            <button
              type="button"
              key={item.view}
              aria-current={activeView === item.view ? "page" : undefined}
              onClick={() => onNavigate(item.view)}
            >
              {item.label}
            </button>
          ))}
        </nav>
      </header>
      {children}
    </section>
  );
}
