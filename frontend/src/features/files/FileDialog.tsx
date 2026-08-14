import { type ReactNode, useEffect, useId, useRef } from "react";

import { CloseIcon } from "../../components/icons";

const FOCUSABLE_SELECTOR = [
  "button:not(:disabled)",
  "input:not(:disabled)",
  "select:not(:disabled)",
  "textarea:not(:disabled)",
  "[href]",
  "[tabindex]:not([tabindex='-1'])",
].join(", ");

export function FileDialog({
  children,
  closeDisabled = false,
  description,
  eyebrow = "Gestion du fichier",
  onClose,
  title,
}: {
  children: ReactNode;
  closeDisabled?: boolean;
  description: string;
  eyebrow?: string;
  onClose: () => void;
  title: string;
}) {
  const titleId = useId();
  const descriptionId = useId();
  const dialogRef = useRef<HTMLElement>(null);
  const onCloseRef = useRef(onClose);
  const closeDisabledRef = useRef(closeDisabled);
  onCloseRef.current = onClose;
  closeDisabledRef.current = closeDisabled;

  useEffect(() => {
    const previouslyFocused = document.activeElement as HTMLElement | null;
    document.body.classList.add("dialog-open");
    const initialFocus =
      dialogRef.current?.querySelector<HTMLElement>("[data-initial-focus]") ??
      dialogRef.current?.querySelector<HTMLElement>("button, input");
    initialFocus?.focus();

    const handleKeyboard = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        if (!closeDisabledRef.current) {
          event.preventDefault();
          onCloseRef.current();
        }
        return;
      }
      if (event.key !== "Tab" || dialogRef.current === null) return;
      const focusable = Array.from(
        dialogRef.current.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR),
      );
      if (focusable.length === 0) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", handleKeyboard);
    return () => {
      document.body.classList.remove("dialog-open");
      document.removeEventListener("keydown", handleKeyboard);
      if (previouslyFocused?.isConnected === true) previouslyFocused.focus();
    };
  }, []);

  return (
    <div
      className="dialog-backdrop"
      onMouseDown={(event) => {
        if (!closeDisabled && event.target === event.currentTarget) onClose();
      }}
    >
      <section
        ref={dialogRef}
        className="mutation-dialog"
        role="dialog"
        aria-modal="true"
        aria-busy={closeDisabled}
        aria-labelledby={titleId}
        aria-describedby={descriptionId}
      >
        <header className="dialog-header">
          <div>
            <p className="eyebrow">{eyebrow}</p>
            <h3 id={titleId}>{title}</h3>
            <p id={descriptionId}>{description}</p>
          </div>
          <button
            type="button"
            className="dialog-close"
            onClick={onClose}
            aria-label="Fermer"
            disabled={closeDisabled}
          >
            <CloseIcon />
          </button>
        </header>
        {children}
      </section>
    </div>
  );
}
