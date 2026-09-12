import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { auditAccessibility } from "../../test/accessibility";
import { AdminStoragePage } from "./AdminStoragePage";

function response(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("AdminStoragePage", () => {
  it("affiche la capacité sans présenter une réconciliation partielle", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/v1/admin/storage") {
        return response({
          total: 1000,
          used: 400,
          available: 600,
          active_users: 1,
          suspended_users: 0,
          trash_entries: 0,
          known_trash_bytes: 0,
        });
      }
      throw new Error(`Requête inattendue : ${url}`);
    });
    vi.stubGlobal(
      "fetch",
      fetchMock,
    );

    const view = render(
      <AdminStoragePage onBack={vi.fn()} onNavigate={vi.fn()} onSessionExpired={vi.fn()} />,
    );

    expect(await screen.findByText("600 o")).toBeTruthy();
    expect(screen.queryByRole("heading", { name: "Intégrité du stockage" })).toBeNull();
    expect(fetchMock).not.toHaveBeenCalledWith("/api/v2/admin/reconciliation?limit=100");
    expect(await auditAccessibility(view.container)).toMatchObject({ violations: [] });
  });

  it("signale une erreur si les métriques de capacité sont indisponibles", async () => {
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      if (String(input) === "/api/v1/admin/storage") return response({ detail: "offline" }, 503);
      throw new Error(`Requête inattendue : ${String(input)}`);
    }));

    render(<AdminStoragePage onBack={vi.fn()} onNavigate={vi.fn()} onSessionExpired={vi.fn()} />);
    expect(await screen.findByText(/impossible de charger les données/i)).toBeTruthy();
  });
});
