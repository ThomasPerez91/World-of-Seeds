import { type ReactNode, useEffect, useRef } from "react";

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
  const navigationRef = useRef<HTMLElement>(null);

  useEffect(() => {
    if (!window.matchMedia("(max-width: 959px)").matches) return;
    navigationRef.current
      ?.querySelector<HTMLElement>('[aria-current="page"]')
      ?.scrollIntoView({ behavior: "smooth", block: "nearest", inline: "center" });
  }, [activeView]);

  return (
    <div className="settings-shell wos-glass-panel">
      <nav ref={navigationRef} className="settings-shell-navigation" aria-label={navigationLabel}>
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
