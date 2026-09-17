import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { auditAccessibility } from "../../test/accessibility";
import { AdminNewGreedyPage } from "./AdminNewGreedyPage";
import { AdminQBittorrentPage } from "./AdminQBittorrentPage";

function response(body: unknown): Response {
  return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
}

describe("Admin runtime pages", () => {
  it("affiche tous les torrents qBittorrent et ne rafraîchit qu'à la demande", async () => {
    const fetchMock = vi.fn(async () => response({
      checked_at: "2026-01-01T12:00:00Z",
      download_speed_bytes: 2048,
      upload_speed_bytes: 1024,
      truncated: false,
      torrents: [{ hash: "a".repeat(40), name: "Un nom de torrent volontairement très long", size_bytes: 4096, state: "downloading", progress: 0.5 }],
    }));
    vi.stubGlobal("fetch", fetchMock);
    const view = render(<AdminQBittorrentPage onBack={vi.fn()} onNavigate={vi.fn()} onSessionExpired={vi.fn()} />);

    expect(await screen.findByText("Un nom de torrent volontairement très long")).toBeTruthy();
    expect(screen.getByText("50 %")).toBeTruthy();
    expect(fetchMock).toHaveBeenCalledTimes(1);
    await new Promise((resolve) => window.setTimeout(resolve, 20));
    expect(fetchMock).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", { name: "Actualiser" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    expect(await auditAccessibility(view.container)).toMatchObject({ violations: [] });
  });

  it("affiche le contrôle NewGreedy et le nom corrélé", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => response({
      checked_at: "2026-01-01T12:00:00Z",
      torrents: [{ hash: "b".repeat(40), name: "Torrent associé", status: "seeding", downloaded_bytes: 1024, uploaded_bytes: 2048, ratio: 2 }],
    })));
    const view = render(<AdminNewGreedyPage onBack={vi.fn()} onNavigate={vi.fn()} onSessionExpired={vi.fn()} />);

    expect(await screen.findByText("Torrent associé")).toBeTruthy();
    expect(screen.getByText("En seed")).toBeTruthy();
    expect(screen.getByText("2", { selector: "td" })).toBeTruthy();
    expect(await auditAccessibility(view.container)).toMatchObject({ violations: [] });
  });
});
