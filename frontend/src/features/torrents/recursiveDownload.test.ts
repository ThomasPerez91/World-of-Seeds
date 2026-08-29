import { describe, expect, it, vi } from "vitest";

import type { TorrentDownloadSnapshotV2 } from "../../api/client";
import {
  type LocalDirectoryHandle,
  type LocalFileHandle,
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
    expect(updates).toHaveBeenLastCalledWith({
      status: "completed",
      downloadedBytes: 6,
      completedFiles: 2,
      error: null,
    });
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
    const localFile = directory.files.get("file.bin");
    expect(localFile).toBeDefined();
    localFile!.content = new Uint8Array([1]);
    await pauseAfterChunk.resume();

    expect(fetcher).toHaveBeenCalledTimes(2);
    expect([...directory.files.get("file.bin")?.content ?? []]).toEqual([1, 2, 3, 4]);
    expect(progress).toHaveBeenLastCalledWith({
      status: "completed",
      downloadedBytes: 4,
      completedFiles: 1,
      error: null,
    });
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
    expect(lastProgress).toEqual({
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
    expect(updates.at(-1)).toEqual({
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
    expect(updates.at(-1)).toEqual({
      status: "error",
      downloadedBytes: 0,
      completedFiles: 0,
      error: "local_disk_full",
    });
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
    expect(updates.at(-1)).toEqual({
      status: "cancelled",
      downloadedBytes: 0,
      completedFiles: 0,
      error: null,
    });
  });
});
