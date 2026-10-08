import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

import { api, type NewGreedyRestartStatus } from "../../api/client";
import { FeedbackProvider } from "../../components/Feedback";
import { I18nProvider } from "../../i18n";
import { NewGreedyControlPanel } from "./NewGreedyControlPanel";

const idle: NewGreedyRestartStatus = { state: "idle", request_id: null, updated_at: null, message_code: "idle" };
const pending: NewGreedyRestartStatus = { state: "pending", request_id: "request-1", updated_at: null, message_code: "requested" };

afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks(); });

it("empêche une ancienne lecture idle d’écraser le redémarrage demandé", async () => {
  vi.useFakeTimers();
  let resolveOld!: (value: NewGreedyRestartStatus) => void;
  let resolveWrite!: (value: NewGreedyRestartStatus) => void;
  const read = vi.spyOn(api, "getNewGreedyRestartStatus")
    .mockResolvedValueOnce(idle)
    .mockImplementationOnce(() => new Promise((resolve) => { resolveOld = resolve; }))
    .mockResolvedValue(pending);
  vi.spyOn(api, "restartNewGreedy").mockImplementation(() => new Promise((resolve) => { resolveWrite = resolve; }));
  vi.spyOn(api, "getNewGreedyConfig").mockResolvedValue({ sections: [], restart_required: false });
  vi.spyOn(api, "getNewGreedyOverview").mockResolvedValue({ torrents: 0, downloading: 0, seeding: 0, stalled: 0, target_reached: 0, total_downloaded_bytes: 0, total_reported_uploaded_bytes: 0, total_fake_uploaded_bytes: 0 });
  const view = render(<I18nProvider><FeedbackProvider><NewGreedyControlPanel onSessionExpired={vi.fn()} /></FeedbackProvider></I18nProvider>);
  await act(async () => Promise.resolve());
  await act(async () => vi.advanceTimersByTimeAsync(2_000));
  expect(read).toHaveBeenCalledTimes(2);
  fireEvent.click(screen.getByRole("button", { name: "Redémarrer NewGreedy" }));
  fireEvent.click(screen.getByRole("button", { name: "Confirmer le redémarrage" }));
  expect(read.mock.calls[1][0]?.aborted).toBe(true);
  await act(async () => vi.advanceTimersByTimeAsync(4_000));
  expect(read).toHaveBeenCalledTimes(2);
  await act(async () => resolveWrite(pending));
  expect(read).toHaveBeenCalledTimes(3);
  await act(async () => resolveOld(idle));
  expect((screen.getByRole("button", { name: "Redémarrer NewGreedy" }) as HTMLButtonElement).disabled).toBe(true);
  view.unmount();
});
