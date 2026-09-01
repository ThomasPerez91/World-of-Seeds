export interface User {
  id: string;
  username: string;
  is_admin: boolean;
  is_active: boolean;
  must_change_credentials: boolean;
  preferred_locale?: "fr" | "en";
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
  retention_expires_at: string | null;
  queue_position_estimate: number | null;
  queue_total_estimate: number | null;
  queue_status: "waiting" | "downloading" | "stalled" | "cooldown" | null;
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

export type TorrentRealtimeEventType =
  | "torrent.requested"
  | "torrent.started"
  | "torrent.paused"
  | "torrent.stalled"
  | "torrent.resumed"
  | "torrent.ready"
  | "torrent.retention_extended"
  | "torrent.queue_changed"
  | "torrent.failed"
  | "torrent.cancelled"
  | "torrent.expired";

export type TorrentRealtimeMessage =
  | { type: Exclude<TorrentRealtimeEventType, "torrent.queue_changed">; request_id: string; occurred_at: string }
  | { type: "torrent.queue_changed"; occurred_at: string }
  | { type: "heartbeat" | "resync_required" };

const torrentRealtimeEventTypes = new Set<TorrentRealtimeEventType>([
  "torrent.requested",
  "torrent.started",
  "torrent.paused",
  "torrent.stalled",
  "torrent.resumed",
  "torrent.ready",
  "torrent.retention_extended",
  "torrent.queue_changed",
  "torrent.failed",
  "torrent.cancelled",
  "torrent.expired",
]);

export function parseTorrentRealtimeMessage(data: unknown): TorrentRealtimeMessage | null {
  if (typeof data !== "string" || data.length > 512) return null;
  let payload: unknown;
  try {
    payload = JSON.parse(data);
  } catch {
    return null;
  }
  if (typeof payload !== "object" || payload === null || !("type" in payload)) return null;
  const record = payload as Record<string, unknown>;
  if (record.type === "heartbeat" || record.type === "resync_required") {
    return Object.keys(record).length === 1 ? { type: record.type } : null;
  }
  if (record.type === "torrent.queue_changed") {
    if (
      typeof record.occurred_at !== "string"
      || !Number.isFinite(Date.parse(record.occurred_at))
      || Object.keys(record).length !== 2
    ) return null;
    return { type: record.type, occurred_at: record.occurred_at };
  }
  if (
    typeof record.type !== "string" ||
    !torrentRealtimeEventTypes.has(record.type as TorrentRealtimeEventType) ||
    typeof record.request_id !== "string" ||
    !/^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(record.request_id) ||
    typeof record.occurred_at !== "string" ||
    !Number.isFinite(Date.parse(record.occurred_at)) ||
    Object.keys(record).length !== 3
  ) return null;
  return {
    type: record.type as TorrentRealtimeEventType,
    request_id: record.request_id,
    occurred_at: record.occurred_at,
  };
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
  retention_expires_at: string | null;
  offset: number;
  limit: number;
  items: TorrentDownloadFileV2[];
}

/** A recursively consumed manifest may span pages; the compatibility UI stores one page only. */
export type TorrentDownloadSnapshotV2 = TorrentDownloadManifestPageV2;

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
  service_controls_available: boolean;
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

export interface CentralAdminOverview extends OptionsResponse {
  service_controls_available: boolean;
  scheduler: {
    desired_generation: number;
    applied_generation: number;
    synchronized: boolean;
    rounds: number;
    lease_active: boolean;
  };
  storage: {
    managed_bytes: number;
    logical_bytes: number;
    disk_total_bytes: number;
    disk_free_bytes: number;
    pressure: "normal" | "warning" | "critical";
    managed_quota_bytes: number;
    user_quota_bytes: number;
  };
  audit: Array<{
    key: string;
    version: number;
    old_value: unknown;
    new_value: unknown;
    actor: string | null;
    source: string;
    changed_at: string;
  }>;
}

export interface AdminReconciliationReport {
  database_scanned: number;
  qbittorrent_scanned: number;
  storage_scanned: number;
  external_torrents: number;
  anomalies: Array<{
    code: string;
    severity: "info" | "warning" | "critical";
    resource_id: string | null;
    action: string;
  }>;
  truncated: boolean;
  next_cursor: string | null;
}

interface BusinessErrorDetail {
  code: string;
  message?: string;
  field?: string | null;
}

function isBusinessErrorDetail(value: unknown): value is BusinessErrorDetail {
  if (typeof value !== "object" || value === null) return false;
  const candidate = value as Partial<BusinessErrorDetail>;
  return typeof candidate.code === "string";
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
        message = body.detail.message ?? message;
        code = body.detail.code;
        field = body.detail.field ?? null;
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

  async changeLocale(preferredLocale: "fr" | "en"): Promise<User> {
    const response = await request<AuthResponse>("/auth/locale", {
      method: "PATCH",
      body: JSON.stringify({ preferred_locale: preferredLocale }),
    });
    return response.user;
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

  getCentralAdminOverview(): Promise<CentralAdminOverview> {
    return requestV2<CentralAdminOverview>("/admin/overview");
  },

  updateCentralAdminOptions(
    changes: Record<string, OptionValue>,
  ): Promise<CentralAdminOverview> {
    return requestV2<CentralAdminOverview>("/admin/options", {
      method: "PATCH",
      body: JSON.stringify({ changes }),
    });
  },

  getAdminReconciliation(limit = 100, cursor?: string): Promise<AdminReconciliationReport> {
    const query = new URLSearchParams({ limit: String(limit) });
    if (cursor) query.set("cursor", cursor);
    return requestV2<AdminReconciliationReport>(`/admin/reconciliation?${query.toString()}`);
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

  openTorrentEventsV2(): WebSocket {
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    return new WebSocket(`${protocol}//${window.location.host}/api/v2/torrents/events`);
  },

  cancelTorrentRequestV2(torrentRequestId: string): Promise<void> {
    return requestV2<void>(`/torrents/${encodeURIComponent(torrentRequestId)}`, {
      method: "DELETE",
    });
  },

  getTorrentDownloadManifestPageV2(
    torrentRequestId: string,
    offset = 0,
    snapshot: string | null = null,
    signal?: AbortSignal,
    limit = 500,
  ): Promise<TorrentDownloadManifestPageV2> {
    const search = new URLSearchParams({ offset: String(offset), limit: String(limit) });
    if (snapshot !== null) search.set("snapshot", snapshot);
    return requestV2<TorrentDownloadManifestPageV2>(
      `/torrents/${encodeURIComponent(torrentRequestId)}/download-manifest?${search.toString()}`,
      { signal },
    );
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
