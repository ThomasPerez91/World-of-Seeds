/// <reference types="node" />
import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import postcss from "postcss";

function readStyles(directory: string, prefix = "."): Record<string, string> {
  return Object.assign({}, ...readdirSync(directory, { withFileTypes: true }).map((entry) => {
    const path = join(directory, entry.name);
    const key = `${prefix}/${entry.name}`;
    return entry.isDirectory() ? readStyles(path, key) : entry.name.endsWith(".css") ? { [key]: readFileSync(path, "utf8") } : {};
  }));
}
const stylesheets = readStyles(join(process.cwd(), "src"));

const cascade = [
  "styles", "features/admin/admin", "production-hotfix", "wos-premium",
  "wos-premium-torrents", "wos-2-1", "wos-2-1-final",
  "admin-settings-parity", "toast-system", "responsive-mobile", "admin-layout",
];

afterEach(() => {
  document.querySelectorAll("style[data-cascade-test]").forEach((element) => element.remove());
  document.body.innerHTML = "";
});

// jsdom has no layout engine. Resolve width media queries explicitly, then check
// the complete stylesheet cascade; pixel geometry still requires a real browser.
function mountCascade(width: number) {
  const style = document.createElement("style");
  style.dataset.cascadeTest = "true";
  style.textContent = cascade.map((name) => {
    const root = postcss.parse(stylesheets[`./${name}.css`]);
    root.walkAtRules("media", (rule) => {
      const query = rule.params;
      const minimum = /min-width:\s*(\d+)px/.exec(query);
      const maximum = /max-width:\s*(\d+)px/.exec(query);
      const matches = !/prefers-|forced-colors|hover:|pointer:|orientation:/.test(query)
        && (minimum === null || width >= Number(minimum[1]))
        && (maximum === null || width <= Number(maximum[1]));
      if (matches) rule.replaceWith(...(rule.nodes ?? []));
      else rule.remove();
    });
    return root.toString();
  }).join("\n");
  document.head.append(style);
  document.body.innerHTML = `<section class="admin-page" data-admin-view="admin-users">
    <table class="admin-runtime-table admin-users-table"><tbody><tr class="user-row"><td data-label="Utilisateur">Alice</td></tr></tbody></table>
    <input type="checkbox" class="admin-checkbox"><div class="options-actions"><button class="ui-button">Enregistrer</button></div>
  </section><section class="user-downloads"><div class="torrent-ready-overview"></div></section>`;
}

describe("CSS compilation boundaries", () => {
  it("uses only the Forest / Green palette at every viewport and system preference", () => {
    for (const [path, source] of Object.entries(stylesheets)) {
      const root = postcss.parse(source, { from: path });
      root.walkRules((rule) => {
        expect(rule.selector, path).not.toContain("data-theme");
      });
      root.walkAtRules("media", (rule) => {
        expect(rule.params, path).not.toContain("prefers-color-scheme");
      });
    }

    const rootRules = postcss.parse(stylesheets["./styles.css"]).nodes;
    const forest = rootRules.find((node) => node.type === "rule" && node.selector === ":root");
    expect(forest?.toString()).toContain("color-scheme: dark");
    expect(forest?.toString()).toContain("--color-background: #202925");
    expect(stylesheets["./wos-premium.css"]).toContain("--wos-bg: #07150f");
    expect(stylesheets["./wos-2-1-final.css"]).toContain("--wos21-login-input-bg: #06271b");
    expect(stylesheets["./wos-2-1-final.css"]).toContain("prefers-reduced-motion: reduce");
  });

  it.each(Object.entries(stylesheets))("parses %s without implicit block recovery", (path, source) => {
    expect(() => postcss.parse(source, { from: path })).not.toThrow();
  });
  it("keeps shared styles outside mobile media queries", () => {
    const root = postcss.parse(stylesheets["./styles.css"]);
    root.walkAtRules("media", (rule) => {
      expect(rule.parent?.type, `${rule.params} must not be nested in a mobile query`).toBe("root");
    });
  });

  it.each([1024, 1440])("keeps users as a fixed desktop table at %d px", (width) => {
    mountCascade(width);
    expect(getComputedStyle(document.querySelector(".user-row")!).display).toBe("table-row");
    expect(getComputedStyle(document.querySelector("td")!).display).toBe("table-cell");
    expect(getComputedStyle(document.querySelector("table")!).tableLayout).toBe("fixed");
    expect(getComputedStyle(document.querySelector("table")!).minWidth).toBe("0px");
  });

  it.each([320, 390, 820])("uses mobile cards and bounded controls at %d px", (width) => {
    mountCascade(width);
    expect(getComputedStyle(document.querySelector(".user-row")!).display).toBe("grid");
    expect(getComputedStyle(document.querySelector(".admin-checkbox")!).width).toBe("1.25rem");
    expect(getComputedStyle(document.querySelector(".options-actions button")!).flexGrow).toBe("0");
    expect(getComputedStyle(document.querySelector(".torrent-ready-overview")!).gridTemplateColumns).toBe("minmax(0, 1fr)");
  });
});
