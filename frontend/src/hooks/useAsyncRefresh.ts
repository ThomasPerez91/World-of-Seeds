import { useCallback, useEffect, useLayoutEffect, useRef } from "react";

interface RefreshOptions<T> {
  onData: (value: T) => void;
  onError: (error: unknown) => void;
  onLoading?: (loading: boolean) => void;
  intervalMs?: number;
  enabled?: boolean;
}

/** Component-local reads: one in flight, one queued manual refresh, no stale callbacks. */
export function useAsyncRefresh<T>(
  loader: (signal: AbortSignal) => Promise<T>,
  options: RefreshOptions<T>,
): () => Promise<void> {
  const callbacks = useRef({ ...options, loader });
  useLayoutEffect(() => {
    callbacks.current = { ...options, loader };
  });
  const refresh = useRef<() => Promise<void>>(async () => undefined);
  const { intervalMs, enabled = true } = options;

  useEffect(() => {
    if (!enabled) return;
    let active = true;
    let controller: AbortController | null = null;
    let pending: Promise<void> | null = null;
    let queued = false;

    const run = (): Promise<void> => {
      if (!active) return Promise.resolve();
      if (pending !== null) {
        queued = true;
        return pending.then(() => pending ?? undefined);
      }
      const current = new AbortController();
      controller = current;
      const isCurrent = () => active && !current.signal.aborted &&
        callbacks.current.loader === loader && callbacks.current.enabled !== false;
      callbacks.current.onLoading?.(true);
      // The microtask also keeps synchronous loader failures in the controlled error path.
      pending = Promise.resolve().then(() => {
        if (!isCurrent()) return;
        return loader(current.signal).then((value) => {
          if (isCurrent()) callbacks.current.onData(value);
        });
      }).catch((error: unknown) => {
        if (!isCurrent()) return;
        if (error instanceof DOMException && error.name === "AbortError") return;
        callbacks.current.onError(error);
      }).finally(() => {
        pending = null;
        if (!isCurrent()) return;
        controller = null;
        callbacks.current.onLoading?.(false);
        if (queued) {
          queued = false;
          void run();
        }
      });
      return pending;
    };

    refresh.current = run;
    void run();
    const timer = intervalMs === undefined ? undefined : window.setInterval(() => {
      // A slow read finishes instead of being repeatedly cancelled by the timer.
      if (pending === null) void run();
    }, intervalMs);
    return () => {
      active = false;
      queued = false;
      controller?.abort();
      if (timer !== undefined) window.clearInterval(timer);
      if (refresh.current === run) refresh.current = async () => undefined;
    };
  }, [loader, intervalMs, enabled]);

  return useCallback(() => refresh.current(), []);
}
