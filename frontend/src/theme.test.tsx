import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { App } from "./App";
import { api, type User } from "./api/client";
import { I18nProvider } from "./i18n";
import { ThemeProvider, useTheme } from "./theme";
import { ThemeSelector } from "./components/ThemeSelector";
import { auditAccessibility } from "./test/accessibility";

const account: User = {
  id: "test-account", username: "a-very-long-username-for-testing", is_admin: false,
  is_active: true, must_change_credentials: false, preferred_locale: "fr", preferred_theme: "dark",
};
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), {
  status, headers: { "Content-Type": "application/json" },
});
function systemMedia(dark = false) {
  let matches = dark;
  const listeners = new Set<() => void>();
  vi.stubGlobal("matchMedia", vi.fn(() => ({
    get matches() { return matches; },
    addEventListener: (_: string, listener: () => void) => listeners.add(listener),
    removeEventListener: (_: string, listener: () => void) => listeners.delete(listener),
  })));
  return { change(value: boolean) { matches = value; act(() => listeners.forEach((listener) => listener())); }, listeners };
}
function Probe() {
  const { preferredTheme, effectiveTheme } = useTheme();
  return <><output>{preferredTheme}/{effectiveTheme}</output><ThemeSelector /></>;
}
function Harness({ user = null }: { user?: User | null }) {
  return <I18nProvider><ThemeProvider user={user}><Probe /></ThemeProvider></I18nProvider>;
}

