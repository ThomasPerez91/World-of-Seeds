import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { FeedbackProvider } from "../../components/Feedback";
import { auditAccessibility } from "../../test/accessibility";
import { AdminUsersPage } from "./AdminUsersPage";

function response(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

describe("AdminUsersPage", () => {
  it.each([320, 375, 390, 430])(
    "contient un nom utilisateur long et garde ses actions accessibles à %d px",
    async (width) => {
      Object.defineProperty(window, "innerWidth", { configurable: true, value: width });
      const username = `pilote-${"nom-long-".repeat(6)}final`;
      vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) =>
        String(input).endsWith("/quota")
          ? response({ used: 1, maximum: 10, reached: false })
          : response([{
              id: "81776682-b0c3-4d3d-8b85-ff284c68394c",
              username,
              is_admin: false,
              is_active: true,
              must_change_credentials: false,
              preferred_locale: "fr",
            }]),
      ));

      const view = render(
        <FeedbackProvider>
          <AdminUsersPage
            onBack={vi.fn()}
            onNavigate={vi.fn()}
            onSessionExpired={vi.fn()}
          />
        </FeedbackProvider>,
      );

      expect(await screen.findByText(username)).toBeTruthy();
      expect(screen.getByRole("navigation", { name: "Sections d’administration" }).querySelectorAll("button")).toHaveLength(4);
      expect(screen.getByRole("button", { name: "Utilisateurs" }).getAttribute("aria-current")).toBe("page");
      expect(screen.getByRole("button", { name: `Suspendre ${username}` })).toBeTruthy();
      expect(screen.getByRole("button", { name: `Supprimer l’accès de ${username}` })).toBeTruthy();
      expect(view.container.querySelector(".user-row > div:nth-child(2) > strong")).toBeTruthy();
      expect(await auditAccessibility(view.container)).toMatchObject({ violations: [] });
    },
  );

  it("affiche le quota et bloque la création lorsqu’il est atteint", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) =>
      String(input).endsWith("/quota")
        ? response({ used: 10, maximum: 10, reached: true })
        : response([]),
    );
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    render(
      <FeedbackProvider>
        <AdminUsersPage onBack={vi.fn()} onNavigate={vi.fn()} onSessionExpired={vi.fn()} />
      </FeedbackProvider>,
    );

    expect(await screen.findByText("10 / 10 utilisés")).toBeTruthy();
    const button = screen.getByRole("button", { name: "Générer un utilisateur" });
    expect(button.hasAttribute("disabled")).toBe(true);
    await user.click(button);
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });
});
