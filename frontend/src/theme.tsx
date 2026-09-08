import {
  createContext, useCallback, useContext, useEffect, useLayoutEffect,
  useRef, useState, type ReactNode,
} from "react";
import { api, type User } from "./api/client";

export type Theme = "light" | "dark" | "system";
type EffectiveTheme = Exclude<Theme, "system">;

declare global {
  interface Window {
    wosTheme: {
      read: () => Theme;
      resolve: (theme: Theme) => EffectiveTheme;
      apply: (theme: Theme) => EffectiveTheme;
    };
  }
}

type ThemeContextValue = {
  preferredTheme: Theme;
  effectiveTheme: EffectiveTheme;
  saving: boolean;
  saveFailed: boolean;
  setTheme: (theme: Theme) => Promise<void>;
};
const ThemeContext = createContext<ThemeContextValue | null>(null);

export function ThemeProvider({ user, children }: { user: User | null; children: ReactNode }) {
  const [preferredTheme, setPreferredTheme] = useState(window.wosTheme.read);
  const [effectiveTheme, setEffectiveTheme] = useState(() => window.wosTheme.resolve(preferredTheme));
  const [saving, setSaving] = useState(false);
  const [saveFailed, setSaveFailed] = useState(false);
  const account = useRef<string | null>(null);
  const generation = useRef(0);
  const pending = useRef(false);

  const apply = useCallback((theme: Theme) => {
    setPreferredTheme(theme);
    setEffectiveTheme(window.wosTheme.apply(theme));
  }, []);

  useLayoutEffect(() => {
    const id = user?.id ?? null;
    if (account.current === id) return;
    account.current = id;
    generation.current += 1;
    pending.current = false;
    setSaving(false);
    setSaveFailed(false);
    // Account wins on session restoration/login; unrelated profile responses cannot
    // overwrite an optimistic theme choice. Logout retains the last local choice.
    if (user) apply(user.preferred_theme ?? "system");
  }, [user, apply]);

  useLayoutEffect(() => {
    setEffectiveTheme(window.wosTheme.apply(preferredTheme));
  }, [preferredTheme]);

  useEffect(() => {
    if (preferredTheme !== "system") return;
    const media = window.matchMedia("(prefers-color-scheme: dark)");
    const update = () => setEffectiveTheme(window.wosTheme.apply("system"));
    media.addEventListener("change", update);
    update();
    return () => media.removeEventListener("change", update);
  }, [preferredTheme]);

  useEffect(() => () => { generation.current += 1; }, []);

  const setTheme = async (theme: Theme) => {
    if (pending.current || theme === preferredTheme) return;
    const previous = preferredTheme;
    const requestGeneration = generation.current;
    setSaveFailed(false);
    apply(theme);
    if (!account.current) return;
    pending.current = true;
    setSaving(true);
    try {
      await api.changeTheme(theme);
    } catch {
      if (requestGeneration === generation.current) {
        apply(previous);
        setSaveFailed(true);
      }
    } finally {
      if (requestGeneration === generation.current) {
        pending.current = false;
        setSaving(false);
      }
    }
  };

  return (
    <ThemeContext.Provider value={{ preferredTheme, effectiveTheme, setTheme, saving, saveFailed }}>
      {children}
    </ThemeContext.Provider>
  );
}

export function useTheme() {
  const context = useContext(ThemeContext);
  if (!context) throw new Error("useTheme requires ThemeProvider");
  return context;
}
