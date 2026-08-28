import type {
  TorrentDownloadFileV2,
  TorrentDownloadManifestPageV2,
} from "../../api/client";

const MAX_MANIFEST_PAGE_SIZE = 500;
const MAX_BUFFERED_MANIFEST_PAGES = 2;

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
  showDirectoryPicker?: (options: { mode: "readwrite" }) => Promise<LocalDirectoryHandle>;
}

export type RecursiveTransferStatus =
  | "running"
  | "paused"
  | "cancelled"
  | "completed"
  | "error";

export interface RecursiveTransferProgress {
  status: RecursiveTransferStatus;
  downloadedBytes: number;
  completedFiles: number;
  error: string | null;
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
  onProgress: (progress: RecursiveTransferProgress) => void;
}

class TransferFailure extends Error {}

export function supportsRecursiveDirectoryDownload(target: Window = window): boolean {
  return typeof (target as DirectoryPickerWindow).showDirectoryPicker === "function";
}

export function pickDownloadDirectory(target: Window = window): Promise<LocalDirectoryHandle> {
  const picker = (target as DirectoryPickerWindow).showDirectoryPicker;
  if (picker === undefined) throw new Error("directory_picker_unavailable");
  return picker.call(target, { mode: "readwrite" });
}

export class RecursiveDownloadController {
  private readonly torrentRequestId: string;
  private readonly snapshot: TorrentDownloadManifestPageV2;
  private readonly directory: LocalDirectoryHandle;
  private readonly loadManifestPage: ManifestPageLoader;
  private readonly concurrency: number;
  private readonly fetcher: typeof fetch;
  private readonly onProgress: (progress: RecursiveTransferProgress) => void;
  private readonly offsets = new Map<string, number>();
  private readonly activeRequests = new Set<AbortController>();
  private readonly pendingFiles: TorrentDownloadFileV2[];
  private readonly maxBufferedFiles: number;
  private status: RecursiveTransferStatus = "paused";
  private error: string | null = null;
  private downloadedBytes = 0;
  private completedFiles = 0;
  private nextManifestOffset: number;
  private manifestComplete: boolean;
  private pageLoading: Promise<void> | null = null;
  private pageError: unknown = null;
  private running: Promise<void> | null = null;

  constructor(options: RecursiveDownloadOptions) {
    const concurrency = options.concurrency ?? 2;
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
      throw new TransferFailure("Le manifeste est incomplet.");
    }
    this.torrentRequestId = options.torrentRequestId;
    this.snapshot = options.firstPage;
    this.directory = options.directory;
    this.loadManifestPage = options.loadManifestPage;
    this.concurrency = concurrency;
    this.fetcher = options.fetcher ?? fetch;
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
        try {
          await this.downloadFile(file);
          this.completedFiles += 1;
          this.offsets.delete(file.id);
          this.emit();
        } catch (error) {
          this.pendingFiles.unshift(file);
          if (error instanceof DOMException && error.name === "AbortError") return;
          if (this.status !== "running") return;
          this.fail(error);
          return;
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
      const file = this.pendingFiles.shift();
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
    loading = this.loadManifestPage(
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
      throw new TransferFailure("Le contenu a changé. Relance le téléchargement.");
    }
  }

  private async downloadFile(file: TorrentDownloadFileV2): Promise<void> {
    if (this.status !== "running") throw new DOMException("transfer stopped", "AbortError");
    const localFile = await this.openLocalFile(file.relative_path);
    if (this.status !== "running") throw new DOMException("transfer stopped", "AbortError");
    const offset = await this.synchronizeLocalOffset(file, localFile);
    if (offset === file.size) return;
    const controller = new AbortController();
    this.activeRequests.add(controller);
    if (this.status !== "running") controller.abort();
    try {
      const headers = new Headers({ "X-WOS-Download-Snapshot": this.snapshot.snapshot_id });
      if (offset > 0) headers.set("Range", `bytes=${offset}-`);
      const response = await this.fetcher(
        `/api/v2/torrents/${encodeURIComponent(this.torrentRequestId)}/files/${encodeURIComponent(file.id)}/download`,
        { headers, credentials: "same-origin", signal: controller.signal },
      );
      if (!this.responseMatchesSnapshot(response, file, offset) || response.body === null) {
        throw new TransferFailure("Le contenu a changé ou la reprise n’est plus valide.");
      }
      const writer = await localFile.createWritable({ keepExistingData: offset > 0 });
      let written = offset;
      let streamError: unknown = null;
      try {
        if (this.status !== "running") {
          throw new DOMException("transfer stopped", "AbortError");
        }
        if (offset > 0) await writer.seek(offset);
        const reader = response.body.getReader();
        while (true) {
          const result = await reader.read();
          if (result.done) break;
          if (written + result.value.byteLength > file.size) {
            throw new TransferFailure("Le fichier reçu dépasse le manifeste.");
          }
          await writer.write(result.value);
          written += result.value.byteLength;
          this.setOffset(file.id, written);
          this.emit();
        }
      } catch (error) {
        streamError = error;
      }
      try {
        await writer.close();
      } catch (error) {
        await this.rollbackAfterCloseFailure(file, localFile, offset);
        throw new TransferFailure(this.safeErrorMessage(error));
      }
      if (streamError !== null) throw streamError;
      if (written !== file.size) throw new TransferFailure("Le fichier reçu est incomplet.");
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
      throw new TransferFailure("La taille du fichier local est invalide.");
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

  private async openLocalFile(relativePath: string): Promise<LocalFileHandle> {
    const components = relativePath.split("/");
    if (
      components.length === 0 ||
      components.some((component) => component === "" || component === "." || component === "..")
    ) {
      throw new TransferFailure("Le manifeste contient un chemin invalide.");
    }
    let directory = this.directory;
    for (const component of components.slice(0, -1)) {
      directory = await directory.getDirectoryHandle(component, { create: true });
    }
    return directory.getFileHandle(components.at(-1) as string, { create: true });
  }

  private abortActive(): void {
    for (const controller of this.activeRequests) controller.abort();
  }

  private fail(error: unknown): void {
    if (this.status !== "running") return;
    this.status = "error";
    this.error = this.safeErrorMessage(error);
    this.abortActive();
    this.emit();
  }

  private safeErrorMessage(error: unknown): string {
    if (error instanceof TransferFailure) return error.message;
    if (error instanceof DOMException) {
      if (error.name === "QuotaExceededError") return "Le disque local ne dispose plus d’assez d’espace.";
      if (error.name === "NotAllowedError" || error.name === "SecurityError") {
        return "L’autorisation d’écriture locale a été refusée.";
      }
      if (error.name === "NotFoundError") return "Le support ou le dossier local n’est plus disponible.";
      if (error.name === "AbortError") return "Le téléchargement a été interrompu.";
    }
    return "L’écriture ou le téléchargement local a échoué.";
  }

  private emit(error: string | null = this.error): void {
    this.onProgress({
      status: this.status,
      downloadedBytes: this.downloadedBytes,
      completedFiles: this.completedFiles,
      error,
    });
  }
}
