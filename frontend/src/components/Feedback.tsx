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

export type ToastId = number;

export interface ToastOptions {
  message: string;
  title?: string;
  tone?: NoticeTone;
  onRetry?: () => void;
  durationMs?: number | null;
  dedupeKey?: string | false;
}

interface Toast extends ToastOptions {
  id: ToastId;
  revision: number;
}

export interface FeedbackApi {
  toast: (options: ToastOptions) => ToastId;
  update: (id: ToastId, options: Partial<ToastOptions>) => void;
  dismiss: (id: ToastId) => void;
}

interface RecentToast {
  id: ToastId;
  timestamp: number;
}

export const MAX_VISIBLE_TOASTS = 4;
export const TOAST_DEDUPE_WINDOW_MS = 1_500;
export const TOAST_EXIT_MS = 180;

const FeedbackContext = createContext<FeedbackApi | null>(null);

function defaultDuration(tone: NoticeTone | undefined): number {
  if (tone === "error") return 7_500;
  if (tone === "warning") return 6_000;
  if (tone === "progress") return 15_000;
  if (tone === "info") return 5_000;
  return 4_500;
}

function signature(options: ToastOptions): string | null {
  if (options.dedupeKey === false) return null;
  if (typeof options.dedupeKey === "string") return options.dedupeKey;
  return `${options.tone ?? "success"}\u0000${options.title ?? ""}\u0000${options.message}`;
}

function ToastItem({
  item,
  exiting,
  onDismiss,
}: {
  item: Toast;
  exiting: boolean;
  onDismiss: () => void;
}) {
  const duration = item.durationMs === undefined ? defaultDuration(item.tone) : item.durationMs;
  const remaining = useRef(duration ?? 0);
  const startedAt = useRef(0);
  const dismissRef = useRef(onDismiss);
  const [paused, setPaused] = useState(false);
  dismissRef.current = onDismiss;

  useEffect(() => {
    remaining.current = duration ?? 0;
  }, [duration, item.revision]);

  useEffect(() => {
    if (paused || exiting || duration === null) return;
    if (remaining.current <= 0) {
      dismissRef.current();
      return;
    }
    startedAt.current = Date.now();
    const timer = window.setTimeout(() => dismissRef.current(), remaining.current);
    return () => {
      window.clearTimeout(timer);
      remaining.current = Math.max(0, remaining.current - (Date.now() - startedAt.current));
    };
  }, [duration, exiting, item.revision, paused]);

  function resumeAfterFocus(event: FocusEvent<HTMLDivElement>) {
    if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setPaused(false);
  }

  return (
    <div
      className={`feedback-toast-item${exiting ? " is-exiting" : ""}`}
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
        onRetry={item.onRetry}
      />
    </div>
  );
}

export function FeedbackProvider({ children }: { children: ReactNode }) {
  const { t } = useI18n();
  const sequence = useRef(0);
  const recentToasts = useRef(new Map<string, RecentToast>());
  const exitTimers = useRef(new Map<ToastId, number>());
  const [toasts, setToasts] = useState<Toast[]>([]);
  const [exiting, setExiting] = useState<Set<ToastId>>(() => new Set());

  const removeToast = useCallback((id: ToastId) => {
    setToasts((current) => current.filter((toast) => toast.id !== id));
    setExiting((current) => {
      if (!current.has(id)) return current;
      const next = new Set(current);
      next.delete(id);
      return next;
    });
    for (const [key, recent] of recentToasts.current) {
      if (recent.id === id) recentToasts.current.delete(key);
    }
    exitTimers.current.delete(id);
  }, []);

  const dismissToast = useCallback((id: ToastId) => {
    if (exitTimers.current.has(id)) return;
    setExiting((current) => new Set(current).add(id));
    const timer = window.setTimeout(() => removeToast(id), TOAST_EXIT_MS);
    exitTimers.current.set(id, timer);
  }, [removeToast]);

  useEffect(() => () => {
    for (const timer of exitTimers.current.values()) window.clearTimeout(timer);
  }, []);

  const toast = useCallback((options: ToastOptions): ToastId => {
    const now = Date.now();
    const key = signature(options);
    if (key !== null) {
      const recent = recentToasts.current.get(key);
      if (recent !== undefined && now - recent.timestamp <= TOAST_DEDUPE_WINDOW_MS) {
        return recent.id;
      }
    }

    const id = ++sequence.current;
    setToasts((current) => [...current, { ...options, id, revision: 0 }]);
    if (key !== null) recentToasts.current.set(key, { id, timestamp: now });
    return id;
  }, []);

  const update = useCallback((id: ToastId, options: Partial<ToastOptions>) => {
    setToasts((current) => current.map((item) => {
      if (item.id !== id) return item;
      const updated = { ...item, ...options, id, revision: item.revision + 1 };
      const key = signature(updated);
      if (key !== null) recentToasts.current.set(key, { id, timestamp: Date.now() });
      return updated;
    }));
  }, []);

  const api = useMemo<FeedbackApi>(() => ({
    toast,
    update,
    dismiss: dismissToast,
  }), [dismissToast, toast, update]);
  const visibleToasts = toasts.slice(0, MAX_VISIBLE_TOASTS);

  return (
    <FeedbackContext.Provider value={api}>
      {children}
      {visibleToasts.length > 0 && (
        <div className="feedback-toast-region" role="region" aria-label={t("feedback.region")}>
          {visibleToasts.map((item) => (
            <ToastItem
              key={item.id}
              item={item}
              exiting={exiting.has(item.id)}
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
