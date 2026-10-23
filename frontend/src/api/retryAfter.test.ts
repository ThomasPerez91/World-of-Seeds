import { describe, expect, it } from "vitest";

import { ApiError, parseRetryAfter } from "./client";

describe("parseRetryAfter", () => {
  it("reads seconds, HTTP dates and ignores anything else", () => {
    const now = Date.parse("2026-10-24T12:00:00Z");
    expect(parseRetryAfter("2", now)).toBe(2_000);
    expect(parseRetryAfter("0", now)).toBe(0);
    expect(parseRetryAfter("Sat, 24 Oct 2026 12:00:30 GMT", now)).toBe(30_000);
    expect(parseRetryAfter("Sat, 24 Oct 2026 11:00:00 GMT", now)).toBe(0);
    expect(parseRetryAfter("3600", now)).toBe(60_000);
    expect(parseRetryAfter("-5", now)).toBeNull();
    expect(parseRetryAfter("soon", now)).toBeNull();
    expect(parseRetryAfter("", now)).toBeNull();
    expect(parseRetryAfter(null, now)).toBeNull();
  });

  it("is exposed by ApiError without changing the existing constructor", () => {
    expect(new ApiError(503, "busy", "busy_code", null, 1_500).retryAfterMs).toBe(1_500);
    expect(new ApiError(500, "failed").retryAfterMs).toBeNull();
  });
});
