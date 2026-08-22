import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { DirectoryListing, TrashEntry } from "../../api/client";
import { FeedbackProvider } from "../../components/Feedback";
import { auditAccessibility } from "../../test/accessibility";
import { FileBrowser } from "./FileBrowser";

const movie = {
  name: "movie.mkv",
  path: "downloads/movie.mkv",
  kind: "file" as const,
  size: 5,
  modified_at: "2026-08-11T08:00:00Z",
  media_type: "video/x-matroska",
  blocked: false,
};

function response(body: unknown, status = 200): Response {
  return new Response(status === 204 ? null : JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("FileBrowser", () => {
  it("expose des actions accessibles et place un gros fichier en corbeille sans rechargement", async () => {
    const user = userEvent.setup();
    const onFilesChanged = vi.fn();
    let trashed = false;
    const listing = (): DirectoryListing => ({
      path: "downloads",
      breadcrumbs: [
        { label: "Mes fichiers", path: "" },
        { label: "downloads", path: "downloads" },
      ],
      entries: trashed ? [] : [movie],
      storage: { total: 1000, used: 5, available: 995 },
      truncated: false,
    });
    const trashEntry: TrashEntry = {
      id: "6ed09f19-65c2-4ed8-932a-08e239855aae",
      original_path: movie.path,
      name: movie.name,
      kind: "file",
      size: movie.size,
      deleted_at: "2026-08-11T09:00:00Z",
    };
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url.startsWith("/api/v1/files")) return response(listing());
        if (url === "/api/v1/trash" && init?.method === "POST") {
          trashed = true;
          return response(trashEntry, 201);
        }
        throw new Error(`Requête inattendue : ${url}`);
      }),
    );

    window.history.replaceState({}, "", "/?path=downloads");
    const view = render(
      <FeedbackProvider>
        <FileBrowser
          onFilesChanged={onFilesChanged}
          onSessionExpired={vi.fn()}
          onStorageChanged={vi.fn()}
          revision={0}
        />
      </FeedbackProvider>,
    );

    const trashButton = await screen.findByRole("button", {
      name: "Placer movie.mkv dans la corbeille",
    });
    const fileNameCell = screen.getByText("movie").closest("td");
    expect(screen.getByText(".mkv")).toBeTruthy();
    expect(fileNameCell?.classList.contains("file-name-cell")).toBe(true);
    expect(fileNameCell?.firstElementChild?.classList.contains("file-name-content")).toBe(
      true,
    );
    expect(screen.getByRole("table").querySelector("caption")?.textContent).toContain(
      "downloads",
    );
    expect(await auditAccessibility(view.container)).toMatchObject({ violations: [] });

    await user.click(trashButton);
    expect(screen.getByRole("dialog").querySelector("[style]")).toBeNull();
    expect(await auditAccessibility(document.body)).toMatchObject({ violations: [] });
    const cancelButton = screen.getByRole("button", { name: "Annuler" });
    expect(document.activeElement).toBe(cancelButton);
    await user.click(screen.getByRole("button", { name: "Placer dans la corbeille" }));

    expect(await screen.findByText("« movie.mkv » a été placé dans la corbeille.")).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "Fermer le message" }));
    expect(screen.queryByText("« movie.mkv » a été placé dans la corbeille.")).toBeNull();
    await waitFor(() => expect(onFilesChanged).toHaveBeenCalledOnce());
  });

  it.each([320, 375, 390, 430])(
    "conserve les noms très longs et les actions au viewport %d px",
    async (width) => {
      Object.defineProperty(window, "innerWidth", { configurable: true, value: width });
      const longName =
        "Film.Name.2026.MULTi.TRUEFRENCH.2160p.UHD.BluRay.REMUX.DV.HDR.HEVC.DTS-HD.MA.7.1-GROUP.mkv";
      vi.stubGlobal(
        "fetch",
        vi.fn(async () =>
          response({
            path: "downloads",
            breadcrumbs: [
              { label: "Mes fichiers", path: "" },
              { label: "Un-dossier-avec-un-nom-extremement-long-qui-ne-doit-pas-deborder", path: "downloads" },
            ],
            entries: [{ ...movie, name: longName, path: `downloads/${longName}` }],
            storage: { total: 1000, used: 5, available: 995 },
            truncated: false,
          }),
        ),
      );
      window.history.replaceState({}, "", "/?path=downloads");

      render(
        <FeedbackProvider>
          <FileBrowser
            onFilesChanged={vi.fn()}
            onSessionExpired={vi.fn()}
            onStorageChanged={vi.fn()}
            revision={0}
          />
        </FeedbackProvider>,
      );

      expect(await screen.findByText(longName.slice(0, -4))).toBeTruthy();
      expect(screen.getByRole("link", { name: `Télécharger ${longName}` })).toBeTruthy();
      expect(screen.getByRole("button", { name: `Renommer ${longName}` })).toBeTruthy();
    },
  );
});
