import { describe, expect, it } from "vitest";

import { UI_VERSION } from "./uiVersion";

describe("UI_VERSION", () => {
  it("expose la version visuelle World of Seeds 2.1", () => {
    expect(UI_VERSION).toBe("2.1.0");
  });
});
