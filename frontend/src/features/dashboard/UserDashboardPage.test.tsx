import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { api, type NetworkThroughput, type TorrentRequestV2 } from "../../api/client";
import { FeedbackProvider } from "../../components/Feedback";
import { I18nProvider, type Locale } from "../../i18n";
import { auditAccessibility } from "../../test/accessibility";
import {
  classifyTorrentActivity,
  loadTorrentActivity,
  NETWORK_REFRESH_MS,
  NetworkThroughputCard,
  UserDashboardPage,
} from "./UserDashboardPage";

function torrent(overrides: Partial<TorrentRequestV2> = {}): TorrentRequestV2 {
  return {
    id: crypto.randomUUID(),
    name: "Example.torrent",
    total_size: 1_024,
    state: "active",
    progress: 0.5,
    error_code: null,
    ready_at: null,
    unsubscribe_at: null,
    retention_expires_at: null,
    queue_position_estimate: null,
    queue_total_estimate: null,
    queue_status: "downloading",
    scheduler_retry_at: null,
    created_at: "2026-09-08T12:00:00Z",
    updated_at: "2026-09-08T12:00:00Z",
    ...overrides,
  };
}

function network(overrides: Partial<NetworkThroughput> = {}): NetworkThroughput {
  return {
    status: "ok",
    period: "realtime",
    sample_interval_seconds: 15,
    download: {
      current_bytes_per_second: 75 * 1024 * 1024,
      samples: [
        { timestamp: "2026-09-13T10:29:45Z", value_bytes_per_second: 64 * 1024 * 1024 },
        { timestamp: "2026-09-13T10:30:00Z", value_bytes_per_second: 75 * 1024 * 1024 },
      ],
    },
    upload: {
      current_bytes_per_second: 1.5 * 1024 * 1024,
      samples: [
        { timestamp: "2026-09-13T10:29:45Z", value_bytes_per_second: 1024 * 1024 },
        { timestamp: "2026-09-13T10:30:00Z", value_bytes_per_second: 1.5 * 1024 * 1024 },
      ],
    },
    ...overrides,
  };
}

function renderDashboard(locale: Locale = "fr") {
  localStorage.setItem("wos.preferred-locale", locale);
  if (!vi.isMockFunction(api.getNetworkThroughput)) {
    vi.spyOn(api, "getNetworkThroughput").mockResolvedValue(network());
  }
  return render(
    <I18nProvider>
      <FeedbackProvider>
        <UserDashboardPage onSessionExpired={vi.fn()} />
      </FeedbackProvider>
    </I18nProvider>,
  );
}

afterEach(() => vi.restoreAllMocks());

