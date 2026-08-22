export interface User {
  id: string;
  username: string;
  is_admin: boolean;
  is_active: boolean;
  must_change_credentials: boolean;
}

interface AuthResponse {
  user: User;
}

export interface PublicSystemHealth {
  status: "ok" | "degraded";
  checked_at: string;
}

export interface LivenessHealth {
  status: "ok";
  service: string;
  version: string;
}

export interface GeneratedCredentials {
  user: User;
  initial_password: string;
}

export type FileEntryKind = "directory" | "file" | "symlink" | "other";

export interface FileEntry {
  name: string;
  path: string;
  kind: FileEntryKind;
  size: number | null;
  modified_at: string;
  media_type: string | null;
  blocked: boolean;
}

export interface StorageUsage {
  total: number;
  used: number;
  available: number;
}

export interface Breadcrumb {
  label: string;
  path: string;
}

export interface DirectoryListing {
  path: string;
  breadcrumbs: Breadcrumb[];
  entries: FileEntry[];
  storage: StorageUsage;
  truncated: boolean;
}

export interface FileMutation {
  path: string;
  name: string;
  kind: "directory" | "file";
}

export type UserTorrentState =
  | "adding"
  | "pending"
  | "downloading"
  | "stalled"
  | "completed"
  | "error";

export interface UserTorrent {
  id: string;
  name: string;
  size_bytes: number;
  progress: number;
  state: UserTorrentState;
  downloaded_bytes: number;
  download_speed_bytes: number;
  eta_seconds: number | null;
  error: string | null;
  created_at: string;
}

export interface UserTorrentListing {
  torrents: UserTorrent[];
}

export interface TorrentUploadResult {
  id: string;
  name: string;
  total_size: number;
}

export type TorrentRequestV2State =
  | "requested"
  | "active"
  | "ready"
  | "cancelled"
  | "expired"
  | "error";

export interface TorrentRequestV2 {
  id: string;
  name: string;
  total_size: number;
  state: TorrentRequestV2State;
  progress: number;
  error_code: string | null;
  created_at: string;
  updated_at: string;
}

export interface TorrentRequestV2Listing {
  items: TorrentRequestV2[];
  offset: number;
  limit: number;
  total: number;
}

export interface TorrentRequestV2CreateResult extends TorrentRequestV2 {
  created: boolean;
  storage_pressure: "normal" | "warning" | "critical";
}

export interface TorrentDownloadFileV2 {
  id: string;
  file_index: number;
  relative_path: string;
  size: number;
}

export interface TorrentDownloadManifestPageV2 {
  snapshot_id: string;
  manifest_version: number;
  file_count: number;
  total_size: number;
  archive_available: boolean;
  offset: number;
  limit: number;
  items: TorrentDownloadFileV2[];
}

export interface TorrentDownloadSnapshotV2 extends Omit<TorrentDownloadManifestPageV2, "items"> {
  items: TorrentDownloadFileV2[];
}

export interface TrashEntry {
  id: string;
  original_path: string;
  name: string;
  kind: "directory" | "file";
  size: number | null;
  deleted_at: string;
}

export interface TrashListing {
  entries: TrashEntry[];
  truncated: boolean;
}

export interface AdminStorageOverview extends StorageUsage {
  active_users: number;
  suspended_users: number;
  trash_entries: number;
  known_trash_bytes: number;
}

export interface AdminTrashEntry extends TrashEntry {
  user_id: string;
  username: string;
}

export interface AdminTrashListing {
  entries: AdminTrashEntry[];
  truncated: boolean;
}

export interface AdminTrashPurgeResult {
  purged: number;
  remaining: number;
}

export type ExternalServiceState = "healthy" | "unavailable" | "unconfigured";

export interface ExternalServiceHealth {
  status: ExternalServiceState;
  latency_ms: number | null;
  version: string | null;
  error_code: string | null;
}

export interface AdminServicesHealth {
  status: "ok" | "degraded";
  checked_at: string;
  newgreedy: ExternalServiceHealth;
  qbittorrent: ExternalServiceHealth;
}

