import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { App } from "./App";
import { auditAccessibility } from "./test/accessibility";

function response(body: unknown, status: number): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("App", () => {
  it("distingue une panne serveur d’un visiteur non authentifié", async () => {
    const user = userEvent.setup();
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(response({ detail: "Database unavailable" }, 503))
      .mockResolvedValueOnce(response({ detail: "Not authenticated" }, 401));
    vi.stubGlobal("fetch", fetchMock);

    const view = render(<App />);
    await screen.findByRole("heading", { name: "Connexion impossible" });
    expect(screen.queryByRole("heading", { name: "Bienvenue" })).toBeNull();
    expect(await auditAccessibility(view.container)).toMatchObject({ violations: [] });

    await user.click(screen.getByRole("button", { name: "Réessayer" }));
    await screen.findByRole("heading", { name: "Bienvenue" });
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(await auditAccessibility(view.container)).toMatchObject({ violations: [] });
  });

  it("fournit des repères et un lien d’évitement sur le tableau de bord", async () => {
    const currentUser = {
      id: "bc68aa7c-d753-4db7-8698-acf8d09045a3",
      username: "thomas",
      is_admin: true,
      is_active: true,
      must_change_credentials: false,
    };
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url === "/api/v1/auth/me") return response({ user: currentUser }, 200);
        if (url.startsWith("/api/v1/files")) {
          return response(
            {
              path: "",
              breadcrumbs: [{ label: "Mes fichiers", path: "" }],
              entries: [],
              storage: { total: 1000, used: 0, available: 1000 },
              truncated: false,
            },
            200,
          );
        }
        if (url === "/api/v1/trash") {
          return response({ entries: [], truncated: false }, 200);
        }
        if (url === "/api/v1/admin/users") return response([currentUser], 200);
        throw new Error(`Requête inattendue : ${url}`);
      }),
    );

    const view = render(<App />);
    await screen.findByRole("heading", { name: "Mes fichiers" });
    const skipLink = screen.getByRole("link", { name: "Aller au contenu principal" });
    expect(skipLink.getAttribute("href")).toBe("#dashboard-content");
    expect(document.querySelector("#dashboard-content")?.getAttribute("tabindex")).toBe("-1");
    expect(screen.queryByText("Bonjour, thomas")).toBeNull();
    expect(document.querySelector(".account-avatar")?.textContent).toBe("T");
    expect(await auditAccessibility(view.container)).toMatchObject({ violations: [] });
    await screen.findByText("Ce dossier est vide");
    await screen.findByText("La corbeille est vide");
    await screen.findByRole("heading", { name: "Comptes utilisateurs" });
    expect(await auditAccessibility(view.container)).toMatchObject({ violations: [] });

    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Ouvrir le menu du compte" }));
    await user.click(screen.getByRole("button", { name: "Paramètres du compte" }));
    await screen.findByRole("heading", { name: "Paramètres du compte" });
    expect(screen.getByLabelText("Nom d’utilisateur")).toHaveProperty("value", "thomas");
    expect(await auditAccessibility(view.container)).toMatchObject({ violations: [] });
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
    const fetchMock = vi.fn(
      async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
        const url = String(input);
        if (url === "/api/v1/auth/me") return response({ user: admin }, 200);
        if (url.startsWith("/api/v1/files")) {
          return response(
            {
              path: "",
              breadcrumbs: [{ label: "Mes fichiers", path: "" }],
              entries: [],
              storage: { total: 1000, used: 0, available: 1000 },
              truncated: false,
            },
            200,
          );
        }
        if (url === "/api/v1/trash") return response({ entries: [], truncated: false }, 200);
        if (url === "/api/v1/admin/users" && init?.method === undefined) {
          return response([admin, guest], 200);
        }
        if (url.endsWith(`/${guest.id}/status`) && init?.method === "PATCH") {
          return response({ ...guest, is_active: false }, 200);
        }
        if (url.endsWith(`/${guest.id}`) && init?.method === "DELETE") {
          return new Response(null, { status: 204 });
        }
        throw new Error(`Requête inattendue : ${init?.method ?? "GET"} ${url}`);
      },
    );
    vi.stubGlobal("fetch", fetchMock);

    const user = userEvent.setup();
    const view = render(<App />);
    await screen.findByText("guest-a1b2c3");

    await user.click(screen.getByRole("button", { name: "Suspendre" }));
    await screen.findByRole("button", { name: "Réactiver" });
    expect(screen.getByText("Suspendu")).toBeDefined();

    await user.click(screen.getByRole("button", { name: "Supprimer l’accès" }));
    await screen.findByText("Le compte sera désactivé, ses sessions fermées et son dossier sera conservé.");
    await user.click(screen.getByRole("button", { name: "Confirmer la suppression" }));
    expect(screen.queryByText("guest-a1b2c3")).toBeNull();
    expect(await auditAccessibility(view.container)).toMatchObject({ violations: [] });
  });
});
