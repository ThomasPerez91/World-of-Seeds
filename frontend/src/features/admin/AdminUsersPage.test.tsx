import { act, fireEvent, render, screen } from "@testing-library/react";
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
              preferred_theme: "system",
              created_at: "2026-09-01T08:00:00Z",
              last_login_at: "2026-09-21T08:00:00Z",
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
      expect(screen.getByRole("navigation", { name: "Sections d’administration" }).querySelectorAll("button")).toHaveLength(7);
      expect(screen.getByRole("button", { name: "Utilisateurs" }).getAttribute("aria-current")).toBe("page");
      expect(screen.getByRole("button", { name: `Suspendre ${username}` })).toBeTruthy();
      expect(screen.getByRole("button", { name: `Supprimer l’accès de ${username}` })).toBeTruthy();
      expect(view.container.querySelector(".user-row-identity-copy > strong")).toBeTruthy();
      expect(view.container.querySelector(".admin-user-avatar.account-avatar")).toBeTruthy();
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

  it("affiche les dates, le temps humain et filtre la dernière connexion", async () => {
    const now = Date.now();
    const accounts = [
      {
        id: "81776682-b0c3-4d3d-8b85-ff284c683941",
        username: "connexion-recente",
        is_admin: false,
        is_active: true,
        must_change_credentials: false,
        preferred_locale: "fr",
        preferred_theme: "system",
        created_at: new Date(now - 40 * 86_400_000).toISOString(),
        last_login_at: new Date(now - 3_600_000).toISOString(),
      },
      {
        id: "81776682-b0c3-4d3d-8b85-ff284c683942",
        username: "connexion-ancienne",
        is_admin: false,
        is_active: true,
        must_change_credentials: false,
        preferred_locale: "fr",
        preferred_theme: "system",
        created_at: new Date(now - 60 * 86_400_000).toISOString(),
        last_login_at: new Date(now - 10 * 86_400_000).toISOString(),
      },
      {
        id: "81776682-b0c3-4d3d-8b85-ff284c683943",
        username: "jamais-connecte",
        is_admin: false,
        is_active: true,
        must_change_credentials: true,
        preferred_locale: "fr",
        preferred_theme: "system",
        created_at: new Date(now - 2 * 86_400_000).toISOString(),
        last_login_at: null,
      },
    ];
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) =>
      String(input).endsWith("/quota")
        ? response({ used: 3, maximum: 10, reached: false })
        : response(accounts),
    ));
    const user = userEvent.setup();
    const view = render(
      <FeedbackProvider>
        <AdminUsersPage onBack={vi.fn()} onNavigate={vi.fn()} onSessionExpired={vi.fn()} />
      </FeedbackProvider>,
    );

    expect(await screen.findByText("connexion-recente")).toBeTruthy();
    expect(screen.getByText("Dernière connexion il y a 1 heure")).toBeTruthy();
    expect(screen.getAllByText("Jamais connecté").length).toBeGreaterThan(0);
    expect(view.container.querySelectorAll("time[datetime]").length).toBeGreaterThanOrEqual(5);

    await user.selectOptions(
      screen.getByLabelText("Filtrer par dernière connexion"),
      "week",
    );
    expect(screen.getByText("connexion-recente")).toBeTruthy();
    expect(screen.queryByText("connexion-ancienne")).toBeNull();
    expect(screen.queryByText("jamais-connecte")).toBeNull();

    await user.selectOptions(
      screen.getByLabelText("Filtrer par dernière connexion"),
      "never",
    );
    expect(screen.getByText("jamais-connecte")).toBeTruthy();
    expect(screen.queryByText("connexion-recente")).toBeNull();
    expect(await auditAccessibility(view.container)).toMatchObject({ violations: [] });
  });

  it("rafraîchit automatiquement un filtre temporel quand son seuil est franchi", async () => {
    vi.useFakeTimers();
    const now = new Date("2026-09-21T12:00:00Z");
    vi.setSystemTime(now);
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) =>
      String(input).endsWith("/quota")
        ? response({ used: 1, maximum: 10, reached: false })
        : response([{
            id: "81776682-b0c3-4d3d-8b85-ff284c683944",
            username: "limite-24-heures",
            is_admin: false,
            is_active: true,
            must_change_credentials: false,
            preferred_locale: "fr",
            preferred_theme: "system",
            created_at: "2026-08-01T08:00:00Z",
            last_login_at: new Date(now.getTime() - 86_390_000).toISOString(),
          }]),
    ));

    try {
      render(
        <FeedbackProvider>
          <AdminUsersPage onBack={vi.fn()} onNavigate={vi.fn()} onSessionExpired={vi.fn()} />
        </FeedbackProvider>,
      );
      await act(async () => {
        await Promise.resolve();
        await Promise.resolve();
      });
      fireEvent.change(screen.getByLabelText("Filtrer par dernière connexion"), {
        target: { value: "day" },
      });
      expect(screen.getByText("limite-24-heures")).toBeTruthy();

      act(() => vi.advanceTimersByTime(60_000));

      expect(screen.queryByText("limite-24-heures")).toBeNull();
      expect(screen.getByText("Aucun utilisateur ne correspond à ce filtre.")).toBeTruthy();
    } finally {
      vi.useRealTimers();
    }
  });
});