export type NewGreedyConfigValue = boolean | number | string;

export interface NewGreedyConfigField {
  id: string;
  key: string;
  label: string;
  description: string;
  input_type: "boolean" | "integer" | "number" | "text" | "select";
  value: NewGreedyConfigValue;
  editable: boolean;
  requires_restart: boolean;
  minimum: number | null;
  maximum: number | null;
  options: string[];
}

export interface NewGreedyConfigSection {
  id: string;
  label: string;
  fields: NewGreedyConfigField[];
}

export interface NewGreedyConfig {
  sections: NewGreedyConfigSection[];
  restart_required: boolean;
}

export interface NewGreedyOverview {
  torrents: number;
  downloading: number;
  seeding: number;
  stalled: number;
  target_reached: number;
  total_downloaded_bytes: number;
  total_reported_uploaded_bytes: number;
  total_fake_uploaded_bytes: number;
}

export interface NewGreedyStatsReset {
  purged: number;
  remaining: number;
}

export interface NewGreedyTorrent {
  id: string;
  mode: "down" | "seed";
  downloaded_bytes: number;
  reported_uploaded_bytes: number;
  fake_uploaded_bytes: number;
  ratio: number | null;
  announce_count: number;
  stalled: boolean;
  target_reached: boolean;
  last_announce_at: string | null;
}

export interface NewGreedyTorrentListing {
  torrents: NewGreedyTorrent[];
}

export interface QBittorrentTorrent {
  id: string;
  name: string;
  state: string;
  progress: number;
  size_bytes: number;
  downloaded_bytes: number;
  uploaded_bytes: number;
  download_speed_bytes: number;
  upload_speed_bytes: number;
  ratio: number;
  eta_seconds: number | null;
  category: string | null;
  tracker_host: string | null;
}

export interface QBittorrentTorrentListing {
  torrents: QBittorrentTorrent[];
  truncated: boolean;
}

export interface NewGreedyRestartStatus {
  state: "idle" | "pending" | "restarting" | "healthy" | "failed" | "rejected";
  request_id: string | null;
  updated_at: string | null;
  message_code:
    | "idle"
    | "requested"
    | "restarting"
    | "healthy"
    | "restart_failed"
    | "cooldown"
    | "invalid_request";
}

export type WosRestartStatus = NewGreedyRestartStatus;

export type OptionValue = boolean | number | string;

export interface OptionField {
  key: string;
  label: string;
  description: string;
  input_type: "boolean" | "integer" | "select";
  value: OptionValue;
  default: OptionValue;
  unit: string | null;
  minimum: number | null;
  maximum: number | null;
  choices: string[];
  editable: boolean;
  restart_required: boolean;
}

export interface OptionSection {
  id: string;
  label: string;
  fields: OptionField[];
}

export interface OptionsResponse {
  sections: OptionSection[];
  changed_keys: string[];
  restart_required: boolean;
}

interface BusinessErrorDetail {
  code: string;
  message: string;
  field: string | null;
}

function isBusinessErrorDetail(value: unknown): value is BusinessErrorDetail {
  if (typeof value !== "object" || value === null) return false;
  const candidate = value as Partial<BusinessErrorDetail>;
  return (
    typeof candidate.code === "string" &&
    typeof candidate.message === "string" &&
    (typeof candidate.field === "string" || candidate.field === null)
  );
}

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    message: string,
    public readonly code: string | null = null,
    public readonly field: string | null = null,
  ) {
    super(message);
  }
}

function readCookie(name: string): string | null {
  const prefix = `${name}=`;
  const value = document.cookie
    .split(";")
    .map((part) => part.trim())
    .find((part) => part.startsWith(prefix));
  return value === undefined ? null : decodeURIComponent(value.slice(prefix.length));
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  return requestAt<T>("/api/v1", path, init);
}

async function requestV2<T>(path: string, init: RequestInit = {}): Promise<T> {
  return requestAt<T>("/api/v2", path, init);
}

