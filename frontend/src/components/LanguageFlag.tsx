import { FR, GB } from "country-flag-icons/react/3x2";

import type { Locale } from "../i18n";

export function LanguageFlag({ locale }: { locale: Locale }) {
  const Flag = locale === "fr" ? FR : GB;
  const countryCode = locale === "fr" ? "FR" : "GB";

  return (
    <span className="language-flag" aria-hidden="true" data-country-code={countryCode}>
      <Flag focusable="false" />
    </span>
  );
}
