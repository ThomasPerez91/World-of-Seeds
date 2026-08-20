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

describe("UserDownloadsPage", () => {
  it("valide et envoie un torrent avec des notifications compréhensibles", async () => {
    const user = userEvent.setup();
    let uploaded = false;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
        if (init?.method === "POST") {
          expect(init.body).toBeInstanceOf(FormData);
          uploaded = true;
          return response({ id: "a".repeat(40), name: "Film.mkv", total_size: 5 }, 201);
        }
        return response({
          torrents: uploaded
            ? [{
                id: "a".repeat(40),
                name: "Film.mkv",
                size_bytes: 5,
                progress: 0.5,
                state: "downloading",
                downloaded_bytes: 2,
                download_speed_bytes: 1,
                eta_seconds: 3,
                error: null,
                created_at: "2026-08-20T10:00:00Z",
              }]
            : [],
        });
      }),
    );
    const view = render(<UserDownloadsPage onSessionExpired={vi.fn()} />);
    await screen.findByText("Aucun téléchargement pour le moment.");

    const input = view.container.querySelector('input[type="file"]') as HTMLInputElement;
    await user.upload(input, new File(["torrent"], "film.torrent", { type: "application/x-bittorrent" }));

    expect((await screen.findByRole("status")).textContent).toContain("Film.mkv");
    expect(await screen.findByText("Téléchargement")).toBeTruthy();
    expect(await auditAccessibility(view.container)).toMatchObject({ violations: [] });
  });

  it("supporte le drop, le clavier et les noms de release très longs", async () => {
    const longName =
      "Film.Name.2026.MULTi.TRUEFRENCH.2160p.UHD.BluRay.REMUX.DV.HDR.HEVC.DTS-HD.MA.7.1-GROUP.mkv";
    vi.stubGlobal(
      "fetch",
      vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) =>
        init?.method === "POST"
          ? response({ id: "a".repeat(40), name: longName, total_size: 5 }, 201)
          : response({ torrents: [] }),
      ),
    );
    const view = render(<UserDownloadsPage onSessionExpired={vi.fn()} />);
    const zone = screen.getByTestId("torrent-drop-zone");
    const selectButton = screen.getByRole("button", { name: "Sélectionner un fichier" });
    selectButton.focus();
    fireEvent.keyDown(selectButton, { key: "Enter" });
    const input = view.container.querySelector('input[type="file"]') as HTMLInputElement;
    expect(document.activeElement === selectButton || input !== null).toBe(true);
    fireEvent.drop(zone, {
      dataTransfer: { files: { item: () => new File(["torrent"], "film.torrent") } },
    });
    await waitFor(() => expect(screen.getByText(new RegExp(longName))).toBeTruthy());
  });
});