describe("UserDashboardPage", () => {
  it("pagine par 100 et classe chaque torrent dans au plus un compteur", async () => {
    const firstPage = Array.from({ length: 100 }, (_, index) => torrent({
      state: index === 0 ? "ready" : index === 1 ? "requested" : "active",
      queue_status: index === 2 ? "waiting" : "downloading",
    }));
    const lastPage = [
      torrent({ state: "cancelled" }),
      torrent({ state: "expired" }),
      torrent({ state: "error" }),
    ];
    const listing = vi.spyOn(api, "listTorrentRequestsV2").mockImplementation(
      async (offset, limit) => ({
        items: offset === 0 ? firstPage : lastPage,
        offset,
        limit,
        total: 103,
      }),
    );

    await expect(loadTorrentActivity(new AbortController().signal)).resolves.toEqual({
      active: 97,
      ready: 1,
      waiting: 2,
    });
    expect(listing).toHaveBeenNthCalledWith(1, 0, 100, expect.any(AbortSignal));
    expect(listing).toHaveBeenNthCalledWith(2, 100, 100, expect.any(AbortSignal));

    const summary = { active: 0, ready: 0, waiting: 0 };
    classifyTorrentActivity(summary, torrent({ state: "ready", queue_status: "waiting" }));
    expect(summary).toEqual({ active: 0, ready: 1, waiting: 0 });
  });

  it("affiche les trois synthèses avec les contrats existants", async () => {
    vi.spyOn(api, "listTorrentRequestsV2").mockImplementation(async (offset, limit) => ({
      items: limit === 100 ? [
        torrent(),
        torrent({ state: "ready", progress: 1, queue_status: null }),
        torrent({ state: "requested", queue_status: "waiting" }),
      ] : [],
      offset,
      limit,
      total: limit === 100 ? 3 : 0,
    }));
    vi.spyOn(api, "getSharedStorageCapacity").mockResolvedValue({
      total_bytes: 2_048,
      used_bytes: 1_024,
      available_bytes: 1_024,
    });
    const view = renderDashboard();

    await screen.findByRole("heading", { name: "Dashboard" });
    const activity = screen.getByRole("region", { name: "Activité torrents" });
    await waitFor(() => expect(within(activity).getAllByText("1", { selector: "dd" })).toHaveLength(3));
    expect(within(activity).getAllByText("1", { selector: "dd" })).toHaveLength(3);
    expect(screen.queryByText("Récupération locale")).toBeNull();
    const networkCard = screen.getByRole("region", { name: "Vitesse réseau" });
    expect(within(networkCard).getByText("Temps réel")).toBeTruthy();
    expect(within(networkCard).getByText("Download")).toBeTruthy();
    expect(within(networkCard).getByText("Upload")).toBeTruthy();
    expect(within(networkCard).getByText("75 Mo/s")).toBeTruthy();
    expect(within(networkCard).getByText("1,5 Mo/s")).toBeTruthy();
    expect(within(networkCard).getAllByRole("img")).toHaveLength(2);
    expect((await screen.findAllByText("1 Ko disponibles")).length).toBeGreaterThan(0);
    expect(screen.getByText("2 Ko au total")).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Mes téléchargements" })).toBeTruthy();
    expect(await auditAccessibility(view.container)).toMatchObject({ violations: [] });
  });

  it("isole les erreurs des cartouches et permet leur nouvelle tentative", async () => {
    const torrents = vi.spyOn(api, "listTorrentRequestsV2").mockRejectedValue(new Error("offline"));
    const storage = vi.spyOn(api, "getSharedStorageCapacity").mockRejectedValue(new Error("offline"));
    vi.spyOn(api, "getNetworkThroughput").mockRejectedValue(new Error("offline"));
    const view = renderDashboard("en");

    expect(await screen.findByText("Torrent activity is temporarily unavailable.")).toBeTruthy();
    expect(screen.getByText("Storage is temporarily unavailable.")).toBeTruthy();
    expect(screen.getByRole("heading", { name: "My downloads" })).toBeTruthy();
    expect(screen.getByText("Network throughput is temporarily unavailable.")).toBeTruthy();
    const retries = screen.getAllByRole("button", { name: "Try again" });
    await userEvent.click(retries[0]);
    await userEvent.click(retries[1]);
    await waitFor(() => expect(torrents.mock.calls.length).toBeGreaterThan(2));
    expect(storage.mock.calls.length).toBeGreaterThan(1);
    expect(await auditAccessibility(view.container)).toMatchObject({ violations: [] });
  });

  it("gère le chargement et l’absence de mesures sans bloquer le Dashboard", async () => {
    let resolveNetwork: ((value: NetworkThroughput) => void) | undefined;
    vi.spyOn(api, "getNetworkThroughput").mockImplementation(() => new Promise((resolve) => {
      resolveNetwork = resolve;
    }));
    const view = renderDashboard();
    expect(await screen.findByText("Lecture du débit réseau…")).toBeTruthy();
    await act(async () => resolveNetwork?.(network({ status: "no_data", download: null, upload: null })));
    expect(await screen.findByText("Mesures réseau indisponibles")).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Mes téléchargements" })).toBeTruthy();
    view.unmount();
  });

  it("rafraîchit le débit toutes les 15 secondes", async () => {
    vi.useFakeTimers();
    const loadNetwork = vi.spyOn(api, "getNetworkThroughput").mockResolvedValue(network());
    const view = render(
      <I18nProvider><NetworkThroughputCard onSessionExpired={vi.fn()} /></I18nProvider>,
    );
    await act(async () => Promise.resolve());
    expect(loadNetwork).toHaveBeenCalledTimes(1);
    await act(async () => vi.advanceTimersByTimeAsync(NETWORK_REFRESH_MS));
    expect(loadNetwork).toHaveBeenCalledTimes(2);
    view.unmount();
    vi.useRealTimers();
  });

  it.each(["light", "dark", "system"] as const)("conserve la carte réseau avec le thème %s", async (theme) => {
    document.documentElement.dataset.theme = theme;
    vi.spyOn(api, "getNetworkThroughput").mockResolvedValue(network());
    const view = render(
      <I18nProvider><NetworkThroughputCard onSessionExpired={vi.fn()} /></I18nProvider>,
    );
    expect(await screen.findByRole("region", { name: "Vitesse réseau" })).toBeTruthy();
    view.unmount();
    delete document.documentElement.dataset.theme;
  });

  it("annule l’agrégation au démontage", async () => {
    let capturedSignal: AbortSignal | undefined;
    vi.spyOn(api, "listTorrentRequestsV2").mockImplementation((_offset, _limit, signal) => {
      capturedSignal = signal;
      return new Promise(() => undefined);
    });
    vi.spyOn(api, "getSharedStorageCapacity").mockImplementation((signal) => {
      return new Promise((_resolve, reject) => signal?.addEventListener("abort", () => {
        reject(new DOMException("aborted", "AbortError"));
      }));
    });
    let networkSignal: AbortSignal | undefined;
    vi.spyOn(api, "getNetworkThroughput").mockImplementation((signal) => {
      networkSignal = signal;
      return new Promise(() => undefined);
    });
    const view = renderDashboard();
    await act(async () => Promise.resolve());
    expect(screen.getByText("Lecture de l’activité…")).toBeTruthy();
    expect(screen.getByText("Lecture du stockage…")).toBeTruthy();
    view.unmount();
    expect(capturedSignal?.aborted).toBe(true);
    expect(networkSignal?.aborted).toBe(true);
  });
});
