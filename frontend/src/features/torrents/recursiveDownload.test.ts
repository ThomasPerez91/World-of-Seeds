import { describe, expect, it, vi } from "vitest";

import { ApiError, type TorrentDownloadSnapshotV2 } from "../../api/client";
import {
  type LocalDirectoryHandle,
  type LocalFileHandle,
  pickDownloadDirectory,
  recursiveDirectoryDownloadCapability,
  RecursiveDownloadController,
  type RecursiveTransferProgress,
  type WritableFileHandle,
} from "./recursiveDownload";

class MemoryFile implements LocalFileHandle {
  content = new Uint8Array();

  async getFile(): Promise<{ readonly size: number }> {
    return { size: this.content.length };
  }

  async createWritable(options?: { keepExistingData?: boolean }): Promise<WritableFileHandle> {
    if (options?.keepExistingData !== true) this.content = new Uint8Array();
    let position = 0;
    return {
      seek: async (next) => { position = next; },
      write: async (chunk) => {
        const length = Math.max(this.content.length, position + chunk.length);
        const next = new Uint8Array(length);
        next.set(this.content);
        next.set(chunk, position);
        this.content = next;
        position += chunk.length;
      },
      close: async () => undefined,
    };
  }
}

class MemoryDirectory implements LocalDirectoryHandle {
  readonly directories = new Map<string, MemoryDirectory>();
  readonly files = new Map<string, MemoryFile>();

  async getDirectoryHandle(name: string): Promise<MemoryDirectory> {
    const directory = this.directories.get(name) ?? new MemoryDirectory();
    this.directories.set(name, directory);
    return directory;
  }

  async getFileHandle(name: string): Promise<MemoryFile> {
    const file = this.files.get(name) ?? new MemoryFile();
    this.files.set(name, file);
    return file;
  }
}

function snapshot(): TorrentDownloadSnapshotV2 {
  return {
    snapshot_id: "a".repeat(64),
    manifest_version: 3,
    file_count: 2,
    total_size: 6,
    archive_available: true,
    retention_expires_at: null,
    offset: 0,
    limit: 500,
    items: [
      { id: "first", file_index: 0, relative_path: "Show/a.bin", size: 3 },
      { id: "second", file_index: 1, relative_path: "Show/sub/b.bin", size: 3 },
    ],
  };
}

function fileResponse(content: Uint8Array, status = 200, rangeStart = 2, total = 4): Response {
  const headers: Record<string, string> = { "X-WOS-Manifest-Version": "3" };
  if (status === 206) {
    headers["Content-Range"] = `bytes ${rangeStart}-${rangeStart + content.length - 1}/${total}`;
  }
  return new Response(content.slice().buffer, {
    status,
    headers,
  });
}

