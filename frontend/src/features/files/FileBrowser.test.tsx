import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { DirectoryListing, TrashEntry } from "../../api/client";
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
      <FileBrowser
        onFilesChanged={onFilesChanged}
        onSessionExpired={vi.fn()}
        revision={0}
      />,
    );

    const trashButton = await screen.findByRole("button", {
      name: "Placer movie.mkv dans la corbeille",
    });
    expect(screen.getByRole("table").querySelector("caption")?.textContent).toContain(
      "downloads",
    );
    expect(await auditAccessibility(view.container)).toMatchObject({ violations: [] });

    await user.click(trashButton);
    const cancelButton = screen.getByRole("button", { name: "Annuler" });
    expect(document.activeElement).toBe(cancelButton);
    await user.click(screen.getByRole("button", { name: "Placer dans la corbeille" }));

    expect(await screen.findByRole("status")).toHaveProperty(
      "textContent",
      "« movie.mkv » a été placé dans la corbeille.",
    );
    await waitFor(() => expect(onFilesChanged).toHaveBeenCalledOnce());
  });
});
