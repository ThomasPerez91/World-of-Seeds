type ModalKind = "error" | "question" | "success" | "warning";

interface ModalOptions {
  cancelText?: string;
  confirmText: string;
  destructive?: boolean;
  kind: ModalKind;
  message: string;
  title: string;
}

let modalSequence = 0;

function showModal({
  cancelText,
  confirmText,
  destructive = false,
  kind,
  message,
  title,
}: ModalOptions): Promise<boolean> {
  const returnFocusTo =
    document.activeElement instanceof HTMLElement ? document.activeElement : null;
  const sequence = ++modalSequence;
  const titleId = `wos-modal-title-${sequence}`;
  const descriptionId = `wos-modal-description-${sequence}`;
  const overlay = document.createElement("div");
  overlay.className = "wos-modal-overlay";

  const modal = document.createElement("div");
  modal.className = `wos-modal ${kind}`;
  modal.setAttribute("role", "dialog");
  modal.setAttribute("aria-modal", "true");
  modal.setAttribute("aria-labelledby", titleId);
  modal.setAttribute("aria-describedby", descriptionId);

  const icon = document.createElement("span");
  icon.className = `wos-modal-icon ${kind}`;
  icon.setAttribute("aria-hidden", "true");
  icon.textContent = { error: "×", question: "?", success: "✓", warning: "!" }[kind];

  const heading = document.createElement("h2");
  heading.id = titleId;
  heading.textContent = title;

  const description = document.createElement("p");
  description.id = descriptionId;
  description.textContent = message;

  const actions = document.createElement("div");
  actions.className = "wos-modal-actions";
  const confirmButton = document.createElement("button");
  confirmButton.type = "button";
  confirmButton.className = `wos-alert-confirm${destructive ? " destructive" : ""}`;
  confirmButton.textContent = confirmText;
  const cancelButton = cancelText === undefined ? null : document.createElement("button");
  if (cancelButton !== null) {
    cancelButton.type = "button";
    cancelButton.className = "wos-alert-cancel";
    cancelButton.textContent = cancelText ?? "";
    actions.append(cancelButton);
  }
  actions.append(confirmButton);
  modal.append(icon, heading, description, actions);
  overlay.append(modal);
  document.body.append(overlay);

  return new Promise((resolve) => {
    let closed = false;
    const close = (confirmed: boolean) => {
      if (closed) return;
      closed = true;
      overlay.remove();
      if (returnFocusTo?.isConnected) returnFocusTo.focus();
      resolve(confirmed);
    };
    confirmButton.addEventListener("click", () => close(true));
    cancelButton?.addEventListener("click", () => close(false));
    overlay.addEventListener("click", (event) => {
      if (event.target === overlay) close(false);
    });
    overlay.addEventListener("keydown", (event) => {
      if (event.key === "Escape") {
        event.preventDefault();
        close(false);
        return;
      }
      if (event.key !== "Tab" || cancelButton === null) return;
      if (event.shiftKey && document.activeElement === cancelButton) {
        event.preventDefault();
        confirmButton.focus();
      } else if (!event.shiftKey && document.activeElement === confirmButton) {
        event.preventDefault();
        cancelButton.focus();
      }
    });
    (destructive && cancelButton !== null ? cancelButton : confirmButton).focus();
  });
}

export async function showOperationSuccess(message: string): Promise<void> {
  await showModal({
    kind: "success",
    title: "Opération réussie",
    message,
    confirmText: "Fermer",
  });
}

export async function showOperationError(message: string): Promise<void> {
  await showModal({
    kind: "error",
    title: "Action impossible",
    message,
    confirmText: "Fermer",
  });
}

export function confirmOperation({
  title,
  message,
  confirmText,
  destructive = false,
}: {
  title: string;
  message: string;
  confirmText: string;
  destructive?: boolean;
}): Promise<boolean> {
  return showModal({
    kind: destructive ? "warning" : "question",
    title,
    message,
    confirmText,
    cancelText: "Annuler",
    destructive,
  });
}
