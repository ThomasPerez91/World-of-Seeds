import { act, fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useRef, useState } from "react";

import { auditAccessibility } from "../test/accessibility";
import {
  FeedbackProvider,
  MAX_VISIBLE_TOASTS,
  TOAST_DEDUPE_WINDOW_MS,
  TOAST_EXIT_MS,
  type ToastId,
  useFeedback,
} from "./Feedback";

function Harness() {
  const feedback = useFeedback();
  const sequence = useRef(0);
  const progressId = useRef<ToastId | null>(null);
  const [retries, setRetries] = useState(0);

  return (
    <>
      <button type="button" onClick={() => feedback.toast({ tone: "success", message: "Terminé." })}>
        Succès
      </button>
      <button type="button" onClick={() => feedback.toast({ tone: "info", message: "Information." })}>
        Info
      </button>
      <button type="button" onClick={() => feedback.toast({ tone: "warning", message: "Attention." })}>
        Warning
      </button>
      <button type="button" onClick={() => feedback.toast({ tone: "error", message: "Échec détaillé." })}>
        Erreur
      </button>
      <button type="button" onClick={() => feedback.toast({ tone: "progress", message: "Traitement en cours." })}>
        Progression
      </button>
      <button
        type="button"
        onClick={() => feedback.toast({
          tone: "error",
          title: "Téléchargement interrompu",
          message: "La récupération peut être relancée.",
          onRetry: () => setRetries((current) => current + 1),
        })}
      >
        Retry
      </button>
      <button
        type="button"
        onClick={() => {
          sequence.current += 1;
          feedback.toast({ tone: "info", message: `Message ${sequence.current}` });
        }}
      >
        Unique
      </button>
      <button
        type="button"
        onClick={() => {
          progressId.current = feedback.toast({
            tone: "progress",
            title: "Téléchargement",
            message: "Préparation…",
            durationMs: null,
          });
        }}
      >
        Persistant
      </button>
      <button
        type="button"
        onClick={() => {
          if (progressId.current === null) return;
          feedback.update(progressId.current, {
            tone: "success",
            title: "Téléchargement terminé",
            message: "Le contenu est disponible.",
            durationMs: 4_500,
          });
        }}
      >
        Mettre à jour
      </button>
      <button
        type="button"
        onClick={() => {
          if (progressId.current !== null) feedback.dismiss(progressId.current);
        }}
      >
        Fermer via API
      </button>
      <span>Retries: {retries}</span>
    </>
  );
}

function renderFeedback() {
  return render(
    <FeedbackProvider>
      <Harness />
    </FeedbackProvider>,
  );
}

afterEach(() => {
  vi.useRealTimers();
});

