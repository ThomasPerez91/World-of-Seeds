import { act, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { api, ApiError, type NetworkThroughput } from "../../api/client";
import { I18nProvider } from "../../i18n";
import { NetworkThroughputCard, NETWORK_REFRESH_MS } from "./UserDashboardPage";

const measurement: NetworkThroughput = {
  status: "ok", period: "realtime", sample_interval_seconds: 15,
  download: { current_bytes_per_second: 1024, samples: [] },
  upload: { current_bytes_per_second: 2048, samples: [] },
};

afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks(); });

describe("NetworkThroughputCard request lifecycle", () => {
  it("affiche une réponse lente au lieu de la réannuler à chaque intervalle", async () => {
    vi.useFakeTimers();
    let complete!: (value: NetworkThroughput) => void;
    const loader = vi.spyOn(api, "getNetworkThroughput").mockImplementation(() => new Promise((resolve) => { complete = resolve; }));
    const view = render(<I18nProvider><NetworkThroughputCard onSessionExpired={vi.fn()} /></I18nProvider>);
    await act(async () => vi.advanceTimersByTimeAsync(NETWORK_REFRESH_MS * 2));
    expect(loader).toHaveBeenCalledTimes(1);
    expect(loader.mock.calls[0][0]?.aborted).toBe(false);
    await act(async () => complete(measurement));
    expect(screen.getByText("1 Ko/s")).toBeTruthy();
    view.unmount();
  });

  it("ignore une ancienne erreur de session après navigation même si le transport ignore abort", async () => {
    let fail!: (error: unknown) => void;
    const expired = vi.fn();
    const loader = vi.spyOn(api, "getNetworkThroughput").mockImplementation(() => new Promise((_resolve, reject) => { fail = reject; }));
    const view = render(<I18nProvider><NetworkThroughputCard onSessionExpired={expired} /></I18nProvider>);
    await act(async () => Promise.resolve());
    view.unmount();
    expect(loader.mock.calls[0][0]?.aborted).toBe(true);
    await act(async () => fail(new ApiError(401, "expired")));
    expect(expired).not.toHaveBeenCalled();
  });
});
