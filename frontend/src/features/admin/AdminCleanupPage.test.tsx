import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { auditAccessibility } from "../../test/accessibility";
import { AdminCleanupPage } from "./AdminCleanupPage";

function response(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

describe("AdminCleanupPage", () => {
  it("trie la liste et limite la purge globale aux éléments filtrés", async () => {
    let items = [
      {
        id: "11111111-1111-4111-8111-111111111111",
        name: "Alpha avec abonnés et un nom volontairement très long",
        size_bytes: 4_096,
        subscriber_count: 2,
        deletion_at: "2026-09-20T10:00:00Z",
      },
      {
        id: "22222222-2222-4222-8222-222222222222",
        name: "Zulu sans abonné",
        size_bytes: 1_024,
        subscriber_count: 0,
        deletion_at: "2026-09-18T10:00:00Z",
      },
    ];
    const purgeBodies: unknown[] = [];
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/v2/admin/cleanup" && init?.method === undefined) {
        return response({ checked_at: "2026-09-17T12:00:00Z", items });
      }
      if (url === "/api/v2/admin/cleanup/purge" && init?.method === "POST") {
        const body = JSON.parse(String(init.body)) as { torrent_ids: string[] };
        purgeBodies.push(body);
        items = items.filter((item) => !body.torrent_ids.includes(item.id));
        return response({
          requested: body.torrent_ids.length,
          scheduled: body.torrent_ids.length,
          scheduled_ids: body.torrent_ids,
          skipped_ids: [],
        });
      }
      throw new Error(`Unexpected request: ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    const view = render(
      <AdminCleanupPage onBack={vi.fn()} onNavigate={vi.fn()} onSessionExpired={vi.fn()} />,
    );

    expect(await screen.findByText("Zulu sans abonné")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Trier la colonne Taille" }));
    let rows = screen.getAllByRole("row").slice(1);
    expect(within(rows[0]).getByText("Zulu sans abonné")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Trier la colonne Taille" }));
    rows = screen.getAllByRole("row").slice(1);
    expect(within(rows[0]).getByText("Alpha avec abonnés et un nom volontairement très long")).toBeTruthy();

    fireEvent.change(screen.getByLabelText("Nombre d’abonnés"), { target: { value: "none" } });
    expect(screen.queryByText("Alpha avec abonnés et un nom volontairement très long")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Purger les résultats filtrés (1)" }));
    fireEvent.click(screen.getByRole("button", { name: "Confirmer la purge" }));

    await waitFor(() => expect(purgeBodies).toEqual([
      { torrent_ids: ["22222222-2222-4222-8222-222222222222"] },
    ]));
    expect(await screen.findByText("Purge planifiée pour 1 contenu(s).")).toBeTruthy();
    expect(screen.getByText("Aucun contenu ne correspond aux filtres actuels.")).toBeTruthy();
    expect(await auditAccessibility(view.container)).toMatchObject({ violations: [] });
  });

  it("permet une purge individuelle et un rafraîchissement manuel", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input) === "/api/v2/admin/cleanup/purge" && init?.method === "POST") {
        return response({
          requested: 1,
          scheduled: 1,
          scheduled_ids: ["33333333-3333-4333-8333-333333333333"],
          skipped_ids: [],
        });
      }
      return response({
        checked_at: "2026-09-17T12:00:00Z",
        items: [{
          id: "33333333-3333-4333-8333-333333333333",
          name: "Contenu individuel",
          size_bytes: 2_048,
          subscriber_count: 1,
          deletion_at: null,
        }],
      });
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<AdminCleanupPage onBack={vi.fn()} onNavigate={vi.fn()} onSessionExpired={vi.fn()} />);

    expect(await screen.findByText("Contenu individuel")).toBeTruthy();
    expect(screen.getByText("Non planifiée")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Purger Contenu individuel" }));
    fireEvent.click(screen.getByRole("button", { name: "Confirmer la purge" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(3));
    fireEvent.click(screen.getByRole("button", { name: "Actualiser" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(4));
  });

  it("pagine l’affichage sans réduire la purge aux éléments de la page", async () => {
    const items = Array.from({ length: 16 }, (_, index) => ({
      id: `00000000-0000-4000-8000-${String(index + 1).padStart(12, "0")}`,
      name: `Contenu ${String(index + 1).padStart(2, "0")}`,
      size_bytes: 1_024,
      subscriber_count: 0,
      deletion_at: "2026-09-18T10:00:00Z",
    }));
    let purgeIds: string[] = [];
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input) === "/api/v2/admin/cleanup/purge" && init?.method === "POST") {
        purgeIds = (JSON.parse(String(init.body)) as { torrent_ids: string[] }).torrent_ids;
        return response({
          requested: purgeIds.length,
          scheduled: purgeIds.length,
          scheduled_ids: purgeIds,
          skipped_ids: [],
        });
      }
      return response({ checked_at: "2026-09-17T12:00:00Z", items });
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<AdminCleanupPage onBack={vi.fn()} onNavigate={vi.fn()} onSessionExpired={vi.fn()} />);

    expect(await screen.findByText("Contenu 01")).toBeTruthy();
    expect(screen.queryByText("Contenu 16")).toBeNull();
    expect(screen.getByText("Page 1 sur 2 · 16 contenus")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Suivant" }));
    expect(screen.getByText("Contenu 16")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Purger les résultats filtrés (16)" }));
    fireEvent.click(screen.getByRole("button", { name: "Confirmer la purge" }));
    await waitFor(() => expect(purgeIds).toEqual(items.map((item) => item.id)));
  });
});