async function requestAt<T>(prefix: string, path: string, init: RequestInit = {}): Promise<T> {
  const method = init.method?.toUpperCase() ?? "GET";
  const headers = new Headers(init.headers);
  headers.set("Accept", "application/json");
  if (init.body !== undefined && !(init.body instanceof FormData)) {
    headers.set("Content-Type", "application/json");
  }
  if (!["GET", "HEAD", "OPTIONS"].includes(method)) {
    const csrfToken = readCookie("wos_csrf");
    if (csrfToken !== null) {
      headers.set("X-CSRF-Token", csrfToken);
    }
  }

  const response = await fetch(`${prefix}${path}`, {
    ...init,
    headers,
    credentials: "same-origin",
  });
  if (!response.ok) {
    let message = "Une erreur est survenue.";
    let code: string | null = null;
    let field: string | null = null;
    try {
      const body = (await response.json()) as { detail?: string | BusinessErrorDetail };
      if (typeof body.detail === "string") {
        message = body.detail;
      } else if (isBusinessErrorDetail(body.detail)) {
        message = body.detail.message;
        code = body.detail.code;
        field = body.detail.field;
      }
    } catch {
      // Keep the generic message for non-JSON failures.
    }
    throw new ApiError(response.status, message, code, field);
  }
  if (response.status === 204) {
    return undefined as T;
  }
  return (await response.json()) as T;
}

