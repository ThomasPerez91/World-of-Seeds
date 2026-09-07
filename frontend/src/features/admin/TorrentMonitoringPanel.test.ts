import { describe, expect, it } from "vitest";

import type { NewGreedyTorrent, QBittorrentTorrent } from "../../api/client";
import { correlateNewGreedyTorrents } from "./TorrentMonitoringPanel";

function qb(id: string): QBittorrentTorrent {
  return {
    id, name: id, state: "downloading", progress: 0, size_bytes: 1,
    downloaded_bytes: 0, uploaded_bytes: 0, download_speed_bytes: 0,
    upload_speed_bytes: 0, ratio: 0, eta_seconds: null, category: null,
    tracker_host: null,
  };
}

function ng(id: string): NewGreedyTorrent {
  return {
    id, mode: "down", downloaded_bytes: 0, reported_uploaded_bytes: 0,
    fake_uploaded_bytes: 0, ratio: null, announce_count: 0, stalled: false,
    target_reached: false, last_announce_at: null,
  };
}

describe("correlateNewGreedyTorrents", () => {
  it("refuse un préfixe ambigu et préfère le hash complet", () => {
    const first = `deadbeef${"a".repeat(32)}`;
    const second = `deadbeef${"b".repeat(32)}`;

    const ambiguous = correlateNewGreedyTorrents([qb(first), qb(second)], [ng("deadbeef")]);
    const exact = correlateNewGreedyTorrents([qb(first), qb(second)], [ng(second)]);

    expect(ambiguous.byQBHash.size).toBe(0);
    expect(ambiguous.unmatched).toBe(1);
    expect(exact.byQBHash.get(second)?.id).toBe(second);
    expect(exact.unmatched).toBe(0);
  });
});
