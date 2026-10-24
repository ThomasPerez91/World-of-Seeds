import {
  ApiError,
  type TorrentDownloadFileV2,
  type TorrentDownloadManifestPageV2,
} from "../../api/client";

const MAX_MANIFEST_PAGE_SIZE = 500;
const MAX_BUFFERED_MANIFEST_PAGES = 2;
const MAX_VISIBLE_QUEUE_ITEMS = 8;
const MAX_DOWNLOAD_BUSY_RETRIES = 6;
const PROGRESS_EMIT_INTERVAL_MS = 250;
const MAX_RECOVERY_RETRIES = 8;
const SIZE_PRIORITY_BURST = 3;
export const DEFAULT_RECURSIVE_DOWNLOAD_CONCURRENCY = 2;

function waitForDownloadSlot(attempt: number, signal: AbortSignal): Promise<void> {
  const delayMs = Math.min(2_000, 100 * (2 ** attempt));
  return waitForRetry(delayMs, signal);
}

function waitForRetry(delayMs: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal.aborted) {
      reject(new DOMException("transfer stopped", "AbortError"));
      return;
    }
    const onAbort = () => {
      clearTimeout(timer);
      reject(new DOMException("transfer stopped", "AbortError"));
    };
    const timer = setTimeout(() => {
      signal.removeEventListener("abort", onAbort);
      resolve();
    }, delayMs);
    signal.addEventListener("abort", onAbort, { once: true });
  });
}

export interface WritableFileHandle {
  write(data: Uint8Array): Promise<void>;
  seek(position: number): Promise<void>;
  close(): Promise<void>;
}

export interface LocalFileHandle {
  createWritable(options?: { keepExistingData?: boolean }): Promise<WritableFileHandle>;
  getFile?(): Promise<{ readonly size: number }>;
}

export interface LocalDirectoryHandle {
  getDirectoryHandle(name: string, options: { create: true }): Promise<LocalDirectoryHandle>;
  getFileHandle(name: string, options: { create: true }): Promise<LocalFileHandle>;
}

interface DirectoryPickerWindow extends Window {
  showDirectoryPicker?: (options: {
    id?: string;
    mode: "readwrite";
    startIn?: "downloads";
  }) => Promise<LocalDirectoryHandle>;
}

export type RecursiveDirectoryDownloadCapability = "available" | "insecure" | "unsupported";

export type RecursiveTransferStatus =
  | "running"
  | "paused"
  | "cancelled"
  | "completed"
  | "error";

export type RecursiveTransferErrorCode =
  | "manifest_incomplete"
  | "manifest_changed"
  | "received_file_too_large"
  | "received_file_incomplete"
  | "local_file_size_invalid"
  | "manifest_path_invalid"
  | "local_file_exists"
  | "local_disk_full"
  | "local_write_denied"
  | "local_destination_missing"
  | "download_busy"
  | "session_expired"
  | "download_unavailable"
  | "download_interrupted"
  | "local_transfer_failed";

export interface RecursiveTransferProgress {
  status: RecursiveTransferStatus;
  downloadedBytes: number;
  completedFiles: number;
  error: RecursiveTransferErrorCode | null;
  queue: readonly LocalTransferQueueItem[];
}

export type LocalTransferQueueItemStatus =
  | "active"
  | "waiting"
  | "paused"
  | "completed"
  | "error"
  | "cancelled";

export interface LocalTransferQueueItem {
  id: string;
  relativePath: string;
  status: LocalTransferQueueItemStatus;
  position: number | null;
}

type ManifestPageLoader = (
  offset: number,
  snapshotId: string,
  signal: AbortSignal,
) => Promise<TorrentDownloadManifestPageV2>;