export const api = {
  health(): Promise<PublicSystemHealth> {
    return request<PublicSystemHealth>("/health/status");
  },

  liveHealth(): Promise<LivenessHealth> {
    return request<LivenessHealth>("/health/live");
  },

  async login(username: string, password: string): Promise<User> {
    const response = await request<AuthResponse>("/auth/login", {
      method: "POST",
      body: JSON.stringify({ username, password }),
    });
    return response.user;
  },

  async me(): Promise<User> {
    const response = await request<AuthResponse>("/auth/me");
    return response.user;
  },

  logout(): Promise<void> {
    return request<void>("/auth/logout", { method: "POST" });
  },

  async changeCredentials(
    currentPassword: string,
    username: string,
    newPassword: string,
  ): Promise<User> {
    const response = await request<AuthResponse>("/auth/credentials", {
      method: "PATCH",
      body: JSON.stringify({
        current_password: currentPassword,
        username,
        new_password: newPassword,
      }),
    });
    return response.user;
  },

  async changeUsername(username: string): Promise<User> {
    const response = await request<AuthResponse>("/auth/username", {
      method: "PATCH",
      body: JSON.stringify({ username }),
    });
    return response.user;
  },

  changePassword(currentPassword: string, newPassword: string): Promise<void> {
    return request<void>("/auth/password", {
      method: "PATCH",
      body: JSON.stringify({
        current_password: currentPassword,
        new_password: newPassword,
      }),
    });
  },

  listUsers(): Promise<User[]> {
    return request<User[]>("/admin/users");
  },

  createUser(): Promise<GeneratedCredentials> {
    return request<GeneratedCredentials>("/admin/users", {
      method: "POST",
    });
  },

  setUserActive(userId: string, isActive: boolean): Promise<User> {
    return request<User>(`/admin/users/${encodeURIComponent(userId)}/status`, {
      method: "PATCH",
      body: JSON.stringify({ is_active: isActive }),
    });
  },

  deleteUser(userId: string): Promise<void> {
    return request<void>(`/admin/users/${encodeURIComponent(userId)}`, {
      method: "DELETE",
    });
  },

  getAdminStorage(): Promise<AdminStorageOverview> {
    return request<AdminStorageOverview>("/admin/storage");
  },

  getAdminServicesHealth(): Promise<AdminServicesHealth> {
    return request<AdminServicesHealth>("/admin/services/health");
  },

  getNewGreedyConfig(): Promise<NewGreedyConfig> {
    return request<NewGreedyConfig>("/admin/services/newgreedy/config");
  },

  updateNewGreedyConfig(
    changes: Record<string, NewGreedyConfigValue>,
  ): Promise<NewGreedyConfig> {
    return request<NewGreedyConfig>("/admin/services/newgreedy/config", {
      method: "PATCH",
      body: JSON.stringify({ changes }),
    });
  },

  getNewGreedyOverview(): Promise<NewGreedyOverview> {
    return request<NewGreedyOverview>("/admin/services/newgreedy/overview");
  },

  resetNewGreedyStats(): Promise<NewGreedyStatsReset> {
    return request<NewGreedyStatsReset>("/admin/services/newgreedy/stats", {
      method: "DELETE",
    });
  },

  listNewGreedyTorrents(): Promise<NewGreedyTorrentListing> {
    return request<NewGreedyTorrentListing>("/admin/services/newgreedy/torrents");
  },

  listQBittorrentTorrents(): Promise<QBittorrentTorrentListing> {
    return request<QBittorrentTorrentListing>("/admin/services/qbittorrent/torrents");
  },

  getNewGreedyRestartStatus(): Promise<NewGreedyRestartStatus> {
    return request<NewGreedyRestartStatus>("/admin/services/newgreedy/restart");
  },

  restartNewGreedy(): Promise<NewGreedyRestartStatus> {
    return request<NewGreedyRestartStatus>("/admin/services/newgreedy/restart", {
      method: "POST",
    });
  },

  getOptions(): Promise<OptionsResponse> {
    return request<OptionsResponse>("/admin/options");
  },

  updateOptions(changes: Record<string, OptionValue>): Promise<OptionsResponse> {
    return request<OptionsResponse>("/admin/options", {
      method: "PATCH",
      body: JSON.stringify({ changes }),
    });
  },

  getWosRestartStatus(): Promise<WosRestartStatus> {
    return request<WosRestartStatus>("/admin/services/wos/restart");
  },

  restartWos(): Promise<WosRestartStatus> {
    return request<WosRestartStatus>("/admin/services/wos/restart", {
      method: "POST",
    });
  },

  listAdminTrash(signal?: AbortSignal): Promise<AdminTrashListing> {
    return request<AdminTrashListing>("/admin/trash", { signal });
  },

  purgeAdminTrash(entryId: string): Promise<void> {
    return request<void>(`/admin/trash/${encodeURIComponent(entryId)}`, {
      method: "DELETE",
    });
  },

  purgeAllAdminTrash(): Promise<AdminTrashPurgeResult> {
    return request<AdminTrashPurgeResult>("/admin/trash", {
      method: "DELETE",
    });
  },

  listFiles(path: string, signal?: AbortSignal): Promise<DirectoryListing> {
    const search = new URLSearchParams();
    if (path !== "") {
      search.set("path", path);
    }
    const query = search.size === 0 ? "" : `?${search.toString()}`;
    return request<DirectoryListing>(`/files${query}`, { signal });
  },

  fileDownloadUrl(path: string): string {
    const search = new URLSearchParams({ path });
    return `/api/v1/files/download?${search.toString()}`;
  },

  folderDownloadUrl(path: string): string {
    const search = new URLSearchParams({ path });
    return `/api/v1/files/download-folder?${search.toString()}`;
  },

  createDirectory(parent: string, name: string): Promise<FileMutation> {
    return request<FileMutation>("/files/directory", {
      method: "POST",
      body: JSON.stringify({ parent, name }),
    });
  },

  renameFile(path: string, basename: string): Promise<FileMutation> {
    return request<FileMutation>("/files/rename", {
      method: "PATCH",
      body: JSON.stringify({ path, basename }),
    });
  },

  uploadTorrent(file: File): Promise<TorrentUploadResult> {
    const form = new FormData();
    form.set("torrent", file, file.name);
    return request<TorrentUploadResult>("/torrents", {
      method: "POST",
      body: form,
    });
  },

  listUserTorrents(signal?: AbortSignal): Promise<UserTorrentListing> {
    return request<UserTorrentListing>("/torrents", { signal });
  },

  createTorrentRequestV2(file: File): Promise<TorrentRequestV2CreateResult> {
    const form = new FormData();
    form.set("torrent", file, file.name);
    return requestV2<TorrentRequestV2CreateResult>("/torrents", {
      method: "POST",
      body: form,
    });
  },

  listTorrentRequestsV2(
    offset: number,
    limit: number,
    signal?: AbortSignal,
  ): Promise<TorrentRequestV2Listing> {
    const search = new URLSearchParams({ offset: String(offset), limit: String(limit) });
    return requestV2<TorrentRequestV2Listing>(`/torrents?${search.toString()}`, { signal });
  },

  cancelTorrentRequestV2(torrentRequestId: string): Promise<void> {
    return requestV2<void>(`/torrents/${encodeURIComponent(torrentRequestId)}`, {
      method: "DELETE",
    });
  },

  async getTorrentDownloadSnapshotV2(
    torrentRequestId: string,
    signal?: AbortSignal,
  ): Promise<TorrentDownloadSnapshotV2> {
    const items: TorrentDownloadFileV2[] = [];
    let offset = 0;
    let snapshot: string | null = null;
    let firstPage: TorrentDownloadManifestPageV2 | null = null;
    do {
      const search = new URLSearchParams({ offset: String(offset), limit: "500" });
      if (snapshot !== null) search.set("snapshot", snapshot);
      const page = await requestV2<TorrentDownloadManifestPageV2>(
        `/torrents/${encodeURIComponent(torrentRequestId)}/download-manifest?${search.toString()}`,
        { signal },
      );
      firstPage ??= page;
      snapshot ??= page.snapshot_id;
      if (
        page.snapshot_id !== snapshot ||
        page.offset !== offset ||
        page.file_count !== firstPage.file_count ||
        page.total_size !== firstPage.total_size
      ) {
        throw new ApiError(409, "Le contenu a changé. Relance le téléchargement.", "download_snapshot_changed");
      }
      items.push(...page.items);
      if (page.items.length === 0 && offset < page.file_count) {
        throw new ApiError(409, "Le manifeste est incomplet.", "download_snapshot_changed");
      }
      offset += page.items.length;
    } while (firstPage !== null && offset < firstPage.file_count);
    if (firstPage === null || items.length !== firstPage.file_count) {
      throw new ApiError(409, "Le manifeste est incomplet.", "download_snapshot_changed");
    }
    return { ...firstPage, items };
  },

  torrentFileDownloadUrlV2(
    torrentRequestId: string,
    torrentFileId: string,
    snapshotId: string,
  ): string {
    const snapshot = new URLSearchParams({ snapshot: snapshotId });
    return `/api/v2/torrents/${encodeURIComponent(torrentRequestId)}/files/${encodeURIComponent(torrentFileId)}/download?${snapshot.toString()}`;
  },

  torrentArchiveDownloadUrlV2(torrentRequestId: string, snapshotId: string): string {
    const snapshot = new URLSearchParams({ snapshot: snapshotId });
    return `/api/v2/torrents/${encodeURIComponent(torrentRequestId)}/download-archive?${snapshot.toString()}`;
  },

  moveFile(path: string, destinationDirectory: string): Promise<FileMutation> {
    return request<FileMutation>("/files/move", {
      method: "POST",
      body: JSON.stringify({
        path,
        destination_directory: destinationDirectory,
      }),
    });
  },

  trashFile(path: string): Promise<TrashEntry> {
    return request<TrashEntry>("/trash", {
      method: "POST",
      body: JSON.stringify({ path }),
    });
  },

  listTrash(signal?: AbortSignal): Promise<TrashListing> {
    return request<TrashListing>("/trash", { signal });
  },

  restoreTrash(entryId: string): Promise<FileMutation> {
    return request<FileMutation>(`/trash/${encodeURIComponent(entryId)}/restore`, {
      method: "POST",
    });
  },

  purgeTrash(entryId: string): Promise<void> {
    return request<void>(`/trash/${encodeURIComponent(entryId)}`, {
      method: "DELETE",
    });
  },
};
