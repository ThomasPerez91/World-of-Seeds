import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { App } from "../App";
import { auditAccessibility } from "../test/accessibility";

const TOKEN = "A".repeat(43);
function mockRequests(status = 204) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input);
    if (path === "/api/v1/auth/me") return Response.json({ detail: "Not authenticated" }, { status: 401 });
    if (path === "/api/v1/health/status") return Response.json({status:"ok",checked_at:"2026-10-22T00:00:00Z"});
    if (path === "/api/v1/auth/password-reset/confirm" && init?.method === "POST") {
      return status === 204 ? new Response(null, {status}) : Response.json({detail:{code:"password_reset_unavailable"}}, {status});
    }
    throw new Error(`Unexpected request: ${path}`);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

describe("Password recovery", () => {
  it("guide depuis le login vers le bon salon Discord, en FR et EN", async () => {
    mockRequests();
    const user = userEvent.setup();
    const view = render(<App />);
    await user.click(await screen.findByRole("button", {name:"Mot de passe oublié ?"}));
    expect(screen.getByRole("link", {name:"Ouvrir le salon WoS sur Discord"}).getAttribute("href")).toBe("https://discord.com/channels/1549418373308358727/1549532212402196511");
    await user.click(screen.getByRole("button",{name:"Langue : Français"}));
    expect(screen.getByRole("heading",{name:"Reset your password"})).toBeTruthy();
    expect(await auditAccessibility(view.container)).toMatchObject({violations:[]});
    await user.click(screen.getByRole("button",{name:"Back to sign in"}));
    expect(await screen.findByRole("button",{name:"Sign in"})).toBeTruthy();
  });

  it("prend en charge un lien ouvert dans un onglet WoS déjà chargé", async () => {
    mockRequests();
    render(<App />);
    await screen.findByRole("button", {name:"Mot de passe oublié ?"});
    window.location.hash = `reset-password=${TOKEN}`;
    await screen.findByLabelText("Nouveau mot de passe");
    await waitFor(()=>expect(window.location.hash).toBe(""));
    expect(screen.queryByRole("link",{name:"Ouvrir le salon WoS sur Discord"})).toBeNull();
  });

  it("retire le secret de l’URL, contrôle la confirmation et ne connecte pas automatiquement", async () => {
    window.history.replaceState({},"",`/#reset-password=${TOKEN}`);
    const fetchMock = mockRequests();
    const user = userEvent.setup();
    const view = render(<App />);
    await screen.findByRole("heading",{name:"Réinitialiser ton mot de passe"});
    expect(window.location.hash).toBe("");
    expect(window.localStorage.getItem("reset-password")).toBeNull();
    expect(await auditAccessibility(view.container)).toMatchObject({violations:[]});
    await user.type(screen.getByLabelText("Nouveau mot de passe"), "replacement-password");
    await user.type(screen.getByLabelText("Confirmer le mot de passe"), "different-password");
    await user.click(screen.getByRole("button",{name:"Enregistrer le nouveau mot de passe"}));
    expect(await screen.findByText("Les deux nouveaux mots de passe ne correspondent pas.")).toBeTruthy();
    expect(fetchMock.mock.calls.filter(([path])=>String(path).includes("/confirm"))).toHaveLength(0);
    await user.clear(screen.getByLabelText("Confirmer le mot de passe"));
    await user.type(screen.getByLabelText("Confirmer le mot de passe"), "replacement-password");
    await user.click(screen.getByRole("button",{name:"Enregistrer le nouveau mot de passe"}));
    await screen.findByRole("heading",{name:"Mot de passe modifié"});
    for (const document of ["Mentions légales", "Conditions d’utilisation"]) {
      await user.click(screen.getByRole("button", { name: document }));
      await user.click(screen.getByRole("button", { name: "Retour" }));
      await screen.findByRole("heading", { name: "Mot de passe modifié" });
      expect(screen.queryByLabelText("Nouveau mot de passe")).toBeNull();
    }
    const calls = fetchMock.mock.calls.filter(([path])=>String(path).includes("/confirm"));
    expect(calls).toHaveLength(1);
    expect(JSON.parse(String(calls[0][1]?.body))).toEqual({token:TOKEN,new_password:"replacement-password"});
    expect(fetchMock.mock.calls.some(([path])=>String(path).includes("/auth/login"))).toBe(false);
    await user.click(screen.getByRole("button",{name:"Retour à la connexion"}));
    expect(await screen.findByRole("button",{name:"Se connecter"})).toBeTruthy();
  });

  it("explique les liens invalides sans exposer leur token et garde les saisies", async () => {
    window.history.replaceState({},"",`/#reset-password=${TOKEN}`);
    const fetchMock = mockRequests(400);
    const user = userEvent.setup();
    render(<App />);
    await user.type(await screen.findByLabelText("Nouveau mot de passe"),"replacement-password");
    await user.type(screen.getByLabelText("Confirmer le mot de passe"),"replacement-password");
    await user.click(screen.getByRole("button",{name:"Enregistrer le nouveau mot de passe"}));
    await waitFor(()=>expect(screen.getByRole("alert").textContent).toContain("Demande un nouveau lien"));
    expect(document.body.textContent).not.toContain(TOKEN);
    expect(screen.getByLabelText("Nouveau mot de passe")).toHaveProperty("value","replacement-password");
    expect(fetchMock.mock.calls.filter(([path])=>String(path).includes("/confirm"))).toHaveLength(1);
  });
});
