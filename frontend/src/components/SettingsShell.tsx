import type { ReactNode } from "react";

export interface SettingsNavigationItem<View extends string> {
  label: string;
  view: View;
}

export function SettingsShell<View extends string>({
  activeView,
  children,
  navigation,
  navigationLabel,
  onNavigate,
}: {
  activeView: View;
  children: ReactNode;
  navigation: readonly SettingsNavigationItem<View>[];
  navigationLabel: string;
  onNavigate: (view: View) => void;
}) {
  return (
    <div className="settings-shell wos-glass-panel">
      <nav className="settings-shell-navigation" aria-label={navigationLabel}>
        {navigation.map((item) => (
          <button
            type="button"
            className="settings-shell-navigation-item"
            key={item.view}
            aria-current={activeView === item.view ? "page" : undefined}
            onClick={() => onNavigate(item.view)}
          >
            {item.label}
          </button>
        ))}
      </nav>
      <div className="settings-shell-content">{children}</div>
    </div>
  );
}
