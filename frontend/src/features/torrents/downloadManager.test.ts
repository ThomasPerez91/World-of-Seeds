import { afterEach, describe, expect, it, vi } from "vitest";

import type { TorrentDownloadManifestPageV2 } from "../../api/client";
import {
  BrowserDownloadManager,
  loadBrowserDownloadPolicy,
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

function failingTarget(): LocalFileHandle {
  return {
    createWritable: vi.fn(async () => {
      throw new DOMException("denied", "NotAllowedError");
    }),
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

function validResponse(): Response {
  return new Response(new Uint8Array([1]), {
    headers: { "X-WOS-Manifest-Version": "1" },
  });
}

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("BrowserDownloadManager", () => {
  it("parses explicit standard and unlimited server policies", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({
        max_concurrent_streams: 2,
        unlimited: false,
      })))
      .mockResolvedValueOnce(new Response(JSON.stringify({
        max_concurrent_streams: null,
        unlimited: true,
      })));
    vi.stubGlobal("fetch", fetchMock);

    await expect(loadBrowserDownloadPolicy()).resolves.toEqual({
      max_concurrent_streams: 2,
      unlimited: false,
    });
    await expect(loadBrowserDownloadPolicy()).resolves.toEqual({
      max_concurrent_streams: null,
      unlimited: true,
    });
  });

  it("starts conservatively until the server policy is applied and queues overflow jobs", async () => {
    const gates = [deferred(), deferred(), deferred()];
    let call = 0;
    vi.stubGlobal("fetch", vi.fn(async () => {
      const index = call++;
      await gates[index].promise;
      return validResponse();
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

    await vi.waitFor(() => expect(call).toBe(1));
    expect(current.activeStreams).toBe(1);
    expect(current.maxConcurrentStreams).toBe(1);
    expect(current.waitingJobs).toBe(2);
    expect(current.jobs.map((job) => job.status)).toEqual(["running", "queued", "queued"]);

    manager.setMaxConcurrentStreams(2);

    await vi.waitFor(() => expect(call).toBe(2));
    expect(current.activeStreams).toBe(2);
    expect(current.maxConcurrentStreams).toBe(2);
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

  it("alterne les permis entre dossiers concurrents au lieu de laisser un job monopoliser la file", async () => {
    const gates = Array.from({ length: 4 }, deferred);
    const started: string[] = [];
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const index = started.length;
      started.push(String(input));
      await gates[index].promise;
      return validResponse();
    }));
    const directory = {
      getDirectoryHandle: vi.fn(async () => directory),
      getFileHandle: vi.fn(async () => target()),
    };
    const folderSnapshot = (prefix: string): TorrentDownloadManifestPageV2 => ({
      ...snapshot(`${prefix}-one`),
      file_count: 2,
      total_size: 2,
      limit: 2,
      items: [
        { id: `${prefix}-one`, file_index: 0, relative_path: `${prefix}/one.bin`, size: 1 },
        { id: `${prefix}-two`, file_index: 1, relative_path: `${prefix}/two.bin`, size: 1 },
      ],
    });
    const manager = new BrowserDownloadManager(1, vi.fn());
    manager.enqueueFolder({
      torrentId: "torrent-a",
      name: "A",
      snapshot: folderSnapshot("a"),
      directory,
      loadManifestPage: vi.fn(),
    });
    manager.enqueueFolder({
      torrentId: "torrent-b",
      name: "B",
      snapshot: folderSnapshot("b"),
      directory,
      loadManifestPage: vi.fn(),
    });

    await vi.waitFor(() => expect(started).toHaveLength(1));
    gates[0].resolve();
    await vi.waitFor(() => expect(started).toHaveLength(2));
    expect(started[0]).toContain("/files/a-one/");
    expect(started[1]).toContain("/files/b-one/");

    gates[1].resolve();
    await vi.waitFor(() => expect(started).toHaveLength(3));
    expect(started[2]).toContain("/files/a-two/");
    gates[2].resolve();
    await vi.waitFor(() => expect(started).toHaveLength(4));
    expect(started[3]).toContain("/files/b-two/");
    gates[3].resolve();
  });

  it("applies a raised administrator stream limit to already queued jobs", async () => {
    const gates = Array.from({ length: 5 }, deferred);
    let call = 0;
    vi.stubGlobal("fetch", vi.fn(async () => {
      const index = call++;
      await gates[index].promise;
      return validResponse();
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
    await vi.waitFor(() => expect(call).toBe(1));
    expect(current.waitingJobs).toBe(4);

    manager.setMaxConcurrentStreams(20);

    await vi.waitFor(() => expect(call).toBe(5));
    expect(current.maxConcurrentStreams).toBe(20);
    expect(current.activeStreams).toBe(5);
    expect(current.waitingJobs).toBe(0);

    gates.forEach((gate) => gate.resolve());
    await vi.waitFor(() => expect(current.activeStreams).toBe(0));
  });

  it("starts ten administrator jobs without a per-user permit queue", async () => {
    const gates = Array.from({ length: 10 }, deferred);
    let call = 0;
    vi.stubGlobal("fetch", vi.fn(async () => {
      const index = call++;
      await gates[index].promise;
      return validResponse();
    }));
    let current: BrowserDownloadManagerSnapshot = {
      activeStreams: 0,
      maxConcurrentStreams: 1,
      waitingJobs: 0,
      jobs: [],
    };
    const manager = new BrowserDownloadManager(1, (next) => {
      current = next;
    });
    manager.setMaxConcurrentStreams(null);

    for (let index = 0; index < 10; index += 1) {
      const page = snapshot(`admin-${index}`);
      manager.enqueueFile({
        torrentId: `torrent-admin-${index}`,
        name: `admin-${index}.bin`,
        snapshot: page,
        file: page.items[0],
        target: target(),
      });
    }

    await vi.waitFor(() => expect(call).toBe(10));
    expect(current.maxConcurrentStreams).toBeNull();
    expect(current.activeStreams).toBe(10);
    expect(current.waitingJobs).toBe(0);
    expect(current.jobs.every((job) => job.status === "running")).toBe(true);

    gates.forEach((gate) => gate.resolve());
    await vi.waitFor(() => expect(current.activeStreams).toBe(0));
  });

  it("releases a permit when a response is rejected before its body is consumed", async () => {
    let call = 0;
    vi.stubGlobal("fetch", vi.fn(async () => {
      call += 1;
      if (call === 1) return new Response(new Uint8Array([1]));
      return validResponse();
    }));
    let current: BrowserDownloadManagerSnapshot = {
      activeStreams: 0,
      maxConcurrentStreams: 1,
      waitingJobs: 0,
      jobs: [],
    };
    const manager = new BrowserDownloadManager(1, (next) => {
      current = next;
    });
    const first = snapshot("invalid-response");
    const second = snapshot("after-invalid-response");

    manager.enqueueFile({
      torrentId: "torrent-invalid-response",
      name: "invalid-response.bin",
      snapshot: first,
      file: first.items[0],
      target: target(),
    });
    manager.enqueueFile({
      torrentId: "torrent-after-invalid-response",
      name: "after-invalid-response.bin",
      snapshot: second,
      file: second.items[0],
      target: target(),
    });

    await vi.waitFor(() => expect(call).toBe(2));
    await vi.waitFor(() => expect(current.jobs[1]?.status).toBe("completed"));
    expect(current.jobs[0]?.status).toBe("error");
    expect(current.jobs[0]?.error).toBe("manifest_changed");
    expect(current.activeStreams).toBe(0);
  });

  it("releases a permit when opening the local writer fails", async () => {
    let call = 0;
    vi.stubGlobal("fetch", vi.fn(async () => {
      call += 1;
      return validResponse();
    }));
    let current: BrowserDownloadManagerSnapshot = {
      activeStreams: 0,
      maxConcurrentStreams: 1,
      waitingJobs: 0,
      jobs: [],
    };
    const manager = new BrowserDownloadManager(1, (next) => {
      current = next;
    });
    const first = snapshot("writer-denied");
    const second = snapshot("after-writer-denied");

    manager.enqueueFile({
      torrentId: "torrent-writer-denied",
      name: "writer-denied.bin",
      snapshot: first,
      file: first.items[0],
      target: failingTarget(),
    });
    manager.enqueueFile({
      torrentId: "torrent-after-writer-denied",
      name: "after-writer-denied.bin",
      snapshot: second,
      file: second.items[0],
      target: target(),
    });

    await vi.waitFor(() => expect(call).toBe(2));
    await vi.waitFor(() => expect(current.jobs[1]?.status).toBe("completed"));
    expect(current.jobs[0]?.status).toBe("error");
    expect(current.jobs[0]?.error).toBe("local_write_denied");
    expect(current.activeStreams).toBe(0);
  });
});
