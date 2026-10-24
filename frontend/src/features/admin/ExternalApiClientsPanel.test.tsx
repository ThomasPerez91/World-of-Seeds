import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ExternalApiClientsPanel } from "./ExternalApiClientsPanel";

const client = {
  id: "11111111-1111-4111-8111-111111111111",
  name: "Bot Discord",
  key_prefix: "wos_live_abcdef…",
  is_active: true,
  scopes: ["downloads:read"],
  created_at: "2026-10-01T10:00:00Z",
  updated_at: "2026-10-01T10:00:00Z",
  last_used_at: null,
};

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("ExternalApiClientsPanel", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("demande confirmation avant de révoquer et n’envoie rien si on annule", async () => {
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === "DELETE") return new Response(null, { status: 204 });
      return json([client]);
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    render(<ExternalApiClientsPanel onSessionExpired={vi.fn()} />);

    await user.click(await screen.findByRole("button", { name: "Révoquer" }));
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByText(/Bot Discord/)).toBeTruthy();
    expect(fetchMock.mock.calls.some(([, init]) => init?.method === "DELETE")).toBe(false);

    await user.click(within(dialog).getByRole("button", { name: "Annuler" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(fetchMock.mock.calls.some(([, init]) => init?.method === "DELETE")).toBe(false);

    await user.click(screen.getByRole("button", { name: "Révoquer" }));
    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Révoquer la clé" }));

    await waitFor(() =>
      expect(fetchMock.mock.calls.filter(([, init]) => init?.method === "DELETE")).toHaveLength(1),
    );
    expect(await screen.findByText("Révoqué")).toBeTruthy();
  });

  it("garde la liste visible quand une révocation échoue", async () => {
    vi.stubGlobal("fetch", vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === "DELETE") return json({ detail: "boom" }, 500);
      return json([client]);
    }));
    const user = userEvent.setup();
    render(<ExternalApiClientsPanel onSessionExpired={vi.fn()} />);

    await user.click(await screen.findByRole("button", { name: "Révoquer" }));
    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Révoquer la clé" }));

    expect(await screen.findByText("Impossible de révoquer le client API.")).toBeTruthy();
    expect(screen.getByText("Bot Discord")).toBeTruthy();
  });
});
