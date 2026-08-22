import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it } from "vitest";

import { auditAccessibility } from "../test/accessibility";
import { FeedbackProvider, useFeedback } from "./Feedback";

function Harness() {
  const feedback = useFeedback();
  const [result, setResult] = useState("none");
  return (
    <>
      <button
        type="button"
        onClick={() => {
          void feedback
            .confirm({
              title: "Supprimer définitivement ?",
              message: "Cette action est irréversible.",
              confirmText: "Supprimer",
              destructive: true,
            })
            .then((confirmed) => setResult(confirmed ? "yes" : "no"));
        }}
      >
        Ouvrir
      </button>
      <button
        type="button"
        onClick={() => feedback.toast({ tone: "success", message: "Terminé." })}
      >
        Notifier
      </button>
      <output>{result}</output>
    </>
  );
}

describe("FeedbackProvider", () => {
  it("rend confirmation et toast dans React, sans style inline", async () => {
    const user = userEvent.setup();
    const view = render(
      <FeedbackProvider>
        <Harness />
      </FeedbackProvider>,
    );

    const opener = screen.getByRole("button", { name: "Ouvrir" });
    await user.click(opener);
    const dialog = screen.getByRole("dialog", { name: "Supprimer définitivement ?" });
    expect(document.activeElement).toBe(screen.getByRole("button", { name: "Annuler" }));
    expect(await auditAccessibility(document.body)).toMatchObject({ violations: [] });
    await user.keyboard("{Escape}");
    expect(screen.getByText("no")).toBeTruthy();
    expect(document.activeElement).toBe(opener);

    await user.click(screen.getByRole("button", { name: "Notifier" }));
    const notifications = screen.getByRole("region", { name: "Notifications" });
    expect(within(notifications).getByRole("status").textContent).toContain("Terminé.");
    expect(dialog.querySelector("[style]")).toBeNull();
    expect(view.container.querySelector("[style]")).toBeNull();
  });
});
