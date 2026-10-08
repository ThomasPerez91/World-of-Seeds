import { act, renderHook } from "@testing-library/react";
import { StrictMode, type ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { useAsyncRefresh } from "./useAsyncRefresh";

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

afterEach(() => vi.useRealTimers());

describe("useAsyncRefresh", () => {
  it("laisse finir une lecture lente sans chevauchement ni annulation par le timer", async () => {
    vi.useFakeTimers();
    const first = deferred<number>();
    const loader = vi.fn<(signal: AbortSignal) => Promise<number>>()
      .mockReturnValueOnce(first.promise).mockResolvedValue(2);
    const onData = vi.fn();
    const view = renderHook(() => useAsyncRefresh(loader, { onData, onError: vi.fn(), intervalMs: 100 }));
    await act(async () => vi.advanceTimersByTimeAsync(450));
    expect(loader).toHaveBeenCalledTimes(1);
    expect(loader.mock.calls[0][0].aborted).toBe(false);
    await act(async () => first.resolve(1));
    expect(onData).toHaveBeenLastCalledWith(1);
    await act(async () => vi.advanceTimersByTimeAsync(50));
    expect(loader).toHaveBeenCalledTimes(2);
    expect(onData).toHaveBeenLastCalledWith(2);
    view.unmount();
    expect(vi.getTimerCount()).toBe(0);
  });

  it("regroupe une rafale manuelle en une seule lecture après celle en cours", async () => {
    const first = deferred<number>();
    const second = deferred<number>();
    const loader = vi.fn<(signal: AbortSignal) => Promise<number>>()
      .mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise);
    const onData = vi.fn();
    const view = renderHook(() => useAsyncRefresh(loader, { onData, onError: vi.fn() }));
    await act(async () => Promise.resolve());
    let finished = false;
    act(() => {
      void view.result.current().then(() => { finished = true; });
      for (let i = 0; i < 20; i++) void view.result.current();
    });
    expect(loader).toHaveBeenCalledTimes(1);
    await act(async () => first.resolve(1));
    expect(loader).toHaveBeenCalledTimes(2);
    expect(finished).toBe(false);
    await act(async () => second.resolve(2));
    expect(finished).toBe(true);
    expect(onData.mock.calls).toEqual([[1], [2]]);
    expect(loader).toHaveBeenCalledTimes(2);
  });

  it.each(["success", "error"])("ignore une ancienne réponse %s lors du changement de source", async (outcome) => {
    const old = deferred<number>();
    const oldLoader = vi.fn(() => old.promise);
    const newLoader = vi.fn(async () => 2);
    const onData = vi.fn();
    const onError = vi.fn();
    const view = renderHook(({ loader }) => useAsyncRefresh(loader, { onData, onError }), {
      initialProps: { loader: oldLoader as (signal: AbortSignal) => Promise<number> },
    });
    await act(async () => Promise.resolve());
    view.rerender({ loader: newLoader });
    await act(async () => Promise.resolve());
    expect(onData).toHaveBeenLastCalledWith(2);
    await act(async () => outcome === "success" ? old.resolve(1) : old.reject(new Error("old 401")));
    expect(onData.mock.calls).toEqual([[2]]);
    expect(onError).not.toHaveBeenCalled();
  });

  it("annule au démontage et abandonne la relance et les callbacks tardifs", async () => {
    vi.useFakeTimers();
    const pending = deferred<number>();
    const loader = vi.fn((_signal: AbortSignal) => pending.promise);
    const onData = vi.fn();
    const onError = vi.fn();
    const onLoading = vi.fn();
    const view = renderHook(() => useAsyncRefresh(loader, { onData, onError, onLoading, intervalMs: 100 }));
    await act(async () => Promise.resolve());
    void view.result.current();
    view.unmount();
    expect(loader.mock.calls[0][0].aborted).toBe(true);
    await act(async () => pending.reject(new Error("late")));
    await act(async () => vi.advanceTimersByTimeAsync(1000));
    expect(onData).not.toHaveBeenCalled();
    expect(onError).not.toHaveBeenCalled();
    expect(onLoading.mock.calls).toEqual([[true]]);
    expect(loader).toHaveBeenCalledTimes(1);
    expect(vi.getTimerCount()).toBe(0);
  });

  it("utilise les callbacks courants sans recharger à chaque render", async () => {
    const pending = deferred<number>();
    const loader = vi.fn(() => pending.promise);
    const oldCallback = vi.fn();
    const newCallback = vi.fn();
    const view = renderHook(({ onData }) => useAsyncRefresh(loader, { onData, onError: vi.fn() }), {
      initialProps: { onData: oldCallback },
    });
    await act(async () => Promise.resolve());
    view.rerender({ onData: newCallback });
    await act(async () => pending.resolve(1));
    expect(loader).toHaveBeenCalledTimes(1);
    expect(oldCallback).not.toHaveBeenCalled();
    expect(newCallback).toHaveBeenCalledWith(1);
  });

  it("suspend la lecture pendant une mutation puis relit sans accepter l’ancien résultat", async () => {
    const old = deferred<number>();
    const loader = vi.fn<(signal: AbortSignal) => Promise<number>>()
      .mockReturnValueOnce(old.promise).mockResolvedValue(2);
    const onData = vi.fn();
    const view = renderHook(({ enabled }) => useAsyncRefresh(loader, { enabled, onData, onError: vi.fn() }), {
      initialProps: { enabled: true },
    });
    await act(async () => Promise.resolve());
    view.rerender({ enabled: false });
    expect(loader.mock.calls[0][0].aborted).toBe(true);
    await act(async () => old.resolve(1));
    expect(onData).not.toHaveBeenCalled();
    view.rerender({ enabled: true });
    await act(async () => Promise.resolve());
    expect(onData.mock.calls).toEqual([[2]]);
  });

  it("résiste au replay StrictMode et ne partage rien entre composants", async () => {
    const pending = deferred<number>();
    const loader = vi.fn(() => pending.promise);
    const onData = vi.fn();
    const wrapper = ({ children }: { children: ReactNode }) => <StrictMode>{children}</StrictMode>;
    const first = renderHook(() => useAsyncRefresh(loader, { onData, onError: vi.fn() }), { wrapper });
    const second = renderHook(() => useAsyncRefresh(loader, { onData, onError: vi.fn() }), { wrapper });
    await act(async () => Promise.resolve());
    expect(loader).toHaveBeenCalledTimes(2);
    first.unmount();
    await act(async () => pending.resolve(3));
    expect(onData.mock.calls).toEqual([[3]]);
    second.unmount();
  });

  it("gère une erreur synchrone et permet une nouvelle tentative", async () => {
    const loader = vi.fn<(signal: AbortSignal) => Promise<number>>()
      .mockImplementationOnce(() => { throw new Error("failed"); }).mockResolvedValue(4);
    const onError = vi.fn();
    const onData = vi.fn();
    const view = renderHook(() => useAsyncRefresh(loader, { onData, onError }));
    await act(async () => Promise.resolve());
    expect(onError).toHaveBeenCalledOnce();
    await act(async () => view.result.current());
    expect(onData).toHaveBeenCalledWith(4);
  });
});