describe("RecursiveDownloadController", () => {
  it.each(["same", "changed"])("reprend un corps tronqué et refuse un Range invalide : %s", async (variant) => {
    vi.useFakeTimers();
    try {
      const directory = new MemoryDirectory();
      const updates: RecursiveTransferProgress[] = [];
      const fetcher = vi.fn()
        .mockResolvedValueOnce(fileResponse(new Uint8Array([1, 2])))
        .mockResolvedValueOnce(fileResponse(new Uint8Array([3, 4]), variant === "same" ? 206 : 200));
      const controller = new RecursiveDownloadController({
        torrentRequestId: "request",
        firstPage: {
          ...snapshot(), file_count: 1, total_size: 4,
          items: [{ id: "file", file_index: 0, relative_path: "file.bin", size: 4 }],
        },
        directory, loadManifestPage: vi.fn(), concurrency: 1, fetcher,
        onProgress: (progress) => updates.push(progress),
      });
      const running = controller.start();
      await vi.runAllTimersAsync();
      await running;
      expect(fetcher).toHaveBeenCalledTimes(2);
      expect(new Headers(fetcher.mock.calls[1][1].headers).get("Range")).toBe("bytes=2-");
      expect(updates.at(-1)?.status).toBe(variant === "same" ? "completed" : "error");
      expect(updates.at(-1)?.error).toBe(variant === "same" ? null : "manifest_changed");
      expect([...directory.files.get("file.bin")!.content]).toEqual(
        variant === "same" ? [1, 2, 3, 4] : [1, 2],
      );
    } finally {
      vi.useRealTimers();
    }
  });

  it.each(["write", "close"])("ne réessaie pas une TypeError locale de %s", async (operation) => {
    class BrokenFile extends MemoryFile {
      override async createWritable(): Promise<WritableFileHandle> {
        const writer = await super.createWritable();
        return { ...writer, [operation]: async () => { throw new TypeError("local failure"); } };
      }
    }
    const directory = new MemoryDirectory();
    directory.files.set("file.bin", new BrokenFile());
    const updates: RecursiveTransferProgress[] = [];
    const fetcher = vi.fn(async () => fileResponse(new Uint8Array([1, 2, 3])));
    const controller = new RecursiveDownloadController({
      torrentRequestId: "request",
      firstPage: {
        ...snapshot(), file_count: 1, total_size: 3,
        items: [{ id: "file", file_index: 0, relative_path: "file.bin", size: 3 }],
      },
      directory, loadManifestPage: vi.fn(), concurrency: 1, fetcher,
      onProgress: (progress) => updates.push(progress),
    });
    await controller.start();
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(updates.at(-1)).toMatchObject({ status: "error", error: "local_transfer_failed" });
  });

  it("reprend les octets conservés après coupure, 503 puis erreur réseau", async () => {
    vi.useFakeTimers();
    try {
      const directory = new MemoryDirectory();
      const oneFile = {
        ...snapshot(), file_count: 1, total_size: 4,
        items: [{ id: "file", file_index: 0, relative_path: "file.bin", size: 4 }],
      };
      const updates: RecursiveTransferProgress[] = [];
      let calls = 0;
      const fetcher = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
        calls += 1;
        const headers = new Headers(init?.headers);
        expect(headers.get("X-WOS-Download-Snapshot")).toBe(oneFile.snapshot_id);
        if (calls === 1) {
          let pulls = 0;
          return new Response(new ReadableStream<Uint8Array>({
            pull(stream) {
              if (pulls++ === 0) stream.enqueue(new Uint8Array([1, 2]));
              else stream.error(new TypeError("connection lost"));
            },
          }), { headers: { "X-WOS-Manifest-Version": "3" } });
        }
        expect(headers.get("Range")).toBe("bytes=2-");
        if (calls === 2) return new Response(null, { status: 503, headers: { "Retry-After": "3" } });
        if (calls === 3) throw new TypeError("offline");
        return fileResponse(new Uint8Array([3, 4]), 206, 2, 4);
      });
      const controller = new RecursiveDownloadController({
        torrentRequestId: "request", firstPage: oneFile, directory,
        loadManifestPage: vi.fn(), concurrency: 1, fetcher,
        onProgress: (progress) => updates.push(progress),
      });
      const running = controller.start();
      await vi.advanceTimersByTimeAsync(999);
      expect(fetcher).toHaveBeenCalledTimes(1);
      await vi.advanceTimersByTimeAsync(1);
      expect(fetcher).toHaveBeenCalledTimes(2);
      await vi.advanceTimersByTimeAsync(2_999);
      expect(fetcher).toHaveBeenCalledTimes(2);
      await vi.runAllTimersAsync();
      await running;
      expect(fetcher).toHaveBeenCalledTimes(4);
      expect([...directory.files.get("file.bin")!.content]).toEqual([1, 2, 3, 4]);
      expect(updates.at(-1)).toMatchObject({ status: "completed", downloadedBytes: 4, error: null });
    } finally {
      vi.useRealTimers();
    }
  });

  it("borne la reprise et permet d’annuler immédiatement pendant son attente", async () => {
    vi.useFakeTimers();
    try {
      const firstPage = { ...snapshot(), file_count: 1, total_size: 3, items: [snapshot().items[0]] };
      const updates: RecursiveTransferProgress[] = [];
      const fetcher = vi.fn(async () => new Response(null, { status: 503 }));
      const controller = new RecursiveDownloadController({
        torrentRequestId: "request", firstPage, directory: new MemoryDirectory(),
        loadManifestPage: vi.fn(), concurrency: 1, fetcher,
        onProgress: (progress) => updates.push(progress),
      });
      const running = controller.start();
      await vi.runAllTimersAsync();
      await running;
      expect(fetcher).toHaveBeenCalledTimes(9);
      expect(updates.at(-1)).toMatchObject({ status: "error", error: "download_interrupted" });
      const resumed = controller.resume();
      await vi.advanceTimersByTimeAsync(0);
      controller.cancel();
      await resumed;
      await vi.runAllTimersAsync();
      expect(fetcher).toHaveBeenCalledTimes(10);
      expect(updates.at(-1)).toMatchObject({ status: "cancelled" });
    } finally {
      vi.useRealTimers();
    }
  });

  it("réessaie une page de manifeste indisponible sans changer son snapshot", async () => {
    vi.useFakeTimers();
    try {
      const firstPage = { ...snapshot(), file_count: 3, total_size: 9, limit: 2 };
      const loadManifestPage = vi.fn()
        .mockRejectedValueOnce(new ApiError(503, "unavailable"))
        .mockResolvedValueOnce({
          ...firstPage, offset: 2,
          items: [{ id: "third", file_index: 2, relative_path: "third.bin", size: 3 }],
        });
      const updates: RecursiveTransferProgress[] = [];
      const controller = new RecursiveDownloadController({
        torrentRequestId: "request", firstPage, directory: new MemoryDirectory(),
        loadManifestPage, concurrency: 1,
        fetcher: vi.fn(async () => fileResponse(new Uint8Array([1, 2, 3]))),
        onProgress: (progress) => updates.push(progress),
      });
      const running = controller.start();
      await vi.runAllTimersAsync();
      await running;
      expect(loadManifestPage).toHaveBeenCalledTimes(2);
      expect(loadManifestPage.mock.calls[1].slice(0, 2)).toEqual([2, firstPage.snapshot_id]);
      expect(updates.at(-1)).toMatchObject({ status: "completed", completedFiles: 3 });
    } finally {
      vi.useRealTimers();
    }
  });
  it("diagnostique la File System Access API et mémorise la destination WoS", async () => {
    const directory = new MemoryDirectory();
    const picker = vi.fn(async () => directory);
    const available = { isSecureContext: true, showDirectoryPicker: picker } as unknown as Window;
    const insecure = { isSecureContext: false, showDirectoryPicker: picker } as unknown as Window;
    const unsupported = { isSecureContext: true } as Window;

    expect(recursiveDirectoryDownloadCapability(available)).toBe("available");
    expect(recursiveDirectoryDownloadCapability(insecure)).toBe("insecure");
    expect(recursiveDirectoryDownloadCapability(unsupported)).toBe("unsupported");
    await expect(pickDownloadDirectory(available)).resolves.toBe(directory);
    expect(picker).toHaveBeenCalledWith({
      id: "world-of-seeds-downloads",
      mode: "readwrite",
      startIn: "downloads",
    });
  });

  it("termine les petits fichiers en priorité sans affamer le plus ancien", async () => {
    const directory = new MemoryDirectory();
    const files = [
      { id: "large", file_index: 0, relative_path: "large.bin", size: 100 },
      { id: "small-1", file_index: 1, relative_path: "small-1.bin", size: 1 },
      { id: "small-2", file_index: 2, relative_path: "small-2.bin", size: 2 },
      { id: "small-3", file_index: 3, relative_path: "small-3.bin", size: 3 },
      { id: "small-4", file_index: 4, relative_path: "small-4.bin", size: 4 },
    ];
    const order: string[] = [];
    const controller = new RecursiveDownloadController({
      torrentRequestId: "request",
      firstPage: {
        ...snapshot(),
        file_count: files.length,
        total_size: files.reduce((total, file) => total + file.size, 0),
        items: files,
      },
      directory,
      loadManifestPage: vi.fn(),
      concurrency: 1,
      fetcher: vi.fn(async (input: RequestInfo | URL) => {
        const id = files.find((file) => String(input).includes(`/files/${file.id}/`))?.id;
        if (id === undefined) throw new Error("unexpected download URL");
        order.push(id);
        const size = files.find((file) => file.id === id)?.size ?? 0;
        return fileResponse(new Uint8Array(size));
      }),
      onProgress: vi.fn(),
    });

    await controller.start();

    expect(order).toEqual(["small-1", "small-2", "small-3", "large", "small-4"]);
  });

  it("recrée les sous-dossiers avec une concurrence bornée", async () => {
    const directory = new MemoryDirectory();
    let active = 0;
    let maximum = 0;
    let releaseFetches: (() => void) | null = null;
    const fetchesStarted = new Promise<void>((resolve) => { releaseFetches = resolve; });
    const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      active += 1;
      maximum = Math.max(maximum, active);
      expect(new Headers(init?.headers).get("X-WOS-Download-Snapshot")).toBe("a".repeat(64));
      if (active === 2) releaseFetches!();
      await fetchesStarted;
      active -= 1;
      return fileResponse(new Uint8Array(String(input).includes("first") ? [1, 2, 3] : [4, 5, 6]));
    });
    const updates = vi.fn();
    const controller = new RecursiveDownloadController({
      torrentRequestId: "request",
      firstPage: snapshot(),
      directory,
      loadManifestPage: vi.fn(),
      concurrency: 2,
      fetcher,
      onProgress: updates,
    });

    await controller.start();

    expect(maximum).toBe(2);
    expect(fetcher).toHaveBeenCalledTimes(2);
    const root = directory.directories.get("Show");
    expect([...root?.files.get("a.bin")?.content ?? []]).toEqual([1, 2, 3]);
    expect([...root?.directories.get("sub")?.files.get("b.bin")?.content ?? []]).toEqual([4, 5, 6]);
    expect(updates).toHaveBeenLastCalledWith(expect.objectContaining({
      status: "completed",
      downloadedBytes: 6,
      completedFiles: 2,
      error: null,
    }));
  });

  it("réessaie automatiquement lorsqu’un slot serveur global est occupé", async () => {
    vi.useFakeTimers();
    try {
      const directory = new MemoryDirectory();
      const oneFile = {
        ...snapshot(),
        file_count: 1,
        total_size: 3,
        items: [snapshot().items[0]],
      };
      const fetcher = vi.fn()
        .mockResolvedValueOnce(new Response(null, { status: 429 }))
        .mockResolvedValueOnce(new Response(null, { status: 429 }))
        .mockResolvedValueOnce(fileResponse(new Uint8Array([1, 2, 3])));
      const updates: RecursiveTransferProgress[] = [];
      const controller = new RecursiveDownloadController({
        torrentRequestId: "request",
        firstPage: oneFile,
        directory,
        loadManifestPage: vi.fn(),
        concurrency: 1,
        fetcher,
        onProgress: (progress) => updates.push(progress),
      });

      const running = controller.start();
      await vi.runAllTimersAsync();
      await running;

      expect(fetcher).toHaveBeenCalledTimes(3);
      expect(updates.at(-1)).toMatchObject({
        status: "completed",
        downloadedBytes: 3,
        error: null,
      });
    } finally {
      vi.useRealTimers();
    }
  });

  it("publie la file locale exacte avec actifs et positions d’attente", async () => {
    const items = Array.from({ length: 5 }, (_, index) => ({
      id: `file-${index}`,
      file_index: index,
      relative_path: `Folder/${"very-long-name-".repeat(16)}${index}.bin`,
      size: 1,
    }));
    let releaseDownloads!: () => void;
    let markStarted!: () => void;
    const downloadsPending = new Promise<void>((resolve) => { releaseDownloads = resolve; });
    const started = new Promise<void>((resolve) => { markStarted = resolve; });
    let active = 0;
    const updates: RecursiveTransferProgress[] = [];
    const controller = new RecursiveDownloadController({
      torrentRequestId: "request",
      firstPage: {
        ...snapshot(),
        file_count: items.length,
        total_size: items.length,
        items,
      },
      directory: new MemoryDirectory(),
      loadManifestPage: vi.fn(),
      concurrency: 2,
      fetcher: vi.fn(async () => {
        active += 1;
        if (active === 2) markStarted();
        await downloadsPending;
        active -= 1;
        return fileResponse(new Uint8Array([1]), 200, 0, 1);
      }),
      onProgress: (progress) => updates.push(progress),
    });

    const running = controller.start();
    await started;
    const visible = updates.at(-1)?.queue ?? [];
    expect(visible.filter((item) => item.status === "active")).toHaveLength(2);
    expect(visible.filter((item) => item.status === "waiting").map((item) => item.position))
      .toEqual([1, 2, 3]);
    expect(visible.every((item) => item.relativePath.length > 200)).toBe(true);

    releaseDownloads();
    await running;
    expect(updates.at(-1)).toMatchObject({ status: "completed", completedFiles: 5 });
  });

  it("revérifie la taille locale avant de reprendre avec HTTP Range", async () => {
    const directory = new MemoryDirectory();
    const oneFile = { ...snapshot(), file_count: 1, total_size: 4, items: [
      { id: "file", file_index: 0, relative_path: "file.bin", size: 4 },
    ] };
    let first = true;
    const fetcher = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      const headers = new Headers(init?.headers);
      if (!first) {
        expect(headers.get("Range")).toBe("bytes=1-");
        return fileResponse(new Uint8Array([2, 3, 4]), 206, 1);
      }
      first = false;
      const signal = init?.signal;
      return new Response(new ReadableStream<Uint8Array>({
        start(controller) {
          controller.enqueue(new Uint8Array([1, 2]));
          signal?.addEventListener("abort", () => {
            controller.error(new DOMException("paused", "AbortError"));
          });
        },
      }), { status: 200, headers: { "X-WOS-Manifest-Version": "3" } });
    });
    let pauseAfterChunk: RecursiveDownloadController | null = null;
    let paused = false;
    const progress = vi.fn((update: RecursiveTransferProgress) => {
      if (!paused && update.downloadedBytes === 2 && update.status === "running") {
        paused = true;
        pauseAfterChunk?.pause();
      }
    });
    pauseAfterChunk = new RecursiveDownloadController({
      torrentRequestId: "request",
      firstPage: oneFile,
      directory,
      loadManifestPage: vi.fn(),
      concurrency: 1,
      fetcher,
      onProgress: progress,
    });

    await pauseAfterChunk.start();
    expect(progress.mock.calls.at(-1)?.[0].queue[0]).toMatchObject({
      status: "paused",
      position: null,
    });
    const localFile = directory.files.get("file.bin");
    expect(localFile).toBeDefined();
    localFile!.content = new Uint8Array([1]);
    await pauseAfterChunk.resume();

    expect(fetcher).toHaveBeenCalledTimes(2);
    expect([...directory.files.get("file.bin")?.content ?? []]).toEqual([1, 2, 3, 4]);
    expect(progress).toHaveBeenLastCalledWith(expect.objectContaining({
      status: "completed",
      downloadedBytes: 4,
      completedFiles: 1,
      error: null,
    }));
  });

  it("commence immédiatement et garde deux pages au plus pour 50 000 fichiers", async () => {
    const fileCount = 50_000;
    const pageSize = 500;
    const items = (offset: number) => Array.from(
      { length: Math.min(pageSize, fileCount - offset) },
      (_, index) => ({
        id: `file-${offset + index}`,
        file_index: offset + index,
        relative_path: `bulk/file-${offset + index}.bin`,
        size: 0,
      }),
    );
    let releaseFirstFiles: (() => void) | null = null;
    const firstFilesBlocked = new Promise<void>((resolve) => { releaseFirstFiles = resolve; });
    let localOpens = 0;
    const emptyFile: LocalFileHandle = {
      createWritable: async () => { throw new Error("zero-sized files must not open a writer"); },
    };
    const directory: LocalDirectoryHandle = {
      getDirectoryHandle: async () => directory,
      getFileHandle: async () => {
        localOpens += 1;
        if (localOpens <= 2) await firstFilesBlocked;
        return emptyFile;
      },
    };
    let pageLoads = 0;
    let activePageLoads = 0;
    let maximumPageLoads = 0;
    const loadManifestPage = vi.fn(async (offset: number, snapshotId: string) => {
      expect(snapshotId).toBe("c".repeat(64));
      pageLoads += 1;
      activePageLoads += 1;
      maximumPageLoads = Math.max(maximumPageLoads, activePageLoads);
      await Promise.resolve();
      activePageLoads -= 1;
      return {
        snapshot_id: snapshotId,
        manifest_version: 4,
        file_count: fileCount,
        total_size: 0,
        archive_available: false,
        retention_expires_at: null,
        offset,
        limit: pageSize,
        items: items(offset),
      };
    });
    let lastProgress: RecursiveTransferProgress | null = null;
    const controller = new RecursiveDownloadController({
      torrentRequestId: "large-request",
      firstPage: {
        snapshot_id: "c".repeat(64),
        manifest_version: 4,
        file_count: fileCount,
        total_size: 0,
        archive_available: false,
        retention_expires_at: null,
        offset: 0,
        limit: pageSize,
        items: items(0),
      },
      directory,
      loadManifestPage,
      concurrency: 2,
      fetcher: vi.fn(),
      onProgress: (progress) => { lastProgress = progress; },
    });

    const running = controller.start();
    await vi.waitFor(() => expect(localOpens).toBe(2));
    expect(pageLoads).toBe(1);
    releaseFirstFiles!();
    await running;

    expect(loadManifestPage).toHaveBeenCalledTimes(99);
    expect(maximumPageLoads).toBe(1);
    expect(lastProgress).toMatchObject({
      status: "completed",
      downloadedBytes: 0,
      completedFiles: fileCount,
      error: null,
    });
  }, 20_000);

  it("revient au dernier offset durable après un échec de fermeture", async () => {
    class TransactionalFile implements LocalFileHandle {
      content = new Uint8Array();
      closes = 0;

      async getFile() { return { size: this.closes === 1 ? 3 : this.content.length }; }

      async createWritable(): Promise<WritableFileHandle> {
        const staged: number[] = [];
        return {
          seek: async () => undefined,
          write: async (chunk) => { staged.push(...chunk); },
          close: async () => {
            this.closes += 1;
            if (this.closes === 1) throw new DOMException("device removed", "NotFoundError");
            this.content = new Uint8Array(staged);
          },
        };
      }
    }
    const file = new TransactionalFile();
    const directory: LocalDirectoryHandle = {
      getDirectoryHandle: async () => directory,
      getFileHandle: async () => file,
    };
    const fetcher = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      expect(new Headers(init?.headers).get("Range")).toBeNull();
      return fileResponse(new Uint8Array([1, 2, 3]));
    });
    const updates: RecursiveTransferProgress[] = [];
    const controller = new RecursiveDownloadController({
      torrentRequestId: "request",
      firstPage: { ...snapshot(), file_count: 1, total_size: 3, items: [snapshot().items[0]] },
      directory,
      loadManifestPage: vi.fn(),
      concurrency: 1,
      fetcher,
      onProgress: (progress) => updates.push(progress),
    });

    await controller.start();
    expect(updates.at(-1)).toMatchObject({ status: "error", downloadedBytes: 0 });
    await controller.resume();

    expect(fetcher).toHaveBeenCalledTimes(2);
    expect([...file.content]).toEqual([1, 2, 3]);
    expect(updates.at(-1)).toMatchObject({
      status: "completed",
      downloadedBytes: 3,
      completedFiles: 1,
      error: null,
    });
  });

  it("ne valide aucun octet lorsqu’une écriture échoue faute d’espace", async () => {
    let failWrite = true;
    const file: LocalFileHandle = {
      getFile: async () => ({ size: 0 }),
      createWritable: async () => ({
        seek: async () => undefined,
        write: async () => {
          if (failWrite) throw new DOMException("disk full", "QuotaExceededError");
        },
        close: async () => undefined,
      }),
    };
    const directory: LocalDirectoryHandle = {
      getDirectoryHandle: async () => directory,
      getFileHandle: async () => file,
    };
    const updates: RecursiveTransferProgress[] = [];
    const controller = new RecursiveDownloadController({
      torrentRequestId: "request",
      firstPage: { ...snapshot(), file_count: 1, total_size: 3, items: [snapshot().items[0]] },
      directory,
      loadManifestPage: vi.fn(),
      concurrency: 1,
      fetcher: vi.fn(async () => fileResponse(new Uint8Array([1, 2, 3]))),
      onProgress: (progress) => updates.push(progress),
    });

    await controller.start();
    expect(updates.at(-1)).toMatchObject({
      status: "error",
      downloadedBytes: 0,
      completedFiles: 0,
      error: "local_disk_full",
    });
    expect(updates.at(-1)?.queue[0]).toMatchObject({ status: "error", position: null });
    failWrite = false;
    await controller.resume();
    expect(updates.at(-1)).toMatchObject({ status: "completed", downloadedBytes: 3 });
  });

  it("échoue fermé si le snapshot d’une page suivante change", async () => {
    const firstPage = { ...snapshot(), file_count: 3, total_size: 9, limit: 2 };
    const updates: RecursiveTransferProgress[] = [];
    const controller = new RecursiveDownloadController({
      torrentRequestId: "request",
      firstPage,
      directory: new MemoryDirectory(),
      loadManifestPage: vi.fn(async (offset) => ({
        ...firstPage,
        snapshot_id: "d".repeat(64),
        offset,
        items: [{ id: "third", file_index: 2, relative_path: "third.bin", size: 3 }],
      })),
      concurrency: 1,
      fetcher: vi.fn(async () => fileResponse(new Uint8Array([1, 2, 3]))),
      onProgress: (progress) => updates.push(progress),
    });

    await controller.start();

    expect(updates.at(-1)).toMatchObject({
      status: "error",
      error: "manifest_changed",
    });
  });

  it("refuse une réponse Range qui ne commence pas à l’offset local", async () => {
    const directory = new MemoryDirectory();
    const oneFile = {
      ...snapshot(),
      file_count: 1,
      total_size: 4,
      items: [{ id: "file", file_index: 0, relative_path: "file.bin", size: 4 }],
    };
    let first = true;
    let paused = false;
    let controller: RecursiveDownloadController;
    const updates: RecursiveTransferProgress[] = [];
    const fetcher = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      if (!first) {
        expect(new Headers(init?.headers).get("Range")).toBe("bytes=2-");
        return new Response(new Uint8Array([3, 4]).buffer, {
          status: 206,
          headers: {
            "Content-Range": "bytes 1-2/4",
            "X-WOS-Manifest-Version": "3",
          },
        });
      }
      first = false;
      const signal = init?.signal;
      return new Response(new ReadableStream<Uint8Array>({
        start(stream) {
          stream.enqueue(new Uint8Array([1, 2]));
          signal?.addEventListener("abort", () => stream.error(new DOMException("paused", "AbortError")));
        },
      }), { headers: { "X-WOS-Manifest-Version": "3" } });
    });
    controller = new RecursiveDownloadController({
      torrentRequestId: "request",
      firstPage: oneFile,
      directory,
      loadManifestPage: vi.fn(),
      concurrency: 1,
      fetcher,
      onProgress: (progress) => {
        updates.push(progress);
        if (!paused && progress.status === "running" && progress.downloadedBytes === 2) {
          paused = true;
          controller.pause();
        }
      },
    });

    await controller.start();
    await controller.resume();

    expect(updates.at(-1)).toMatchObject({
      status: "error",
      downloadedBytes: 2,
      error: "manifest_changed",
    });
  });

  it("annule les streams fichier et manifeste sans valider d’octets", async () => {
    const firstPage = { ...snapshot(), file_count: 3, total_size: 9, limit: 2 };
    let manifestAborted = false;
    let fileAborted = false;
    let markFileStarted: (() => void) | null = null;
    const fileStarted = new Promise<void>((resolve) => { markFileStarted = resolve; });
    const updates: RecursiveTransferProgress[] = [];
    const fetcher = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      markFileStarted!();
      return new Response(new ReadableStream<Uint8Array>({
        start(stream) {
          init?.signal?.addEventListener("abort", () => {
            fileAborted = true;
            stream.error(new DOMException("cancelled", "AbortError"));
          });
        },
      }), { headers: { "X-WOS-Manifest-Version": "3" } });
    });
    const controller = new RecursiveDownloadController({
      torrentRequestId: "request",
      firstPage,
      directory: new MemoryDirectory(),
      loadManifestPage: (_offset, _snapshotId, signal) => new Promise((_resolve, reject) => {
        signal.addEventListener("abort", () => {
          manifestAborted = true;
          reject(new DOMException("cancelled", "AbortError"));
        });
      }),
      concurrency: 1,
      fetcher,
      onProgress: (progress) => updates.push(progress),
    });

    const running = controller.start();
    await fileStarted;
    controller.cancel();
    await running;

    expect(manifestAborted).toBe(true);
    expect(fileAborted).toBe(true);
    expect(updates.at(-1)).toMatchObject({
      status: "cancelled",
      downloadedBytes: 0,
      completedFiles: 0,
      error: null,
    });
    expect(updates.at(-1)?.queue.every((item) => item.status === "cancelled")).toBe(true);
  });
});
