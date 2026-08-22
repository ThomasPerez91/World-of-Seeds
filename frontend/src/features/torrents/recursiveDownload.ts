import type { TorrentDownloadFileV2, TorrentDownloadSnapshotV2 } from "../../api/client";

export interface WritableFileHandle {
  write(data: Uint8Array): Promise<void>;
  seek(position: number): Promise<void>;
  close(): Promise<void>;
}

export interface LocalFileHandle {
  createWritable(options?: { keepExistingData?: boolean }): Promise<WritableFileHandle>;
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

interface RecursiveDownloadOptions {
  torrentRequestId: string;
  snapshot: TorrentDownloadSnapshotV2;
  directory: LocalDirectoryHandle;
  concurrency?: number;
  fetcher?: typeof fetch;
  onProgress: (progress: RecursiveTransferProgress) => void;
}

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
  private readonly snapshot: TorrentDownloadSnapshotV2;
  private readonly directory: LocalDirectoryHandle;
  private readonly concurrency: number;
  private readonly fetcher: typeof fetch;
  private readonly onProgress: (progress: RecursiveTransferProgress) => void;
  private readonly offsets = new Map<string, number>();
  private readonly completed = new Set<string>();
  private readonly activeRequests = new Set<AbortController>();
  private status: RecursiveTransferStatus = "paused";
  private downloadedBytes = 0;
  private running: Promise<void> | null = null;

  constructor(options: RecursiveDownloadOptions) {
    const concurrency = options.concurrency ?? 2;
    if (!Number.isInteger(concurrency) || concurrency < 1 || concurrency > 4) {
      throw new Error("download_concurrency_invalid");
    }
    this.torrentRequestId = options.torrentRequestId;
    this.snapshot = options.snapshot;
    this.directory = options.directory;
    this.concurrency = concurrency;
    this.fetcher = options.fetcher ?? fetch;
    this.onProgress = options.onProgress;
  }

  start(): Promise<void> {
    if (this.running !== null) return this.running;
    if (this.status === "cancelled" || this.status === "completed") return Promise.resolve();
    this.status = "running";
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
    this.abortActive();
    this.emit();
  }

  private async run(): Promise<void> {
    let cursor = 0;
    const nextFile = (): TorrentDownloadFileV2 | null => {
      while (cursor < this.snapshot.items.length) {
        const file = this.snapshot.items[cursor++];
        if (!this.completed.has(file.id)) return file;
      }
      return null;
    };
    const worker = async (): Promise<void> => {
      while (this.status === "running") {
        const file = nextFile();
        if (file === null) return;
        try {
          await this.downloadFile(file);
        } catch (error) {
          if (error instanceof DOMException && error.name === "AbortError") return;
          if (this.status !== "running") return;
          this.status = "error";
          this.abortActive();
          this.emit(error instanceof Error ? error.message : "Le téléchargement a échoué.");
          throw error;
        }
      }
    };
    await Promise.all(Array.from({ length: this.concurrency }, worker));
    if (this.status === "running" && this.completed.size === this.snapshot.items.length) {
      this.status = "completed";
      this.emit();
    }
  }

  private async downloadFile(file: TorrentDownloadFileV2): Promise<void> {
    const offset = this.offsets.get(file.id) ?? 0;
    const controller = new AbortController();
    this.activeRequests.add(controller);
    try {
      const headers = new Headers({ "X-WOS-Download-Snapshot": this.snapshot.snapshot_id });
      if (offset > 0) headers.set("Range", `bytes=${offset}-`);
      const response = await this.fetcher(
        `/api/v2/torrents/${encodeURIComponent(this.torrentRequestId)}/files/${encodeURIComponent(file.id)}/download`,
        { headers, credentials: "same-origin", signal: controller.signal },
      );
      const expectedStatus = offset > 0 ? 206 : 200;
      const contentRange = response.headers.get("Content-Range");
      if (
        response.status !== expectedStatus ||
        response.headers.get("X-WOS-Manifest-Version") !== String(this.snapshot.manifest_version) ||
        (offset > 0 &&
          (contentRange === null ||
            !contentRange.startsWith(`bytes ${offset}-`) ||
            !contentRange.endsWith(`/${file.size}`))) ||
        response.body === null
      ) {
        throw new Error("Le contenu a changé ou la reprise n’est plus valide.");
      }
      const localFile = await this.openLocalFile(file.relative_path);
      const writer = await localFile.createWritable({ keepExistingData: offset > 0 });
      if (offset > 0) await writer.seek(offset);
      const reader = response.body.getReader();
      let written = offset;
      try {
        while (true) {
          const result = await reader.read();
          if (result.done) break;
          await writer.write(result.value);
          written += result.value.byteLength;
          if (written > file.size) throw new Error("Le fichier reçu dépasse le manifeste.");
          this.offsets.set(file.id, written);
          this.downloadedBytes += result.value.byteLength;
          this.emit();
        }
      } finally {
        await writer.close();
      }
      if (written !== file.size) throw new Error("Le fichier reçu est incomplet.");
      this.completed.add(file.id);
      this.emit();
    } finally {
      this.activeRequests.delete(controller);
    }
  }

  private async openLocalFile(relativePath: string): Promise<LocalFileHandle> {
    const components = relativePath.split("/");
    if (
      components.length === 0 ||
      components.some((component) => component === "" || component === "." || component === "..")
    ) {
      throw new Error("Le manifeste contient un chemin invalide.");
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

  private emit(error: string | null = null): void {
    this.onProgress({
      status: this.status,
      downloadedBytes: this.downloadedBytes,
      completedFiles: this.completed.size,
      error,
    });
  }
}
