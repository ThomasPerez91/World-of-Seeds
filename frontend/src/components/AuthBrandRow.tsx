import { LanguageSelector } from "./LanguageSelector";
import { type Locale } from "../i18n";

export function AuthBrandRow({
  disabled = false,
  onLocaleChange,
}: {
  disabled?: boolean;
  onLocaleChange?: (locale: Locale) => void | Promise<void>;
}) {
  return (
    <div className="login-brand-row">
      <img className="login-title-image" src="/title.webp?rev=a9c84cc" alt="" aria-hidden="true" />
      <LanguageSelector variant="flag-toggle" disabled={disabled} onChange={onLocaleChange} />
    </div>
  );
}
