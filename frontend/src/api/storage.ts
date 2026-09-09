import { ApiError } from "./client";

export interface SharedStorageCapacity {
  total_bytes: number;
  used_bytes: number;
  available_bytes: number;
}

interface BusinessErrorDetail {
  code?: unknown;
  message?: unknown;
  field?: unknown;
}

export async function getSharedStorageCapacity(signal?: AbortSignal): Promise<SharedStorageCapacity> {
  const response = await fetch("/api/v2/storage", {
    headers: { Accept: "application/json" },
    credentials: "same-origin",
    signal,
  });
  if (!response.ok) {
    let message = "Une erreur est survenue.";
    let code: string | null = null;
    let field: string | null = null;
    try {
      const body = (await response.json()) as { detail?: string | BusinessErrorDetail };
      if (typeof body.detail === "string") {
        message = body.detail;
      } else if (typeof body.detail === "object" && body.detail !== null) {
        if (typeof body.detail.message === "string") message = body.detail.message;
        if (typeof body.detail.code === "string") code = body.detail.code;
        if (typeof body.detail.field === "string") field = body.detail.field;
      }
    } catch {
      // Keep the generic message for non-JSON failures.
    }
    throw new ApiError(response.status, message, code, field);
  }
  return (await response.json()) as SharedStorageCapacity;
}
