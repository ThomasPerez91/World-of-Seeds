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

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    message: string,
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
  const method = init.method?.toUpperCase() ?? "GET";
  const headers = new Headers(init.headers);
  headers.set("Accept", "application/json");
  if (init.body !== undefined) {
    headers.set("Content-Type", "application/json");
  }
  if (!["GET", "HEAD", "OPTIONS"].includes(method)) {
    const csrfToken = readCookie("wos_csrf");
    if (csrfToken !== null) {
      headers.set("X-CSRF-Token", csrfToken);
    }
  }

  const response = await fetch(`/api/v1${path}`, {
    ...init,
    headers,
    credentials: "same-origin",
  });
  if (!response.ok) {
    let message = "Une erreur est survenue.";
    try {
      const body = (await response.json()) as { detail?: string };
      message = body.detail ?? message;
    } catch {
      // Keep the generic message for non-JSON failures.
    }
    throw new ApiError(response.status, message);
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

  renameFile(path: string, name: string): Promise<FileMutation> {
    return request<FileMutation>("/files/rename", {
      method: "PATCH",
      body: JSON.stringify({ path, name }),
    });
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
