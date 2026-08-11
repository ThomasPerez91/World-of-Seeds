import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { auditAccessibility } from "../../test/accessibility";
import { FileDialog } from "./FileDialog";

describe("FileDialog", () => {
  it("gère le focus, le clavier et restaure le déclencheur", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    const opener = document.createElement("button");
    opener.textContent = "Ouvrir";
    document.body.append(opener);
    opener.focus();

    const view = render(
      <FileDialog title="Confirmer" description="Vérifie cette action." onClose={onClose}>
        <button type="button" data-initial-focus>
          Continuer
        </button>
      </FileDialog>,
    );

    const continueButton = screen.getByRole("button", { name: "Continuer" });
    const closeButton = screen.getByRole("button", { name: "Fermer" });
    expect(document.activeElement).toBe(continueButton);

    await user.tab();
    expect(document.activeElement).toBe(closeButton);
    await user.tab({ shift: true });
    expect(document.activeElement).toBe(continueButton);

    const results = await auditAccessibility(view.container);
    expect(results.violations).toEqual([]);

    await user.keyboard("{Escape}");
    expect(onClose).toHaveBeenCalledOnce();
    view.unmount();
    expect(document.activeElement).toBe(opener);
    opener.remove();
  });

  it("bloque toutes les méthodes de fermeture pendant une mutation", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    render(
      <FileDialog
        title="Mutation en cours"
        description="Le déplacement doit finir."
        onClose={onClose}
        closeDisabled
      >
        <button type="button">Patienter</button>
      </FileDialog>,
    );

    await user.keyboard("{Escape}");
    await user.click(screen.getByRole("button", { name: "Fermer" }));
    await user.click(document.querySelector(".dialog-backdrop") as HTMLElement);
    expect(onClose).not.toHaveBeenCalled();
  });
});
