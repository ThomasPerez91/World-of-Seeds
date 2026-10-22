import { useState, type FormEvent } from "react";
import { api } from "../api/client";
import { useI18n } from "../i18n";
import { AuthBrandRow } from "./AuthBrandRow";
import { AuthPasswordField } from "./AuthPasswordField";
import { LegalLinks, type LegalDocument } from "./LegalPage";
import { Button } from "./ui";

export const DISCORD_RECOVERY_URL = "https://discord.com/channels/1549418373308358727/1549532212402196511";

// The fragment is not sent to the server. AppContent removes it from history immediately.
export function readResetToken(): string | null {
  return new URLSearchParams(window.location.hash.slice(1)).get("reset-password");
}

export function PasswordResetScreen({ token, onBack, onCompleted, onOpenLegal }: {
  token: string | null;
  onBack: () => void;
  onCompleted: () => void;
  onOpenLegal: (document: LegalDocument) => void;
}) {
  const { t, apiError } = useI18n();
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [complete, setComplete] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (submitting || token === null) return;
    const values = new FormData(event.currentTarget);
    const password = String(values.get("reset-new-password"));
    if (password !== String(values.get("reset-confirmation"))) {
      setError(t("credentials.mismatch"));
      return;
    }
    setSubmitting(true);
    setError("");
    try {
      await api.confirmPasswordReset(token, password);
      setComplete(true);
      onCompleted();
    } catch (caught) {
      setError(apiError(caught, "error.temporary"));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main className="login-page credential-page">
      <section className="login-card credential-change-card" aria-labelledby="reset-title">
        <AuthBrandRow disabled={submitting} />
        <div className="login-divider" />
        <header className="credential-heading">
          <h1 id="reset-title">{t(complete ? "reset.successTitle" : "reset.title")}</h1>
          <p className="form-intro">{t(complete ? "reset.success" : token === null ? "reset.instructions" : "reset.choose")}</p>
        </header>
        {complete ? <Button type="button" onClick={onBack}>{t("reset.back")}</Button> : token === null ? (
          <div className="password-reset-help">
            <a className="ui-button" href={DISCORD_RECOVERY_URL} target="_blank" rel="noopener noreferrer">{t("reset.discord")}</a>
            <p className="field-hint">{t("reset.support")}</p>
          </div>
        ) : (
          <form onSubmit={submit}>
            <AuthPasswordField id="reset-new-password" label={t("credentials.newPassword")} invalid={error !== ""} minLength={12} describedBy="reset-error" />
            <AuthPasswordField id="reset-confirmation" label={t("credentials.confirmPassword")} invalid={error !== ""} minLength={12} describedBy="reset-error" />
            <Button type="submit" disabled={submitting}>{t(submitting ? "credentials.submitting" : "reset.submit")}</Button>
            <p id="reset-error" className="form-message error-message" role="alert">{error}</p>
          </form>
        )}
        {!complete && <button type="button" className="auth-text-button" disabled={submitting} onClick={onBack}>{t("reset.back")}</button>}
        <LegalLinks onOpen={onOpenLegal} />
      </section>
    </main>
  );
}