describe("theme", () => {
  it("bootstraps stored values and tolerates invalid or unavailable storage", () => {
    systemMedia(true);
    localStorage.setItem("wos.preferred-theme", "invalid");
    expect(window.wosTheme.read()).toBe("system");
    expect(window.wosTheme.apply(window.wosTheme.read())).toBe("dark");
    localStorage.setItem("wos.preferred-theme", "light");
    expect(window.wosTheme.apply(window.wosTheme.read())).toBe("light");
    const read = vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => { throw new Error("blocked"); });
    const write = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("blocked"); });
    expect(window.wosTheme.read()).toBe("system");
    expect(window.wosTheme.apply("light")).toBe("light");
    read.mockRestore(); write.mockRestore();
  });

  it("tracks system changes only in system mode and cleans up its listener", async () => {
    const media = systemMedia(true);
    const view = render(<Harness />);
    expect(screen.getByText("system/dark")).toBeTruthy();
    media.change(false);
    expect(screen.getByText("system/light")).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: "Sombre" }));
    media.change(true); media.change(false);
    expect(screen.getByText("dark/dark")).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: "Système" }));
    expect(screen.getByText("system/light")).toBeTruthy();
    view.unmount(); expect(media.listeners.size).toBe(0);
  });

  it("rolls back failed saves but ignores a response belonging to a previous session", async () => {
    systemMedia();
    let reject!: (reason: Error) => void;
    const save = vi.spyOn(api, "changeTheme").mockImplementation(() => new Promise((_, fail) => { reject = fail; }));
    try {
      const view = render(<Harness user={account} />);
      const light = screen.getByRole("button", { name: "Clair" });
      light.focus();
      await userEvent.keyboard("{Enter}");
      expect(screen.getByText("light/light")).toBeTruthy();
      expect(light.getAttribute("aria-disabled")).toBe("true");
      expect(light.hasAttribute("disabled")).toBe(false);
      expect(document.activeElement).toBe(light);
      expect(screen.getByText("Enregistrement…")).toBeTruthy();
      await act(async () => reject(new Error("offline")));
      expect(screen.getByText("dark/dark")).toBeTruthy();
      expect(screen.getByRole("alert").textContent).toContain("rétabli");
      expect(localStorage.getItem("wos.preferred-theme")).toBe("dark");
      await userEvent.click(screen.getByRole("button", { name: "Clair" }));
      view.rerender(<Harness user={{ ...account, id: "another", preferred_theme: "system" }} />);
      await act(async () => reject(new Error("old request")));
      expect(screen.getByText("system/light")).toBeTruthy();
      expect(screen.queryByRole("alert")).toBeNull();
    } finally {
      save.mockRestore();
    }
  });

  it("restores /auth/me and persists through account menu, Preferences and reconnection", async () => {
    systemMedia(); localStorage.setItem("wos.preferred-theme", "light");
    let current = { ...account };
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/v1/auth/me") return json({ user: current });
      if (url === "/api/v1/auth/theme" || url === "/api/v1/auth/locale") {
        current = { ...current, ...JSON.parse(String(init?.body)) }; return json({ user: current });
      }
      if (url.startsWith("/api/v1/files")) return json({ path: "", breadcrumbs: [], entries: [],
        storage: { total: 1000, used: 0, available: 1000 }, truncated: false });
      throw new Error(`Unexpected request ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup(); const view = render(<App />);
    await screen.findByRole("heading", { name: "Dashboard" });
    expect(document.documentElement.dataset.theme).toBe("dark");
    expect(screen.queryByRole("combobox", { name: "Langue" })).toBeNull();
    const trigger = screen.getByRole("button", { name: "Ouvrir le menu du compte" });
    await user.click(trigger);
    const light = screen.getByRole("button", { name: "Clair" });
    light.focus(); await user.keyboard("{Enter}");
    await waitFor(() => expect(current.preferred_theme).toBe("light"));
    expect(light.getAttribute("aria-pressed")).toBe("true");
    expect(document.documentElement.dataset.theme).toBe("light");
    expect(await auditAccessibility(view.container)).toMatchObject({ violations: [] });
    await user.keyboard("{Escape}");
    expect(document.activeElement).toBe(trigger);
    expect(trigger.getAttribute("aria-expanded")).toBe("false");
    await user.click(trigger);
    await user.click(screen.getByRole("button", { name: "Paramètres du compte" }));
    await screen.findByRole("heading", { name: "Préférences" });
    await user.click(screen.getByRole("button", { name: "Système" }));
    await waitFor(() => expect(current.preferred_theme).toBe("system"));
    await user.selectOptions(screen.getByRole("combobox", { name: "Langue" }), "en");
    await screen.findByRole("heading", { name: "Preferences" });
    expect(current.preferred_locale).toBe("en");
    expect(screen.getByRole("button", { name: "System" }).getAttribute("aria-pressed")).toBe("true");
    expect(await auditAccessibility(view.container)).toMatchObject({ violations: [] });
    expect(fetchMock).toHaveBeenCalledWith("/api/v1/auth/theme", expect.objectContaining({
      method: "PATCH", body: JSON.stringify({ preferred_theme: "light" }), credentials: "same-origin",
    }));
    view.unmount(); render(<App />);
    await screen.findByRole("heading", { name: "Dashboard" });
    expect(document.documentElement.dataset.theme).toBe("light");
    expect(localStorage.getItem("wos.preferred-theme")).toBe("system");
  });

  it("keeps the browser theme on loading and login, then restores the authenticated account", async () => {
    systemMedia(true); localStorage.setItem("wos.preferred-theme", "light");
    let resolveMe!: (response: Response) => void;
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/v1/auth/me") return new Promise<Response>((resolve) => { resolveMe = resolve; });
      if (url === "/api/v1/health/live") return json({ status: "ok" });
      if (url === "/api/v1/auth/login") return json({ user: account });
      if (url.startsWith("/api/v1/files")) return json({ path: "", breadcrumbs: [], entries: [], storage: { total: 0, used: 0, available: 0 }, truncated: false });
      throw new Error(url);
    }));
    const user = userEvent.setup(); render(<App />);
    expect(document.documentElement.dataset.theme).toBe("light");
    await act(async () => resolveMe(json({}, 401)));
    await screen.findByRole("heading", { name: "Bienvenue" });
    expect(document.documentElement.dataset.theme).toBe("light");
    await user.type(screen.getByLabelText("Nom d’utilisateur"), "test-account");
    await user.type(screen.getByLabelText("Mot de passe"), "fake-test-password");
    await user.click(screen.getByRole("button", { name: "Se connecter" }));
    await screen.findByRole("heading", { name: "Dashboard" });
    expect(document.documentElement.dataset.theme).toBe("dark");
  });
});
