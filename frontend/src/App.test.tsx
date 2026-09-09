import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { App } from "./App";
import { auditAccessibility } from "./test/accessibility";
import { APP_VERSION } from "./version";

function response(body: unknown, status: number): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function storageResponse(total = 2048, used = 1024, available = 1024): Response {
  return response(
    {
      total_bytes: total,
      used_bytes: used,
      available_bytes: available,
    },
    200,
  );
}

function emptyTorrentListing(url: string): Response | null {
  if (!url.startsWith("/api/v2/torrents?")) return null;
  const search = new URL(url, "https://world-of-seeds.test").searchParams;
  const offset = Number(search.get("offset") ?? "0");
  const limit = Number(search.get("limit") ?? "50");
  return response({ items: [], offset, limit, total: 0 }, 200);
}

describe("App", () => {
  it("conserve la connexion si la préférence de langue ne peut pas être enregistrée", async () => {
    const signedInUser = {
      id: "bc68aa7c-d753-4db7-8698-acf8d09045a3",
      username: "thomas",
      is_admin: false,
      is_active: true,
      must_change_credentials: false,
      preferred_locale: "fr",
    };
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/v1/auth/me") return response({ detail: "Not authenticated" }, 401);
      if (url === "/api/v1/health/status") {
        return response({ status: "ok", checked_at: "2026-09-09T12:00:00Z" }, 200);
      }
      if (url === "/api/v1/auth/login" && init?.method === "POST") {
        return response({ user: signedInUser }, 200);
      }
      if (url === "/api/v1/auth/locale" && init?.method === "PATCH") {
        return response({ detail: "Database unavailable" }, 503);
      }
      if (url === "/api/v2/storage") return storageResponse(1000, 0, 1000);
      const torrentListing = emptyTorrentListing(url);
      if (torrentListing !== null) return torrentListing;
      throw new Error(`Unexpected request: ${init?.method ?? "GET"} ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    const user = userEvent.setup();
    render(<App />);
    await screen.findByRole("heading", { name: "Bienvenue" });
    await user.selectOptions(screen.getByRole("combobox", { name: "Langue" }), "en");
    await user.type(screen.getByLabelText("Username"), "thomas");
    await user.type(screen.getByLabelText("Password"), "correct-password");
    await user.click(screen.getByRole("button", { name: "Sign in" }));

    await screen.findByRole("heading", { name: "Dashboard" });
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/auth/locale",
      expect.objectContaining({ method: "PATCH" }),
    );
  });

  it("enregistre le choix de langue pendant la personnalisation du premier accès", async () => {
    let currentUser = {
      id: "bc68aa7c-d753-4db7-8698-acf8d09045a3",
      username: "guest-a1b2c3",
      is_admin: false,
      is_active: true,
      must_change_credentials: true,
      preferred_locale: "fr",
    };
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/v1/auth/me") return response({ user: currentUser }, 200);
      if (url === "/api/v1/auth/locale" && init?.method === "PATCH") {
        currentUser = { ...currentUser, preferred_locale: "en" };
        return response({ user: currentUser }, 200);
      }
      throw new Error(`Unexpected request: ${init?.method ?? "GET"} ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    const user = userEvent.setup();
    render(<App />);
    await screen.findByRole("heading", { name: "Personnalise ton accès" });
    await user.selectOptions(screen.getByRole("combobox", { name: "Langue" }), "en");

    await screen.findByRole("heading", { name: "Personalize your account" });
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/auth/locale",
      expect.objectContaining({ method: "PATCH" }),
    );
  });

  it("restaure la langue anglaise et la capacité partagée sur les espaces utilisateur et admin", async () => {
    const currentUser = {
      id: "bc68aa7c-d753-4db7-8698-acf8d09045a3",
      username: "thomas",
      is_admin: true,
      is_active: true,
      must_change_credentials: false,
      preferred_locale: "en",
    };
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url === "/api/v1/auth/me") return response({ user: currentUser }, 200);
        if (url === "/api/v2/storage") return storageResponse();
        const torrentListing = emptyTorrentListing(url);
        if (torrentListing !== null) return torrentListing;
        if (url === "/api/v1/admin/users") return response([currentUser], 200);
        throw new Error(`Unexpected request: ${url}`);
      }),
    );

    const user = userEvent.setup();
    const view = render(<App />);
    await screen.findByRole("heading", { name: "Dashboard" });
    expect(await screen.findByText("1 KB available")).toBeTruthy();
    expect(document.documentElement.lang).toBe("en");

    await user.click(screen.getByRole("button", { name: "Open account menu" }));
    await user.click(screen.getByRole("button", { name: "Administration" }));
    await screen.findByRole("heading", { name: "User accounts" });
    expect(screen.getByRole("button", { name: "Generate user" })).toBeTruthy();
    expect(await auditAccessibility(view.container)).toMatchObject({ violations: [] });
  });

  it("distingue une panne serveur d’un visiteur non authentifié", async () => {
    let meAttempts = 0;
    let healthAttempts = 0;
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/v1/auth/me") {
        meAttempts += 1;
        return meAttempts === 1
          ? response({ detail: "Database unavailable" }, 503)
          : response({ detail: "Not authenticated" }, 401);
      }
      if (url === "/api/v1/health/status") {
        healthAttempts += 1;
        return response(
          {
            status: healthAttempts === 1 ? "ok" : "degraded",
            checked_at: "2026-08-15T14:00:00Z",
          },
          200,
        );
      }
      throw new Error(`Unexpected request: ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    const user = userEvent.setup();
    const view = render(<App />);
    await screen.findByRole("heading", { name: "Connexion impossible" });
    expect(screen.queryByRole("heading", { name: "Bienvenue" })).toBeNull();
    expect(await auditAccessibility(view.container)).toMatchObject({ violations: [] });

    await user.click(screen.getByRole("button", { name: "Réessayer" }));
    await screen.findByRole("heading", { name: "Bienvenue" });
    expect(screen.getByText("Tous les services fonctionnent normalement.")).toBeDefined();
    await user.click(screen.getByRole("button", { name: "Vérifier l’état du service" }));
    await screen.findByText("Le service est momentanément interrompu.");
    expect(await auditAccessibility(view.container)).toMatchObject({ violations: [] });
  });

  it("fournit des repères, retire Fichiers/Corbeille et nettoie les anciens liens", async () => {
    const currentUser = {
      id: "bc68aa7c-d753-4db7-8698-acf8d09045a3",
      username: "thomas",
      is_admin: true,
      is_active: true,
      must_change_credentials: false,
    };
    window.history.replaceState({}, "", "/?path=downloads%2Farchive");
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url === "/api/v1/auth/me") return response({ user: currentUser }, 200);
        if (url === "/api/v2/storage") return storageResponse(1000, 0, 1000);
        const torrentListing = emptyTorrentListing(url);
        if (torrentListing !== null) return torrentListing;
        if (url === "/api/v1/admin/users") return response([currentUser], 200);
        throw new Error(`Requête inattendue : ${url}`);
      }),
    );

    const user = userEvent.setup();
    const view = render(<App />);
    await screen.findByRole("heading", { name: "Dashboard" });
    const skipLink = screen.getByRole("link", { name: "Aller au contenu principal" });
    expect(skipLink.getAttribute("href")).toBe("#dashboard-content");
    expect(document.querySelector("#dashboard-content")?.getAttribute("tabindex")).toBe("-1");
    expect(document.querySelector(".account-avatar")?.textContent).toBe("T");
    expect(screen.getAllByText(`v${APP_VERSION}`).length).toBeGreaterThan(0);
    expect(screen.queryByRole("button", { name: "Mes fichiers" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Corbeille" })).toBeNull();
    expect(new URL(window.location.href).searchParams.has("path")).toBe(false);
    expect(await auditAccessibility(view.container)).toMatchObject({ violations: [] });

    await user.click(screen.getByRole("button", { name: "Ouvrir le menu du compte" }));
    await user.click(screen.getByRole("button", { name: "Administration" }));
    await screen.findByRole("heading", { name: "Comptes utilisateurs" });
    expect(screen.queryByRole("button", { name: "Corbeilles" })).toBeNull();

    await user.click(screen.getByRole("button", { name: "Ouvrir le Dashboard" }));
    await screen.findByRole("heading", { name: "Dashboard" });
    await user.click(screen.getByRole("button", { name: "Ouvrir le menu du compte" }));
    await user.click(screen.getByRole("button", { name: "Paramètres du compte" }));
    await screen.findByRole("heading", { name: "Paramètres du compte" });
    expect(screen.getByRole("textbox", { name: "Nom d’utilisateur" })).toHaveProperty(
      "value",
      "thomas",
    );
  });

  it("rend les informations légales accessibles avant la connexion", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url === "/api/v1/auth/me") return response({ detail: "Not authenticated" }, 401);
        if (url === "/api/v1/health/status") {
          return response({ status: "ok", checked_at: "2026-09-09T12:00:00Z" }, 200);
        }
        throw new Error(`Unexpected request: ${url}`);
      }),
    );

    const user = userEvent.setup();
    const view = render(<App />);
    await screen.findByRole("heading", { name: "Bienvenue" });

    await user.click(screen.getByRole("button", { name: "Mentions légales" }));
    await screen.findByRole("heading", { name: "Mentions légales et vie privée" });
    expect(screen.getByText(/OVH SAS/)).toBeDefined();
    expect(await auditAccessibility(view.container)).toMatchObject({ violations: [] });

    await user.click(screen.getByRole("button", { name: "Conditions d’utilisation" }));
    await screen.findByRole("heading", { name: "Conditions d’utilisation" });
    await user.click(screen.getByRole("button", { name: "Retour" }));
    await screen.findByRole("heading", { name: "Bienvenue" });
  });

  it("permet à l’administrateur de suspendre puis supprimer un accès", async () => {
    const admin = {
      id: "bc68aa7c-d753-4db7-8698-acf8d09045a3",
      username: "admin",
      is_admin: true,
      is_active: true,
      must_change_credentials: false,
    };
    const guest = {
      id: "81776682-b0c3-4d3d-8b85-ff284c68394c",
      username: "guest-a1b2c3",
      is_admin: false,
      is_active: true,
      must_change_credentials: true,
    };
    let deleteAttempts = 0;
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
      const url = String(input);
      if (url === "/api/v1/auth/me") return response({ user: admin }, 200);
      if (url === "/api/v2/storage") return storageResponse(1000, 0, 1000);
      const torrentListing = emptyTorrentListing(url);
      if (torrentListing !== null) return torrentListing;
      if (url === "/api/v1/admin/users" && init?.method === undefined) {
        return response([admin, guest], 200);
      }
      if (url.endsWith(`/${guest.id}/status`) && init?.method === "PATCH") {
        return response({ ...guest, is_active: false }, 200);
      }
      if (url.endsWith(`/${guest.id}`) && init?.method === "DELETE") {
        deleteAttempts += 1;
        if (deleteAttempts === 1) return response({ detail: "Database unavailable" }, 503);
        return new Response(null, { status: 204 });
      }
      throw new Error(`Requête inattendue : ${init?.method ?? "GET"} ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    const user = userEvent.setup();
    const view = render(<App />);
    await screen.findByRole("heading", { name: "Dashboard" });
    await user.click(screen.getByRole("button", { name: "Ouvrir le menu du compte" }));
    await user.click(screen.getByRole("button", { name: "Administration" }));
    await screen.findByText("guest-a1b2c3");

    await user.click(screen.getByRole("button", { name: "Suspendre guest-a1b2c3" }));
    await screen.findByRole("button", { name: "Réactiver guest-a1b2c3" });
    expect(screen.getByText("Suspendu")).toBeDefined();

    await user.click(screen.getByRole("button", { name: "Supprimer l’accès de guest-a1b2c3" }));
    await screen.findByRole("heading", { name: "Supprimer l’accès de guest-a1b2c3 ?" });
    expect(deleteAttempts).toBe(0);
    await user.click(screen.getByRole("button", { name: "Confirmer la suppression" }));
    await screen.findByText("Impossible de supprimer l’accès de cet utilisateur.");
    expect(screen.getByRole("heading", { name: "Supprimer l’accès de guest-a1b2c3 ?" })).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "Confirmer la suppression" }));
    expect(screen.queryByText("guest-a1b2c3")).toBeNull();
    expect(await auditAccessibility(view.container)).toMatchObject({ violations: [] });
  });

  it("sépare le renommage du compte et le changement de mot de passe", async () => {
    let currentUser = {
      id: "bc68aa7c-d753-4db7-8698-acf8d09045a3",
      username: "thomas",
      is_admin: false,
      is_active: true,
      must_change_credentials: false,
    };
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
      const url = String(input);
      if (url === "/api/v1/auth/me") return response({ user: currentUser }, 200);
      if (url === "/api/v2/storage") return storageResponse(1000, 0, 1000);
      const torrentListing = emptyTorrentListing(url);
      if (torrentListing !== null) return torrentListing;
      if (url === "/api/v1/auth/username" && init?.method === "PATCH") {
        currentUser = { ...currentUser, username: "Shadowsun" };
        return response({ user: currentUser }, 200);
      }
      if (url === "/api/v1/auth/password" && init?.method === "PATCH") {
        return new Response(null, { status: 204 });
      }
      if (url === "/api/v1/health/status") {
        return response({ status: "ok", checked_at: "2026-09-09T12:00:00Z" }, 200);
      }
      throw new Error(`Requête inattendue : ${init?.method ?? "GET"} ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    const user = userEvent.setup();
    const view = render(<App />);
    await screen.findByRole("heading", { name: "Dashboard" });
    await user.click(screen.getByRole("button", { name: "Ouvrir le menu du compte" }));
    await user.click(screen.getByRole("button", { name: "Paramètres du compte" }));

    const usernameInput = screen.getByRole("textbox", { name: "Nom d’utilisateur" });
    await user.clear(usernameInput);
    await user.type(usernameInput, "Shadowsun");
    await user.click(screen.getByRole("button", { name: "Mettre à jour le nom" }));
    await screen.findByText("Ton nom d’utilisateur a été mis à jour.");
    expect(screen.getByRole("button", { name: "Ouvrir le menu du compte" }).textContent).toContain(
      "Shadowsun",
    );

    await user.type(screen.getByLabelText("Mot de passe actuel"), "current-password-long");
    await user.type(screen.getByLabelText("Nouveau mot de passe"), "new-password-long");
    await user.type(screen.getByLabelText("Confirmer le mot de passe"), "new-password-long");
    await user.click(screen.getByRole("button", { name: "Modifier le mot de passe" }));

    await screen.findByRole("heading", { name: "Bienvenue" });
    expect(await auditAccessibility(view.container)).toMatchObject({ violations: [] });
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/auth/username",
      expect.objectContaining({ method: "PATCH" }),
    );
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/auth/password",
      expect.objectContaining({ method: "PATCH" }),
    );
  });

  it("affiche le stockage admin sans exposer de corbeille legacy", async () => {
    const admin = {
      id: "bc68aa7c-d753-4db7-8698-acf8d09045a3",
      username: "admin",
      is_admin: true,
      is_active: true,
      must_change_credentials: false,
    };
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url === "/api/v1/auth/me") return response({ user: admin }, 200);
        if (url === "/api/v2/storage") return storageResponse(1000, 400, 600);
        const torrentListing = emptyTorrentListing(url);
        if (torrentListing !== null) return torrentListing;
        if (url === "/api/v1/admin/users") return response([admin], 200);
        if (url === "/api/v1/admin/storage") {
          return response(
            {
              total: 1000,
              used: 400,
              available: 600,
              active_users: 1,
              suspended_users: 0,
            },
            200,
          );
        }
        if (url === "/api/v2/admin/reconciliation?limit=100") {
          return response(
            {
              database_scanned: 0,
              qbittorrent_scanned: 0,
              storage_scanned: 0,
              external_torrents: 0,
              anomalies: [],
              truncated: false,
              next_cursor: null,
            },
            200,
          );
        }
        throw new Error(`Requête inattendue : ${url}`);
      }),
    );

    const user = userEvent.setup();
    const view = render(<App />);
    await screen.findByRole("heading", { name: "Dashboard" });
    await user.click(screen.getByRole("button", { name: "Ouvrir le menu du compte" }));
    await user.click(screen.getByRole("button", { name: "Administration" }));
    await screen.findByRole("heading", { name: "Comptes utilisateurs" });
    expect(screen.queryByRole("button", { name: "Corbeilles" })).toBeNull();

    await user.click(screen.getByRole("button", { name: "Stockage" }));
    await screen.findByRole("heading", { name: "Stockage de la seedbox" });
    expect(screen.getByText("Comptes actifs")).toBeDefined();
    expect(screen.getByText("Comptes suspendus")).toBeDefined();
    expect(screen.queryByText("Éléments en corbeille")).toBeNull();
    expect(screen.queryByText("Taille connue des corbeilles")).toBeNull();
    expect(await auditAccessibility(view.container)).toMatchObject({ violations: [] });
  });
});
