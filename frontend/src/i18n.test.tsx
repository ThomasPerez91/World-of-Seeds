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

describe("I18nProvider", () => {
  it("bascule toute l’interface et les formats Intl en anglais puis persiste le choix", async () => {
    const user = userEvent.setup();
    render(
      <I18nProvider>
        <Probe />
      </I18nProvider>,
    );

    expect(screen.getByText("Connexion")).toBeTruthy();
    expect(screen.getByText("1,5 Ko")).toBeTruthy();
    expect(screen.getByText("Le nombre maximal de téléchargements actifs est atteint.")).toBeTruthy();
    await user.selectOptions(screen.getByRole("combobox", { name: "Langue" }), "en");

    expect(screen.getByText("Sign in")).toBeTruthy();
    expect(screen.getByText("1.5 KB")).toBeTruthy();
    expect(screen.getByText("1,234.5")).toBeTruthy();
    expect(screen.getByText("The maximum number of active downloads has been reached.")).toBeTruthy();
    expect(document.documentElement.lang).toBe("en");
    expect(window.localStorage.getItem("wos.preferred-locale")).toBe("en");
  });
});
