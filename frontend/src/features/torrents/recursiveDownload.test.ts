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

function fileResponse(content: Uint8Array, status = 200): Response {
  const headers: Record<string, string> = { "X-WOS-Manifest-Version": "3" };
  if (status === 206) headers["Content-Range"] = "bytes 2-3/4";
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
    const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      active += 1;
      maximum = Math.max(maximum, active);
      expect(new Headers(init?.headers).get("X-WOS-Download-Snapshot")).toBe("a".repeat(64));
      await Promise.resolve();
      active -= 1;
      return fileResponse(new Uint8Array(String(input).includes("first") ? [1, 2, 3] : [4, 5, 6]));
    });
    const updates = vi.fn();
    const controller = new RecursiveDownloadController({
      torrentRequestId: "request",
      snapshot: snapshot(),
      directory,
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

  it("reprend un fichier interrompu avec HTTP Range", async () => {
    const directory = new MemoryDirectory();
    const oneFile = { ...snapshot(), file_count: 1, total_size: 4, items: [
      { id: "file", file_index: 0, relative_path: "file.bin", size: 4 },
    ] };
    let first = true;
    const fetcher = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      const headers = new Headers(init?.headers);
      if (!first) {
        expect(headers.get("Range")).toBe("bytes=2-");
        return fileResponse(new Uint8Array([3, 4]), 206);
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
      snapshot: oneFile,
      directory,
      concurrency: 1,
      fetcher,
      onProgress: progress,
    });

    await pauseAfterChunk.start();
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
});
