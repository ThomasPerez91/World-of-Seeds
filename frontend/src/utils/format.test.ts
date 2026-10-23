import { describe, expect, it } from "vitest";

import { formatBytes } from "./format";

describe("formatBytes", () => {
  it("formats regular sizes", () => {
    expect(formatBytes(0, "—", "en-US")).toBe("0 B");
    expect(formatBytes(1536, "—", "en-US")).toBe("1.5 KB");
    expect(formatBytes(150 * 1024 ** 3, "—", "en-US")).toBe("150 GB");
    expect(formatBytes(2048, "—", "fr-FR")).toContain("Ko");
  });

  it("never prints NaN or a negative size", () => {
    expect(formatBytes(null)).toBe("—");
    expect(formatBytes(-1)).toBe("—");
    expect(formatBytes(Number.NaN)).toBe("—");
    expect(formatBytes(Number.POSITIVE_INFINITY, "n/a")).toBe("n/a");
    expect(formatBytes(0.4, "—", "en-US")).toBe("0 B");
  });

  it("promotes to the next unit instead of printing 1024", () => {
    expect(formatBytes(1024 ** 2 - 40, "—", "en-US")).toBe("1 MB");
    expect(formatBytes(1024 - 0.2, "—", "en-US")).toBe("1 KB");
  });
});
