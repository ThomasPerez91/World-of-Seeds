import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { auditAccessibility } from "../../test/accessibility";
import { AdminSettingsPage } from "./AdminSettingsPage";
import { translatedNewGreedyFieldIds } from "./newGreedyTranslations";
import { translatedOptionKeys } from "./optionTranslations";

const options = {
  service_controls_available: true,
  sections: [
    {
      id: "torrents",
      label: "Torrents",
      fields: [
        {
          key: "WOS_TORRENT_MAX_ACTIVE_PER_USER",
          label: "Torrents actifs par utilisateur",
          description: "Nombre maximal de demandes actives.",
          input_type: "integer",
          value: 5,
          default: 5,
          unit: "count",
          minimum: 1,
          maximum: 100,
          choices: [],
          editable: true,
          restart_required: false,
        },
      ],
    },
  ],
  changed_keys: [],
  restart_required: false,
  scheduler: {
    desired_generation: 4,
    applied_generation: 3,
    synchronized: false,
    rounds: 9,
    lease_active: true,
  },
  storage: {
    managed_bytes: 100,
    logical_bytes: 150,
    disk_total_bytes: 1000,
    disk_free_bytes: 600,
    pressure: "warning",
    managed_quota_bytes: 0,
    user_quota_bytes: 0,
  },
  audit: [
    {
      key: "WOS_TORRENT_MAX_ACTIVE_PER_USER",
      version: 1,
      old_value: null,
      new_value: 5,
      actor: null,
      source: "bootstrap",
      changed_at: "2026-08-22T10:00:00Z",
    },
  ],
} as const;

function response(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("AdminSettingsPage", () => {
  it("possède une traduction anglaise stable pour chaque option V2", () => {
    expect(translatedOptionKeys.size).toBe(36);
    expect(translatedOptionKeys.has("WOS_ADMIN_REFRESH_INTERVAL_SECONDS")).toBe(true);
    expect(translatedNewGreedyFieldIds.size).toBe(44);
    expect(translatedNewGreedyFieldIds.has("advanced.inject_hours")).toBe(true);
  });

  it("affiche une erreur métier structurée sous le champ concerné", async () => {
    const fetchMock = vi.fn(
      async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
        const url = String(input);
        if (url === "/api/v2/admin/overview" && init?.method === undefined) {
          return response(options);
        }
        if (url === "/api/v2/admin/options" && init?.method === "PATCH") {
          return response(
            {
              detail: {
                code: "invalid_option",
                message: "Cette limite est incompatible avec la capacité globale.",
                field: "WOS_TORRENT_MAX_ACTIVE_PER_USER",
              },
            },
            422,
          );
        }
        throw new Error(`Requête inattendue : ${init?.method ?? "GET"} ${url}`);
      },
    );
    vi.stubGlobal("fetch", fetchMock);

    const user = userEvent.setup();
    const view = render(
      <AdminSettingsPage
        onBack={vi.fn()}
        onNavigate={vi.fn()}
        onSessionExpired={vi.fn()}
      />,
    );

    const input = await screen.findByRole("spinbutton", {
      name: "Torrents actifs par utilisateur",
    });
    await user.clear(input);
    await user.type(input, "6");
    await user.click(screen.getByRole("button", { name: "Enregistrer" }));

    expect((await screen.findByRole("alert")).textContent).toContain(
      "Cette limite est incompatible avec la capacité globale.",
    );
    expect(input.getAttribute("aria-invalid")).toBe("true");
    expect(await auditAccessibility(view.container)).toMatchObject({ violations: [] });
  });

  it("confirme le redémarrage puis attend le retour du service", async () => {
    const fetchMock = vi.fn(
      async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
        const url = String(input);
        if (url === "/api/v2/admin/overview") return response(options);
        if (url === "/api/v1/admin/services/wos/restart" && init?.method === "POST") {
          return response({
            state: "pending",
            request_id: "request-1",
            updated_at: "2026-08-17T12:00:00Z",
            message_code: "requested",
          }, 202);
        }
        if (url === "/api/v1/admin/services/wos/restart") {
          return response({
            state: "healthy",
            request_id: "request-1",
            updated_at: "2026-08-17T12:00:02Z",
            message_code: "healthy",
          });
        }
        if (url === "/api/v1/health/live") {
          return response({ status: "ok", service: "world-of-seeds", version: "1.2.1" });
        }
        throw new Error(`Requête inattendue : ${init?.method ?? "GET"} ${url}`);
      },
    );
    vi.stubGlobal("fetch", fetchMock);

    const user = userEvent.setup();
    render(
      <AdminSettingsPage
        onBack={vi.fn()}
        onNavigate={vi.fn()}
        onSessionExpired={vi.fn()}
      />,
    );

    await screen.findByRole("heading", { name: "Paramètres fonctionnels" });
    await user.click(screen.getByRole("button", { name: "Redémarrer WOS" }));
    await screen.findByRole("dialog");
    await user.click(screen.getByRole("button", { name: "Confirmer le redémarrage" }));

    expect(
      await screen.findByText(
        "World of Seeds a redémarré avec succès.",
        {},
        { timeout: 2500 },
      ),
    ).toBeDefined();
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/admin/services/wos/restart",
      expect.objectContaining({ method: "POST" }),
    );
  });
});
