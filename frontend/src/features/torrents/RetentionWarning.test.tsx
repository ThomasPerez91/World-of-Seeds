import { act, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { I18nProvider } from "../../i18n";
import { auditAccessibility } from "../../test/accessibility";
import {
  RETENTION_DANGER_MS,
  RETENTION_WARNING_MS,
  RetentionWarning,
  retentionWarningPresentation,
} from "./RetentionWarning";

const NOW = Date.parse("2026-09-04T14:30:00Z");

function deadline(remainingMs: number): string {
  return new Date(NOW + remainingMs).toISOString();
}

afterEach(() => vi.useRealTimers());

describe("RetentionWarning", () => {
  it.each([
    [RETENTION_WARNING_MS + 1_000, "normal"],
    [RETENTION_WARNING_MS, "warning"],
    [RETENTION_DANGER_MS + 1_000, "warning"],
    [RETENTION_DANGER_MS, "danger"],
    [60 * 60 * 1_000, "danger"],
    [5 * 60 * 1_000, "danger"],
    [0, "elapsed"],
  ] as const)("classe la borne %d ms en %s", (remainingMs, expected) => {
    expect(retentionWarningPresentation(deadline(remainingMs), NOW)?.tier).toBe(expected);
  });

  it("franchit localement la borne exacte de 48 h sans requête serveur", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(NOW);
    const view = render(
      <I18nProvider>
        <RetentionWarning retentionExpiresAt={deadline(RETENTION_WARNING_MS + 1_000)} />
      </I18nProvider>,
    );

    expect(screen.queryByTestId("retention-warning")).toBeNull();
    await act(async () => { await vi.advanceTimersByTimeAsync(1_000); });

    const warning = screen.getByTestId("retention-warning");
    expect(warning.classList.contains("warning")).toBe(true);
    expect(warning.textContent).toContain("Suppression automatique dans 2 j");
    expect(warning.textContent).toContain("Expiration le");
    expect(view.container.querySelector("svg[aria-hidden='true']")).toBeTruthy();
    vi.useRealTimers();
    expect(await auditAccessibility(view.container)).toMatchObject({ violations: [] });
  });

  it("remplace immédiatement une ancienne échéance lors d’une resynchronisation", () => {
    vi.useFakeTimers();
    vi.setSystemTime(NOW);
    const view = render(
      <I18nProvider>
        <RetentionWarning retentionExpiresAt={deadline(9 * 60 * 60 * 1_000)} />
      </I18nProvider>,
    );
    expect(screen.getByText("Suppression automatique dans 9 h")).toBeTruthy();

    view.rerender(
      <I18nProvider>
        <RetentionWarning retentionExpiresAt={deadline(72 * 60 * 60 * 1_000)} />
      </I18nProvider>,
    );
    expect(screen.queryByTestId("retention-warning")).toBeNull();
  });

  it("affiche l’urgence en anglais sans aria-live répétitif", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(NOW);
    window.localStorage.setItem("wos.preferred-locale", "en");
    const view = render(
      <I18nProvider>
        <RetentionWarning retentionExpiresAt={deadline(45 * 60 * 1_000)} />
      </I18nProvider>,
    );

    expect(screen.getByText("Deletion imminent in 45 min")).toBeTruthy();
    expect(screen.getByText(/Expires on/)).toBeTruthy();
    expect(view.container.querySelector("[aria-live]")).toBeNull();
    expect(view.container.querySelector(".retention-warning.danger")).toBeTruthy();
    vi.useRealTimers();
    expect(await auditAccessibility(view.container)).toMatchObject({ violations: [] });
  });

  it("n’affiche jamais de compte à rebours négatif", () => {
    vi.useFakeTimers();
    vi.setSystemTime(NOW);
    render(
      <I18nProvider>
        <RetentionWarning retentionExpiresAt={deadline(-1)} />
      </I18nProvider>,
    );
    expect(screen.queryByTestId("retention-warning")).toBeNull();
  });
});
