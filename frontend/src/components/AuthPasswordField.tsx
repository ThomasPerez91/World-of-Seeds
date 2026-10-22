import { useState } from "react";
import { Eye, EyeOff, LockKeyhole } from "lucide-react";
import { useI18n } from "../i18n";

export function AuthPasswordField({ id, label, invalid, minLength, describedBy = "credential-error" }: {
  id: string;
  label: string;
  invalid: boolean;
  minLength?: number;
  describedBy?: string;
}) {
  const { t } = useI18n();
  const [visible, setVisible] = useState(false);
  return (
    <div className="credential-field">
      <label htmlFor={id}>{label}</label>
      <div className="login-input login-password-input">
        <LockKeyhole aria-hidden="true" />
        <input
          id={id}
          name={id}
          type={visible ? "text" : "password"}
          autoComplete={id === "current-password" ? "current-password" : "new-password"}
          minLength={minLength}
          maxLength={256}
          aria-describedby={describedBy}
          aria-invalid={invalid}
          required
        />
        <button
          type="button"
          className="password-visibility"
          aria-label={visible ? t("login.hidePassword") : t("login.showPassword")}
          aria-controls={id}
          aria-pressed={visible}
          onClick={() => setVisible((value) => !value)}
        >
          {visible ? <EyeOff aria-hidden="true" /> : <Eye aria-hidden="true" />}
        </button>
      </div>
    </div>
  );
}