interface RecursiveDownloadOptions {
  torrentRequestId: string;
  firstPage: TorrentDownloadManifestPageV2;
  directory: LocalDirectoryHandle;
  loadManifestPage: ManifestPageLoader;
  concurrency?: number;
  fetcher?: typeof fetch;
  /**
   * A destination the user already confirmed (the browser Save dialog asks before replacing a
   * file) may be overwritten. Without it, a non-empty file this transfer did not create stops
   * the transfer instead of being truncated.
   */
  replaceExistingFiles?: boolean;
  onProgress: (progress: RecursiveTransferProgress) => void;
}

class TransferFailure extends Error {
  constructor(readonly code: RecursiveTransferErrorCode) {
    super(code);
  }
}

class InterruptedTransfer extends TransferFailure {
  constructor(readonly retryAfterMs = 0) {
    super("download_interrupted");
  }
}

const WINDOWS_RESERVED_NAME = /^(con|prn|aux|nul|com[1-9]|lpt[1-9])(\..*)?$/i;
const MAX_LOCAL_NAME_BYTES = 255;

function utf8Length(value: string): number {
  return new TextEncoder().encode(value).length;
}

function truncateUtf8(value: string, maxBytes: number): string {
  let result = "";
  let bytes = 0;
  for (const character of value) {
    const size = utf8Length(character);
    if (bytes + size > maxBytes) break;
    result += character;
    bytes += size;
  }
  return result;
}

/**
 * Key under which two names are the same entry on a local file system: NTFS and the default
 * APFS volume ignore case, APFS and HFS+ ignore Unicode normalization. Upper-casing first folds
 * the letters whose lower-case forms differ although their upper-case form is shared.
 */
export function localNameKey(name: string): string {
  return name.normalize("NFC").toUpperCase().toLowerCase();
}

/**
 * A torrent name valid on Linux can be refused by a local file system (`:` in a title, a
 * reserved Windows name, a trailing dot, a very long name). Only such names are rewritten, and
 * always the same way, so a resumed transfer reopens the same local file. `disambiguate` forces
 * the suffix for a name that would otherwise share its entry with a sibling.
 */
