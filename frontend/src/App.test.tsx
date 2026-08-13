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
      expires_at: null,
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
    await screen.findByRole("heading", { name: "Accès temporaires" });
    expect(await auditAccessibility(view.container)).toMatchObject({ violations: [] });

    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Ouvrir le menu du compte" }));
    await user.click(screen.getByRole("button", { name: "Paramètres du compte" }));
    await screen.findByRole("heading", { name: "Paramètres du compte" });
    expect(screen.getByLabelText("Nom d’utilisateur")).toHaveProperty("value", "thomas");
    expect(await auditAccessibility(view.container)).toMatchObject({ violations: [] });
  });
});
