import { expect, it, vi } from "vitest";

import { api } from "../api/client";

it.each([
  api.getAdminServicesHealth,
  api.getNewGreedyConfig,
  api.getNewGreedyOverview,
  api.getNewGreedyRestartStatus,
  api.listNewGreedyTorrents,
  api.listQBittorrentTorrents,
])("transmet le signal des lectures de supervision jusqu’à fetch", async (read) => {
  const fetchMock = vi.fn(async () => new Response("{}", { status: 200, headers: { "Content-Type": "application/json" } }));
  vi.stubGlobal("fetch", fetchMock);
  const controller = new AbortController();
  await read(controller.signal);
  expect(fetchMock).toHaveBeenCalledWith(expect.any(String), expect.objectContaining({ signal: controller.signal, credentials: "same-origin" }));
});
