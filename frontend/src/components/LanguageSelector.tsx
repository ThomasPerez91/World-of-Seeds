import { useI18n, type Locale } from "../i18n";

export function LanguageSelector({
  disabled = false,
  onChange,
}: {
  disabled?: boolean;
  onChange?: (locale: Locale) => void | Promise<void>;
}) {
  const { locale, setLocale, t } = useI18n();

  function change(nextLocale: Locale) {
    setLocale(nextLocale);
    void onChange?.(nextLocale);
  }

  return (
    <label className="language-selector">
      <span>{t("language.label")}</span>
      <select
        value={locale}
        disabled={disabled}
        onChange={(event) => change(event.target.value as Locale)}
      >
        <option value="fr">{t("language.fr")}</option>
        <option value="en">{t("language.en")}</option>
      </select>
    </label>
  );
}
