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
  it("affiche l’inventaire borné et signale les torrents externes en lecture seule", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
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
        if (url === "/api/v2/admin/reconciliation?limit=100") {
          return response({
            database_scanned: 2,
            qbittorrent_scanned: 3,
            storage_scanned: 2,
            external_torrents: 1,
            anomalies: [
              {
                code: "external_torrents_read_only",
                severity: "info",
                resource_id: null,
                action: "none",
              },
            ],
            truncated: false,
          });
        }
        throw new Error(`Requête inattendue : ${url}`);
      }),
    );

    const view = render(
      <AdminStoragePage onBack={vi.fn()} onNavigate={vi.fn()} onSessionExpired={vi.fn()} />,
    );

    await screen.findByRole("heading", { name: "Intégrité du stockage" });
    expect(screen.getByText("Éléments externes")).toBeTruthy();
    expect(screen.getByText("Base de données")).toBeTruthy();
    expect(screen.getByText("Aucune action")).toBeTruthy();
    expect(await auditAccessibility(view.container)).toMatchObject({ violations: [] });
  });

  it("conserve les métriques de capacité si l’analyse d’intégrité échoue", async () => {
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      if (String(input) === "/api/v1/admin/storage") {
        return response({ total: 1000, used: 400, available: 600, active_users: 1, suspended_users: 0 });
      }
      if (String(input) === "/api/v2/admin/reconciliation?limit=100") {
        return response({ detail: "offline" }, 503);
      }
      throw new Error(`Requête inattendue : ${String(input)}`);
    }));

    render(<AdminStoragePage onBack={vi.fn()} onNavigate={vi.fn()} onSessionExpired={vi.fn()} />);
    expect(await screen.findByText("600 o")).toBeTruthy();
    expect(await screen.findByText(/analyse d’intégrité est momentanément indisponible/i)).toBeTruthy();
  });
});
