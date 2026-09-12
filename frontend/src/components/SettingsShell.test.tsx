import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { SettingsShell } from "./SettingsShell";

describe("SettingsShell", () => {
  it("partage une navigation unique avec un panneau de contenu transparent", async () => {
    const onNavigate = vi.fn();
    const user = userEvent.setup();
    const view = render(
      <SettingsShell
        activeView="general"
        navigation={[
          { view: "general", label: "Général" },
          { view: "security", label: "Sécurité" },
        ]}
        navigationLabel="Sections"
        onNavigate={onNavigate}
      >
        <section>Contenu</section>
      </SettingsShell>,
    );

    expect(screen.getByRole("button", { name: "Général" }).getAttribute("aria-current")).toBe("page");
    expect(view.container.querySelectorAll(".wos-glass-panel")).toHaveLength(1);
    await user.click(screen.getByRole("button", { name: "Sécurité" }));
    expect(onNavigate).toHaveBeenCalledWith("security");
  });
});
