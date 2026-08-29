import {
  createContext,
  type ReactNode,
  useCallback,
  useContext,
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
} from "react";

import { Notice, type NoticeTone } from "./Notice";
import { useI18n } from "../i18n";

const FOCUSABLE_SELECTOR = "button:not(:disabled), [href], [tabindex]:not([tabindex='-1'])";

export interface ConfirmationOptions {
  confirmText: string;
  destructive?: boolean;
  message: string;
  title: string;
}

export interface ToastOptions {
  message: string;
  title?: string;
  tone?: NoticeTone;
}

interface ConfirmationRequest extends ConfirmationOptions {
  id: number;
  resolve: (confirmed: boolean) => void;
}

interface Toast extends ToastOptions {
  id: number;
}

interface FeedbackApi {
  confirm: (options: ConfirmationOptions) => Promise<boolean>;
  toast: (options: ToastOptions) => void;
}

const FeedbackContext = createContext<FeedbackApi | null>(null);

export function ConfirmDialog({
  closeDisabled = false,
  options,
  onClose,
}: {
  closeDisabled?: boolean;
  options: ConfirmationOptions;
  onClose: (confirmed: boolean) => void;
}) {
  const { t } = useI18n();
  const titleId = useId();
  const descriptionId = useId();
  const dialogRef = useRef<HTMLDivElement>(null);
  const closeRef = useRef(onClose);
  const closeDisabledRef = useRef(closeDisabled);
  closeRef.current = onClose;
  closeDisabledRef.current = closeDisabled;

  useEffect(() => {
    const previouslyFocused = document.activeElement as HTMLElement | null;
    document.body.classList.add("dialog-open");
    const initialSelector = options.destructive
      ? "[data-cancel-action]"
      : "[data-confirm-action]";
    dialogRef.current?.querySelector<HTMLElement>(initialSelector)?.focus();
    const handleKeyboard = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        if (!closeDisabledRef.current) {
          event.preventDefault();
          closeRef.current(false);
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
  }, [options.destructive]);

  const kind = options.destructive ? "warning" : "question";
  return (
    <div
      className="wos-modal-overlay"
      onMouseDown={(event) => {
        if (!closeDisabled && event.target === event.currentTarget) onClose(false);
      }}
    >
      <div
        ref={dialogRef}
        className={`wos-modal ${kind}`}
        role="dialog"
        aria-modal="true"
        aria-busy={closeDisabled}
        aria-labelledby={titleId}
        aria-describedby={descriptionId}
      >
        <span className={`wos-modal-icon ${kind}`} aria-hidden="true">
          {options.destructive ? "!" : "?"}
        </span>
        <h2 id={titleId}>{options.title}</h2>
        <p id={descriptionId}>{options.message}</p>
        <div className="wos-modal-actions">
          <button
            type="button"
            className="wos-alert-cancel"
            data-cancel-action
            onClick={() => onClose(false)}
            disabled={closeDisabled}
          >
            {t("common.cancel")}
          </button>
          <button
            type="button"
            className={`wos-alert-confirm${options.destructive ? " destructive" : ""}`}
            data-confirm-action
            onClick={() => onClose(true)}
            disabled={closeDisabled}
          >
            {closeDisabled ? t("common.processing") : options.confirmText}
          </button>
        </div>
      </div>
    </div>
  );
}

export function FeedbackProvider({ children }: { children: ReactNode }) {
  const { t } = useI18n();
  const sequence = useRef(0);
  const confirmationsRef = useRef<ConfirmationRequest[]>([]);
  const [confirmations, setConfirmations] = useState<ConfirmationRequest[]>([]);
  const [toasts, setToasts] = useState<Toast[]>([]);
  confirmationsRef.current = confirmations;

  useEffect(
    () => () => {
      for (const confirmation of confirmationsRef.current) confirmation.resolve(false);
    },
    [],
  );

  const confirm = useCallback((options: ConfirmationOptions) => {
    return new Promise<boolean>((resolve) => {
      setConfirmations((current) => [
        ...current,
        { ...options, id: ++sequence.current, resolve },
      ]);
    });
  }, []);

  const dismissToast = useCallback((id: number) => {
    setToasts((current) => current.filter((toast) => toast.id !== id));
  }, []);

  const toast = useCallback((options: ToastOptions) => {
    const id = ++sequence.current;
    setToasts((current) => [...current.slice(-2), { ...options, id }]);
    window.setTimeout(() => dismissToast(id), options.tone === "error" ? 10_000 : 6_000);
  }, [dismissToast]);

  const closeConfirmation = useCallback((confirmed: boolean) => {
    setConfirmations((current) => {
      const [active, ...remaining] = current;
      active?.resolve(confirmed);
      return remaining;
    });
  }, []);

  const api = useMemo(() => ({ confirm, toast }), [confirm, toast]);

  return (
    <FeedbackContext.Provider value={api}>
      {children}
      {confirmations[0] !== undefined && (
        <ConfirmDialog
          key={confirmations[0].id}
          options={confirmations[0]}
          onClose={closeConfirmation}
        />
      )}
      {toasts.length > 0 && (
        <div className="feedback-toast-region" role="region" aria-label={t("feedback.region")}>
          {toasts.map((item) => (
            <Notice
              key={item.id}
              message={item.message}
              title={item.title}
              tone={item.tone}
              onDismiss={() => dismissToast(item.id)}
            />
          ))}
        </div>
      )}
    </FeedbackContext.Provider>
  );
}

export function useFeedback(): FeedbackApi {
  const feedback = useContext(FeedbackContext);
  if (feedback === null) throw new Error("FeedbackProvider is missing");
  return feedback;
}
