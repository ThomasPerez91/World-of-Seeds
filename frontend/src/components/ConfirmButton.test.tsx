import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { CONFIRM_ARM_MS, ConfirmButton } from "./ConfirmButton";

function renderButton(onConfirm = vi.fn(), disabled = false) {
  const view = render(
    <ConfirmButton
      aria-label="Supprimer le film"
      armedChildren={<span data-testid="armed">confirmer</span>}
      disabled={disabled}
      hint="Clique de nouveau pour confirmer"
      onConfirm={onConfirm}
    >
      <span data-testid="idle">supprimer</span>
    </ConfirmButton>,
  );
  return { onConfirm, view };
}

describe("ConfirmButton", () => {
  afterEach(() => vi.useRealTimers());

  it("arms on the first click, announces it, and confirms on the second", () => {
    const { onConfirm } = renderButton();
    const button = screen.getByRole("button", { name: "Supprimer le film" });

    fireEvent.click(button);

    expect(onConfirm).not.toHaveBeenCalled();
    expect(screen.getByTestId("armed")).toBeTruthy();
    expect(screen.getByRole("status").textContent).toBe("Clique de nouveau pour confirmer");
    // The accessible name never changes and no toggle semantics are invented.
    expect(button.getAttribute("aria-pressed")).toBeNull();
    expect(screen.getByRole("button", { name: "Supprimer le film" })).toBe(button);

    fireEvent.click(button);
    expect(onConfirm).toHaveBeenCalledTimes(1);
    expect(screen.getByTestId("idle")).toBeTruthy();
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("disarms when the button loses focus", () => {
    const { onConfirm } = renderButton();
    const button = screen.getByRole("button", { name: "Supprimer le film" });

    fireEvent.click(button);
    fireEvent.blur(button);
    fireEvent.click(button);

    expect(onConfirm).not.toHaveBeenCalled();
    expect(screen.getByTestId("armed")).toBeTruthy();
  });

  it("disarms by itself after a few seconds", () => {
    vi.useFakeTimers();
    const { onConfirm } = renderButton();
    const button = screen.getByRole("button", { name: "Supprimer le film" });

    fireEvent.click(button);
    act(() => {
      vi.advanceTimersByTime(CONFIRM_ARM_MS + 10);
    });

    expect(screen.getByTestId("idle")).toBeTruthy();
    fireEvent.click(button);
    expect(onConfirm).not.toHaveBeenCalled();
  });

  it("never confirms while disabled", () => {
    const { onConfirm } = renderButton(vi.fn(), true);
    const button = screen.getByRole("button", { name: "Supprimer le film" });

    fireEvent.click(button);
    fireEvent.click(button);

    expect(onConfirm).not.toHaveBeenCalled();
  });
});
