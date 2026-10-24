import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ConfirmIcon, DeleteIcon } from "./icons";

describe("DeleteIcon", () => {
  it("is a plain decorative trash icon: confirmation lives in ConfirmButton", () => {
    const view = render(
      <button type="button" aria-label="Supprimer">
        <DeleteIcon />
      </button>,
    );

    expect(view.container.querySelector(".lucide-trash-2")).toBeTruthy();
    expect(view.container.querySelector("svg")?.getAttribute("aria-hidden")).toBe("true");
    expect(view.container.querySelector("button")?.getAttribute("aria-pressed")).toBeNull();
  });

  it("offers a check icon for the armed state", () => {
    const view = render(<ConfirmIcon />);

    expect(view.container.querySelector(".lucide-check")).toBeTruthy();
  });
});
