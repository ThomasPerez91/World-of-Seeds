import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { api } from "../../api/client";
import { FeedbackProvider } from "../../components/Feedback";
import { I18nProvider } from "../../i18n";
import { NATIVE_DOWNLOAD_STORAGE_KEY } from "../torrents/UserDownloadsPage";
import { UserDashboardPage } from "./UserDashboardPage";

afterEach(() => {
  vi.restoreAllMocks();
  localStorage.removeItem(NATIVE_DOWNLOAD_STORAGE_KEY);
});

describe("UserDashboardPage local recovery", () => {
  it("affiche les téléchargements natifs signalés par UserDownloadsPage", async () => {
    localStorage.setItem(NATIVE_DOWNLOAD_STORAGE_KEY, JSON.stringify([{
      id: "native-1",
      kind: "file",
      name: "Film.mkv",
      startedAt: Date.now(),
      status: "started",
    }]));
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

    render(
      <I18nProvider>
        <FeedbackProvider>
          <UserDashboardPage onSessionExpired={vi.fn()} />
        </FeedbackProvider>
      </I18nProvider>,
    );

    expect(await screen.findByText("Récupération locale")).toBeTruthy();
    expect(screen.getByText("Film.mkv")).toBeTruthy();
  });
});
