import { act, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { I18nProvider } from "../../i18n";
import { auditAccessibility } from "../../test/accessibility";
import {
  SubscriptionExpiryIndicator,
  subscriptionExpiryPresentation,
} from "./SubscriptionExpiryIndicator";

const MINUTE_MS = 60_000;
const START = Date.parse("2026-09-12T08:00:00Z");
const TOTAL = 90 * MINUTE_MS;
const DEADLINE = START + TOTAL;

function atRemaining(remainingMs: number): number {
  return DEADLINE - remainingMs;
}

afterEach(() => vi.useRealTimers());

describe("SubscriptionExpiryIndicator", () => {
  it.each([
    [60 * MINUTE_MS + 1, "success"],
    [60 * MINUTE_MS, "warning"],
    [45 * MINUTE_MS, "warning"],
    [30 * MINUTE_MS, "warning"],
    [30 * MINUTE_MS - 1, "danger"],
  ] as const)("classe le ratio restant à la borne %d", (remainingMs, expected) => {
    expect(subscriptionExpiryPresentation(
      new Date(START).toISOString(),
      new Date(DEADLINE).toISOString(),
      atRemaining(remainingMs),
    )?.tier).toBe(expected);
  });

  it("ignore les échéances invalides, incohérentes ou déjà atteintes", () => {
    expect(subscriptionExpiryPresentation("invalid", new Date(DEADLINE).toISOString(), START)).toBeNull();
    expect(subscriptionExpiryPresentation(new Date(DEADLINE).toISOString(), new Date(START).toISOString(), START)).toBeNull();
    expect(subscriptionExpiryPresentation(new Date(START).toISOString(), new Date(DEADLINE).toISOString(), START - 1)).toBeNull();
    expect(subscriptionExpiryPresentation(new Date(START).toISOString(), new Date(DEADLINE).toISOString(), DEADLINE)).toBeNull();
  });

  it("rend une pill icon-only accessible avec un tooltip français", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(START);
    const view = render(
      <I18nProvider>
        <SubscriptionExpiryIndicator
          readyAt={new Date(START).toISOString()}
          unsubscribeAt={new Date(DEADLINE).toISOString()}
        />
      </I18nProvider>,
    );

    const indicator = screen.getByTestId("subscription-expiry-indicator");
    expect(indicator.classList.contains("success")).toBe(true);
    const accessibleIcon = screen.getByRole("img", { name: /Désabonnement automatique dans/ });
    accessibleIcon.focus();
    expect(document.activeElement).toBe(accessibleIcon);
    expect(screen.getByRole("tooltip").textContent).toMatch(/Désabonnement automatique dans.*—/);
    expect(view.container.querySelector("[title]")).toBeNull();
    vi.useRealTimers();
    expect(await auditAccessibility(view.container)).toMatchObject({ violations: [] });
  });

  it("change de couleur à la minute sans rechargement", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(atRemaining(60 * MINUTE_MS + 30_000));
    render(
      <I18nProvider>
        <SubscriptionExpiryIndicator
          readyAt={new Date(START).toISOString()}
          unsubscribeAt={new Date(DEADLINE).toISOString()}
        />
      </I18nProvider>,
    );

    expect(screen.getByTestId("subscription-expiry-indicator").classList.contains("success")).toBe(true);
    await act(async () => { await vi.advanceTimersByTimeAsync(MINUTE_MS); });
    expect(screen.getByTestId("subscription-expiry-indicator").classList.contains("warning")).toBe(true);
  });

  it("traduit le tooltip en anglais", () => {
    vi.useFakeTimers();
    vi.setSystemTime(atRemaining(15 * MINUTE_MS));
    window.localStorage.setItem("wos.preferred-locale", "en");
    render(
      <I18nProvider>
        <SubscriptionExpiryIndicator
          readyAt={new Date(START).toISOString()}
          unsubscribeAt={new Date(DEADLINE).toISOString()}
        />
      </I18nProvider>,
    );

    expect(screen.getByTestId("subscription-expiry-indicator").classList.contains("danger")).toBe(true);
    expect(screen.getByRole("img", { name: /Automatic unsubscribe in 15 min/ })).toBeTruthy();
  });
});
