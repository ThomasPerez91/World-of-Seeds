import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { auditAccessibility } from "../../test/accessibility";
import { FeedbackProvider } from "../../components/Feedback";
import {
  MAX_TORRENT_BATCH_FILES,
  TORRENT_UPLOAD_CONCURRENCY,
  UserDownloadsPage,
} from "./UserDownloadsPage";

function response(body: unknown, status = 200): Response {
  return new Response(status === 204 ? null : JSON.stringify(body), {
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

function renderPage() {
  return render(
    <FeedbackProvider>
      <UserDownloadsPage onSessionExpired={vi.fn()} />
    </FeedbackProvider>,
  );
}

class MockWebSocket {
  static instances: MockWebSocket[] = [];

  readonly url: string;
  onopen: ((event: Event) => void) | null = null;
  onmessage: ((event: MessageEvent) => void) | null = null;
  onerror: ((event: Event) => void) | null = null;
  onclose: ((event: Event) => void) | null = null;
  closed = false;

  constructor(url: string) {
    this.url = url;
    MockWebSocket.instances.push(this);
  }

  open() {
    this.onopen?.(new Event("open"));
  }

  message(payload: object) {
    this.onmessage?.(new MessageEvent("message", { data: JSON.stringify(payload) }));
  }

  close() {
    if (this.closed) return;
    this.closed = true;
    this.onclose?.(new Event("close"));
  }
}

describe("UserDownloadsPage", () => {
  it("remplace le polling par les invalidations WebSocket et resynchronise après reconnexion", async () => {
    MockWebSocket.instances = [];
    const fetchMock = vi.fn(async () => response({
      items: [torrent()], offset: 0, limit: 10, total: 1,
    }));
    vi.stubGlobal("fetch", fetchMock);
    vi.stubGlobal("WebSocket", MockWebSocket);
    const view = renderPage();

    await screen.findByText("En cours");
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(MockWebSocket.instances).toHaveLength(1);
    expect(MockWebSocket.instances[0].url).toContain("/api/v2/torrents/events");
    MockWebSocket.instances[0].open();
    MockWebSocket.instances[0].message({ type: "heartbeat" });
    await act(async () => Promise.resolve());
    expect(fetchMock).toHaveBeenCalledTimes(1);
    MockWebSocket.instances[0].message({
      type: "torrent.ready",
      request_id: torrent().id,
      occurred_at: "2026-08-28T07:00:00+00:00",
      passkey: "must-not-be-accepted",
    });
    await act(async () => Promise.resolve());
    expect(fetchMock).toHaveBeenCalledTimes(1);

    MockWebSocket.instances[0].message({
      type: "torrent.ready",
      request_id: torrent().id,
      occurred_at: "2026-08-28T07:00:00+00:00",
    });
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));

    vi.useFakeTimers();
    MockWebSocket.instances[0].close();
    await act(async () => { await vi.advanceTimersByTimeAsync(1_000); });
    expect(MockWebSocket.instances).toHaveLength(2);
    MockWebSocket.instances[1].open();
    vi.useRealTimers();
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(3));
    view.unmount();
  });

  it("ignore une invalidation WebSocket obsolète après le rafraîchissement autoritatif d’un upload", async () => {
    MockWebSocket.instances = [];
    const user = userEvent.setup();
    let offsetZeroRequests = 0;
    let offsetTenRequests = 0;
    let releaseUpload!: () => void;
    let markUploadStarted!: () => void;
    let releaseStalePage!: () => void;
    let markStalePageStarted!: () => void;
    const uploadPending = new Promise<void>((resolve) => { releaseUpload = resolve; });
    const uploadStarted = new Promise<void>((resolve) => { markUploadStarted = resolve; });
    const stalePagePending = new Promise<void>((resolve) => { releaseStalePage = resolve; });
    const stalePageStarted = new Promise<void>((resolve) => { markStalePageStarted = resolve; });

    vi.stubGlobal("WebSocket", MockWebSocket);
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (init?.method === "POST") {
          markUploadStarted();
          await uploadPending;
          return response({
            ...torrent({ name: "Ajout récent.mkv", state: "requested", progress: 0 }),
            created: true,
            storage_pressure: "normal",
          }, 201);
        }
        if (url.includes("offset=10")) {
          offsetTenRequests += 1;
          if (offsetTenRequests > 1) {
            markStalePageStarted();
            await stalePagePending;
          }
          return response({
            items: [torrent({
              id: "c8c69f91-8e73-48b3-a14f-35199ce7c101",
              name: offsetTenRequests > 1 ? "Page obsolète.mkv" : "Deuxième page.mkv",
            })],
            offset: 10,
            limit: 10,
            total: 11,
          });
        }
        offsetZeroRequests += 1;
        return response({
          items: [torrent({ name: offsetZeroRequests === 1 ? "Page initiale.mkv" : "Page fraîche.mkv" })],
          offset: 0,
          limit: 10,
          total: 11,
        });
      }),
    );
    const view = renderPage();

    await screen.findByText("Page initiale.mkv");
    await user.click(screen.getByRole("button", { name: "Suivant" }));
    await screen.findByText("Deuxième page.mkv");
    const input = view.container.querySelector('input[type="file"]') as HTMLInputElement;
    fireEvent.change(input, { target: { files: [new File(["torrent"], "nouveau.torrent")] } });
    await uploadStarted;

    MockWebSocket.instances.at(-1)?.message({
      type: "torrent.ready",
      request_id: torrent().id,
      occurred_at: "2026-08-30T12:00:00+00:00",
    });
    await stalePageStarted;
    releaseUpload();
    await screen.findByText("Page fraîche.mkv");

    releaseStalePage();
    await act(async () => Promise.resolve());
    await waitFor(() => expect(screen.queryByText("Page obsolète.mkv")).toBeNull());
    expect(screen.getByText("Page fraîche.mkv")).toBeTruthy();
    view.unmount();
  });

  it("ne recharge plus automatiquement la liste toutes les dix secondes", async () => {
    MockWebSocket.instances = [];
    const fetchMock = vi.fn(async () => response({
      items: [torrent()], offset: 0, limit: 10, total: 1,
    }));
    vi.stubGlobal("fetch", fetchMock);
    vi.stubGlobal("WebSocket", MockWebSocket);
    const view = renderPage();

    await screen.findByText("En cours");
    vi.useFakeTimers();
    await act(async () => { await vi.advanceTimersByTimeAsync(30_000); });
    expect(fetchMock).toHaveBeenCalledTimes(1);
    vi.useRealTimers();
    view.unmount();
  });

  it("télécharge récursivement un manifeste READY via le sélecteur de dossier", async () => {
    const user = userEvent.setup();
    const writes: number[] = [];
    const fileHandle = {
      createWritable: vi.fn(async () => ({
        seek: vi.fn(),
        write: vi.fn(async (value: Uint8Array) => writes.push(...value)),
        close: vi.fn(),
      })),
    };
    const directory = {
      getDirectoryHandle: vi.fn(async () => directory),
      getFileHandle: vi.fn(async () => fileHandle),
    };
    vi.stubGlobal("showDirectoryPicker", vi.fn(async () => directory));
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.includes("download-manifest")) {
          return response({
            snapshot_id: "a".repeat(64),
            manifest_version: 1,
            file_count: 1,
            total_size: 3,
            archive_available: true,
            offset: 0,
            limit: 500,
            items: [{ id: "file-id", file_index: 0, relative_path: "Film/file.bin", size: 3 }],
          });
        }
        if (url.includes("/files/file-id/download")) {
          return new Response(new Uint8Array([1, 2, 3]).buffer, {
            headers: { "X-WOS-Manifest-Version": "1" },
          });
        }
        return response({
          items: [torrent({ state: "ready", progress: 1 })],
          offset: 0,
          limit: 10,
          total: 1,
        });
      }),
    );
    const view = renderPage();

    await user.click(await screen.findByRole("button", { name: "Télécharger" }));

    expect(await screen.findByText("« Film.mkv » a été téléchargé.")).toBeTruthy();
    expect(screen.getByText("1/1 fichiers · 3 o sur 3 o")).toBeTruthy();
    expect(writes).toEqual([1, 2, 3]);
    expect(view.container.querySelector("[style]")).toBeNull();
  });

  it("propose les fichiers et le ZIP streamé sans File System Access API", async () => {
    const user = userEvent.setup();
    let manifestRequests = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        if (String(input).includes("download-manifest")) {
          manifestRequests += 1;
          expect(String(input)).toContain("limit=50");
          return response({
            snapshot_id: "b".repeat(64),
            manifest_version: 1,
            file_count: 50_000,
            total_size: 3,
            archive_available: true,
            offset: 0,
            limit: 500,
            items: [{ id: "file-id", file_index: 0, relative_path: "Film/file.bin", size: 3 }],
          });
        }
        return response({
          items: [torrent({ state: "ready", progress: 1 })],
          offset: 0,
          limit: 10,
          total: 1,
        });
      }),
    );
    const view = renderPage();

    await user.click(await screen.findByRole("button", { name: "Télécharger" }));

    const archive = await screen.findByRole("link", {
      name: "Télécharger le petit dossier en ZIP",
    });
    const individual = screen.getByRole("link", { name: "Télécharger" });
    expect(archive.getAttribute("href")).toContain("download-archive?snapshot=");
    expect(individual.getAttribute("href")).toContain("/files/file-id/download?snapshot=");
    expect(screen.getByText("Film/file.bin")).toBeTruthy();
    expect(screen.getByText("1 / 1000")).toBeTruthy();
    expect(manifestRequests).toBe(1);
    expect(view.container.querySelector("[style]")).toBeNull();
    expect(await auditAccessibility(view.container)).toMatchObject({ violations: [] });
  });

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
    const view = renderPage();
    await screen.findByText("Aucun téléchargement pour le moment.");

    const input = view.container.querySelector('input[type="file"]') as HTMLInputElement;
    await user.upload(
      input,
      new File(["torrent"], "film.torrent", { type: "application/x-bittorrent" }),
    );

    expect(await screen.findByText("Lot terminé")).toBeTruthy();
    expect(screen.getByText("1 ajoutés · 0 déjà présents · 0 invalides · 0 en erreur")).toBeTruthy();
    expect(await screen.findByText("En cours")).toBeTruthy();
    expect(screen.getByRole("progressbar", { name: "Progression de Film.mkv" }).getAttribute("value")).toBe("0.5");
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
    const view = renderPage();

    expect(await screen.findByTitle(longName)).toBeTruthy();
    expect(view.container.querySelector(".torrent-name-cell > span")).toBeTruthy();
    expect(screen.getByText("Page 1 sur 2 · 11 demandes")).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "Suivant" }));

    expect(await screen.findByText("Deuxième page.mkv")).toBeTruthy();
    expect(screen.getByText("Disponible")).toBeTruthy();
    expect(calls.some((url) => url.includes("offset=10") && url.includes("limit=10"))).toBe(true);
  });

  it("supporte le drop, le sélecteur clavier et les erreurs métier bornées", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) =>
        init?.method === "POST"
          ? response({
              ...torrent({ state: "requested", progress: 0 }),
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
    const view = renderPage();
    const zone = screen.getByTestId("torrent-drop-zone");
    const selectButton = screen.getAllByRole("button", { name: "Ajouter des torrents" })[1];
    const input = view.container.querySelector('input[type="file"]') as HTMLInputElement;
    const inputClick = vi.spyOn(input, "click");
    selectButton.focus();
    await userEvent.setup().keyboard("{Enter}");
    expect(inputClick).toHaveBeenCalledOnce();
    const dropped = new File(["torrent"], "film.torrent", { type: "application/x-bittorrent" });
    fireEvent.drop(zone, {
      dataTransfer: { files: [dropped] },
    });

    await waitFor(() => expect(screen.getByText("film.torrent")).toBeTruthy());
    expect(screen.getByText("Déjà présent")).toBeTruthy();
    expect(await screen.findByText("Erreur")).toBeTruthy();
    expect(screen.getByRole("alert").textContent).toContain("intervention");
  });

  it("annule directement une demande via l’API V2", async () => {
    const user = userEvent.setup();
    let cancelled = false;
    const calls: Array<{ method: string; url: string }> = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        const method = init?.method ?? "GET";
        calls.push({ method, url });
        if (method === "DELETE") {
          cancelled = true;
          return response(null, 204);
        }
        return response({
          items: [torrent({ state: cancelled ? "cancelled" : "active" })],
          offset: 0,
          limit: 10,
          total: 1,
        });
      }),
    );
    const view = renderPage();

    await user.click(await screen.findByRole("button", { name: "Annuler la demande Film.mkv" }));
    expect(await screen.findByText("La demande « Film.mkv » a été annulée.")).toBeTruthy();
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(await auditAccessibility(document.body)).toMatchObject({ violations: [] });
    expect(await screen.findByText("Annulé")).toBeTruthy();
    expect(calls).toContainEqual({
      method: "DELETE",
      url: "/api/v2/torrents/d86528f5-bc01-4a8b-86a1-74fe3404864b",
    });
    expect(view.container.querySelector("[style]")).toBeNull();
  });

  it.each([1, 2, 10, 50])(
    "traite un lot de %d fichiers avec au plus trois envois concurrents",
    async (count) => {
      let active = 0;
      let maximumActive = 0;
      let postCount = 0;
      vi.stubGlobal(
        "fetch",
        vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
          if (init?.method === "POST") {
            postCount += 1;
            active += 1;
            maximumActive = Math.max(maximumActive, active);
            await new Promise((resolve) => window.setTimeout(resolve, 2));
            active -= 1;
            return response({
              ...torrent({ name: `Film ${postCount}` }),
              created: true,
              storage_pressure: "normal",
            }, 201);
          }
          return response({ items: [], offset: 0, limit: 10, total: 0 });
        }),
      );
      const view = renderPage();
      await screen.findByText("Aucun téléchargement pour le moment.");
      const input = view.container.querySelector('input[type="file"]') as HTMLInputElement;
      const files = Array.from({ length: count }, (_, index) =>
        new File([`torrent-${index}`], `film-${index}.torrent`, {
          type: "application/x-bittorrent",
          lastModified: index + 1,
        }));

      fireEvent.change(input, { target: { files } });

      await screen.findByText(
        `${count} ajoutés · 0 déjà présents · 0 invalides · 0 en erreur`,
      );
      expect(postCount).toBe(count);
      expect(maximumActive).toBe(Math.min(count, TORRENT_UPLOAD_CONCURRENCY));
      expect(maximumActive).toBeLessThanOrEqual(TORRENT_UPLOAD_CONCURRENCY);
      expect(input.multiple).toBe(true);
    },
  );

  it("isole les doublons, invalides et échecs sans interrompre le lot", async () => {
    const timestamp = 1_800_000_000_000;
    const original = new File(["same"], "same.torrent", { lastModified: timestamp });
    const duplicate = new File(["same"], "same.torrent", { lastModified: timestamp });
    const files = [
      original,
      duplicate,
      new File(["invalid"], "notes.txt"),
      new File([], "empty.torrent"),
      new File(["server"], "server-error.torrent"),
      new File(["ok"], "ok.torrent"),
    ];
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
        if (init?.method !== "POST") {
          return response({ items: [], offset: 0, limit: 10, total: 0 });
        }
        const file = (init.body as FormData).get("torrent") as File;
        if (file.name === "server-error.torrent") return response({ detail: "failed" }, 500);
        return response({
          ...torrent({ name: file.name }),
          created: true,
          storage_pressure: "normal",
        }, 201);
      });
    vi.stubGlobal("fetch", fetchMock);
    const view = renderPage();
    await screen.findByText("Aucun téléchargement pour le moment.");
    const input = view.container.querySelector('input[type="file"]') as HTMLInputElement;

    fireEvent.change(input, { target: { files } });

    await screen.findByText("2 ajoutés · 1 déjà présents · 2 invalides · 1 en erreur");
    expect(screen.getAllByText("Ajouté")).toHaveLength(2);
    expect(screen.getByText("Déjà présent")).toBeTruthy();
    expect(screen.getAllByText("Invalide")).toHaveLength(2);
    expect(screen.getByText("Erreur", { selector: ".batch-result" })).toBeTruthy();
    const postCalls = fetchMock.mock.calls.filter(([, init]) => init?.method === "POST");
    expect(postCalls).toHaveLength(3);
  });

  it("classe 409, 413, 422, quota, 503 et erreur réseau sans arrêter les autres envois", async () => {
    const files = [
      "ok-before.torrent",
      "conflict-409.torrent",
      "large-413.torrent",
      "invalid-422.torrent",
      "quota-507.torrent",
      "service-503.torrent",
      "network.torrent",
      "ok-after.torrent",
    ].map((name, index) => new File([`payload-${index}`], name));
    const successful: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
        if (init?.method !== "POST") {
          return response({ items: [], offset: 0, limit: 10, total: 0 });
        }
        const file = (init.body as FormData).get("torrent") as File;
        if (file.name === "network.torrent") throw new TypeError("network unavailable");
        const status = Number(file.name.match(/-(409|413|422|507|503)\./)?.[1] ?? 0);
        if (status !== 0) {
          return response({
            detail: {
              code: status === 507 ? "user_quota_exceeded" : `upload_${status}`,
              message: "rejected",
            },
          }, status);
        }
        successful.push(file.name);
        return response({
          ...torrent({ name: file.name }),
          created: true,
          storage_pressure: file.name === "ok-before.torrent" ? "warning" : "normal",
        }, 201);
      }),
    );
    const view = renderPage();
    await screen.findByText("Aucun téléchargement pour le moment.");
    const input = view.container.querySelector('input[type="file"]') as HTMLInputElement;

    fireEvent.change(input, { target: { files } });

    await screen.findByText("2 ajoutés · 0 déjà présents · 2 invalides · 4 en erreur");
    expect(successful).toEqual(["ok-before.torrent", "ok-after.torrent"]);
    expect(screen.getAllByText("Invalide")).toHaveLength(2);
    expect(screen.getAllByText("Erreur", { selector: ".batch-result" })).toHaveLength(4);
  });

  it("désactive les entrées pendant un envoi lent et réutilise picker puis drop", async () => {
    let releaseFirst!: () => void;
    const firstPending = new Promise<void>((resolve) => { releaseFirst = resolve; });
    const uploaded: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
        if (init?.method !== "POST") {
          return response({ items: [], offset: 0, limit: 10, total: 0 });
        }
        const file = (init.body as FormData).get("torrent") as File;
        uploaded.push(file.name);
        if (file.name === "first.torrent") await firstPending;
        return response({
          ...torrent({ name: file.name }),
          created: true,
          storage_pressure: "normal",
        }, 201);
      }),
    );
    const view = renderPage();
    await screen.findByText("Aucun téléchargement pour le moment.");
    const input = view.container.querySelector('input[type="file"]') as HTMLInputElement;
    const uploadButtons = screen.getAllByRole("button", { name: "Ajouter des torrents" });

    fireEvent.change(input, { target: { files: [new File(["one"], "first.torrent")] } });
    await waitFor(() => expect(uploaded).toEqual(["first.torrent"]));
    expect(input.disabled).toBe(true);
    expect(uploadButtons.every((button) => (button as HTMLButtonElement).disabled)).toBe(true);
    fireEvent.change(input, { target: { files: [new File(["ignored"], "ignored.torrent")] } });
    expect(uploaded).toEqual(["first.torrent"]);

    releaseFirst();
    await screen.findByText("1 ajoutés · 0 déjà présents · 0 invalides · 0 en erreur");
    expect(input.disabled).toBe(false);
    expect(input.value).toBe("");

    fireEvent.drop(screen.getByTestId("torrent-drop-zone"), {
      dataTransfer: { files: [new File(["two"], "second.torrent")] },
    });
    await waitFor(() => expect(uploaded).toEqual(["first.torrent", "second.torrent"]));
    expect(await screen.findByText("second.torrent")).toBeTruthy();
    expect(input.value).toBe("");
  });

  it("refuse plus de cinquante fichiers avant tout envoi", async () => {
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) =>
      response({ items: [], offset: 0, limit: 10, total: 0 }));
    vi.stubGlobal("fetch", fetchMock);
    const view = renderPage();
    await screen.findByText("Aucun téléchargement pour le moment.");
    const input = view.container.querySelector('input[type="file"]') as HTMLInputElement;
    const files = Array.from({ length: MAX_TORRENT_BATCH_FILES + 1 }, (_, index) =>
      new File(["torrent"], `film-${index}.torrent`));

    fireEvent.change(input, { target: { files } });

    await screen.findByText("Un lot peut contenir au maximum 50 fichiers .torrent.");
    expect(fetchMock.mock.calls.filter(([, init]) => init?.method === "POST")).toHaveLength(0);
    expect(input.value).toBe("");
  });
});
