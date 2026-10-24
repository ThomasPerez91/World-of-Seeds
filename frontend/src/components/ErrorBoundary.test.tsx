import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ErrorBoundary } from "./ErrorBoundary";

function Broken(): never {
  throw new Error("render failure");
}

describe("ErrorBoundary", () => {
  afterEach(() => {
    document.documentElement.lang = "";
    vi.restoreAllMocks();
  });

  it("renders its children while nothing fails", () => {
    render(<ErrorBoundary><p>contenu</p></ErrorBoundary>);
    expect(screen.getByText("contenu")).toBeTruthy();
  });

  it("replaces a crashed tree with a recoverable message", () => {
    const log = vi.spyOn(console, "error").mockImplementation(() => undefined);
    render(<ErrorBoundary><Broken /></ErrorBoundary>);

    expect(screen.getByRole("alert").textContent).toContain("Une erreur est survenue");
    expect(screen.getByRole("button", { name: "Recharger la page" })).toBeTruthy();
    expect(log).toHaveBeenCalledWith("ui_render_failed", expect.any(Error), expect.any(String));
  });

  it("follows the document language", () => {
    document.documentElement.lang = "en";
    vi.spyOn(console, "error").mockImplementation(() => undefined);
    render(<ErrorBoundary><Broken /></ErrorBoundary>);

    expect(screen.getByRole("button", { name: "Reload the page" })).toBeTruthy();
  });
});
