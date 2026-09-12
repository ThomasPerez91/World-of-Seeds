import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { auditAccessibility } from "../../test/accessibility";
import { FeedbackProvider } from "../../components/Feedback";
import { I18nProvider, type Locale } from "../../i18n";
import {
  MAX_TORRENT_BATCH_FILES,
  TORRENT_UPLOAD_CONCURRENCY,
  isVisibleTorrentRequest,
  matchesTorrentFilter,
  torrentQueueLabel,
  torrentRowStatus,
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
    ready_at: null,
    unsubscribe_at: null,
    retention_expires_at: null,
    queue_position_estimate: null,
    queue_total_estimate: null,
    queue_status: null,
    created_at: "2026-08-20T10:00:00Z",
    updated_at: "2026-08-20T10:05:00Z",
    ...overrides,
  };
}

function renderPage(
  locale: Locale = "fr",
  callbacks: {
    onActivityChanged?: () => void;
    onLocalTransferChanged?: (summary: unknown) => void;
  } = {},
) {
  window.localStorage.setItem("wos.preferred-locale", locale);
  return render(
    <I18nProvider>
      <FeedbackProvider>
        <UserDownloadsPage onSessionExpired={vi.fn()} {...callbacks} />
      </FeedbackProvider>
    </I18nProvider>,
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
  it("mappe les états actifs sans confondre une erreur avec un blocage", () => {
    const request = (overrides: Record<string, unknown>) =>
      torrent(overrides) as Parameters<typeof torrentRowStatus>[0];

    expect(torrentRowStatus(request({ state: "active", queue_status: "downloading" }))).toBe("downloading");
    expect(torrentRowStatus(request({ state: "active", queue_status: "stalled" }))).toBe("downloading");
    expect(torrentRowStatus(request({ state: "ready" }))).toBe("ready");
    expect(torrentRowStatus(request({ state: "requested", queue_status: "waiting" }))).toBe("waiting");
    expect(torrentRowStatus(request({ state: "active", queue_status: "cooldown" }))).toBe("waiting");
    expect(torrentRowStatus(request({ state: "error" }))).toBe("error");
    expect(isVisibleTorrentRequest(request({ state: "expired" }))).toBe(false);
    expect(isVisibleTorrentRequest(request({ state: "cancelled" }))).toBe(false);

    expect(torrentQueueLabel(request({ state: "requested", queue_position_estimate: 3 }))).toBe("#3");
    expect(torrentQueueLabel(request({ state: "ready", queue_position_estimate: 3 }))).toBe("-");
    expect(torrentQueueLabel(request({ state: "active", queue_position_estimate: null }))).toBe("-");
    expect(matchesTorrentFilter(request({ state: "active", queue_status: "cooldown" }), "waiting")).toBe(true);
    expect(matchesTorrentFilter(request({ state: "active", queue_status: "cooldown" }), "active")).toBe(false);
  });

  it("rend les six colonnes et les actions principales sous forme d’icônes accessibles", async () => {
    const user = userEvent.setup();
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      if (String(input).includes("download-manifest")) {
        return response({
          snapshot_id: "1".repeat(64),
          manifest_version: 1,
          file_count: 1,
          total_size: 5,
          archive_available: true,
          retention_expires_at: null,
          offset: 0,
          limit: 50,
          items: [{ id: "file", file_index: 0, relative_path: "Film.mkv", size: 5 }],
        });
      }
      return response({
        items: [torrent({ state: "ready", progress: 0.42, queue_position_estimate: 4 })],
        offset: 0,
        limit: 10,
        total: 1,
      });
    }));
    const view = renderPage();

    const article = await screen.findByRole("article", { name: "Film.mkv" });
    const heading = view.container.querySelector(".torrent-list-heading") as HTMLElement;
    expect(view.container.querySelector(".torrent-list-heading + .torrent-accordion-list")).toBeTruthy();
    expect(Array.from(heading.children).map((cell) => cell.textContent)).toEqual([
      "Nom", "État", "File", "Progression", "Taille", "",
    ]);
    expect(within(article).getByText("Prêt").classList).toContain("ready");
    expect(within(article).getByText("-").classList).toContain("torrent-summary-queue");
    expect(article.querySelector(".torrent-summary-heading")?.textContent).toBe("Film.mkv");
    expect(article.querySelector(".torrent-summary-size")?.textContent).not.toBe("");
    expect(article.querySelector(".torrent-summary-progress")?.classList).toContain("ready");
    expect(within(article).getByRole("progressbar").getAttribute("value")).toBe("100");

    const detailsButton = within(article).getByRole("button", { name: "Afficher les détails de Film.mkv" });
    const downloadButton = within(article).getByRole("button", { name: "Télécharger" });
    const deleteButton = within(article).getByRole("button", { name: "Supprimer « Film.mkv »" });
    expect(detailsButton.textContent).toBe("");
    expect(downloadButton.textContent).toBe("");
    expect(deleteButton.textContent).toBe("");
    expect(within(article).getByRole("tooltip", { name: "Détails" })).toBeTruthy();
    expect(within(article).getByRole("tooltip", { name: "Télécharger" })).toBeTruthy();
    expect(within(article).getByRole("tooltip", { name: "Supprimer" })).toBeTruthy();
    expect(detailsButton.getAttribute("aria-expanded")).toBe("false");
    expect(article.querySelector(".torrent-row-grid")?.getAttribute("role")).toBeNull();
    expect(article.querySelector("details")).toBeNull();

    await user.click(article.querySelector(".torrent-summary-size") as HTMLElement);
    expect(within(article).getByRole("button", { name: "Masquer les détails de Film.mkv" }).getAttribute("aria-expanded")).toBe("true");
    expect(article.querySelector(".torrent-detail-dates")?.children).toHaveLength(2);
    await user.click(article.querySelector(".torrent-summary-size") as HTMLElement);
    expect(within(article).getByRole("button", { name: "Afficher les détails de Film.mkv" }).getAttribute("aria-expanded")).toBe("false");

    await user.click(downloadButton);
    expect(within(article).getByRole("button", { name: "Masquer les détails de Film.mkv" }).getAttribute("aria-expanded")).toBe("true");
    await user.click(deleteButton);
    expect(within(article).getByRole("button", { name: "Masquer les détails de Film.mkv" }).getAttribute("aria-expanded")).toBe("true");

    await user.click(detailsButton);
    expect(within(article).getByRole("button", { name: "Afficher les détails de Film.mkv" }).getAttribute("aria-expanded")).toBe("false");
  });

  it("associe aussi des tooltips aux actions d’un torrent actif", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => response({
      items: [torrent()], offset: 0, limit: 10, total: 1,
    })));
    const view = renderPage();
    const article = await screen.findByRole("article", { name: "Film.mkv" });

    expect(within(article).getByRole("tooltip", { name: "Détails" })).toBeTruthy();
    expect(within(article).getByRole("tooltip", { name: "Actualiser" })).toBeTruthy();
    expect(within(article).getByRole("tooltip", { name: "Annuler" })).toBeTruthy();
    expect(within(article).getByRole("button", { name: "Actualiser « Film.mkv »" })).toBeTruthy();
    expect(within(article).getByRole("button", { name: "Annuler la demande Film.mkv" })).toBeTruthy();
    expect(await auditAccessibility(view.container)).toMatchObject({ violations: [] });
  });

  it("remplace le polling par les invalidations WebSocket et resynchronise après reconnexion", async () => {
    MockWebSocket.instances = [];
    const fetchMock = vi.fn(async () => response({
      items: [torrent()], offset: 0, limit: 10, total: 1,
    }));
    vi.stubGlobal("fetch", fetchMock);
    vi.stubGlobal("WebSocket", MockWebSocket);
    const view = renderPage();

    await screen.findByText("Téléchargement", { selector: ".torrent-primary-state" });
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

    const detailsButton = screen.getByRole("button", { name: "Afficher les détails de Film.mkv" });
    await userEvent.click(detailsButton);
    expect(screen.getByRole("button", { name: "Masquer les détails de Film.mkv" })).toBeTruthy();
    MockWebSocket.instances[0].message({
      type: "torrent.started",
      request_id: torrent().id,
      occurred_at: "2026-08-28T07:01:00+00:00",
    });
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    expect(screen.getByRole("button", { name: "Masquer les détails de Film.mkv" }).getAttribute("aria-expanded")).toBe("true");

    MockWebSocket.instances[0].message({
      type: "torrent.ready",
      request_id: torrent().id,
      occurred_at: "2026-08-28T07:00:00+00:00",
    });
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(3));

    vi.useFakeTimers();
    MockWebSocket.instances[0].close();
    await act(async () => { await vi.advanceTimersByTimeAsync(1_000); });
    expect(MockWebSocket.instances).toHaveLength(2);
    MockWebSocket.instances[1].open();
    vi.useRealTimers();
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(4));
    view.unmount();
  });

  it("ignore une invalidation WebSocket obsolète après le rafraîchissement autoritatif d’un upload", async () => {
    MockWebSocket.instances = [];
    const user = userEvent.setup();
    let listRequests = 0;
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
        listRequests += 1;
        if (listRequests === 2) {
          markStalePageStarted();
          await stalePagePending;
        }
        return response({
          items: [torrent({ name: listRequests === 1 ? "Page initiale.mkv" : listRequests === 2 ? "Page obsolète.mkv" : "Page fraîche.mkv" })],
          offset: 0,
          limit: 100,
          total: 1,
        });
      }),
    );
    const view = renderPage();

    await screen.findByText("Page initiale.mkv");
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

  it("actualise automatiquement les torrents sans recharger la page", async () => {
    MockWebSocket.instances = [];
    vi.useFakeTimers();
    const fetchMock = vi.fn(async () => response({
      items: [torrent()], offset: 0, limit: 10, total: 1,
    }));
    vi.stubGlobal("fetch", fetchMock);
    vi.stubGlobal("WebSocket", MockWebSocket);
    const view = renderPage();

    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    expect(screen.getByText("Téléchargement", { selector: ".torrent-primary-state" })).toBeTruthy();
    await act(async () => { await vi.advanceTimersByTimeAsync(4_000); });
    expect(fetchMock.mock.calls.length).toBeGreaterThan(1);
    vi.useRealTimers();
    view.unmount();
  });

  it("retire immédiatement une demande après l’événement realtime d’expiration", async () => {
    MockWebSocket.instances = [];
    let requests = 0;
    let releaseResync!: (value: Response) => void;
    const resyncPending = new Promise<Response>((resolve) => { releaseResync = resolve; });
    vi.stubGlobal("WebSocket", MockWebSocket);
    vi.stubGlobal("fetch", vi.fn(async () => {
      requests += 1;
      if (requests === 1) {
        return response({ items: [torrent()], offset: 0, limit: 25, total: 1 });
      }
      return resyncPending;
    }));
    const view = renderPage();

    expect(await screen.findByRole("article", { name: "Film.mkv" })).toBeTruthy();
    MockWebSocket.instances[0].message({
      type: "torrent.expired",
      request_id: torrent().id,
      occurred_at: "2026-09-12T08:00:00Z",
    });

    await waitFor(() => expect(screen.queryByRole("article", { name: "Film.mkv" })).toBeNull());
    releaseResync(response({ items: [], offset: 0, limit: 25, total: 0 }));
    await waitFor(() => expect(requests).toBe(2));
    view.unmount();
  });

  it("affiche les rangs estimés, états qualitatifs et disclaimer sans inventer un FIFO", async () => {
    const now = Date.now();
    const rows = [
      torrent({ id: crypto.randomUUID(), name: "Premier", queue_status: "waiting", queue_position_estimate: 1, queue_total_estimate: 1_005 }),
      torrent({ id: crypto.randomUUID(), name: "Neuvième", queue_status: "waiting", queue_position_estimate: 9, queue_total_estimate: 1_005 }),
      torrent({ id: crypto.randomUUID(), name: "Lointain", queue_status: "waiting", queue_position_estimate: 158, queue_total_estimate: 1_005 }),
      torrent({ id: crypto.randomUUID(), name: "Très lointain", queue_status: "waiting", queue_position_estimate: 1_005, queue_total_estimate: 1_005 }),
      torrent({ id: crypto.randomUUID(), name: "Sélectionné", queue_status: "downloading" }),
      torrent({ id: crypto.randomUUID(), name: "Sans sources", queue_status: "stalled" }),
      torrent({ id: crypto.randomUUID(), name: "Nouvelle tentative", queue_status: "cooldown" }),
      torrent({
        id: crypto.randomUUID(),
        name: "Prêt",
        state: "ready",
        progress: 1,
        ready_at: new Date(now - 60 * 60 * 1_000).toISOString(),
        unsubscribe_at: new Date(now + 2 * 60 * 60 * 1_000).toISOString(),
      }),
      torrent({ id: crypto.randomUUID(), name: "Échec réel", state: "error" }),
      torrent({ id: crypto.randomUUID(), name: "Archive annulée", state: "cancelled" }),
      torrent({ id: crypto.randomUUID(), name: "Archive expirée", state: "expired" }),
    ];
    vi.stubGlobal("fetch", vi.fn(async () => response({
      items: rows, offset: 0, limit: 10, total: rows.length,
    })));
    const view = renderPage();

    expect(within(await screen.findByRole("article", { name: "Premier" })).getByText("#1")).toBeTruthy();
    expect(within(screen.getByRole("article", { name: "Neuvième" })).getByText("#9")).toBeTruthy();
    expect(within(screen.getByRole("article", { name: "Lointain" })).getByText("#158")).toBeTruthy();
    expect(within(screen.getByRole("article", { name: "Très lointain" })).getByText("#1005")).toBeTruthy();
    expect(within(screen.getByRole("article", { name: "Sélectionné" })).getByText("Téléchargement")).toBeTruthy();
    expect(within(screen.getByRole("article", { name: "Sans sources" })).getByText("Téléchargement")).toBeTruthy();
    expect(within(screen.getByRole("article", { name: "Nouvelle tentative" })).getByText("En attente")).toBeTruthy();
    expect(screen.getByText("1005 torrents en attente")).toBeTruthy();
    expect(screen.getByText("La position peut évoluer selon l’équité, la taille et la disponibilité.")).toBeTruthy();
    const readyCard = screen.getByRole("article", { name: "Prêt" });
    expect(within(readyCard).getByText("-")).toBeTruthy();
    const status = within(readyCard).getByText("Prêt", { selector: ".torrent-primary-state" }).parentElement;
    expect(status?.querySelector("[data-testid='subscription-expiry-indicator']")).toBeTruthy();
    expect(readyCard.querySelector(".retention-warning")).toBeNull();
    expect(within(screen.getByRole("article", { name: "Échec réel" })).getByText("Erreur")).toBeTruthy();
    expect(screen.queryByText("Bloqué")).toBeNull();
    expect(screen.queryByRole("article", { name: "Archive annulée" })).toBeNull();
    expect(screen.queryByRole("article", { name: "Archive expirée" })).toBeNull();
    expect(view.container.querySelector("table")).toBeNull();
    expect(screen.getAllByRole("article")).toHaveLength(rows.length - 2);
    expect(await auditAccessibility(view.container)).toMatchObject({ violations: [] });
  });

  it("préserve les callbacks d’activité et de récupération locale du Dashboard", async () => {
    const onActivityChanged = vi.fn();
    const onLocalTransferChanged = vi.fn();
    vi.stubGlobal("fetch", vi.fn(async () => response({
      items: [torrent()], offset: 0, limit: 10, total: 1,
    })));

    renderPage("fr", { onActivityChanged, onLocalTransferChanged });

    await screen.findByRole("article", { name: "Film.mkv" });
    expect(onActivityChanged).toHaveBeenCalled();
    expect(onLocalTransferChanged).toHaveBeenCalledWith({
      active: 0,
      completedFiles: 0,
      fileCount: 0,
      jobCount: 0,
      kind: null,
      maximum: 2,
      status: "idle",
      waiting: 0,
      name: null,
      otherJobs: 0,
      downloadedBytes: 0,
      totalBytes: 0,
      percent: 0,
    });
  });

  it("remplace un rang estimé après une invalidation scheduler et resync", async () => {
    MockWebSocket.instances = [];
    let requests = 0;
    vi.stubGlobal("WebSocket", MockWebSocket);
    vi.stubGlobal("fetch", vi.fn(async () => {
      requests += 1;
      return response({
        items: [torrent({
          queue_status: requests === 1 ? "waiting" : "downloading",
          queue_position_estimate: requests === 1 ? 9 : null,
          queue_total_estimate: requests === 1 ? 12 : null,
        })],
        offset: 0,
        limit: 10,
        total: 1,
      });
    }));
    const view = renderPage();

    expect(await screen.findByText("#9")).toBeTruthy();
    MockWebSocket.instances[0].message({
      type: "torrent.queue_changed",
      occurred_at: "2026-09-01T12:00:00Z",
    });

    expect(await screen.findByText("Téléchargement", { selector: ".torrent-primary-state" })).toBeTruthy();
    expect(screen.queryByText("#9")).toBeNull();
    expect(requests).toBe(2);
    view.unmount();
  });

  it("coalesce plusieurs invalidations globales pendant un resync", async () => {
    MockWebSocket.instances = [];
    let requests = 0;
    let releaseRefresh!: () => void;
    let markRefreshStarted!: () => void;
    const refreshPending = new Promise<void>((resolve) => { releaseRefresh = resolve; });
    const refreshStarted = new Promise<void>((resolve) => { markRefreshStarted = resolve; });
    vi.stubGlobal("WebSocket", MockWebSocket);
    vi.stubGlobal("fetch", vi.fn(async () => {
      requests += 1;
      if (requests === 2) {
        markRefreshStarted();
        await refreshPending;
      }
      return response({ items: [torrent()], offset: 0, limit: 10, total: 1 });
    }));
    const view = renderPage();

    await screen.findByText("Téléchargement", { selector: ".torrent-primary-state" });
    const event = {
      type: "torrent.queue_changed",
      occurred_at: "2026-09-01T12:00:00Z",
    };
    MockWebSocket.instances[0].message(event);
    await refreshStarted;
    MockWebSocket.instances[0].message(event);
    MockWebSocket.instances[0].message(event);
    MockWebSocket.instances[0].message(event);
    releaseRefresh();

    await waitFor(() => expect(requests).toBe(3));
    await act(async () => Promise.resolve());
    expect(requests).toBe(3);
    view.unmount();
  });

  it("affiche le contrat de file en anglais", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => response({
      items: [torrent({
        queue_status: "waiting",
        queue_position_estimate: 9,
        queue_total_estimate: 12,
      })],
      offset: 0,
      limit: 10,
      total: 1,
    })));
    const view = renderPage("en");

    expect(await screen.findByText("#9")).toBeTruthy();
    expect(screen.getByText("12 torrents waiting")).toBeTruthy();
    expect(screen.getByText("The position may change with fairness, size, and availability.")).toBeTruthy();
    expect(screen.getByText(/WOS downloads the complete torrent/)).toBeTruthy();
    expect(await auditAccessibility(view.container)).toMatchObject({ violations: [] });
  });

  it("remplace l’échéance d’abonnement après un événement de prolongation et resync", async () => {
    MockWebSocket.instances = [];
    const readyAt = new Date(Date.now() - 63 * 60 * 60 * 1_000).toISOString();
    const initialDeadline = new Date(Date.now() + 9 * 60 * 60 * 1_000).toISOString();
    const extendedDeadline = new Date(Date.now() + 72 * 60 * 60 * 1_000).toISOString();
    let requests = 0;
    vi.stubGlobal("WebSocket", MockWebSocket);
    vi.stubGlobal("fetch", vi.fn(async () => {
      requests += 1;
      return response({
        items: [torrent({
          state: "ready",
          progress: 1,
          ready_at: readyAt,
          unsubscribe_at: requests === 1 ? initialDeadline : extendedDeadline,
        })],
        offset: 0,
        limit: 10,
        total: 1,
      });
    }));
    const view = renderPage();

    expect((await screen.findByTestId("subscription-expiry-indicator")).classList.contains("danger")).toBe(true);
    MockWebSocket.instances[0].message({
      type: "torrent.retention_extended",
      request_id: torrent().id,
      occurred_at: "2026-08-31T22:00:00Z",
    });

    await waitFor(() => expect(requests).toBe(2));
    await waitFor(() => expect(screen.getByTestId("subscription-expiry-indicator").classList.contains("warning")).toBe(true));
    view.unmount();
  });

  it("rejoue une invalidation reçue pendant une resynchronisation déjà en vol", async () => {
    MockWebSocket.instances = [];
    const readyAt = new Date(Date.now() - 63 * 60 * 60 * 1_000).toISOString();
    const initialDeadline = new Date(Date.now() + 9 * 60 * 60 * 1_000).toISOString();
    const extendedDeadline = new Date(Date.now() + 72 * 60 * 60 * 1_000).toISOString();
    let requests = 0;
    let releaseStale!: () => void;
    let markStaleStarted!: () => void;
    const stalePending = new Promise<void>((resolve) => { releaseStale = resolve; });
    const staleStarted = new Promise<void>((resolve) => { markStaleStarted = resolve; });
    vi.stubGlobal("WebSocket", MockWebSocket);
    vi.stubGlobal("fetch", vi.fn(async () => {
      requests += 1;
      if (requests === 2) {
        markStaleStarted();
        await stalePending;
      }
      return response({
        items: [torrent({
          state: "ready",
          progress: 1,
          ready_at: readyAt,
          unsubscribe_at: requests < 3 ? initialDeadline : extendedDeadline,
        })],
        offset: 0,
        limit: 10,
        total: 1,
      });
    }));
    const view = renderPage();

    expect((await screen.findByTestId("subscription-expiry-indicator")).classList.contains("danger")).toBe(true);
    MockWebSocket.instances[0].message({
      type: "torrent.ready",
      request_id: torrent().id,
      occurred_at: "2026-08-31T22:00:00Z",
    });
    await staleStarted;
    MockWebSocket.instances[0].message({
      type: "torrent.retention_extended",
      request_id: torrent().id,
      occurred_at: "2026-08-31T22:00:01Z",
    });

    releaseStale();
    await waitFor(() => expect(requests).toBe(3));
    await waitFor(() => expect(screen.getByTestId("subscription-expiry-indicator").classList.contains("warning")).toBe(true));
    view.unmount();
  });

  it("resynchronise la deadline d’un manifeste ouvert après une prolongation", async () => {
    MockWebSocket.instances = [];
    const user = userEvent.setup();
    const readyAt = new Date(Date.now() - 71 * 60 * 60 * 1_000 - 15 * 60 * 1_000).toISOString();
    const initialDeadline = new Date(Date.now() + 45 * 60 * 1_000).toISOString();
    const extendedDeadline = new Date(Date.now() + 72 * 60 * 60 * 1_000).toISOString();
    let manifestRequests = 0;
    vi.stubGlobal("WebSocket", MockWebSocket);
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      if (String(input).includes("download-manifest")) {
        manifestRequests += 1;
        return response({
          snapshot_id: "d".repeat(64),
          manifest_version: 1,
          file_count: 1,
          total_size: 2,
          archive_available: true,
          retention_expires_at: manifestRequests === 1 ? initialDeadline : extendedDeadline,
          offset: 0,
          limit: 50,
          items: [{ id: "root", file_index: 0, relative_path: "root.mkv", size: 2 }],
        });
      }
      return response({
        items: [torrent({
          state: "ready",
          progress: 1,
          ready_at: readyAt,
          unsubscribe_at: manifestRequests === 0 ? initialDeadline : extendedDeadline,
        })],
        offset: 0,
        limit: 10,
        total: 1,
      });
    }));
    const view = renderPage();

    await user.click(await screen.findByRole("button", { name: "Télécharger" }));
    expect(screen.getByRole("img", { name: /Désabonnement automatique dans 45 min/ })).toBeTruthy();
    MockWebSocket.instances[0].message({
      type: "torrent.retention_extended",
      request_id: torrent().id,
      occurred_at: "2026-08-31T22:00:00Z",
    });

    await waitFor(() => expect(manifestRequests).toBe(2));
    await waitFor(() => expect(screen.getByTestId("subscription-expiry-indicator").classList.contains("warning")).toBe(true));
    view.unmount();
  });

  it("retire défensivement un état EXPIRED reçu dans une ancienne réponse", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => response({
      items: [torrent({
        state: "expired",
        progress: 1,
        retention_expires_at: new Date(Date.now() - 60_000).toISOString(),
      })],
      offset: 0,
      limit: 10,
      total: 1,
    })));
    renderPage();

    await waitFor(() => expect(screen.queryByRole("article", { name: "Film.mkv" })).toBeNull());
    expect(screen.queryByText("Bloqué")).toBeNull();
    expect(screen.queryByTestId("subscription-expiry-indicator")).toBeNull();
  });

  it.each([320, 360, 375, 390, 430, 768, 1280])(
    "garde une pill d’échéance accessible avec un nom de plus de 200 caractères à %d px",
    async (width) => {
      Object.defineProperty(window, "innerWidth", { configurable: true, value: width });
      const longName = `${"Film.Name.2026.MULTi.TRUEFRENCH.2160p.UHD.BluRay.REMUX.DV.HDR.".repeat(4)}mkv`;
      const readyAt = new Date(Date.now() - 63 * 60 * 60 * 1_000).toISOString();
      const expiresAt = new Date(Date.now() + 9 * 60 * 60 * 1_000).toISOString();
      expect(longName.length).toBeGreaterThan(200);
      vi.stubGlobal("fetch", vi.fn(async () => response({
        items: [
          torrent({
            name: longName,
            state: "ready",
            progress: 1,
            ready_at: readyAt,
            unsubscribe_at: expiresAt,
          }),
          torrent({
            id: crypto.randomUUID(),
            name: `queued-${longName}`,
            queue_status: "waiting",
            queue_position_estimate: 1_005,
            queue_total_estimate: 1_005,
          }),
        ],
        offset: 0,
        limit: 10,
        total: 2,
      })));
      const view = renderPage();

      const longArticle = await screen.findByRole("article", { name: longName });
      expect(longArticle.querySelector(".torrent-summary-heading")?.getAttribute("aria-label")).toBe(longName);
      expect(screen.getByTestId("subscription-expiry-indicator").classList.contains("danger")).toBe(true);
      expect(screen.getByText("#1005")).toBeTruthy();
      expect(screen.getByRole("img", { name: /Désabonnement automatique dans \d+ h/ })).toBeTruthy();
      expect(view.container.querySelector("[style]")).toBeNull();
      expect(await auditAccessibility(view.container)).toMatchObject({ violations: [] });
    },
  );

  it("affiche une seule échéance racine pour un manifeste multi-fichier", async () => {
    const user = userEvent.setup();
    const readyAt = new Date(Date.now() - 71 * 60 * 60 * 1_000 - 15 * 60 * 1_000).toISOString();
    const expiresAt = new Date(Date.now() + 45 * 60 * 1_000).toISOString();
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      if (String(input).includes("download-manifest")) {
        return response({
          snapshot_id: "c".repeat(64),
          manifest_version: 1,
          file_count: 3,
          total_size: 6,
          archive_available: true,
          retention_expires_at: expiresAt,
          offset: 0,
          limit: 50,
          items: [
            { id: "root", file_index: 0, relative_path: "root.mkv", size: 2 },
            { id: "nested-1", file_index: 1, relative_path: "Folder/one.mkv", size: 2 },
            { id: "nested-2", file_index: 2, relative_path: "Folder/two.srt", size: 2 },
          ],
        });
      }
      return response({
        items: [torrent({
          state: "ready",
          progress: 1,
          ready_at: readyAt,
          unsubscribe_at: expiresAt,
        })],
        offset: 0,
        limit: 10,
        total: 1,
      });
    }));
    renderPage();

    await user.click(await screen.findByRole("button", { name: `Afficher les détails de ${torrent().name}` }));
    const content = await screen.findByRole("region", { name: `Contenu de ${torrent().name}` });
    expect(screen.getAllByTestId("subscription-expiry-indicator")).toHaveLength(1);
    expect(screen.getByRole("img", { name: /Désabonnement automatique dans 45 min/ })).toBeTruthy();
    expect(within(content).getByText("root.mkv")).toBeTruthy();
    expect(within(content).getByText("one.mkv")).toBeTruthy();
    expect(within(content).getByText("two.srt")).toBeTruthy();
  });

  it("ouvre le contenu depuis le bouton de téléchargement et permet de télécharger chaque sous-dossier", async () => {
    const user = userEvent.setup();
    const snapshot = "e".repeat(64);
    const directoryUrls: string[] = [];
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("download-directories")) {
        directoryUrls.push(url);
        const parsed = new URL(url, "https://wos.test");
        const parent = parsed.searchParams.get("parent");
        const offset = Number(parsed.searchParams.get("offset"));
        const files = parent === "Series/Saison 01"
          ? [
              { id: "episode-1", file_index: 0, relative_path: "Series/Saison 01/Episode 01.mkv", size: 5 },
              { id: "episode-2", file_index: 1, relative_path: "Series/Saison 01/Episode 02.mkv", size: 4 },
            ]
          : [];
        return response({
          snapshot_id: snapshot,
          path: parent ?? "",
          direct_file_count: files.length,
          offset,
          limit: 500,
          files,
          directories: parent === "Series"
            ? [{
                name: "Saison 01",
                relative_path: "Series/Saison 01",
                file_count: 2,
                total_size: 9,
                archive_available: true,
              }]
            : parent === null ? [{
                name: "Series",
                relative_path: "Series",
                file_count: 2,
                total_size: 9,
                archive_available: true,
              }] : [],
        });
      }
      if (url.includes("download-manifest")) {
        return response({
          snapshot_id: snapshot,
          manifest_version: 1,
          file_count: 2,
          total_size: 9,
          archive_available: true,
          retention_expires_at: null,
          offset: 0,
          limit: 50,
          items: [
            { id: "episode-1", file_index: 0, relative_path: "Series/Saison 01/Episode 01.mkv", size: 5 },
            { id: "episode-2", file_index: 1, relative_path: "Series/Saison 01/Episode 02.mkv", size: 4 },
          ],
        });
      }
      return response({
        items: [torrent({ state: "ready", progress: 1 })], offset: 0, limit: 10, total: 1,
      });
    }));
    const view = renderPage();

    const article = await screen.findByRole("article", { name: "Film.mkv" });
    await user.click(within(article).getByRole("button", { name: "Télécharger" }));

    expect(await within(article).findByRole("button", { name: "Masquer les détails de Film.mkv" })).toBeTruthy();
    const content = await within(article).findByRole("region", { name: "Contenu de Film.mkv" });
    const directoryName = content.querySelector('.ready-directory-name[aria-label="Series"]');
    expect(directoryName).toBeTruthy();
    expect(directoryName?.getAttribute("tabindex")).toBeNull();
    expect(within(content).queryByRole("link", { name: "Télécharger le dossier « Series » en ZIP" })).toBeNull();
    expect(within(content).queryByRole("heading", { name: "Dossiers et fichiers" })).toBeNull();
    expect(within(content).queryByText(/Le mode compatible propose/)).toBeNull();
    const rootToggle = within(content).getByRole("button", { name: "Ouvrir le dossier « Series »" });
    expect(rootToggle.querySelector("small")).toBeNull();
    expect(rootToggle.querySelector(".ready-directory-chevron")).toBeTruthy();

    await user.click(rootToggle);
    const seasonDownload = await within(content).findByRole("link", {
      name: "Télécharger le dossier « Saison 01 » en ZIP",
    });
    const seasonUrl = new URL(seasonDownload.getAttribute("href") ?? "", "https://wos.test");
    expect(seasonUrl.searchParams.get("path")).toBe("Series/Saison 01");
    expect(seasonUrl.searchParams.get("snapshot")).toBe(snapshot);
    expect(within(content).queryByText("2 fichiers · 9 o", { selector: ".ready-directory-copy small" })).toBeTruthy();
    expect(directoryUrls.some((url) => new URL(url, "https://wos.test").searchParams.get("parent") === "Series")).toBe(true);
    expect(within(content).queryByText("Episode 01.mkv")).toBeNull();
    await user.click(within(content).getByRole("button", { name: "Ouvrir le dossier « Saison 01 »" }));
    expect(await within(content).findByText("Episode 01.mkv")).toBeTruthy();
    expect(within(content).getByText("Episode 02.mkv")).toBeTruthy();
    expect(within(content).queryByText("Aucun sous-dossier")).toBeNull();
    expect(content.querySelector(".ready-file-list")).toBeNull();
    expect(content.querySelectorAll(".ready-directory-list")).toHaveLength(1);
    expect(await auditAccessibility(view.container)).toMatchObject({ violations: [] });
  });

  it("charge uniquement le manifeste du READY ouvert et affiche son chargement", async () => {
    const user = userEvent.setup();
    const readyId = "c8c69f91-8e73-48b3-a14f-35199ce7c101";
    let manifestRequests = 0;
    let releaseManifest!: (result: Response) => void;
    const manifestPending = new Promise<Response>((resolve) => { releaseManifest = resolve; });
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      if (String(input).includes("download-manifest")) {
        manifestRequests += 1;
        return manifestPending;
      }
      return response({
        items: [
          torrent({ name: "Encore actif.mkv" }),
          torrent({ id: readyId, name: "Prêt.mkv", state: "ready", progress: 1 }),
        ],
        offset: 0,
        limit: 10,
        total: 2,
      });
    }));
    renderPage();

    const active = await screen.findByRole("article", { name: "Encore actif.mkv" });
    const ready = screen.getByRole("article", { name: "Prêt.mkv" });
    expect(manifestRequests).toBe(0);
    await user.click(within(active).getByRole("button", { name: "Afficher les détails de Encore actif.mkv" }));
    expect(manifestRequests).toBe(0);
    await user.click(within(ready).getByRole("button", { name: "Afficher les détails de Prêt.mkv" }));
    expect(await screen.findByText("Chargement du contenu…")).toBeTruthy();
    expect(manifestRequests).toBe(1);

    releaseManifest(response({
      snapshot_id: "1".repeat(64),
      manifest_version: 1,
      file_count: 1,
      total_size: 4,
      archive_available: false,
      retention_expires_at: null,
      offset: 0,
      limit: 50,
      items: [{ id: "one", file_index: 0, relative_path: "Prêt.mkv", size: 4 }],
    }));
    expect(await screen.findByText("Prêt.mkv", { selector: ".ready-directory-list strong" })).toBeTruthy();
  });

  it("isole une erreur de manifeste et permet de réessayer", async () => {
    const user = userEvent.setup();
    let manifestRequests = 0;
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      if (String(input).includes("download-manifest")) {
        manifestRequests += 1;
        if (manifestRequests === 1) return response({ detail: "unavailable" }, 503);
        return response({
          snapshot_id: "2".repeat(64),
          manifest_version: 1,
          file_count: 1,
          total_size: 4,
          archive_available: false,
          retention_expires_at: null,
          offset: 0,
          limit: 50,
          items: [{ id: "retry-file", file_index: 0, relative_path: "Après retry.mkv", size: 4 }],
        });
      }
      return response({
        items: [torrent({ state: "ready", progress: 1 })], offset: 0, limit: 10, total: 1,
      });
    }));
    renderPage();

    const article = await screen.findByRole("article", { name: "Film.mkv" });
    await user.click(within(article).getByRole("button", { name: "Afficher les détails de Film.mkv" }));
    const content = await screen.findByRole("region", { name: "Contenu de Film.mkv" });
    expect((await within(content).findByRole("alert")).textContent).toContain("Le manifeste est indisponible.");
    await user.click(within(content).getByRole("button", { name: "Réessayer" }));
    expect(await within(content).findByText("Après retry.mkv")).toBeTruthy();
    expect(manifestRequests).toBe(2);
  });

  it("télécharge un READY mono-fichier avec un lien natif sans sélecteur de dossier", async () => {
    const user = userEvent.setup();
    const picker = vi.fn();
    const nativeClick = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);
    vi.stubGlobal("showDirectoryPicker", picker);
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      if (String(input).includes("download-manifest")) {
        return response({
          snapshot_id: "3".repeat(64),
          manifest_version: 1,
          file_count: 1,
          total_size: 7,
          archive_available: false,
          retention_expires_at: null,
          offset: 0,
          limit: 50,
          items: [{ id: "single-id", file_index: 0, relative_path: "Folder/Film final.mkv", size: 7 }],
        });
      }
      return response({
        items: [torrent({ state: "ready", progress: 1 })], offset: 0, limit: 10, total: 1,
      });
    }));
    const view = renderPage();

    const article = await screen.findByRole("article", { name: "Film.mkv" });
    await user.click(within(article).getByRole("button", { name: "Télécharger" }));
    const link = await within(article).findByRole("link", { name: "Télécharger « Folder/Film final.mkv »", hidden: true });
    expect(link.getAttribute("href")).toContain(`/files/single-id/download?snapshot=${"3".repeat(64)}`);
    expect(link.getAttribute("download")).toBe("Film final.mkv");
    expect(nativeClick).toHaveBeenCalledOnce();
    expect(picker).not.toHaveBeenCalled();
    expect(await auditAccessibility(view.container)).toMatchObject({ violations: [] });
  });

  it("charge la suite des fichiers directement dans le dossier de l’arborescence", async () => {
    const user = userEvent.setup();
    const snapshot = "4".repeat(64);
    const directoryUrls: string[] = [];
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("download-directories")) {
        directoryUrls.push(url);
        const parsed = new URL(url, "https://wos.test");
        const parent = parsed.searchParams.get("parent");
        const requestedOffset = Number(parsed.searchParams.get("offset"));
        if (parent === null) {
          return response({
            snapshot_id: snapshot,
            path: "",
            directories: [{
              name: "Folder",
              relative_path: "Folder",
              file_count: 3,
              total_size: 3,
              archive_available: false,
            }],
            direct_file_count: 0,
            offset: requestedOffset,
            limit: 500,
            files: [],
          });
        }
        return response({
          snapshot_id: snapshot,
          path: "Folder",
          directories: [],
          direct_file_count: 3,
          offset: requestedOffset,
          limit: 500,
          files: requestedOffset === 0
            ? [
                { id: "file-0", file_index: 0, relative_path: "Folder/0.bin", size: 1 },
                { id: "file-1", file_index: 1, relative_path: "Folder/1.bin", size: 1 },
              ]
            : [{ id: "file-2", file_index: 2, relative_path: "Folder/2.bin", size: 1 }],
        });
      }
      if (url.includes("download-manifest")) {
        return response({
          snapshot_id: snapshot,
          manifest_version: 1,
          file_count: 3,
          total_size: 3,
          archive_available: false,
          retention_expires_at: null,
          offset: 0,
          limit: 50,
          items: [],
        });
      }
      return response({
        items: [torrent({ state: "ready", progress: 1 })], offset: 0, limit: 10, total: 1,
      });
    }));
    renderPage();

    const article = await screen.findByRole("article", { name: "Film.mkv" });
    await user.click(within(article).getByRole("button", { name: "Afficher les détails de Film.mkv" }));
    const content = await screen.findByRole("region", { name: "Contenu de Film.mkv" });
    await user.click(await within(content).findByRole("button", { name: "Ouvrir le dossier « Folder »" }));
    expect(await within(content).findByText("0.bin")).toBeTruthy();
    expect(within(content).getByText("1.bin")).toBeTruthy();
    expect(within(content).queryByText("2.bin")).toBeNull();
    expect(within(content).queryByRole("link", { name: /ZIP/ })).toBeNull();
    await user.click(within(content).getByRole("button", { name: "Afficher plus de fichiers" }));
    expect(await within(content).findByText("2.bin")).toBeTruthy();
    expect(within(content).queryByRole("button", { name: "Afficher plus de fichiers" })).toBeNull();
    expect(directoryUrls.some((candidate) => new URL(candidate, "https://wos.test").searchParams.get("offset") === "2")).toBe(true);
  });

  it("conserve la pagination du manifeste si l’arborescence est indisponible", async () => {
    const user = userEvent.setup();
    const snapshot = "6".repeat(64);
    const manifestOffsets: number[] = [];
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("download-directories")) {
        return response({ detail: "unavailable" }, 503);
      }
      if (url.includes("download-manifest")) {
        const requestedOffset = Number(new URL(url, "https://wos.test").searchParams.get("offset"));
        manifestOffsets.push(requestedOffset);
        const count = requestedOffset === 0 ? 50 : 1;
        return response({
          snapshot_id: snapshot,
          manifest_version: 1,
          file_count: 51,
          total_size: 51,
          archive_available: false,
          retention_expires_at: null,
          offset: requestedOffset,
          limit: 50,
          items: Array.from({ length: count }, (_, index) => ({
            id: `fallback-${requestedOffset + index}`,
            file_index: requestedOffset + index,
            relative_path: `Folder/${requestedOffset + index}.bin`,
            size: 1,
          })),
        });
      }
      return response({
        items: [torrent({ state: "ready", progress: 1 })], offset: 0, limit: 10, total: 1,
      });
    }));
    renderPage();

    const article = await screen.findByRole("article", { name: "Film.mkv" });
    await user.click(within(article).getByRole("button", { name: "Afficher les détails de Film.mkv" }));
    const content = await screen.findByRole("region", { name: "Contenu de Film.mkv" });
    expect(await within(content).findByText("0.bin")).toBeTruthy();
    expect(within(content).getByText("1 / 2")).toBeTruthy();
    await user.click(within(content).getByRole("button", { name: "Suivant" }));
    expect(await within(content).findByText("50.bin")).toBeTruthy();
    expect(within(content).getByText("2 / 2")).toBeTruthy();
    expect(manifestOffsets).toEqual([0, 50]);
  });

  it("démarre Télécharger tout avec la page zéro mise en cache", async () => {
    const user = userEvent.setup();
    const snapshot = "7".repeat(64);
    const manifestOffsets: number[] = [];
    const downloadedIds: string[] = [];
    const fileHandle = {
      createWritable: vi.fn(async () => ({ seek: vi.fn(), write: vi.fn(), close: vi.fn() })),
    };
    const directory = {
      getDirectoryHandle: vi.fn(async () => directory),
      getFileHandle: vi.fn(async () => fileHandle),
    };
    const picker = vi.fn(async () => directory);
    vi.stubGlobal("showDirectoryPicker", picker);
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("download-manifest")) {
        const requestedOffset = Number(new URL(url, "https://wos.test").searchParams.get("offset"));
        manifestOffsets.push(requestedOffset);
        const count = requestedOffset === 0 ? 50 : 1;
        return response({
          snapshot_id: snapshot,
          manifest_version: 1,
          file_count: 51,
          total_size: 51,
          archive_available: false,
          retention_expires_at: null,
          offset: requestedOffset,
          limit: 50,
          items: Array.from({ length: count }, (_, index) => ({
            id: `cached-${requestedOffset + index}`,
            file_index: requestedOffset + index,
            relative_path: `cached-${requestedOffset + index}.bin`,
            size: 1,
          })),
        });
      }
      if (url.includes("/files/")) {
        const id = url.match(/\/files\/([^/]+)\/download/)?.[1];
        if (id !== undefined) downloadedIds.push(id);
        return new Response(new Uint8Array([1]).buffer, {
          headers: { "X-WOS-Manifest-Version": "1" },
        });
      }
      return response({
        items: [torrent({ state: "ready", progress: 1 })], offset: 0, limit: 10, total: 1,
      });
    }));
    renderPage();

    const article = await screen.findByRole("article", { name: "Film.mkv" });
    await user.click(within(article).getByRole("button", { name: "Afficher les détails de Film.mkv" }));
    const content = await screen.findByRole("region", { name: "Contenu de Film.mkv" });
    await user.click(within(content).getByRole("button", { name: "Tout télécharger" }));

    expect(await screen.findByText("« Film.mkv » a été téléchargé.")).toBeTruthy();
    expect(picker).toHaveBeenCalledOnce();
    expect(downloadedIds).toHaveLength(51);
    expect(downloadedIds).toContain("cached-0");
    expect(manifestOffsets).toEqual([0, 50]);
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
            file_count: 2,
            total_size: 3,
            archive_available: true,
            retention_expires_at: null,
            offset: 0,
            limit: 500,
            items: [
              { id: "file-id", file_index: 0, relative_path: "Film/one.bin", size: 1 },
              { id: "file-two", file_index: 1, relative_path: "Film/two.bin", size: 2 },
            ],
          });
        }
        if (url.includes("/files/file-id/download")) {
          return new Response(new Uint8Array([1]).buffer, {
            headers: { "X-WOS-Manifest-Version": "1" },
          });
        }
        if (url.includes("/files/file-two/download")) {
          return new Response(new Uint8Array([2, 3]).buffer, {
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

    await user.click(await screen.findByRole("button", { name: "Afficher les détails de Film.mkv" }));
    await user.click(await screen.findByRole("button", { name: "Tout télécharger" }));

    expect(await screen.findByText("« Film.mkv » a été téléchargé.")).toBeTruthy();
    expect(screen.getByText("2/2 fichiers · 3 o sur 3 o")).toBeTruthy();
    expect(writes).toEqual([1, 2, 3]);
    expect(view.container.querySelector("[style]")).toBeNull();
  });

  it("rend la file locale active et les positions 1/2/3 sur mobile", async () => {
    Object.defineProperty(window, "innerWidth", { configurable: true, value: 320 });
    const longPath = `${"Folder/very-long-episode-name-".repeat(9)}`;
    const items = Array.from({ length: 5 }, (_, index) => ({
      id: `file-${index}`,
      file_index: index,
      relative_path: `${longPath}${index}.mkv`,
      size: 1,
    }));
    let releaseDownloads!: () => void;
    let markStarted!: () => void;
    const downloadsPending = new Promise<void>((resolve) => { releaseDownloads = resolve; });
    const started = new Promise<void>((resolve) => { markStarted = resolve; });
    let active = 0;
    const fileHandle = {
      createWritable: vi.fn(async () => ({
        seek: vi.fn(),
        write: vi.fn(),
        close: vi.fn(),
      })),
    };
    const directory = {
      getDirectoryHandle: vi.fn(async () => directory),
      getFileHandle: vi.fn(async () => fileHandle),
    };
    vi.stubGlobal("showDirectoryPicker", vi.fn(async () => directory));
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("download-manifest")) {
        return response({
          snapshot_id: "e".repeat(64),
          manifest_version: 1,
          file_count: items.length,
          total_size: items.length,
          archive_available: true,
          retention_expires_at: null,
          offset: 0,
          limit: 500,
          items,
        });
      }
      if (url.includes("/api/v2/downloads/policy")) {
        return response({ max_concurrent_streams: 2 });
      }
      if (url.includes("/files/")) {
        active += 1;
        if (active === 2) markStarted();
        await downloadsPending;
        active -= 1;
        return new Response(new Uint8Array([1]).buffer, {
          headers: { "X-WOS-Manifest-Version": "1" },
        });
      }
      return response({
        items: [torrent({ state: "ready", progress: 1 })],
        offset: 0,
        limit: 10,
        total: 1,
      });
    }));
    const view = renderPage();

    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Afficher les détails de Film.mkv" }));
    await user.click(await screen.findByRole("button", { name: "Tout télécharger" }));
    await started;
    expect(await screen.findByText("En attente — 1er")).toBeTruthy();
    expect(screen.getByText("En attente — 2e")).toBeTruthy();
    expect(screen.getByText("En attente — 3e")).toBeTruthy();
    expect(screen.getByRole("list", { name: "File de récupération vers cet appareil" })).toBeTruthy();
    expect(screen.getByText("2/2 actifs")).toBeTruthy();
    expect(screen.getByText("3 en attente")).toBeTruthy();
    const localPanel = screen.getByRole("region", { name: "Téléchargement local de Film.mkv" });
    expect(localPanel.closest(".torrent-accordion-card")?.getAttribute("aria-label")).toBe("Film.mkv");
    expect(screen.getByText("La file de récupération est liée à cet onglet. Le fermer ou actualiser la page peut interrompre la file ; les téléchargements pourront être relancés.")).toBeTruthy();
    expect(screen.getAllByText("Téléchargement en cours")).toHaveLength(2);
    expect(await auditAccessibility(view.container)).toMatchObject({ violations: [] });
    expect(view.container.querySelector("[style]")).toBeNull();

    releaseDownloads();
    expect(await screen.findByText("« Film.mkv » a été téléchargé.")).toBeTruthy();
  });

  it("explique la volatilité de la file locale en anglais sans annonce dynamique", async () => {
    vi.stubGlobal("showDirectoryPicker", vi.fn(async () => ({
      getFileHandle: vi.fn(async () => ({
        createWritable: vi.fn(async () => ({
          seek: vi.fn(),
          write: vi.fn(),
          close: vi.fn(),
        })),
      })),
    })));
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      if (String(input).includes("download-manifest")) {
        return response({
          snapshot_id: "f".repeat(64),
          manifest_version: 1,
          file_count: 2,
          total_size: 2,
          archive_available: true,
          retention_expires_at: null,
          offset: 0,
          limit: 500,
          items: [
            { id: "file", file_index: 0, relative_path: "file.bin", size: 1 },
            { id: "file-two", file_index: 1, relative_path: "file-two.bin", size: 1 },
          ],
        });
      }
      if (String(input).includes("/files/")) {
        return new Response(new Uint8Array([1]).buffer, {
          headers: { "X-WOS-Manifest-Version": "1" },
        });
      }
      return response({
        items: [torrent({ state: "ready", progress: 1 })],
        offset: 0,
        limit: 10,
        total: 1,
      });
    }));
    const view = renderPage("en");

    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Show details for Film.mkv" }));
    await user.click(await screen.findByRole("button", { name: "Download all" }));

    const notice = await screen.findByText("The transfer queue belongs to this tab. Closing it or refreshing the page may interrupt the queue; downloads can be started again.");
    expect(notice.closest("[aria-live]")).toBeNull();
    expect(await auditAccessibility(view.container)).toMatchObject({ violations: [] });
  });

  it("pilote pause, reprise et annulation locale sans supprimer le TorrentRequest", async () => {
    const user = userEvent.setup();
    const otherId = "c8c69f91-8e73-48b3-a14f-35199ce7c101";
    const calls: Array<{ method: string; url: string }> = [];
    const fileHandle = {
      createWritable: vi.fn(async () => ({ seek: vi.fn(), write: vi.fn(), close: vi.fn() })),
    };
    const directory = {
      getDirectoryHandle: vi.fn(async () => directory),
      getFileHandle: vi.fn(async () => fileHandle),
    };
    vi.stubGlobal("showDirectoryPicker", vi.fn(async () => directory));
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      calls.push({ method: init?.method ?? "GET", url });
      if (url.includes("download-manifest")) {
        if (url.includes(otherId)) {
          return response({
            snapshot_id: "6".repeat(64),
            manifest_version: 1,
            file_count: 1,
            total_size: 1,
            archive_available: false,
            retention_expires_at: null,
            offset: 0,
            limit: 50,
            items: [{ id: "other-file", file_index: 0, relative_path: "Other/consultable.bin", size: 1 }],
          });
        }
        return response({
          snapshot_id: "5".repeat(64),
          manifest_version: 1,
          file_count: 2,
          total_size: 2,
          archive_available: false,
          retention_expires_at: null,
          offset: 0,
          limit: 50,
          items: [
            { id: "pause-one", file_index: 0, relative_path: "one.bin", size: 1 },
            { id: "pause-two", file_index: 1, relative_path: "two.bin", size: 1 },
          ],
        });
      }
      if (url.includes("/files/")) {
        return new Promise<Response>((_resolve, reject) => {
          init?.signal?.addEventListener("abort", () => reject(new DOMException("stopped", "AbortError")));
        });
      }
      return response({
        items: [
          torrent({ state: "ready", progress: 1 }),
          torrent({ id: otherId, name: "Autre READY", state: "ready", progress: 1 }),
        ],
        offset: 0,
        limit: 10,
        total: 2,
      });
    }));
    renderPage();

    const article = await screen.findByRole("article", { name: "Film.mkv" });
    await user.click(within(article).getByRole("button", { name: "Afficher les détails de Film.mkv" }));
    await user.click(await within(article).findByRole("button", { name: "Tout télécharger" }));
    expect(within(article).queryByRole("link", { name: "Télécharger le ZIP" })).toBeNull();
    const otherArticle = screen.getByRole("article", { name: "Autre READY" });
    await user.click(within(otherArticle).getByRole("button", { name: "Afficher les détails de Autre READY" }));
    expect(await within(otherArticle).findByText("consultable.bin")).toBeTruthy();
    expect(within(article).getByRole("button", { name: "Mettre en pause" })).toBeTruthy();
    await user.click(await within(article).findByRole("button", { name: "Mettre en pause" }));
    expect(await within(article).findByText("En pause", { selector: ".ui-badge" })).toBeTruthy();
    await user.click(within(article).getByRole("button", { name: "Reprendre" }));
    expect(await within(article).findByRole("button", { name: "Mettre en pause" })).toBeTruthy();
    await user.click(within(article).getByRole("button", { name: "Annuler la récupération" }));
    expect(await within(article).findByText("Annulé", { selector: ".ui-badge" })).toBeTruthy();
    expect(within(article).getByRole("button", { name: "Fermer" })).toBeTruthy();
    expect(calls.some(({ method }) => method === "DELETE")).toBe(false);
  });

  it("présente une erreur locale humaine et une reprise accessible", async () => {
    const user = userEvent.setup();
    const fileHandle = {
      createWritable: vi.fn(async () => ({ seek: vi.fn(), write: vi.fn(), close: vi.fn() })),
    };
    const directory = {
      getDirectoryHandle: vi.fn(async () => directory),
      getFileHandle: vi.fn(async () => fileHandle),
    };
    vi.stubGlobal("showDirectoryPicker", vi.fn(async () => directory));
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("download-manifest")) {
        return response({
          snapshot_id: "8".repeat(64),
          manifest_version: 1,
          file_count: 2,
          total_size: 2,
          archive_available: false,
          retention_expires_at: null,
          offset: 0,
          limit: 50,
          items: [
            { id: "bad-one", file_index: 0, relative_path: "bad-one.bin", size: 1 },
            { id: "bad-two", file_index: 1, relative_path: "bad-two.bin", size: 1 },
          ],
        });
      }
      if (url.includes("/files/")) return new Response(new Uint8Array([1]).buffer);
      return response({
        items: [torrent({ state: "ready", progress: 1 })], offset: 0, limit: 10, total: 1,
      });
    }));
    const view = renderPage();

    const article = await screen.findByRole("article", { name: "Film.mkv" });
    await user.click(within(article).getByRole("button", { name: "Afficher les détails de Film.mkv" }));
    await user.click(await within(article).findByRole("button", { name: "Tout télécharger" }));
    const alert = await within(article).findByRole("alert");
    expect(alert.textContent).toContain("Le contenu a changé. Relance le téléchargement.");
    expect(within(article).getByRole("button", { name: "Reprendre" })).toBeTruthy();
    expect(await auditAccessibility(view.container)).toMatchObject({ violations: [] });
  });

  it("propose les fichiers et le ZIP streamé sans File System Access API", async () => {
    const user = userEvent.setup();
    let manifestRequests = 0;
    const longPath = "The.Cleaning.Lady/Saison.02/The.Cleaning.Lady.S02E04.MULTi.2160p.WEB-DL.DV.HDR.mkv";
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
            retention_expires_at: null,
            offset: 0,
            limit: 500,
            items: [{ id: "file-id", file_index: 0, relative_path: longPath, size: 3 }],
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

    await user.click(await screen.findByRole("button", { name: "Afficher les détails de Film.mkv" }));

    const archive = await screen.findByRole("link", {
      name: "Télécharger le ZIP",
    });
    const individual = screen.getByRole("link", { name: `Télécharger « ${longPath} »` });
    expect(archive.getAttribute("href")).toContain("download-archive?snapshot=");
    expect(individual.getAttribute("href")).toContain("/files/file-id/download?snapshot=");
    const filePath = view.container.querySelector(`.ready-file-path[aria-label="${longPath}"]`);
    expect(filePath?.getAttribute("aria-label")).toBe(longPath);
    expect(filePath?.querySelector("strong")?.textContent).toBe(longPath.split("/").at(-1));
    expect(screen.queryByText("1 / 1000")).toBeNull();
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

    expect(await screen.findByText("Torrent ajouté")).toBeTruthy();
    expect(screen.getByText("film.torrent")).toBeTruthy();
    expect(screen.queryByText("Lot terminé")).toBeNull();
    expect(view.container.querySelector(".torrent-upload-batch")).toBeNull();
    expect(await screen.findByText("Téléchargement", { selector: ".torrent-primary-state" })).toBeTruthy();
    expect(screen.getByRole("progressbar", { name: "Progression de Film.mkv" }).getAttribute("value")).toBe("50");
    expect(view.container.querySelector("table")).toBeNull();
    expect(screen.getByRole("article", { name: "Film.mkv" })).toBeTruthy();
    expect(view.container.querySelector(".torrent-accordion-list")).toBeTruthy();
    expect(view.container.querySelector("[style]")).toBeNull();
    expect(await auditAccessibility(view.container)).toMatchObject({ violations: [] });
  });

  it("pagine par blocs de 25 et conserve les noms longs dans le résumé accessible", async () => {
    const user = userEvent.setup();
    const longName =
      "Film.Name.2026.MULTi.TRUEFRENCH.2160p.UHD.BluRay.REMUX.DV.HDR.HEVC.DTS-HD.MA.7.1-GROUP.mkv";
    const calls: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        calls.push(url);
        const allItems = Array.from({ length: 51 }, (_, index) => torrent({
            id: `d86528f5-bc01-4a8b-86a1-${String(index).padStart(12, "0")}`,
            name: index === 0 ? longName : index === 25 ? "Deuxième page.mkv" : index === 50 ? "Troisième page.mkv" : `Film ${index}.mkv`,
            state: index >= 25 ? "ready" : "requested",
            progress: index >= 25 ? 1 : 0,
          }));
        const requestUrl = new URL(url, "http://localhost");
        const requestedOffset = Number(requestUrl.searchParams.get("offset") ?? 0);
        const requestedLimit = Number(requestUrl.searchParams.get("limit") ?? 25);
        return response({
          items: allItems.slice(requestedOffset, requestedOffset + requestedLimit),
          offset: requestedOffset,
          limit: requestedLimit,
          total: allItems.length,
        });
      }),
    );
    const view = renderPage();

    const longArticle = await screen.findByRole("article", { name: longName });
    expect(longArticle.querySelector(".torrent-summary-heading")?.getAttribute("aria-label")).toBe(longName);
    expect(view.container.querySelector(".torrent-summary-heading > strong")).toBeTruthy();
    expect(screen.getByText("Page 1 sur 3 · 51 demandes")).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "Suivant" }));

    expect(await screen.findByText("Deuxième page.mkv")).toBeTruthy();
    expect(screen.getAllByText("Prêt")).toHaveLength(25);
    await user.click(screen.getByRole("button", { name: "Suivant" }));
    expect(await screen.findByText("Troisième page.mkv")).toBeTruthy();
    await user.click(screen.getByRole("button", { name: /Prêts26/ }));
    expect(screen.getByText("Page 1 sur 2 · 26 demandes")).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "Suivant" }));
    expect(screen.getByText("Troisième page.mkv")).toBeTruthy();
    await user.type(screen.getByRole("searchbox", { name: "Rechercher un torrent" }), "Troisième");
    expect(screen.getByText("Page 1 sur 1 · 1 demande")).toBeTruthy();
    expect(calls.some((url) => url.includes("offset=0") && url.includes("limit=25"))).toBe(true);
    expect(calls.some((url) => url.includes("offset=25") && url.includes("limit=25"))).toBe(true);
    expect(calls.some((url) => url.includes("offset=50") && url.includes("limit=25"))).toBe(true);
  });

  it("combine recherche partielle insensible à la casse et filtre de statut", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => response({
      items: [
        torrent({ id: "d86528f5-bc01-4a8b-86a1-74fe3404864b", name: "ubuntu-24.04.iso", state: "ready", progress: 1 }),
        torrent({ id: "c8c69f91-8e73-48b3-a14f-35199ce7c101", name: "BUN-source.iso", state: "active" }),
        torrent({ id: "6a6cbfc8-11d0-4cf2-82f8-3533924c83de", name: "archive.iso", state: "requested", queue_status: "waiting" }),
      ],
      offset: 0,
      limit: 100,
      total: 3,
    })));
    renderPage();

    const search = await screen.findByRole("searchbox", { name: "Rechercher un torrent" });
    await userEvent.type(search, "BUN");
    expect(screen.getByRole("article", { name: "ubuntu-24.04.iso" })).toBeTruthy();
    expect(screen.getByRole("article", { name: "BUN-source.iso" })).toBeTruthy();

    await userEvent.click(screen.getByRole("button", { name: /Prêts/ }));
    expect(screen.getByRole("article", { name: "ubuntu-24.04.iso" })).toBeTruthy();
    expect(screen.queryByRole("article", { name: "BUN-source.iso" })).toBeNull();
    expect(screen.getByRole("button", { name: /Tous3/ })).toBeTruthy();
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
    const selectButton = screen.getByRole("button", { name: "Ajouter des torrents" });
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
    expect(screen.getByText("Torrent déjà présent")).toBeTruthy();
    expect(await screen.findByText("Erreur", { selector: ".torrent-primary-state" })).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: "Afficher les détails de Film.mkv" }));
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

    const article = await screen.findByRole("article", { name: "Film.mkv" });
    expect(within(article).getByRole("button", { name: "Afficher les détails de Film.mkv" }).getAttribute("aria-expanded")).toBe("false");
    await user.click(screen.getByRole("button", { name: "Annuler la demande Film.mkv" }));
    expect(await screen.findByText("La demande « Film.mkv » a été annulée.")).toBeTruthy();
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(await auditAccessibility(document.body)).toMatchObject({ violations: [] });
    await waitFor(() => expect(screen.queryByRole("article", { name: "Film.mkv" })).toBeNull());
    expect(screen.queryByText("Bloqué")).toBeNull();
    expect(screen.queryByRole("button", { name: "Annuler la demande Film.mkv" })).toBeNull();
    expect(calls).toContainEqual({
      method: "DELETE",
      url: "/api/v2/torrents/d86528f5-bc01-4a8b-86a1-74fe3404864b",
    });
    expect(view.container.querySelector("[style]")).toBeNull();
  });

  it("distingue Supprimer pour un READY tout en conservant le DELETE autoritatif", async () => {
    const user = userEvent.setup();
    let deleted = false;
    const calls: Array<{ method: string; url: string }> = [];
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const method = init?.method ?? "GET";
      const url = String(input);
      calls.push({ method, url });
      if (method === "DELETE") {
        deleted = true;
        return response(null, 204);
      }
      return response({
        items: deleted ? [] : [torrent({ state: "ready", progress: 1 })],
        offset: 0,
        limit: 10,
        total: deleted ? 0 : 1,
      });
    }));
    renderPage();

    await user.click(await screen.findByRole("button", { name: "Supprimer « Film.mkv »" }));
    expect(await screen.findByText("La demande « Film.mkv » a été annulée.")).toBeTruthy();
    expect(calls).toContainEqual({
      method: "DELETE",
      url: "/api/v2/torrents/d86528f5-bc01-4a8b-86a1-74fe3404864b",
    });
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

      await waitFor(() => expect(screen.getAllByText("Torrent ajouté")).toHaveLength(count));
      expect(view.container.querySelector(".torrent-upload-batch")).toBeNull();
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

    await waitFor(() => expect(screen.getAllByText("Torrent ajouté")).toHaveLength(2));
    expect(screen.getByText("Torrent déjà présent")).toBeTruthy();
    expect(screen.getAllByText("Échec de l’ajout")).toHaveLength(3);
    expect(screen.getByText(/empty\.torrent.*Le fichier \.torrent est vide\./s)).toBeTruthy();
    expect(view.container.querySelector(".torrent-upload-batch")).toBeNull();
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

    await waitFor(() => expect(screen.getAllByText("Torrent ajouté")).toHaveLength(2));
    expect(successful).toEqual(["ok-before.torrent", "ok-after.torrent"]);
    expect(screen.getAllByText("Échec de l’ajout")).toHaveLength(6);
    expect(screen.getByText("Stockage sous pression")).toBeTruthy();
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
    expect(uploadButtons.every((button) => button.getAttribute("aria-disabled") === "true")).toBe(true);
    fireEvent.change(input, { target: { files: [new File(["ignored"], "ignored.torrent")] } });
    expect(uploaded).toEqual(["first.torrent"]);

    releaseFirst();
    await screen.findByText("Torrent ajouté");
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
