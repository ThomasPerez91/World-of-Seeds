import { describe, expect, it } from "vitest";

import {
  RECONNECT_MAX_MS,
  isRetryableUploadStatus,
  isTerminalUploadCode,
  reconnectDelay,
  uploadRetryDelay,
} from "./resilience";

describe("reconnectDelay", () => {
  it("grows exponentially with jitter and never exceeds the ceiling", () => {
    expect(reconnectDelay(0, () => 1)).toBe(1_000);
    expect(reconnectDelay(0, () => 0)).toBe(500);
    expect(reconnectDelay(3, () => 1)).toBe(8_000);
    expect(reconnectDelay(3, () => 0)).toBe(4_000);
    expect(reconnectDelay(20, () => 1)).toBe(RECONNECT_MAX_MS);
    expect(reconnectDelay(20, () => 0)).toBe(RECONNECT_MAX_MS / 2);
    expect(reconnectDelay(-5, () => 1)).toBe(1_000);
  });
});

describe("upload retry policy", () => {
  it("retries only busy answers and honours Retry-After within a bound", () => {
    expect(isRetryableUploadStatus(503)).toBe(true);
    expect(isRetryableUploadStatus(429)).toBe(true);
    expect(isRetryableUploadStatus(422)).toBe(false);
    expect(uploadRetryDelay(null, 1)).toBe(1_000);
    expect(uploadRetryDelay(null, 3)).toBe(4_000);
    expect(uploadRetryDelay(7_000, 1)).toBe(7_000);
    expect(uploadRetryDelay(120_000, 1)).toBe(15_000);
  });

  it("recognises the account-level refusals that end a batch", () => {
    expect(isTerminalUploadCode("torrent_limit_reached")).toBe(true);
    expect(isTerminalUploadCode("user_quota_exceeded")).toBe(true);
    expect(isTerminalUploadCode("torrent_invalid")).toBe(false);
    expect(isTerminalUploadCode(null)).toBe(false);
  });
});
