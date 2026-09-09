import type {
  TorrentDownloadFileV2,
  TorrentDownloadManifestPageV2,
} from "../../api/client";
import {
  DEFAULT_RECURSIVE_DOWNLOAD_CONCURRENCY,
  RecursiveDownloadController,
  type LocalDirectoryHandle,
  type LocalFileHandle,
  type LocalTransferQueueItem,
  type RecursiveTransferErrorCode,
  type RecursiveTransferProgress,
} from "./recursiveDownload";

export type BrowserDownloadJobStatus =
  | "queued"
  | "running"
  | "paused"
  | "cancelled"
  | "completed"
  | "error";

export interface BrowserDownloadJobSnapshot {
  id: string;
  torrentId: string;
  kind: "file" | "folder";
  name: string;
  status: BrowserDownloadJobStatus;
  downloadedBytes: number;
  totalBytes: number;
  completedFiles: number;
  fileCount: number;
  queuePosition: number | null;
  error: RecursiveTransferErrorCode | null;
  queue: readonly LocalTransferQueueItem[];
}

export interface BrowserDownloadManagerSnapshot {
  activeStreams: number;
  maxConcurrentStreams: number;
  waitingJobs: number;
  jobs: readonly BrowserDownloadJobSnapshot[];
}

export interface BrowserDownloadPolicy {
  max_concurrent_streams: number;
}

export class DownloadPolicyRequestError extends Error {
  constructor(readonly status: number) {
    super("download_policy_request_failed");
  }
}

interface DirectoryPickerWindow extends Window {
  showSaveFilePicker?: (options: {
    suggestedName: string;
    types?: Array<{ description: string; accept: Record<string, string[]> }>;
  }) => Promise<LocalFileHandle>;
}

interface ManagedJob {
  controller: RecursiveDownloadController;
  snapshot: BrowserDownloadJobSnapshot;
}

interface EnqueueFolderOptions {
  torrentId: string;
  name: string;
  snapshot: TorrentDownloadManifestPageV2;
  directory: LocalDirectoryHandle;
  loadManifestPage: (
    offset: number,
    snapshotId: string,
    signal: AbortSignal,
  ) => Promise<TorrentDownloadManifestPageV2>;
}

interface EnqueueFileOptions {
  torrentId: string;
  name: string;
  snapshot: TorrentDownloadManifestPageV2;
  file: TorrentDownloadFileV2;
  target: LocalFileHandle;
}

interface PermitWaiter {
  resolve: (release: () => void) => void;
  reject: (error: DOMException) => void;
  signal: AbortSignal;
  onAbort: () => void;
}

class DownloadPermitPool {
  private limit: number;
  private active = 0;
  private readonly waiters: PermitWaiter[] = [];

  constructor(limit: number, private readonly onChange: () => void) {
    this.limit = limit;
  }

  get activeCount(): number {
    return this.active;
  }

  setLimit(limit: number): void {
    this.limit = limit;
    this.drain();
    this.onChange();
  }

  acquire(signal: AbortSignal): Promise<() => void> {
    if (signal.aborted) return Promise.reject(new DOMException("aborted", "AbortError"));
    return new Promise((resolve, reject) => {
      const waiter: PermitWaiter = {
        resolve,
        reject,
        signal,
        onAbort: () => undefined,
      };
      waiter.onAbort = () => {
        const index = this.waiters.indexOf(waiter);
        if (index >= 0) this.waiters.splice(index, 1);
        reject(new DOMException("aborted", "AbortError"));
        this.onChange();
      };
      signal.addEventListener("abort", waiter.onAbort, { once: true });
      this.waiters.push(waiter);
      this.drain();
    });
  }

  private drain(): void {
    while (this.active < this.limit && this.waiters.length > 0) {
      const waiter = this.waiters.shift();
      if (waiter === undefined) break;
      waiter.signal.removeEventListener("abort", waiter.onAbort);
      if (waiter.signal.aborted) {
        waiter.reject(new DOMException("aborted", "AbortError"));
        continue;
      }
      this.active += 1;
      let released = false;
      waiter.resolve(() => {
        if (released) return;
        released = true;
        this.active = Math.max(0, this.active - 1);
        this.drain();
        this.onChange();
      });
      this.onChange();
    }
  }
}

export async function loadBrowserDownloadPolicy(signal?: AbortSignal): Promise<BrowserDownloadPolicy> {
  const response = await fetch("/api/v2/downloads/policy", {
    credentials: "same-origin",
    headers: { Accept: "application/json" },
    signal,
  });
  if (!response.ok) throw new DownloadPolicyRequestError(response.status);
  const policy = (await response.json()) as Partial<BrowserDownloadPolicy>;
  if (
    !Number.isInteger(policy.max_concurrent_streams)
    || (policy.max_concurrent_streams ?? 0) < 1
    || (policy.max_concurrent_streams ?? 0) > 20
  ) {
    throw new Error("download_policy_invalid");
  }
  return policy as BrowserDownloadPolicy;
}

export function supportsManagedFileDownload(target: Window = window): boolean {
  return typeof (target as DirectoryPickerWindow).showSaveFilePicker === "function";
}

export async function pickManagedDownloadFile(
  suggestedName: string,
  target: Window = window,
): Promise<LocalFileHandle> {
  const picker = (target as DirectoryPickerWindow).showSaveFilePicker;
  if (picker === undefined) throw new Error("save_file_picker_unavailable");
  return picker.call(target, { suggestedName });
}

class SingleTargetDirectory implements LocalDirectoryHandle {
  constructor(private readonly target: LocalFileHandle) {}

  async getDirectoryHandle(): Promise<LocalDirectoryHandle> {
    return this;
  }

