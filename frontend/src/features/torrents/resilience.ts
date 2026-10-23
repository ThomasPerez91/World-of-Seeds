// Pure policies shared by the downloads page: kept apart so they can be tested without a DOM.

export const RECONNECT_BASE_MS = 1_000;
export const RECONNECT_MAX_MS = 30_000;
// A connection that stayed open this long is considered healthy again.
export const RECONNECT_STABLE_MS = 10_000;

/** Exponential back-off with equal jitter: spreads clients after a server restart. */
export function reconnectDelay(attempt: number, random: () => number = Math.random): number {
  const ceiling = Math.min(RECONNECT_MAX_MS, RECONNECT_BASE_MS * 2 ** Math.max(0, attempt));
  return Math.round(ceiling / 2 + (ceiling / 2) * random());
}

export const UPLOAD_MAX_RETRIES = 3;
const UPLOAD_RETRY_MAX_MS = 15_000;

/** 429/503 mean "busy, try again": the server tells how long through Retry-After. */
export function isRetryableUploadStatus(status: number): boolean {
  return status === 429 || status === 503;
}

export function uploadRetryDelay(retryAfterMs: number | null, attempt: number): number {
  const backoff = 1_000 * 2 ** Math.max(0, attempt - 1);
  return Math.min(UPLOAD_RETRY_MAX_MS, Math.max(backoff, retryAfterMs ?? 0));
}

// Account-level refusals: every remaining file of the batch would fail the same way.
const TERMINAL_UPLOAD_CODES: ReadonlySet<string> = new Set([
  "torrent_limit_reached",
  "user_quota_exceeded",
  "managed_quota_exceeded",
  "disk_pressure_critical",
]);

export function isTerminalUploadCode(code: string | null): boolean {
  return code !== null && TERMINAL_UPLOAD_CODES.has(code);
}

export function sleep(milliseconds: number): Promise<void> {
  return new Promise((resolve) => window.setTimeout(resolve, milliseconds));
}
