import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { LanguageSelector } from "./components/LanguageSelector";
import { ApiError } from "./api/client";
import { I18nProvider, useI18n } from "./i18n";

function Probe() {
  const { apiError, formatBytes, formatDate, formatNumber, t } = useI18n();
  return (
    <>
      <LanguageSelector />
      <span>{t("login.title")}</span>
      <span>{formatBytes(1536)}</span>
      <span>{formatNumber(1234.5)}</span>
      <span>{formatDate("2026-08-29T13:05:00Z", { dateStyle: "short" })}</span>
      <span>{apiError(new ApiError(429, "Limite atteinte.", "torrent_limit_reached"), "downloads.uploadRetry")}</span>
    </>
  );
}

function FlagProbe() {
  return <LanguageSelector variant="flag-toggle" />;
}

describe("I18nProvider", () => {
  it("bascule toute l’interface et les formats Intl en anglais puis persiste le choix", async () => {
    const user = userEvent.setup();
    render(
      <I18nProvider>
        <Probe />
      </I18nProvider>,
    );

    expect(screen.getByText("Connexion")).toBeTruthy();
    expect(document.querySelector('[data-country-code="FR"] svg')).toBeTruthy();
    expect(screen.getByText("1,5 Ko")).toBeTruthy();
    expect(screen.getByText("Le nombre maximal de téléchargements actifs est atteint.")).toBeTruthy();
    await user.selectOptions(screen.getByRole("combobox", { name: "Langue" }), "en");

    expect(screen.getByText("Sign in")).toBeTruthy();
    expect(document.querySelector('[data-country-code="GB"] svg')).toBeTruthy();
    expect(screen.getByText("1.5 KB")).toBeTruthy();
    expect(screen.getByText("1,234.5")).toBeTruthy();
    expect(screen.getByText("The maximum number of active downloads has been reached.")).toBeTruthy();
    expect(document.documentElement.lang).toBe("en");
    expect(window.localStorage.getItem("wos.preferred-locale")).toBe("en");
  });

  it("bascule le login avec le bouton drapeau compact", async () => {
    window.localStorage.setItem("wos.preferred-locale", "fr");
    const user = userEvent.setup();
    render(
      <I18nProvider>
        <FlagProbe />
      </I18nProvider>,
    );

    const frenchButton = screen.getByRole("button", { name: "Langue : Français" });
    expect(frenchButton.querySelector('[data-country-code="FR"] svg')).toBeTruthy();
    expect(frenchButton.textContent).not.toMatch(/🇫🇷|🇬🇧/u);
    await user.click(frenchButton);

    const englishButton = screen.getByRole("button", { name: "Language : English" });
    expect(englishButton.querySelector('[data-country-code="GB"] svg')).toBeTruthy();
    expect(englishButton.textContent).not.toMatch(/🇫🇷|🇬🇧/u);
    expect(document.documentElement.lang).toBe("en");
    expect(window.localStorage.getItem("wos.preferred-locale")).toBe("en");
  });
});
