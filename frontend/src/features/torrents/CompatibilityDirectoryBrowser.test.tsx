import { render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { I18nProvider } from "../../i18n";
import type { TorrentDownloadManifestPageV2 } from "../../api/client";
import { CompatibilityDirectoryBrowser } from "./CompatibilityDirectoryBrowser";

function response(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

const snapshot: TorrentDownloadManifestPageV2 = {
  snapshot_id: "a".repeat(64),
  manifest_version: 1,
  file_count: 14,
  total_size: 230_000_000_000,
  archive_available: false,
  retention_expires_at: null,
  offset: 0,
  limit: 50,
  items: [],
};

describe("CompatibilityDirectoryBrowser", () => {
  it("conserve le téléchargement ZIP des dossiers même si l’archive globale est indisponible", async () => {
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("download-directories")) {
        return response({
          snapshot_id: snapshot.snapshot_id,
          path: "",
          directories: [
            {
              name: "Saison 1",
              relative_path: "Saison 1",
              file_count: 7,
              total_size: 34_000_000_000,
              archive_available: true,
            },
            {
              name: "Saison 2",
              relative_path: "Saison 2",
              file_count: 7,
              total_size: 196_000_000_000,
              archive_available: false,
            },
          ],
          direct_file_count: 0,
          offset: 0,
          limit: 500,
          files: [],
        });
      }
      throw new Error(`Unexpected request: ${url}`);
    }));

    render(
      <I18nProvider>
        <CompatibilityDirectoryBrowser
          fallbackLoading={false}
          onDownloadFile={vi.fn()}
          onLoadFallbackPage={vi.fn()}
          onNativeDownload={vi.fn()}
          snapshot={snapshot}
          torrentId="d86528f5-bc01-4a8b-86a1-74fe3404864b"
        />
      </I18nProvider>,
    );

    const seasonOne = await screen.findByRole("link", {
      name: "Télécharger le dossier « Saison 1 » en ZIP",
    });
    expect(seasonOne.getAttribute("download")).toBe("Saison 1.zip");
    expect(seasonOne.getAttribute("href")).toContain("download-folder-archive");
    expect(seasonOne.getAttribute("href")).toContain("path=Saison+1");
    expect(screen.queryByRole("link", {
      name: "Télécharger le dossier « Saison 2 » en ZIP",
    })).toBeNull();

    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(1));
  });
});
