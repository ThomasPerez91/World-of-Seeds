import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { api } from "../../api/client";
import { FeedbackProvider } from "../../components/Feedback";
import { I18nProvider } from "../../i18n";
import { UserDashboardPage } from "./UserDashboardPage";

const oldNativeDownloadKey = "wos.local-download-starts";

afterEach(() => {
  vi.restoreAllMocks();
  localStorage.removeItem(oldNativeDownloadKey);
});

describe("UserDashboardPage native downloads", () => {
  it("ignore un ancien historique natif au chargement et après un refresh", async () => {
    const previousHistory = JSON.stringify([{
      id: "native-1",
      kind: "file",
      name: "Film.mkv",
      startedAt: Date.now(),
      status: "started",
    }]);
    localStorage.setItem(oldNativeDownloadKey, previousHistory);
    vi.spyOn(api, "listTorrentRequestsV2").mockImplementation(async (offset, limit) => ({
      items: [],
      offset,
      limit,
      total: 0,
    }));
    vi.spyOn(api, "getSharedStorageCapacity").mockResolvedValue({
      total_bytes: 2_048,
      used_bytes: 1_024,
      available_bytes: 1_024,
    });
    vi.spyOn(api, "getNetworkThroughput").mockResolvedValue({
      status: "no_data",
      period: "realtime",
      sample_interval_seconds: 15,
      download: null,
      upload: null,
    });

    const dashboard = (
      <I18nProvider>
        <FeedbackProvider>
          <UserDashboardPage onSessionExpired={vi.fn()} />
        </FeedbackProvider>
      </I18nProvider>
    );

    const first = render(dashboard);
    await screen.findByRole("heading", { name: "Dashboard" });
    expect(screen.queryByText("Récupération locale")).toBeNull();
    expect(screen.queryByText("Film.mkv")).toBeNull();
    expect(localStorage.getItem(oldNativeDownloadKey)).toBe(previousHistory);

    first.unmount();
    render(dashboard);
    await screen.findByRole("heading", { name: "Dashboard" });
    expect(screen.queryByText("Récupération locale")).toBeNull();
    expect(screen.queryByText("Film.mkv")).toBeNull();
  });
});
