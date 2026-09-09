import type { ReactNode } from "react";

import { BackIcon } from "../../components/icons";
import { useI18n, type MessageKey } from "../../i18n";

export type AdminView =
  | "admin-users"
  | "admin-services"
  | "admin-settings"
  | "admin-storage";

const navigation: { view: AdminView; label: MessageKey }[] = [
  { view: "admin-users", label: "admin.users" },
  { view: "admin-services", label: "admin.services" },
  { view: "admin-settings", label: "admin.settings" },
  { view: "admin-storage", label: "admin.storage" },
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
  const { t } = useI18n();
  return (
    <section className="admin-page" aria-labelledby="administration-title">
      <button type="button" className="back-button" onClick={onBack}>
        <BackIcon /> {t("common.backDashboard")}
      </button>
      <header className="admin-page-header">
        <h1 id="administration-title">{t("admin.title")}</h1>
        <nav className="admin-navigation" aria-label={t("admin.navigation")}>
          {navigation.map((item) => (
            <button
              type="button"
              key={item.view}
              aria-current={activeView === item.view ? "page" : undefined}
              onClick={() => onNavigate(item.view)}
            >
              {t(item.label)}
            </button>
          ))}
        </nav>
      </header>
      {children}
    </section>
  );
}
