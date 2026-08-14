import { CloseIcon } from "./icons";

export function Notice({ message, onDismiss }: { message: string; onDismiss: () => void }) {
  if (message === "") return null;

  return (
    <div className="operation-notice" role="status">
      <span>{message}</span>
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
