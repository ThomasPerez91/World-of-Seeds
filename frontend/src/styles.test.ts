import { describe, expect, it } from "vitest";
import postcss from "postcss";

const stylesheets = import.meta.glob<string>("./**/*.css", { query: "?raw", import: "default", eager: true });

describe("CSS compilation boundaries", () => {
  it.each(Object.entries(stylesheets))("parses %s without implicit block recovery", (path, source) => {
    expect(() => postcss.parse(source, { from: path })).not.toThrow();
  });
  it("keeps shared styles outside mobile media queries", () => {
    const root = postcss.parse(stylesheets["./styles.css"]);
    root.walkAtRules("media", (rule) => {
      expect(rule.parent?.type, `${rule.params} must not be nested in a mobile query`).toBe("root");
    });
  });
});
