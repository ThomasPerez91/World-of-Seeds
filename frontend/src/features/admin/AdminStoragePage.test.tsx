import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { auditAccessibility } from "../../test/accessibility";
import { AdminStoragePage } from "./AdminStoragePage";

function response(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
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

    await screen.findByRole("heading", { name: "Réconciliation V2" });
    expect(screen.getByText("1 torrent externe observé, toujours en lecture seule.")).toBeTruthy();
    expect(screen.getByText("Aucune action")).toBeTruthy();
    expect(await auditAccessibility(view.container)).toMatchObject({ violations: [] });
  });
});
