import { type ReactNode, useEffect, useId, useRef } from "react";

export function FileDialog({
  children,
  description,
  eyebrow = "Gestion du fichier",
  onClose,
  title,
}: {
  children: ReactNode;
  description: string;
  eyebrow?: string;
  onClose: () => void;
  title: string;
}) {
  const titleId = useId();
  const descriptionId = useId();
  const dialogRef = useRef<HTMLElement>(null);
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;

  useEffect(() => {
    const previouslyFocused = document.activeElement as HTMLElement | null;
    document.body.classList.add("dialog-open");
    const initialFocus =
      dialogRef.current?.querySelector<HTMLElement>("[data-initial-focus]") ??
      dialogRef.current?.querySelector<HTMLElement>("button, input");
    initialFocus?.focus();

    const handleKeyboard = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        onCloseRef.current();
        return;
      }
      if (event.key !== "Tab" || dialogRef.current === null) return;
      const focusable = Array.from(
        dialogRef.current.querySelectorAll<HTMLElement>(
          "button:not(:disabled), input:not(:disabled), [href], [tabindex]:not([tabindex='-1'])",
        ),
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
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <section
        ref={dialogRef}
        className="mutation-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={descriptionId}
      >
        <header className="dialog-header">
          <div>
            <p className="eyebrow">{eyebrow}</p>
            <h3 id={titleId}>{title}</h3>
            <p id={descriptionId}>{description}</p>
          </div>
          <button type="button" className="dialog-close" onClick={onClose} aria-label="Fermer">
            ×
          </button>
        </header>
        {children}
      </section>
    </div>
  );
}
