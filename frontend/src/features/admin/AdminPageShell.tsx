import type { ReactNode } from "react";

import { BackIcon } from "../../components/icons";

export type AdminView =
  | "admin-users"
  | "admin-services"
  | "admin-settings"
  | "admin-storage"
  | "admin-trash";

const navigation: { view: AdminView; label: string }[] = [
  { view: "admin-users", label: "Utilisateurs" },
  { view: "admin-services", label: "Services" },
  { view: "admin-settings", label: "Paramètres" },
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
        <BackIcon /> Retour aux fichiers
      </button>
      <header className="admin-page-header">
        <h1 id="administration-title">Administration</h1>
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
