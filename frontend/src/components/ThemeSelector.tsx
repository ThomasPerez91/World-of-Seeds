import { useI18n } from "../i18n";
import { useTheme, type Theme } from "../theme";
import { Button } from "./ui";

export function ThemeSelector() {
  const { t } = useI18n();
  const { preferredTheme, setTheme, saving, saveFailed } = useTheme();
  return (
    <div className="theme-selector">
      <div role="group" aria-label={t("theme.label")} aria-busy={saving}>
        {(["light", "dark", "system"] as Theme[]).map((theme) => (
          <Button
            key={theme}
            variant="ghost"
            aria-pressed={preferredTheme === theme}
            aria-disabled={saving}
            onClick={() => {
              if (!saving) void setTheme(theme);
            }}
          >
            {t(`theme.${theme}`)}
          </Button>
        ))}
      </div>
      {saving && <p role="status" className="field-hint">{t("credentials.submitting")}</p>}
      {saveFailed && <p role="alert" className="error-message">{t("theme.saveFailed")}</p>}
    </div>
  );
}