  async getFileHandle(): Promise<LocalFileHandle> {
    return this.target;
  }
}

export class BrowserDownloadManager {
  private readonly jobs = new Map<string, ManagedJob>();
  private readonly order: string[] = [];
  private maxConcurrentStreams: number;
  private readonly permits: DownloadPermitPool;

  constructor(
    maxConcurrentStreams: number,
    private readonly onChange: (snapshot: BrowserDownloadManagerSnapshot) => void,
  ) {
    this.assertConcurrency(maxConcurrentStreams);
    this.maxConcurrentStreams = maxConcurrentStreams;
    this.permits = new DownloadPermitPool(maxConcurrentStreams, () => this.emit());
  }

  setMaxConcurrentStreams(value: number): void {
    this.assertConcurrency(value);
    this.maxConcurrentStreams = value;
    this.permits.setLimit(value);
    this.emit();
  }

  enqueueFolder(options: EnqueueFolderOptions): string {
    return this.enqueue({
      torrentId: options.torrentId,
      kind: "folder",
      name: options.name,
      firstPage: options.snapshot,
      directory: options.directory,
      loadManifestPage: options.loadManifestPage,
    });
  }

  enqueueFile(options: EnqueueFileOptions): string {
    const syntheticSnapshot: TorrentDownloadManifestPageV2 = {
      ...options.snapshot,
      file_count: 1,
      total_size: options.file.size,
      offset: 0,
      limit: 1,
      items: [options.file],
    };
    return this.enqueue({
      torrentId: options.torrentId,
      kind: "file",
      name: options.name,
      firstPage: syntheticSnapshot,
      directory: new SingleTargetDirectory(options.target),
      loadManifestPage: async () => {
        throw new Error("single_file_manifest_page_unexpected");
      },
    });
  }

  pause(jobId: string): void {
    const job = this.jobs.get(jobId);
    if (job === undefined || !["queued", "running"].includes(job.snapshot.status)) return;
    job.controller.pause();
  }

  resume(jobId: string): void {
    const job = this.jobs.get(jobId);
    if (job === undefined || !["paused", "error"].includes(job.snapshot.status)) return;
    void job.controller.resume();
  }

  cancel(jobId: string): void {
    const job = this.jobs.get(jobId);
    if (job === undefined) return;
    job.controller.cancel();
  }

  remove(jobId: string): void {
    const job = this.jobs.get(jobId);
    if (job === undefined || !["completed", "cancelled"].includes(job.snapshot.status)) return;
    this.jobs.delete(jobId);
    const index = this.order.indexOf(jobId);
    if (index >= 0) this.order.splice(index, 1);
    this.emit();
  }

  dispose(): void {
    for (const job of this.jobs.values()) job.controller.cancel();
    this.jobs.clear();
    this.order.splice(0);
  }

  private enqueue(options: {
    torrentId: string;
    kind: "file" | "folder";
    name: string;
    firstPage: TorrentDownloadManifestPageV2;
    directory: LocalDirectoryHandle;
    loadManifestPage: (
      offset: number,
      snapshotId: string,
      signal: AbortSignal,
    ) => Promise<TorrentDownloadManifestPageV2>;
  }): string {
    const id = crypto.randomUUID();
    const initial: BrowserDownloadJobSnapshot = {
      id,
      torrentId: options.torrentId,
      kind: options.kind,
      name: options.name,
      status: "queued",
      downloadedBytes: 0,
      totalBytes: options.firstPage.total_size,
      completedFiles: 0,
      fileCount: options.firstPage.file_count,
      queuePosition: null,
      error: null,
      queue: [],
    };
    const controller = new RecursiveDownloadController({
      torrentRequestId: options.torrentId,
      firstPage: options.firstPage,
      directory: options.directory,
      loadManifestPage: options.loadManifestPage,
      concurrency: DEFAULT_RECURSIVE_DOWNLOAD_CONCURRENCY,
      acquirePermit: (signal) => this.permits.acquire(signal),
      onProgress: (progress) => this.onProgress(id, progress),
    });
    this.jobs.set(id, { controller, snapshot: initial });
    this.order.push(id);
    this.emit();
    void controller.start();
    return id;
  }

  private onProgress(jobId: string, progress: RecursiveTransferProgress): void {
    const job = this.jobs.get(jobId);
    if (job === undefined) return;
    const hasActiveFile = progress.queue.some((item) => item.status === "active");
    const status: BrowserDownloadJobStatus = progress.status === "running" && !hasActiveFile
      ? "queued"
      : progress.status;
    job.snapshot = {
      ...job.snapshot,
      status,
      downloadedBytes: progress.downloadedBytes,
      completedFiles: progress.completedFiles,
      error: progress.error,
      queue: progress.queue,
      queuePosition: null,
    };
    this.emit();
  }

  private emit(): void {
    let position = 0;
    const jobs: BrowserDownloadJobSnapshot[] = [];
    for (const id of this.order) {
      const job = this.jobs.get(id);
      if (job === undefined) continue;
      if (job.snapshot.status === "queued") {
        position += 1;
        jobs.push({ ...job.snapshot, queuePosition: position });
      } else {
        jobs.push({ ...job.snapshot, queuePosition: null });
      }
    }
    this.onChange({
      activeStreams: this.permits.activeCount,
      maxConcurrentStreams: this.maxConcurrentStreams,
      waitingJobs: jobs.filter((job) => job.status === "queued").length,
      jobs,
    });
  }

  private assertConcurrency(value: number): void {
    if (!Number.isInteger(value) || value < 1 || value > 20) {
      throw new Error("download_manager_concurrency_invalid");
    }
  }
}
