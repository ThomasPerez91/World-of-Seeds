import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { I18nProvider } from "../../i18n";
import type { TorrentDownloadManifestPageV2 } from "../../api/client";
import { buildManifestTree, CompatibilityDirectoryBrowser } from "./CompatibilityDirectoryBrowser";

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
  it("construit une arborescence imbriquée et agrège les dossiers", () => {
    const tree = buildManifestTree([
      { id: "one", file_index: 0, relative_path: "Saison 1/Episode 1/video.mkv", size: 10 },
      { id: "two", file_index: 1, relative_path: "Saison 1/Episode 2/video.mkv", size: 20 },
      { id: "root", file_index: 2, relative_path: "poster.jpg", size: 3 },
    ]);

    expect(tree).toMatchObject([
      {
        kind: "directory",
        name: "Saison 1",
        relativePath: "Saison 1",
        fileCount: 2,
        totalSize: 30,
        children: [
          {
            kind: "directory",
            name: "Episode 1",
            fileCount: 1,
            totalSize: 10,
            children: [{ kind: "file", name: "video.mkv", relativePath: "Saison 1/Episode 1/video.mkv" }],
          },
          { kind: "directory", name: "Episode 2", fileCount: 1, totalSize: 20 },
        ],
      },
      { kind: "file", name: "poster.jpg", relativePath: "poster.jpg" },
    ]);
  });

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
          onDownloadFolder={vi.fn()}
          onLoadFallbackPage={vi.fn()}
          onNativeDownload={vi.fn()}
          snapshot={snapshot}
          torrentId="d86528f5-bc01-4a8b-86a1-74fe3404864b"
        />
      </I18nProvider>,
    );

    const seasonOne = await screen.findByRole("link", {
      name: "Télécharger le ZIP — Saison 1",
    });
    expect(seasonOne.getAttribute("download")).toBe("Saison 1.zip");
    expect(seasonOne.getAttribute("href")).toContain("download-folder-archive");
    expect(seasonOne.getAttribute("href")).toContain("path=Saison+1");
    expect(screen.queryByRole("link", {
      name: "Télécharger le ZIP — Saison 2",
    })).toBeNull();

    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(1));
  });

  it("permet de récupérer un sous-dossier avec le sélecteur natif", async () => {
    const onDownloadFolder = vi.fn();
    vi.stubGlobal("showDirectoryPicker", vi.fn());
    vi.stubGlobal("fetch", vi.fn(async () => response({
      snapshot_id: snapshot.snapshot_id,
      path: "",
      directories: [{
        name: "Saison 1",
        relative_path: "Saison 1",
        file_count: 7,
        total_size: 34_000_000_000,
        archive_available: false,
      }],
      direct_file_count: 0,
      offset: 0,
      limit: 500,
      files: [],
    })));

    render(
      <I18nProvider>
        <CompatibilityDirectoryBrowser
          fallbackLoading={false}
          onDownloadFile={vi.fn()}
          onDownloadFolder={onDownloadFolder}
          onLoadFallbackPage={vi.fn()}
          onNativeDownload={vi.fn()}
          snapshot={snapshot}
          torrentId="d86528f5-bc01-4a8b-86a1-74fe3404864b"
        />
      </I18nProvider>,
    );

    fireEvent.click(await screen.findByRole("button", { name: "Télécharger le dossier « Saison 1 »" }));
    expect(onDownloadFolder).toHaveBeenCalledWith({
      name: "Saison 1",
      relative_path: "Saison 1",
      file_count: 7,
      total_size: 34_000_000_000,
      archive_available: false,
    });
  });
});
