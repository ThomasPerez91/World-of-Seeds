import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { auditAccessibility } from "../test/accessibility";
import { FeedbackProvider, useFeedback } from "./Feedback";

function Harness() {
  const feedback = useFeedback();
  return (
    <>
      <button
        type="button"
        onClick={() => feedback.toast({ tone: "success", message: "Terminé." })}
      >
        Notifier
      </button>
      <button
        type="button"
        onClick={() => feedback.toast({ tone: "error", message: "Échec détaillé." })}
      >
        Signaler
      </button>
    </>
  );
}

describe("FeedbackProvider", () => {
  it("empile et ferme les toasts accessibles sans voler le focus", async () => {
    const user = userEvent.setup();
    const view = render(
      <FeedbackProvider>
        <Harness />
      </FeedbackProvider>,
    );

    const notifier = screen.getByRole("button", { name: "Notifier" });
    await user.click(notifier);
    await user.click(screen.getByRole("button", { name: "Signaler" }));
    const notifications = screen.getByRole("region", { name: "Notifications" });
    expect(within(notifications).getByRole("status").textContent).toContain("Terminé.");
    expect(within(notifications).getByRole("alert").textContent).toContain("Échec détaillé.");
    expect(document.activeElement).toBe(screen.getByRole("button", { name: "Signaler" }));
    expect(await auditAccessibility(document.body)).toMatchObject({ violations: [] });
    const closeButtons = within(notifications).getAllByRole("button", { name: "Fermer le message" });
    await user.click(closeButtons[0]);
    expect(within(notifications).queryByText("Terminé.")).toBeNull();
    expect(view.container.querySelector("[style]")).toBeNull();
  });
});
