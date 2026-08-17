import {
  CloseIcon,
  ErrorIcon,
  InfoIcon,
  LoadingIcon,
  SuccessIcon,
  WarningIcon,
} from "./icons";

export type NoticeTone = "success" | "error" | "warning" | "info" | "progress";

const icons = {
  success: SuccessIcon,
  error: ErrorIcon,
  warning: WarningIcon,
  info: InfoIcon,
  progress: LoadingIcon,
};

const defaultTitles = {
  success: "Opération réussie",
  error: "Action impossible",
  warning: "Attention",
  info: "Information",
  progress: "Opération en cours",
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
  if (message === "") return null;
  const Icon = icons[tone];

  return (
    <div
      className={`operation-notice ${tone}`}
      role={tone === "error" ? "alert" : "status"}
      aria-live={tone === "error" ? "assertive" : "polite"}
    >
      <Icon className={tone === "progress" ? "rotating" : undefined} />
      <div className="notice-copy">
        <strong>{title ?? defaultTitles[tone]}</strong>
        <span>{message}</span>
      </div>
      {onRetry !== undefined && (
        <button type="button" className="notice-retry" onClick={onRetry}>
          Réessayer
        </button>
      )}
      <button
        type="button"
        className="notice-dismiss"
        onClick={onDismiss}
        aria-label="Fermer le message"
      >
        <CloseIcon />
      </button>
    </div>
  );
}