export async function localFileName(
  component: string,
  options: { disambiguate?: boolean } = {},
): Promise<string> {
  // eslint-disable-next-line no-control-regex
  let name = component.replace(/[<>:"|?*\u0000-\u001f]/g, "_");
  name = name.replace(/[. ]+$/, (trailing) => "_".repeat(trailing.length));
  if (WINDOWS_RESERVED_NAME.test(name)) name = `_${name}`;
  // Reserve the suffix namespace too: a literal mapped-looking name must not alias
  // an escaped name, whatever its case or Unicode form. Hash the original component,
  // including for directories/resume.
  if (
    options.disambiguate === true
    || name !== component
    || utf8Length(name) > MAX_LOCAL_NAME_BYTES
    || localNameKey(name).includes("~wos-")
  ) {
    const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(component));
    const hash = Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0"))
      .join("").slice(0, 32);
    const suffix = `~wos-${hash}`;
    const dot = name.lastIndexOf(".");
    const extension = dot > 0 && name.length - dot <= 16 ? name.slice(dot) : "";
    const base = name.slice(0, name.length - extension.length);
    name = truncateUtf8(base, MAX_LOCAL_NAME_BYTES - utf8Length(suffix + extension))
      + suffix + extension;
  }
  return name;
}

function isNetworkFailure(error: unknown): boolean {
  return error instanceof TypeError
    || (error instanceof DOMException && error.name === "NetworkError");
}

function recoveryDelay(attempt: number, retryAfterMs = 0): number {
  return Math.min(15_000, Math.max(1_000 * (2 ** attempt), retryAfterMs));
}

export function recursiveDirectoryDownloadCapability(
  target: Window = window,
): RecursiveDirectoryDownloadCapability {
  if (target.isSecureContext === false) return "insecure";
  if (typeof (target as DirectoryPickerWindow).showDirectoryPicker !== "function") {
    return "unsupported";
  }
  return "available";
}

export function supportsRecursiveDirectoryDownload(target: Window = window): boolean {
  return recursiveDirectoryDownloadCapability(target) === "available";
}

export function pickDownloadDirectory(target: Window = window): Promise<LocalDirectoryHandle> {
  const picker = (target as DirectoryPickerWindow).showDirectoryPicker;
  if (picker === undefined) throw new Error("directory_picker_unavailable");
  return picker.call(target, { id: "world-of-seeds-downloads", mode: "readwrite", startIn: "downloads" });
}

export class RecursiveDownloadController {
  private readonly torrentRequestId: string;
  private readonly snapshot: TorrentDownloadManifestPageV2;
  private readonly directory: LocalDirectoryHandle;
  private readonly loadManifestPage: ManifestPageLoader;
  private readonly concurrency: number;
  private readonly fetcher: typeof fetch;
  private readonly onProgress: (progress: RecursiveTransferProgress) => void;
  private readonly replaceExistingFiles: boolean;
  private readonly offsets = new Map<string, number>();
  // Local names are decided once per transfer: a sibling whose name folds to an entry already
  // taken in the same directory gets the suffix, and every retry or resume reopens that file.
  // Only the fold keys are kept per directory and the per-file state is dropped at completion,
  // so the memory follows the files still in flight plus one short key per file seen.
  private readonly directoryNames = new Map<string, Promise<string>>();
  private readonly fileNames = new Map<string, Promise<string>>();
  private readonly claimedNames = new Map<string, Set<string>>();
  private readonly ownedFiles = new Set<string>();
  private readonly activeRequests = new Set<AbortController>();
  private readonly pendingFiles: TorrentDownloadFileV2[];
  private readonly activeFiles = new Map<string, TorrentDownloadFileV2>();
  private readonly recentlyCompleted: TorrentDownloadFileV2[] = [];
  private failedFileId: string | null = null;
  private readonly maxBufferedFiles: number;
  private status: RecursiveTransferStatus = "paused";
  private error: RecursiveTransferErrorCode | null = null;
  private downloadedBytes = 0;
  private lastProgressEmit = 0;
  private progressTimer: ReturnType<typeof setTimeout> | null = null;
  private completedFiles = 0;
  private nextManifestOffset: number;
  private manifestComplete: boolean;
  private pageLoading: Promise<void> | null = null;
  private pageError: unknown = null;
  private running: Promise<void> | null = null;
  private sizePrioritySelections = 0;

  constructor(options: RecursiveDownloadOptions) {
    const concurrency = options.concurrency ?? DEFAULT_RECURSIVE_DOWNLOAD_CONCURRENCY;
    if (!Number.isInteger(concurrency) || concurrency < 1 || concurrency > 4) {
      throw new Error("download_concurrency_invalid");
    }
    if (
      options.firstPage.offset !== 0 ||
      !Number.isInteger(options.firstPage.limit) ||
      options.firstPage.limit < 1 ||
      options.firstPage.limit > MAX_MANIFEST_PAGE_SIZE ||
      options.firstPage.items.length > options.firstPage.limit ||
      options.firstPage.items.length > options.firstPage.file_count ||
      (options.firstPage.items.length === 0 && options.firstPage.file_count > 0)
    ) {
      throw new TransferFailure("manifest_incomplete");
    }
    this.torrentRequestId = options.torrentRequestId;
    this.snapshot = options.firstPage;
    this.directory = options.directory;
    this.loadManifestPage = options.loadManifestPage;
    this.concurrency = concurrency;
    this.fetcher = options.fetcher ?? fetch;
    this.replaceExistingFiles = options.replaceExistingFiles ?? false;
    this.onProgress = options.onProgress;
    this.pendingFiles = [...options.firstPage.items];
    this.maxBufferedFiles = options.firstPage.limit * MAX_BUFFERED_MANIFEST_PAGES;
    this.nextManifestOffset = options.firstPage.items.length;
    this.manifestComplete = this.nextManifestOffset === options.firstPage.file_count;
  }

  start(): Promise<void> {
    if (this.running !== null) return this.running;
    if (this.status === "cancelled" || this.status === "completed") return Promise.resolve();
    this.status = "running";
    this.error = null;
    this.pageError = null;
    this.emit();
    this.running = this.run().finally(() => {
      this.running = null;
    });
    return this.running;
  }

  pause(): void {
    if (this.status !== "running") return;
    this.status = "paused";
    this.abortActive();
    this.emit();
  }

  async resume(): Promise<void> {
    if (this.running !== null) await this.running;
    return this.start();
  }

  cancel(): void {
    if (this.status === "completed" || this.status === "cancelled") return;
    this.status = "cancelled";
    this.error = null;
    this.abortActive();
    this.emit();
  }

  private async run(): Promise<void> {
    this.startManifestPrefetch();
    const worker = async (): Promise<void> => {
      while (this.status === "running") {
        const file = await this.takeNextFile();
        if (file === null) return;
        if (this.failedFileId === file.id) this.failedFileId = null;
        this.activeFiles.set(file.id, file);
        this.emit();
        try {
          await this.downloadFile(file);
          this.completedFiles += 1;
          this.offsets.delete(file.id);
          this.fileNames.delete(file.id);
          this.ownedFiles.delete(file.id);
          this.recentlyCompleted.unshift(file);
          this.recentlyCompleted.splice(2);
        } catch (error) {
          this.pendingFiles.unshift(file);
          if (error instanceof DOMException && error.name === "AbortError") return;
          if (this.status !== "running") return;
          this.failedFileId = file.id;
          this.fail(error);
          return;
        } finally {
          this.activeFiles.delete(file.id);
          this.emit();
        }
      }
    };
    await Promise.all(Array.from({ length: this.concurrency }, worker));
    if (this.status === "running" && this.completedFiles === this.snapshot.file_count) {
      this.status = "completed";
      this.emit();
    }
  }

  private async takeNextFile(): Promise<TorrentDownloadFileV2 | null> {
    while (this.status === "running") {
      if (this.pageError !== null) throw this.pageError;
      const file = this.takePrioritizedPendingFile();
      if (file !== undefined) {
        this.startManifestPrefetch();
        return file;
      }
      if (this.manifestComplete) return null;
      const loading = this.startManifestPrefetch(true);
      if (loading !== null) await loading;
    }
    return null;
  }

  private takePrioritizedPendingFile(): TorrentDownloadFileV2 | undefined {
    if (this.pendingFiles.length === 0) return undefined;
    if (this.sizePrioritySelections >= SIZE_PRIORITY_BURST) {
      this.sizePrioritySelections = 0;
      return this.pendingFiles.shift();
    }
    let selectedIndex = 0;
    for (let index = 1; index < this.pendingFiles.length; index += 1) {
      const candidate = this.pendingFiles[index];
      const selected = this.pendingFiles[selectedIndex];
      if (
        candidate.size < selected.size
        || (candidate.size === selected.size && candidate.file_index < selected.file_index)
      ) {
        selectedIndex = index;
      }
    }
    this.sizePrioritySelections += 1;
    return this.pendingFiles.splice(selectedIndex, 1)[0];
  }

  private startManifestPrefetch(force = false): Promise<void> | null {
    if (this.pageLoading !== null) return this.pageLoading;
    if (this.status !== "running" || this.manifestComplete) return null;
    if (!force && this.pendingFiles.length > this.maxBufferedFiles - this.snapshot.limit) {
      return null;
    }
    const expectedOffset = this.nextManifestOffset;
    const controller = new AbortController();
    this.activeRequests.add(controller);
    let loading: Promise<void>;
    loading = this.loadManifestWithRecovery(
      expectedOffset,
      this.snapshot.snapshot_id,
      controller.signal,
    ).then((page) => {
      if (this.status !== "running") return;
      this.validateManifestPage(page, expectedOffset);
      this.pendingFiles.push(...page.items);
      this.nextManifestOffset += page.items.length;
      this.manifestComplete = this.nextManifestOffset === this.snapshot.file_count;
    }).catch((error: unknown) => {
      if (error instanceof DOMException && error.name === "AbortError" && this.status !== "running") {
        return;
      }
      this.pageError = error;
      this.fail(error);
    }).finally(() => {
      this.activeRequests.delete(controller);
      if (this.pageLoading === loading) this.pageLoading = null;
    });
    this.pageLoading = loading;
    return loading;
  }

  private async loadManifestWithRecovery(
    offset: number,
    snapshotId: string,
    signal: AbortSignal,
  ): Promise<TorrentDownloadManifestPageV2> {
    for (let attempt = 0; ; attempt += 1) {
      try {
        return await this.loadManifestPage(offset, snapshotId, signal);
      } catch (error) {
        const transient = isNetworkFailure(error) || (error instanceof ApiError && error.status >= 500);
        if (!transient || attempt >= MAX_RECOVERY_RETRIES || signal.aborted) throw error;
        await waitForRetry(recoveryDelay(attempt), signal);
      }
    }
  }

  private validateManifestPage(page: TorrentDownloadManifestPageV2, expectedOffset: number): void {
    if (
      page.snapshot_id !== this.snapshot.snapshot_id ||
      page.manifest_version !== this.snapshot.manifest_version ||
      page.file_count !== this.snapshot.file_count ||
      page.total_size !== this.snapshot.total_size ||
      page.offset !== expectedOffset ||
      page.limit !== this.snapshot.limit ||
      page.items.length > page.limit ||
      page.items.length === 0 ||
      expectedOffset + page.items.length > this.snapshot.file_count
    ) {
      throw new TransferFailure("manifest_changed");
    }
  }

  private async downloadFile(file: TorrentDownloadFileV2): Promise<void> {
    const recovery = new AbortController();
    this.activeRequests.add(recovery);
    try {
      for (let attempt = 0; ; attempt += 1) {
        try {
          await this.downloadFileOnce(file);
          return;
        } catch (error) {
          if (
            !(error instanceof InterruptedTransfer)
            || attempt >= MAX_RECOVERY_RETRIES
            || this.status !== "running"
          ) throw error;
          await waitForRetry(recoveryDelay(attempt, error.retryAfterMs), recovery.signal);
        }
      }
    } finally {
      this.activeRequests.delete(recovery);
    }
  }

  private async downloadFileOnce(file: TorrentDownloadFileV2): Promise<void> {
    if (this.status !== "running") throw new DOMException("transfer stopped", "AbortError");
    const localFile = await this.openLocalFile(file);
    if (this.status !== "running") throw new DOMException("transfer stopped", "AbortError");
    const offset = await this.synchronizeLocalOffset(file, localFile);
    if (offset === file.size) return;
    const controller = new AbortController();
    this.activeRequests.add(controller);
    if (this.status !== "running") controller.abort();
    try {
      const headers = new Headers({ "X-WOS-Download-Snapshot": this.snapshot.snapshot_id });
      if (offset > 0) headers.set("Range", `bytes=${offset}-`);
      let response: Response;
      for (let attempt = 0; ; attempt += 1) {
        try {
          response = await this.fetcher(
            `/api/v2/torrents/${encodeURIComponent(this.torrentRequestId)}/files/${encodeURIComponent(file.id)}/download`,
            { headers, credentials: "same-origin", signal: controller.signal },
          );
        } catch (error) {
          if (isNetworkFailure(error) && !controller.signal.aborted) throw new InterruptedTransfer();
          throw error;
        }
        if (response.status !== 429) break;
        await response.body?.cancel().catch(() => undefined);
        if (attempt >= MAX_DOWNLOAD_BUSY_RETRIES) {
          throw new TransferFailure("download_busy");
        }
        await waitForDownloadSlot(attempt, controller.signal);
      }
      if (response.status === 401) {
        // The session is absolute (it does not follow the lease heartbeat): say so instead
        // of reporting a changed manifest.
        await response.body?.cancel().catch(() => undefined);
        throw new TransferFailure("session_expired");
      }
      if (response.status === 404 || response.status === 410) {
        await response.body?.cancel().catch(() => undefined);
        throw new TransferFailure("download_unavailable");
      }
      if (response.status >= 500) {
        await response.body?.cancel().catch(() => undefined);
        const retryAfter = Number(response.headers.get("Retry-After"));
        throw new InterruptedTransfer(
          Number.isFinite(retryAfter) && retryAfter > 0 ? retryAfter * 1_000 : 0,
        );
      }
      if (!this.responseMatchesSnapshot(response, file, offset) || response.body === null) {
        if (response.body !== null) {
          await response.body.cancel().catch(() => undefined);
        }
        throw new TransferFailure("manifest_changed");
      }
      let writer: WritableFileHandle;
      try {
        writer = await localFile.createWritable({ keepExistingData: offset > 0 });
      } catch (error) {
        await response.body.cancel().catch(() => undefined);
        throw error;
      }
      let written = offset;
      let streamError: unknown = null;
      let reader: ReadableStreamDefaultReader<Uint8Array> | null = null;
      try {
        if (this.status !== "running") {
          await response.body.cancel().catch(() => undefined);
          throw new DOMException("transfer stopped", "AbortError");
        }
        if (offset > 0) await writer.seek(offset);
        reader = response.body.getReader();
        while (true) {
          let result: ReadableStreamReadResult<Uint8Array>;
          try {
            result = await reader.read();
          } catch (error) {
            if (isNetworkFailure(error) && !controller.signal.aborted) throw new InterruptedTransfer();
            throw error;
          }
          if (result.done) break;
          if (written + result.value.byteLength > file.size) {
            throw new TransferFailure("received_file_too_large");
          }
          await writer.write(result.value);
          written += result.value.byteLength;
          this.setOffset(file.id, written);
          this.emitProgress();
        }
      } catch (error) {
        if (reader === null) {
          await response.body.cancel().catch(() => undefined);
        } else {
          await reader.cancel(error).catch(() => undefined);
        }
        streamError = error;
      }
      try {
        await writer.close();
      } catch (error) {
        await this.rollbackAfterCloseFailure(file, localFile, offset);
        throw new TransferFailure(this.safeErrorCode(error));
      }
      if (streamError !== null) throw streamError;
      if (written !== file.size) throw new InterruptedTransfer();
    } finally {
      this.activeRequests.delete(controller);
    }
  }

  private responseMatchesSnapshot(response: Response, file: TorrentDownloadFileV2, offset: number): boolean {
    if (response.headers.get("X-WOS-Manifest-Version") !== String(this.snapshot.manifest_version)) {
      return false;
    }
    if (offset === 0) return response.status === 200;
    if (response.status !== 206) return false;
    const match = /^bytes (\d+)-(\d+)\/(\d+)$/.exec(response.headers.get("Content-Range") ?? "");
    if (match === null) return false;
    const start = Number(match[1]);
    const end = Number(match[2]);
    const total = Number(match[3]);
    return start === offset && end >= start && end < total && total === file.size;
  }

  private async synchronizeLocalOffset(
    file: TorrentDownloadFileV2,
    localFile: LocalFileHandle,
  ): Promise<number> {
    const recorded = this.offsets.get(file.id) ?? 0;
    if (recorded === 0 || localFile.getFile === undefined) return recorded;
    const actual = (await localFile.getFile()).size;
    if (!Number.isSafeInteger(actual) || actual < 0) {
      throw new TransferFailure("local_file_size_invalid");
    }
    const safeOffset = actual <= file.size ? Math.min(actual, recorded) : 0;
    this.setOffset(file.id, safeOffset);
    return safeOffset;
  }

  private async rollbackAfterCloseFailure(
    file: TorrentDownloadFileV2,
    localFile: LocalFileHandle,
    startingOffset: number,
  ): Promise<void> {
    let durableOffset = startingOffset;
    if (localFile.getFile !== undefined) {
      try {
        const actual = (await localFile.getFile()).size;
        if (Number.isSafeInteger(actual) && actual >= 0 && actual <= file.size) {
          durableOffset = Math.min(actual, startingOffset);
        }
      } catch {
        durableOffset = startingOffset;
      }
    }
    this.setOffset(file.id, durableOffset);
    this.emit();
  }

  private setOffset(fileId: string, offset: number): void {
    const previous = this.offsets.get(fileId) ?? 0;
    this.downloadedBytes += offset - previous;
    if (offset === 0) this.offsets.delete(fileId);
    else this.offsets.set(fileId, offset);
  }

  private async openLocalFile(file: TorrentDownloadFileV2): Promise<LocalFileHandle> {
    const components = file.relative_path.split("/");
    if (
      components.length === 0 ||
      components.some((component) => component === "" || component === "." || component === "..")
    ) {
      throw new TransferFailure("manifest_path_invalid");
    }
    let directory = this.directory;
    let parentPath = "";
    for (const component of components.slice(0, -1)) {
      const path = parentPath === "" ? component : `${parentPath}/${component}`;
      const name = await this.localName(this.directoryNames, path, parentPath, component);
      directory = await directory.getDirectoryHandle(name, { create: true });
      parentPath = path;
    }
    const name = await this.localName(
      this.fileNames,
      file.id,
      parentPath,
      components.at(-1) as string,
    );
    const handle = await directory.getFileHandle(name, { create: true });
    await this.claimLocalFile(file, handle);
    return handle;
  }

  private localName(
    names: Map<string, Promise<string>>,
    key: string,
    parentPath: string,
    component: string,
  ): Promise<string> {
    let name = names.get(key);
    if (name === undefined) {
      const pending = this.claimLocalName(parentPath, component);
      names.set(key, pending);
      // A failed attempt must not be remembered: the next try decides again.
      pending.catch(() => {
        if (names.get(key) === pending) names.delete(key);
      });
      name = pending;
    }
    return name;
  }

  private async claimLocalName(parentPath: string, component: string): Promise<string> {
    const plain = await localFileName(component);
    const key = localNameKey(plain);
    let claimed = this.claimedNames.get(parentPath);
    if (claimed === undefined) {
      claimed = new Set();
      this.claimedNames.set(parentPath, claimed);
    }
    // Check and claim without awaiting in between: concurrent workers share this directory.
    if (!claimed.has(key)) {
      claimed.add(key);
      return plain;
    }
    return localFileName(component, { disambiguate: true });
  }

  /**
   * `createWritable` truncates: a non-empty file that this transfer did not create (and has not
   * been told to replace) belongs to the user and stops the transfer instead.
   */
  private async claimLocalFile(file: TorrentDownloadFileV2, handle: LocalFileHandle): Promise<void> {
    if (this.replaceExistingFiles || this.ownedFiles.has(file.id)) return;
    if (handle.getFile !== undefined) {
      const size = (await handle.getFile()).size;
      if (!Number.isSafeInteger(size) || size < 0) {
        throw new TransferFailure("local_file_size_invalid");
      }
      if (size > 0) throw new TransferFailure("local_file_exists");
    }
    this.ownedFiles.add(file.id);
  }

  private abortActive(): void {
    for (const controller of this.activeRequests) controller.abort();
  }

  private fail(error: unknown): void {
    if (this.status !== "running") return;
    this.status = "error";
    this.error = this.safeErrorCode(error);
    this.abortActive();
    this.emit();
  }

  private safeErrorCode(error: unknown): RecursiveTransferErrorCode {
    if (error instanceof TransferFailure) return error.code;
    if (error instanceof DOMException) {
      if (error.name === "QuotaExceededError") return "local_disk_full";
      if (error.name === "NotAllowedError" || error.name === "SecurityError") {
        return "local_write_denied";
      }
      if (error.name === "NotFoundError") return "local_destination_missing";
      // A file where a folder is needed, or a folder where a file is needed.
      if (error.name === "TypeMismatchError") return "local_file_exists";
      if (error.name === "AbortError") return "download_interrupted";
    }
    return "local_transfer_failed";
  }

  // A network chunk is 16-64 KiB: at tens of MB/s that would re-render the page thousands of
  // times per second. Byte progress is sampled (leading edge, then one trailing emit); every
  // state change still emits at once and carries the exact count.
  private emitProgress(): void {
    const wait = PROGRESS_EMIT_INTERVAL_MS - (Date.now() - this.lastProgressEmit);
    if (wait <= 0) {
      this.emit();
      return;
    }
    if (this.progressTimer !== null) return;
    this.progressTimer = setTimeout(() => {
      this.progressTimer = null;
      this.emit();
    }, wait);
  }

  private emit(error: RecursiveTransferErrorCode | null = this.error): void {
    if (this.progressTimer !== null) {
      clearTimeout(this.progressTimer);
      this.progressTimer = null;
    }
    this.lastProgressEmit = Date.now();
    this.onProgress({
      status: this.status,
      downloadedBytes: this.downloadedBytes,
      completedFiles: this.completedFiles,
      error,
      queue: this.queueSnapshot(),
    });
  }

  private queueSnapshot(): readonly LocalTransferQueueItem[] {
    const activeStatus: LocalTransferQueueItemStatus = this.status === "paused"
      ? "paused"
      : this.status === "error"
        ? "error"
        : this.status === "cancelled"
          ? "cancelled"
          : "active";
    const active = [...this.activeFiles.values()].map((file) => ({
      id: file.id,
      relativePath: file.relative_path,
      status: activeStatus,
      position: null,
    }));
    const completed = this.recentlyCompleted.map((file) => ({
      id: file.id,
      relativePath: file.relative_path,
      status: "completed" as const,
      position: null,
    }));
    const remaining = Math.max(0, MAX_VISIBLE_QUEUE_ITEMS - active.length - completed.length);
    const waiting = this.prioritizedPendingPreview(remaining).map((file, index) => {
      const status: LocalTransferQueueItemStatus = this.status === "cancelled"
        ? "cancelled"
        : this.status === "paused"
          ? "paused"
        : file.id === this.failedFileId
          ? "error"
          : "waiting";
      return {
        id: file.id,
        relativePath: file.relative_path,
        status,
        position: status === "waiting" ? index + 1 : null,
      };
    });
    return [...active, ...waiting, ...completed].slice(0, MAX_VISIBLE_QUEUE_ITEMS);
  }

  private prioritizedPendingPreview(limit: number): TorrentDownloadFileV2[] {
    const pending = [...this.pendingFiles];
    const result: TorrentDownloadFileV2[] = [];
    let burst = this.sizePrioritySelections;
    while (pending.length > 0 && result.length < limit) {
      if (burst >= SIZE_PRIORITY_BURST) {
        result.push(pending.shift() as TorrentDownloadFileV2);
        burst = 0;
        continue;
      }
      let selectedIndex = 0;
      for (let index = 1; index < pending.length; index += 1) {
        if (
          pending[index].size < pending[selectedIndex].size
          || (
            pending[index].size === pending[selectedIndex].size
            && pending[index].file_index < pending[selectedIndex].file_index
          )
        ) {
          selectedIndex = index;
        }
      }
      result.push(pending.splice(selectedIndex, 1)[0]);
      burst += 1;
    }
    return result;
  }
}
