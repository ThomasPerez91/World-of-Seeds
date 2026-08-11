import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { TrashEntry, TrashListing } from "../../api/client";
import { auditAccessibility } from "../../test/accessibility";
import { TrashBrowser } from "./TrashBrowser";

const entry: TrashEntry = {
  id: "6ed09f19-65c2-4ed8-932a-08e239855aae",
  original_path: "downloads/movie.mkv",
  name: "movie.mkv",
  kind: "file",
  size: 5,
  deleted_at: "2026-08-11T09:00:00Z",
};

function response(body: unknown, status = 200): Response {
  return new Response(status === 204 ? null : JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("TrashBrowser", () => {
  it("sécurise la suppression définitive et restaure sans perdre le focus", async () => {
    const user = userEvent.setup();
    const onFilesChanged = vi.fn();
    let restored = false;
    const listing = (): TrashListing => ({ entries: restored ? [] : [entry], truncated: false });
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url === "/api/v1/trash" && (init?.method === undefined || init.method === "GET")) {
          return response(listing());
        }
        if (url.endsWith(`/${entry.id}/restore`) && init?.method === "POST") {
          restored = true;
          return response({ path: entry.original_path, name: entry.name, kind: entry.kind });
        }
        throw new Error(`Requête inattendue : ${url}`);
      }),
    );

    const view = render(
      <TrashBrowser
        onFilesChanged={onFilesChanged}
        onSessionExpired={vi.fn()}
        revision={0}
      />,
    );
    await screen.findByRole("heading", { name: "movie.mkv" });
    expect(await auditAccessibility(view.container)).toMatchObject({ violations: [] });

    const purgeButton = screen.getByRole("button", {
      name: "Supprimer définitivement movie.mkv",
    });
    await user.click(purgeButton);
    expect(document.activeElement).toBe(screen.getByRole("button", { name: "Annuler" }));
    expect(await auditAccessibility(view.container)).toMatchObject({ violations: [] });
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(document.activeElement).toBe(purgeButton);

    await user.click(screen.getByRole("button", { name: "Restaurer" }));
    const restoreConfirmation = within(screen.getByRole("dialog")).getByRole("button", {
      name: "Restaurer",
    });
    expect(document.activeElement).toBe(restoreConfirmation);
    await user.click(restoreConfirmation);

    await screen.findByText("« movie.mkv » a été restauré.");
    await waitFor(() => expect(onFilesChanged).toHaveBeenCalledOnce());
  });
});
