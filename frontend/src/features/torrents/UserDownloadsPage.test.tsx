import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { auditAccessibility } from "../../test/accessibility";
import { UserDownloadsPage } from "./UserDownloadsPage";

function response(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function torrent(overrides: Record<string, unknown> = {}) {
  return {
    id: "d86528f5-bc01-4a8b-86a1-74fe3404864b",
    name: "Film.mkv",
    total_size: 5,
    progress: 0.5,
    state: "active",
    error_code: null,
    created_at: "2026-08-20T10:00:00Z",
    updated_at: "2026-08-20T10:05:00Z",
    ...overrides,
  };
}

describe("UserDownloadsPage", () => {
  it("utilise l’API V2 et affiche les états durables sans style inline", async () => {
    const user = userEvent.setup();
    let uploaded = false;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        expect(url.startsWith("/api/v2/torrents")).toBe(true);
        if (init?.method === "POST") {
          expect(init.body).toBeInstanceOf(FormData);
          uploaded = true;
          return response({
            ...torrent({ state: "requested", progress: 0 }),
            created: true,
            storage_pressure: "normal",
          }, 201);
        }
        return response({
          items: uploaded ? [torrent()] : [],
          offset: 0,
          limit: 10,
          total: uploaded ? 1 : 0,
        });
      }),
    );
    const view = render(<UserDownloadsPage onSessionExpired={vi.fn()} />);
    await screen.findByText("Aucun téléchargement pour le moment.");

    const input = view.container.querySelector('input[type="file"]') as HTMLInputElement;
    await user.upload(
      input,
      new File(["torrent"], "film.torrent", { type: "application/x-bittorrent" }),
    );

    expect((await screen.findByRole("status")).textContent).toContain("Film.mkv");
    expect(await screen.findByText("En cours")).toBeTruthy();
    expect(screen.getByRole("progressbar").getAttribute("value")).toBe("0.5");
    expect(screen.getByRole("columnheader", { name: "Actions" })).toBeTruthy();
    expect(view.container.querySelector(".torrent-row-actions")).toBeTruthy();
    expect(view.container.querySelector("[style]")).toBeNull();
    expect(await auditAccessibility(view.container)).toMatchObject({ violations: [] });
  });

  it("pagine côté serveur et conserve les noms longs dans une cellule à ellipsis", async () => {
    const user = userEvent.setup();
    const longName =
      "Film.Name.2026.MULTi.TRUEFRENCH.2160p.UHD.BluRay.REMUX.DV.HDR.HEVC.DTS-HD.MA.7.1-GROUP.mkv";
    const calls: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        calls.push(url);
        const secondPage = url.includes("offset=10");
        return response({
          items: [torrent({
            id: secondPage
              ? "c8c69f91-8e73-48b3-a14f-35199ce7c101"
              : "d86528f5-bc01-4a8b-86a1-74fe3404864b",
            name: secondPage ? "Deuxième page.mkv" : longName,
            state: secondPage ? "ready" : "requested",
            progress: secondPage ? 1 : 0,
          })],
          offset: secondPage ? 10 : 0,
          limit: 10,
          total: 11,
        });
      }),
    );
    const view = render(<UserDownloadsPage onSessionExpired={vi.fn()} />);

    expect(await screen.findByTitle(longName)).toBeTruthy();
    expect(view.container.querySelector(".torrent-name-cell > span")).toBeTruthy();
    expect(screen.getByText("Page 1 sur 2 · 11 demandes")).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "Suivant" }));

    expect(await screen.findByText("Deuxième page.mkv")).toBeTruthy();
    expect(screen.getByText("Disponible")).toBeTruthy();
    expect(calls.some((url) => url.includes("offset=10") && url.includes("limit=10"))).toBe(true);
  });

  it("supporte le drop, le clavier et les erreurs métier bornées", async () => {
    const longName = "Release-avec-un-nom-très-long.mkv";
    vi.stubGlobal(
      "fetch",
      vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) =>
        init?.method === "POST"
          ? response({
              ...torrent({ name: longName, state: "requested", progress: 0 }),
              created: false,
              storage_pressure: "warning",
            }, 201)
          : response({
              items: [torrent({ state: "error", error_code: "torrent_failed" })],
              offset: 0,
              limit: 10,
              total: 1,
            }),
      ),
    );
    const view = render(<UserDownloadsPage onSessionExpired={vi.fn()} />);
    const zone = screen.getByTestId("torrent-drop-zone");
    const selectButton = screen.getByRole("button", { name: "Ajouter un torrent" });
    selectButton.focus();
    fireEvent.keyDown(selectButton, { key: "Enter" });
    const input = view.container.querySelector('input[type="file"]') as HTMLInputElement;
    expect(document.activeElement === selectButton || input !== null).toBe(true);
    fireEvent.drop(zone, {
      dataTransfer: { files: { item: () => new File(["torrent"], "film.torrent") } },
    });

    await waitFor(() => expect(screen.getByText(new RegExp(longName))).toBeTruthy());
    expect(await screen.findByText("Erreur")).toBeTruthy();
    expect(screen.getByRole("alert").textContent).toContain("intervention");
  });
});
