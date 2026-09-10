import { useI18n, type Locale } from "../i18n";

export function LanguageSelector({
  disabled = false,
  onChange,
  variant = "select",
}: {
  disabled?: boolean;
  onChange?: (locale: Locale) => void | Promise<void>;
  variant?: "select" | "flag-toggle";
}) {
  const { locale, setLocale, t } = useI18n();

  function change(nextLocale: Locale) {
    setLocale(nextLocale);
    void onChange?.(nextLocale);
  }

  if (variant === "flag-toggle") {
    const nextLocale: Locale = locale === "fr" ? "en" : "fr";
    const currentLabel = t(locale === "fr" ? "language.fr" : "language.en");
    return (
      <button
        type="button"
        className="language-flag-toggle"
        aria-label={`${t("language.label")} : ${currentLabel}`}
        disabled={disabled}
        onClick={() => change(nextLocale)}
      >
        <span aria-hidden="true">{locale === "fr" ? "🇫🇷" : "🇬🇧"}</span>
      </button>
    );
  }

  return (
    <label className="language-selector">
      <span>{t("language.label")}</span>
      <select
        aria-label={t("language.label")}
        value={locale}
        disabled={disabled}
        onChange={(event) => change(event.target.value as Locale)}
      >
        <option value="fr">🇫🇷  {t("language.fr")}</option>
        <option value="en">🇬🇧  {t("language.en")}</option>
      </select>
    </label>
  );
}
