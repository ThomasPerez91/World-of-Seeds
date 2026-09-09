import { afterEach, describe, expect, it, vi } from "vitest";

import type { TorrentDownloadManifestPageV2 } from "../../api/client";
import {
  BrowserDownloadManager,
  type BrowserDownloadManagerSnapshot,
} from "./downloadManager";
import type { LocalFileHandle } from "./recursiveDownload";

function deferred() {
  let resolve!: () => void;
  const promise = new Promise<void>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

function target(): LocalFileHandle {
  return {
    createWritable: vi.fn(async () => ({
      seek: vi.fn(async () => undefined),
      write: vi.fn(async () => undefined),
      close: vi.fn(async () => undefined),
    })),
  };
}

function snapshot(fileId: string): TorrentDownloadManifestPageV2 {
  return {
    snapshot_id: "a".repeat(64),
    manifest_version: 1,
    file_count: 1,
    total_size: 1,
    archive_available: false,
    retention_expires_at: null,
    offset: 0,
    limit: 1,
    items: [{ id: fileId, file_index: 0, relative_path: `${fileId}.bin`, size: 1 }],
  };
}

afterEach(() => vi.restoreAllMocks());

describe("BrowserDownloadManager", () => {
  it("queues overflow jobs instead of failing when the stream limit is reached", async () => {
    const gates = [deferred(), deferred(), deferred()];
    let call = 0;
    vi.stubGlobal("fetch", vi.fn(async () => {
      const index = call++;
      await gates[index].promise;
      return new Response(new Uint8Array([1]), {
        headers: { "X-WOS-Manifest-Version": "1" },
      });
    }));
    let current: BrowserDownloadManagerSnapshot = {
      activeStreams: 0,
      maxConcurrentStreams: 2,
      waitingJobs: 0,
      jobs: [],
    };
    const manager = new BrowserDownloadManager(2, (next) => {
      current = next;
    });

    for (const id of ["one", "two", "three"]) {
      const page = snapshot(id);
      manager.enqueueFile({
        torrentId: `torrent-${id}`,
        name: `${id}.bin`,
        snapshot: page,
        file: page.items[0],
        target: target(),
      });
    }

    await vi.waitFor(() => expect(call).toBe(2));
    expect(current.activeStreams).toBe(2);
    expect(current.waitingJobs).toBe(1);
    expect(current.jobs.map((job) => job.status)).toEqual(["running", "running", "queued"]);

    gates[0].resolve();
    await vi.waitFor(() => expect(call).toBe(3));
    expect(current.activeStreams).toBe(2);
    expect(current.jobs[2].status).toBe("running");

    gates[1].resolve();
    gates[2].resolve();
    await vi.waitFor(() => expect(current.jobs.every((job) => job.status === "completed")).toBe(true));
    expect(current.activeStreams).toBe(0);
  });

  it("applies a raised administrator stream limit to already queued jobs", async () => {
    const gates = Array.from({ length: 5 }, deferred);
    let call = 0;
    vi.stubGlobal("fetch", vi.fn(async () => {
      const index = call++;
      await gates[index].promise;
      return new Response(new Uint8Array([1]), {
        headers: { "X-WOS-Manifest-Version": "1" },
      });
    }));
    let current: BrowserDownloadManagerSnapshot = {
      activeStreams: 0,
      maxConcurrentStreams: 2,
      waitingJobs: 0,
      jobs: [],
    };
    const manager = new BrowserDownloadManager(2, (next) => {
      current = next;
    });

    for (let index = 0; index < 5; index += 1) {
      const page = snapshot(`file-${index}`);
      manager.enqueueFile({
        torrentId: `torrent-${index}`,
        name: `file-${index}.bin`,
        snapshot: page,
        file: page.items[0],
        target: target(),
      });
    }
    await vi.waitFor(() => expect(call).toBe(2));

    manager.setMaxConcurrentStreams(20);

    await vi.waitFor(() => expect(call).toBe(5));
    expect(current.maxConcurrentStreams).toBe(20);
    expect(current.activeStreams).toBe(5);
    expect(current.waitingJobs).toBe(0);

    gates.forEach((gate) => gate.resolve());
    await vi.waitFor(() => expect(current.activeStreams).toBe(0));
  });
});
