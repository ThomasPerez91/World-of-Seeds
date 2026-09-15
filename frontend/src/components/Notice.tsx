import {
  CloseIcon,
  ErrorIcon,
  InfoIcon,
  LoadingIcon,
  SuccessIcon,
  WarningIcon,
} from "./icons";
import { useI18n } from "../i18n";
import { IconButton, Tooltip } from "./ui";

export type NoticeTone = "success" | "error" | "warning" | "info" | "progress";

const icons = {
  success: SuccessIcon,
  error: ErrorIcon,
  warning: WarningIcon,
  info: InfoIcon,
  progress: LoadingIcon,
};

export function Notice({
  message,
  onDismiss,
  onRetry,
  title,
  tone = "success",
}: {
  message: string;
  onDismiss: () => void;
  onRetry?: () => void;
  title?: string;
  tone?: NoticeTone;
}) {
  const { t } = useI18n();
  if (message === "") return null;
  const Icon = icons[tone];
  const heading = title ?? message;

  return (
    <div
      className={`operation-notice ${tone}`}
      role={tone === "error" ? "alert" : "status"}
      aria-live={tone === "error" ? "assertive" : "polite"}
      aria-atomic="true"
    >
      <Icon className={tone === "progress" ? "rotating" : undefined} />
      <div className="notice-copy">
        <strong>{heading}</strong>
        {title !== undefined && <span>{message}</span>}
      </div>
      {onRetry !== undefined && (
        <button type="button" className="notice-retry" onClick={onRetry}>
          {t("common.retry")}
        </button>
      )}
      <Tooltip content={t("common.close")} focusable={false} className="notice-dismiss-tooltip">
        <IconButton
          className="notice-dismiss"
          onClick={onDismiss}
          label={t("common.close")}
        >
          <CloseIcon />
        </IconButton>
      </Tooltip>
    </div>
  );
}
