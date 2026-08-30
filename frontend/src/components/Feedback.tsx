import {
  createContext,
  type FocusEvent,
  type ReactNode,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import { useI18n } from "../i18n";
import { Notice, type NoticeTone } from "./Notice";

export interface ToastOptions {
  message: string;
  title?: string;
  tone?: NoticeTone;
}

interface Toast extends ToastOptions {
  id: number;
}

interface FeedbackApi {
  toast: (options: ToastOptions) => void;
}

const FeedbackContext = createContext<FeedbackApi | null>(null);

function ToastItem({ item, onDismiss }: { item: Toast; onDismiss: () => void }) {
  const duration = item.tone === "error" ? 12_000 : item.tone === "progress" ? 15_000 : 7_000;
  const remaining = useRef(duration);
  const startedAt = useRef(0);
  const dismissRef = useRef(onDismiss);
  const [paused, setPaused] = useState(false);
  dismissRef.current = onDismiss;

  useEffect(() => {
    if (paused) return;
    startedAt.current = Date.now();
    const timer = window.setTimeout(() => dismissRef.current(), remaining.current);
    return () => {
      window.clearTimeout(timer);
      remaining.current = Math.max(0, remaining.current - (Date.now() - startedAt.current));
    };
  }, [paused]);

  function resumeAfterFocus(event: FocusEvent<HTMLDivElement>) {
    if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setPaused(false);
  }

  return (
    <div
      className="feedback-toast-item"
      onMouseEnter={() => setPaused(true)}
      onMouseLeave={() => setPaused(false)}
      onFocusCapture={() => setPaused(true)}
      onBlurCapture={resumeAfterFocus}
    >
      <Notice
        message={item.message}
        title={item.title}
        tone={item.tone}
        onDismiss={onDismiss}
      />
    </div>
  );
}

export function FeedbackProvider({ children }: { children: ReactNode }) {
  const { t } = useI18n();
  const sequence = useRef(0);
  const [toasts, setToasts] = useState<Toast[]>([]);

  const dismissToast = useCallback((id: number) => {
    setToasts((current) => current.filter((toast) => toast.id !== id));
  }, []);

  const toast = useCallback((options: ToastOptions) => {
    const id = ++sequence.current;
    setToasts((current) => [...current.slice(-4), { ...options, id }]);
  }, []);

  const api = useMemo(() => ({ toast }), [toast]);

  return (
    <FeedbackContext.Provider value={api}>
      {children}
      {toasts.length > 0 && (
        <div className="feedback-toast-region" role="region" aria-label={t("feedback.region")}>
          {toasts.map((item) => (
            <ToastItem
              key={item.id}
              item={item}
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
