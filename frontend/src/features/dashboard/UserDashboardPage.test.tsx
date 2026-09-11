import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { api, type TorrentRequestV2 } from "../../api/client";
import { FeedbackProvider } from "../../components/Feedback";
import { I18nProvider, type Locale } from "../../i18n";
import { auditAccessibility } from "../../test/accessibility";
import { summarizeLocalTransfer } from "../torrents/UserDownloadsPage";
import {
  classifyTorrentActivity,
  loadTorrentActivity,
  LocalDownloadCard,
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
    retention_expires_at: null,
    queue_position_estimate: null,
    queue_total_estimate: null,
    queue_status: "downloading",
    created_at: "2026-09-08T12:00:00Z",
    updated_at: "2026-09-08T12:00:00Z",
    ...overrides,
  };
}

function renderDashboard(locale: Locale = "fr") {
  localStorage.setItem("wos.preferred-locale", locale);
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
    expect(screen.getByText("Aucune récupération locale en cours")).toBeTruthy();
    expect(screen.getByText("Les téléchargements lancés depuis ce navigateur apparaissent ici.")).toBeTruthy();
    expect((await screen.findAllByText("1 Ko disponibles")).length).toBeGreaterThan(0);
    expect(screen.getByText("2 Ko au total")).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Mes téléchargements" })).toBeTruthy();
    expect(await auditAccessibility(view.container)).toMatchObject({ violations: [] });
  });

  it("isole les erreurs des cartouches et permet leur nouvelle tentative", async () => {
    const torrents = vi.spyOn(api, "listTorrentRequestsV2").mockRejectedValue(new Error("offline"));
    const storage = vi.spyOn(api, "getSharedStorageCapacity").mockRejectedValue(new Error("offline"));
    const view = renderDashboard("en");

    expect(await screen.findByText("Torrent activity is temporarily unavailable.")).toBeTruthy();
    expect(screen.getByText("Storage is temporarily unavailable.")).toBeTruthy();
    expect(screen.getByRole("heading", { name: "My downloads" })).toBeTruthy();
    expect(screen.getByText("No local download in progress")).toBeTruthy();
    const retries = screen.getAllByRole("button", { name: "Try again" });
    await userEvent.click(retries[0]);
    await userEvent.click(retries[1]);
    await waitFor(() => expect(torrents.mock.calls.length).toBeGreaterThan(2));
    expect(storage.mock.calls.length).toBeGreaterThan(1);
    expect(await auditAccessibility(view.container)).toMatchObject({ violations: [] });
  });

  it("résume exactement les états locaux visibles sans inventer une file globale", () => {
    const local = summarizeLocalTransfer({
      status: "running",
      downloadedBytes: 128,
      completedFiles: 1,
      error: null,
      queue: [
        { id: "a", relativePath: "active", status: "active", position: null },
        { id: "b", relativePath: "waiting", status: "waiting", position: 1 },
        { id: "c", relativePath: "waiting-2", status: "waiting", position: 2 },
        { id: "d", relativePath: "done", status: "completed", position: null },
      ],
    });
    expect(local).toEqual({
      active: 1,
      additionalCount: 0,
      maximum: 2,
      status: "running",
      waiting: 2,
      name: null,
      downloadedBytes: 128,
      totalBytes: 0,
      percent: 0,
    });

    const view = render(
      <I18nProvider><LocalDownloadCard local={local} /></I18nProvider>,
    );
    expect(screen.getByText("1 / 2 actifs")).toBeTruthy();
    expect(screen.getByText("2 fichiers en attente")).toBeTruthy();
    expect(screen.getByText(/lancés depuis ce navigateur/)).toBeTruthy();
    expect(view.container.textContent).not.toContain("globale");
  });

  it("affiche un téléchargement natif lancé sans inventer de progression", () => {
    const view = render(
      <I18nProvider>
        <LocalDownloadCard local={{
          active: 0,
          additionalCount: 2,
          maximum: 2,
          status: "started",
          waiting: 0,
          name: "The.Cleaning.Lady.S02E01.mkv",
          downloadedBytes: 0,
          totalBytes: 0,
          percent: 0,
        }} />
      </I18nProvider>,
    );

    expect(screen.getByText("The.Cleaning.Lady.S02E01.mkv")).toBeTruthy();
    expect(screen.getByText("Téléchargement lancé dans le navigateur")).toBeTruthy();
    expect(screen.getByText("+ 2 autres téléchargements")).toBeTruthy();
    expect(view.container.querySelector("progress")).toBeNull();
    expect(view.container.textContent).not.toContain("0 %");
  });

  it("reflète explicitement l’échec d’une récupération gérée", () => {
    render(
      <I18nProvider>
        <LocalDownloadCard local={{
          active: 0,
          additionalCount: 0,
          maximum: 2,
          status: "error",
          waiting: 0,
          name: null,
          downloadedBytes: 0,
          totalBytes: 0,
          percent: 0,
        }} />
      </I18nProvider>,
    );

    expect(screen.getByText("Erreur")).toBeTruthy();
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
    const view = renderDashboard();
    await act(async () => Promise.resolve());
    expect(screen.getByText("Lecture de l’activité…")).toBeTruthy();
    expect(screen.getByText("Lecture du stockage…")).toBeTruthy();
    view.unmount();
    expect(capturedSignal?.aborted).toBe(true);
  });
});
