import { useI18n, type Locale } from "../i18n";

function LanguageFlag({ locale }: { locale: Locale }) {
  if (locale === "fr") {
    return (
      <svg
        className="language-flag"
        viewBox="0 0 3 2"
        aria-hidden="true"
        focusable="false"
        data-language-flag="fr"
      >
        <path fill="#002654" d="M0 0h1v2H0z" />
        <path fill="#fff" d="M1 0h1v2H1z" />
        <path fill="#ed2939" d="M2 0h1v2H2z" />
      </svg>
    );
  }

  return (
    <svg
      className="language-flag"
      viewBox="0 0 60 36"
      aria-hidden="true"
      focusable="false"
      data-language-flag="en"
    >
      <path fill="#012169" d="M0 0h60v36H0z" />
      <path stroke="#fff" strokeWidth="8" d="m0 0 60 36M60 0 0 36" />
      <path stroke="#c8102e" strokeWidth="4" d="m0 0 60 36M60 0 0 36" />
      <path stroke="#fff" strokeWidth="12" d="M30 0v36M0 18h60" />
      <path stroke="#c8102e" strokeWidth="7" d="M30 0v36M0 18h60" />
    </svg>
  );
}

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
        <LanguageFlag locale={locale} />
      </button>
    );
  }

  return (
    <label className="language-selector">
      <span>{t("language.label")}</span>
      <span className="language-select-control">
        <LanguageFlag locale={locale} />
        <select
          aria-label={t("language.label")}
          value={locale}
          disabled={disabled}
          onChange={(event) => change(event.target.value as Locale)}
        >
          <option value="fr">{t("language.fr")}</option>
          <option value="en">{t("language.en")}</option>
        </select>
      </span>
    </label>
  );
}
