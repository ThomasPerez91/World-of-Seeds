import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { DeleteIcon } from "./icons";

describe("DeleteIcon", () => {
  it("demande deux clics dans les actions torrent et remplace la poubelle par une validation", () => {
    const onClick = vi.fn();
    const view = render(
      <div className="torrent-card-actions">
        <button type="button" aria-label="Supprimer" onClick={onClick}>
          <DeleteIcon />
        </button>
      </div>,
    );
    const button = screen.getByRole("button", { name: "Supprimer" });

    expect(view.container.querySelector(".lucide-trash-2")).toBeTruthy();
    fireEvent.click(button);

    expect(onClick).not.toHaveBeenCalled();
    expect(view.container.querySelector(".lucide-check")).toBeTruthy();
    expect(button.getAttribute("aria-pressed")).toBe("true");

    fireEvent.click(button);
    expect(onClick).toHaveBeenCalledTimes(1);
    expect(view.container.querySelector(".lucide-trash-2")).toBeTruthy();
  });

  it("annule la confirmation si le bouton perd le focus", () => {
    const onClick = vi.fn();
    const view = render(
      <div className="torrent-card-actions">
        <button type="button" aria-label="Supprimer" onClick={onClick}>
          <DeleteIcon />
        </button>
      </div>,
    );
    const button = screen.getByRole("button", { name: "Supprimer" });

    fireEvent.click(button);
    fireEvent.blur(button);

    expect(view.container.querySelector(".lucide-trash-2")).toBeTruthy();
    fireEvent.click(button);
    expect(onClick).not.toHaveBeenCalled();
  });

  it("conserve le comportement immédiat hors des cartes torrent", () => {
    const onClick = vi.fn();
    render(
      <button type="button" aria-label="Réinitialiser" onClick={onClick}>
        <DeleteIcon />
      </button>,
    );

    fireEvent.click(screen.getByRole("button", { name: "Réinitialiser" }));
    expect(onClick).toHaveBeenCalledTimes(1);
  });
});
