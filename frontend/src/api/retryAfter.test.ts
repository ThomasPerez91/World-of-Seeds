import { describe, expect, it } from "vitest";

import { ApiError, parseRetryAfter, withReadTimeout } from "./client";

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

describe("withReadTimeout", () => {
  it("bounds reads and leaves mutations alone", () => {
    const read = withReadTimeout("GET", undefined);
    expect(read).toBeInstanceOf(AbortSignal);
    expect(read?.aborted).toBe(false);

    const caller = new AbortController();
    const combined = withReadTimeout("GET", caller.signal);
    expect(combined?.aborted).toBe(false);
    caller.abort();
    expect(combined?.aborted).toBe(true);

    expect(withReadTimeout("POST", undefined)).toBeUndefined();
    expect(withReadTimeout("POST", caller.signal)).toBe(caller.signal);
  });

  it("aborts a read that outlives its timeout", async () => {
    const signal = withReadTimeout("GET", undefined, 5);
    await new Promise((resolve) => setTimeout(resolve, 30));

    expect(signal?.aborted).toBe(true);
  });
});