describe("FeedbackProvider", () => {
  it("rend success, info, warning, error et progress avec les rôles live attendus", async () => {
    const user = userEvent.setup();
    const view = renderFeedback();

    await user.click(screen.getByRole("button", { name: "Succès" }));
    await user.click(screen.getByRole("button", { name: "Info" }));
    await user.click(screen.getByRole("button", { name: "Warning" }));
    await user.click(screen.getByRole("button", { name: "Erreur" }));

    const notifications = screen.getByRole("region", { name: "Notifications" });
    expect(within(notifications).getAllByRole("status")).toHaveLength(3);
    const alert = within(notifications).getByRole("alert");
    expect(alert.getAttribute("aria-live")).toBe("assertive");
    expect(alert.getAttribute("aria-atomic")).toBe("true");
    expect(within(notifications).getByText("Terminé.", { selector: "strong" })).toBeTruthy();
    expect(within(notifications).getByText("Information.", { selector: "strong" })).toBeTruthy();
    expect(within(notifications).getByText("Attention.", { selector: "strong" })).toBeTruthy();
    expect(within(notifications).getByText("Échec détaillé.", { selector: "strong" })).toBeTruthy();

    const firstClose = within(notifications).getAllByRole("button", { name: "Fermer" })[0];
    await user.click(firstClose);
    await new Promise((resolve) => window.setTimeout(resolve, TOAST_EXIT_MS + 20));
    await user.click(screen.getByRole("button", { name: "Progression" }));
    expect(within(notifications).getByText("Traitement en cours.", { selector: "strong" })).toBeTruthy();
    expect(await auditAccessibility(view.container)).toMatchObject({ violations: [] });
  });

  it("affiche un bouton Fermer carré, focusable et associé au tooltip WOS", async () => {
    const user = userEvent.setup();
    const view = renderFeedback();
    const trigger = screen.getByRole("button", { name: "Succès" });
    await user.click(trigger);

    const notifications = screen.getByRole("region", { name: "Notifications" });
    const close = within(notifications).getByRole("button", { name: "Fermer" });
    expect(within(notifications).getByRole("tooltip", { name: "Fermer" })).toBeTruthy();
    close.focus();
    expect(document.activeElement).toBe(close);
    expect(close.classList).toContain("notice-dismiss");
    expect(view.container.querySelector("[style]")).toBeNull();

    await user.click(close);
    await new Promise((resolve) => window.setTimeout(resolve, TOAST_EXIT_MS + 20));
    expect(screen.queryByRole("region", { name: "Notifications" })).toBeNull();
  });

  it("ferme automatiquement un succès après sa durée et son animation de sortie", async () => {
    vi.useFakeTimers();
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    renderFeedback();
    await user.click(screen.getByRole("button", { name: "Succès" }));

    await act(async () => { await vi.advanceTimersByTimeAsync(4_499); });
    expect(screen.getByText("Terminé.")).toBeTruthy();
    await act(async () => { await vi.advanceTimersByTimeAsync(1); });
    expect(screen.getByText("Terminé.").closest(".feedback-toast-item")?.classList).toContain("is-exiting");
    await act(async () => { await vi.advanceTimersByTimeAsync(TOAST_EXIT_MS); });
    expect(screen.queryByText("Terminé.")).toBeNull();
  });

  it("met le timer en pause au survol puis le reprend", async () => {
    vi.useFakeTimers();
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    renderFeedback();
    await user.click(screen.getByRole("button", { name: "Succès" }));
    const item = screen.getByText("Terminé.").closest(".feedback-toast-item") as HTMLElement;

    await act(async () => { await vi.advanceTimersByTimeAsync(2_000); });
    fireEvent.mouseEnter(item);
    await act(async () => { await vi.advanceTimersByTimeAsync(10_000); });
    expect(screen.getByText("Terminé.")).toBeTruthy();
    fireEvent.mouseLeave(item);
    await act(async () => { await vi.advanceTimersByTimeAsync(2_500 + TOAST_EXIT_MS); });
    expect(screen.queryByText("Terminé.")).toBeNull();
  });

  it("met aussi le timer en pause au focus et le reprend à la sortie du toast", async () => {
    vi.useFakeTimers();
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    renderFeedback();
    await user.click(screen.getByRole("button", { name: "Succès" }));
    const close = screen.getByRole("button", { name: "Fermer" });

    await act(async () => { await vi.advanceTimersByTimeAsync(1_500); });
    fireEvent.focus(close);
    await act(async () => { await vi.advanceTimersByTimeAsync(8_000); });
    expect(screen.getByText("Terminé.")).toBeTruthy();
    fireEvent.blur(close, { relatedTarget: null });
    await act(async () => { await vi.advanceTimersByTimeAsync(3_000 + TOAST_EXIT_MS); });
    expect(screen.queryByText("Terminé.")).toBeNull();
  });

  it("limite l’affichage à quatre toasts et dépile la file sans perdre le suivant", async () => {
    vi.useFakeTimers();
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    renderFeedback();
    const add = screen.getByRole("button", { name: "Unique" });
    for (let index = 0; index < MAX_VISIBLE_TOASTS + 1; index += 1) await user.click(add);

    const notifications = screen.getByRole("region", { name: "Notifications" });
    expect(within(notifications).getAllByRole("status")).toHaveLength(MAX_VISIBLE_TOASTS);
    expect(within(notifications).queryByText(`Message ${MAX_VISIBLE_TOASTS + 1}`)).toBeNull();

    await user.click(within(notifications).getAllByRole("button", { name: "Fermer" })[0]);
    await act(async () => { await vi.advanceTimersByTimeAsync(TOAST_EXIT_MS); });
    expect(within(notifications).getByText(`Message ${MAX_VISIBLE_TOASTS + 1}`)).toBeTruthy();
    expect(within(notifications).getAllByRole("status")).toHaveLength(MAX_VISIBLE_TOASTS);
  });

  it("déduplique brièvement une notification identique sans bloquer les opérations suivantes", async () => {
    vi.useFakeTimers();
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    renderFeedback();
    const trigger = screen.getByRole("button", { name: "Succès" });

    await user.click(trigger);
    await user.click(trigger);
    expect(screen.getAllByText("Terminé.")).toHaveLength(1);

    await act(async () => { await vi.advanceTimersByTimeAsync(TOAST_DEDUPE_WINDOW_MS + 1); });
    await user.click(trigger);
    expect(screen.getAllByText("Terminé.")).toHaveLength(2);
  });

  it("conserve le retry et permet update puis dismiss d’un toast de progression", async () => {
    vi.useFakeTimers();
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    renderFeedback();

    await user.click(screen.getByRole("button", { name: "Retry" }));
    const retry = screen.getByRole("button", { name: "Réessayer" });
    await user.click(retry);
    expect(screen.getByText("Retries: 1")).toBeTruthy();

    await user.click(screen.getByRole("button", { name: "Persistant" }));
    await act(async () => { await vi.advanceTimersByTimeAsync(60_000); });
    expect(screen.getByText("Préparation…")).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "Mettre à jour" }));
    expect(screen.getByText("Téléchargement terminé")).toBeTruthy();
    expect(screen.getByText("Le contenu est disponible.")).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "Fermer via API" }));
    await act(async () => { await vi.advanceTimersByTimeAsync(TOAST_EXIT_MS); });
    expect(screen.queryByText("Téléchargement terminé")).toBeNull();
  });
});
