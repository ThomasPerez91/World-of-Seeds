import { render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { AdminServicesPage } from "./AdminServicesPage";

function response(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

describe("AdminServicesPage", () => {
  it("préfère la version runtime et utilise les versions attendues en repli", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => response({
      status: "ok",
      checked_at: "2026-09-12T10:00:00Z",
      newgreedy: { status: "healthy", latency_ms: 12, version: "1.8.0", error_code: null },
      qbittorrent: { status: "healthy", latency_ms: 9, version: null, error_code: null },
      service_controls_available: false,
    })));

    render(
      <AdminServicesPage onBack={vi.fn()} onNavigate={vi.fn()} onSessionExpired={vi.fn()} />,
    );

    const newgreedy = await screen.findByLabelText("NewGreedy : Opérationnel");
    const qbittorrent = screen.getByLabelText("qBittorrent : Opérationnel");
    expect(within(newgreedy).getByText("1.8.0")).toBeTruthy();
    expect(within(qbittorrent).getByText("5.2.3")).toBeTruthy();
    expect(within(newgreedy).queryByText("1.7.5")).toBeNull();
  });

  it("affiche les deux versions de repli lorsque le runtime ne les fournit pas", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => response({
      status: "ok",
      checked_at: "2026-09-12T10:00:00Z",
      newgreedy: { status: "healthy", latency_ms: 12, version: null, error_code: null },
      qbittorrent: { status: "healthy", latency_ms: 9, version: null, error_code: null },
      service_controls_available: false,
    })));

    render(
      <AdminServicesPage onBack={vi.fn()} onNavigate={vi.fn()} onSessionExpired={vi.fn()} />,
    );

    expect(await screen.findByText("1.7.5")).toBeTruthy();
    expect(screen.getByText("5.2.3")).toBeTruthy();
  });
});
