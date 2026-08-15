import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  api,
  ApiError,
  type NewGreedyTorrent,
  type QBittorrentTorrent,
  type QBittorrentTorrentListing,
} from "../../api/client";
import { RefreshIcon } from "../../components/icons";
import { formatBytes } from "../../utils/format";

const PAGE_SIZE = 50;

type TorrentStateKind = "active" | "complete" | "error" | "paused" | "pending";

function statePresentation(state: string): { kind: TorrentStateKind; label: string } {
  if (["downloading", "forcedDL"].includes(state)) {
    return { kind: "active", label: "Téléchargement" };
  }
  if (["uploading", "forcedUP", "stalledUP"].includes(state)) {
    return { kind: "complete", label: "En seed" };
  }
  if (["pausedDL", "pausedUP", "stoppedDL", "stoppedUP"].includes(state)) {
    return { kind: "paused", label: "En pause" };
  }
  if (["error", "missingFiles", "unknown"].includes(state)) {
    return { kind: "error", label: "Erreur" };
  }
  if (state === "stalledDL") {
    return { kind: "pending", label: "En attente de sources" };
  }
  if (
    [
      "allocating",
      "checkingDL",
      "checkingUP",
      "checkingResumeData",
      "metaDL",
      "moving",
      "queuedDL",
      "queuedUP",
    ].includes(state)
  ) {
    return { kind: "pending", label: "Préparation" };
  }
  return { kind: "pending", label: state };
}

function formatSpeed(bytesPerSecond: number): string {
  return `${formatBytes(bytesPerSecond)}/s`;
}

function formatEta(seconds: number | null): string {
  if (seconds === null) return "—";
  if (seconds <= 0) return "Terminé";
  const days = Math.floor(seconds / 86_400);
  const hours = Math.floor((seconds % 86_400) / 3_600);
  const minutes = Math.floor((seconds % 3_600) / 60);
  if (days > 0) return `${days} j ${hours} h`;
  if (hours > 0) return `${hours} h ${minutes} min`;
  return `${Math.max(1, minutes)} min`;
}

function NewGreedyTorrentStatus({
  available,
  torrent,
}: {
  available: boolean;
  torrent: NewGreedyTorrent | undefined;
}) {
  if (!available) return <span className="torrent-secondary-state unavailable">Indisponible</span>;
  if (torrent === undefined) {
    return <span className="torrent-secondary-state">Non suivi</span>;
  }
  if (torrent.stalled) {
    return <span className="torrent-secondary-state warning">Bloqué</span>;
  }
  if (torrent.target_reached) {
    return <span className="torrent-secondary-state complete">Objectif atteint</span>;
  }
  return (
    <span className="torrent-secondary-state active">
      {torrent.mode === "down" ? "Actif" : "Seed"} · {formatBytes(torrent.fake_uploaded_bytes)}
    </span>
  );
}

export function TorrentMonitoringPanel({
  onSessionExpired,
}: {
  onSessionExpired: () => void;
}) {
  const [listing, setListing] = useState<QBittorrentTorrentListing | null>(null);
  const [newgreedy, setNewgreedy] = useState<NewGreedyTorrent[]>([]);
  const [newgreedyAvailable, setNewgreedyAvailable] = useState(false);
  const [error, setError] = useState("");
  const [newgreedyError, setNewgreedyError] = useState("");
  const [loading, setLoading] = useState(true);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);
  const [visibleCount, setVisibleCount] = useState(PAGE_SIZE);
  const mounted = useRef(true);
  const inFlight = useRef(false);

  const load = useCallback(async () => {
    if (inFlight.current) return;
    inFlight.current = true;
    if (mounted.current) setLoading(true);
    try {
      const [qbittorrentResult, newgreedyResult] = await Promise.allSettled([
        api.listQBittorrentTorrents(),
        api.listNewGreedyTorrents(),
      ]);
      if (!mounted.current) return;

      const authenticationFailure = [qbittorrentResult, newgreedyResult].some(
        (result) =>
          result.status === "rejected" &&
          result.reason instanceof ApiError &&
          result.reason.status === 401,
      );
      if (authenticationFailure) {
        onSessionExpired();
        return;
      }

      if (qbittorrentResult.status === "fulfilled") {
        setListing(qbittorrentResult.value);
        setError("");
      } else {
        setError("La liste qBittorrent est indisponible.");
      }
      if (newgreedyResult.status === "fulfilled") {
        setNewgreedy(newgreedyResult.value.torrents);
        setNewgreedyAvailable(true);
        setNewgreedyError("");
      } else {
        setNewgreedyAvailable(false);
        setNewgreedyError("Les états NewGreedy ne peuvent pas être corrélés.");
      }
      setLastUpdated(new Date());
    } finally {
      inFlight.current = false;
      if (mounted.current) setLoading(false);
    }
  }, [onSessionExpired]);

  useEffect(() => {
    mounted.current = true;
    void load();
    const interval = window.setInterval(() => void load(), 15_000);
    return () => {
      mounted.current = false;
      window.clearInterval(interval);
    };
  }, [load]);

  const newgreedyByHash = useMemo(
    () => new Map(newgreedy.map((torrent) => [torrent.id.slice(0, 8), torrent])),
    [newgreedy],
  );
  const torrents = listing?.torrents ?? [];
  const visibleTorrents = torrents.slice(0, visibleCount);
  const qbittorrentHashes = useMemo(
    () => new Set(torrents.map((torrent) => torrent.id.slice(0, 8))),
    [torrents],
  );
  const unmatchedNewGreedy = newgreedy.filter(
    (entry) => !qbittorrentHashes.has(entry.id.slice(0, 8)),
  ).length;

  return (
    <section className="torrent-monitoring" aria-labelledby="torrent-monitoring-title">
      <div className="torrent-monitoring-heading">
        <div>
          <p className="eyebrow">Activité</p>
          <h3 id="torrent-monitoring-title">Torrents qBittorrent</h3>
          <p>
            {torrents.length} torrent{torrents.length > 1 ? "s" : ""}
            {unmatchedNewGreedy > 0
              ? ` · ${unmatchedNewGreedy} état(s) NewGreedy non corrélé(s)`
              : ""}
          </p>
        </div>
        <button
          type="button"
          className="refresh-button torrent-refresh-button"
          disabled={loading}
          onClick={() => void load()}
        >
          <RefreshIcon className={loading ? "rotating" : undefined} />
          {loading ? "Actualisation…" : "Actualiser"}
        </button>
      </div>

      <p className="form-message error-message" role="alert">
        {[error, newgreedyError].filter(Boolean).join(" ")}
      </p>
      {loading && listing === null ? (
        <div className="torrent-list-state" role="status">
          Lecture des torrents…
        </div>
      ) : listing === null ? (
        <div className="torrent-list-state error">Aucun état qBittorrent disponible.</div>
      ) : torrents.length === 0 ? (
        <div className="torrent-list-state">Aucun torrent dans qBittorrent.</div>
      ) : (
        <>
          <ol className="torrent-monitoring-list">
            {visibleTorrents.map((torrent) => {
              const presentation = statePresentation(torrent.state);
              const linkedNewGreedy = newgreedyByHash.get(torrent.id.slice(0, 8));
              return (
                <li key={torrent.id}>
                  <article className="torrent-monitoring-card">
                    <header>
                      <div className="torrent-monitoring-name">
                        <h4 title={torrent.name}>{torrent.name}</h4>
                        <p>
                          {[torrent.category, torrent.tracker_host].filter(Boolean).join(" · ") ||
                            "Sans catégorie ni tracker"}
                        </p>
                      </div>
                      <span className={`torrent-primary-state ${presentation.kind}`}>
                        {presentation.label}
                      </span>
                    </header>
                    <div className="torrent-progress">
                      <progress value={torrent.progress} max={1}>
                        {Math.round(torrent.progress * 100)} %
                      </progress>
                      <strong>{Math.round(torrent.progress * 100)} %</strong>
                    </div>
                    <dl className="torrent-monitoring-metrics">
                      <div>
                        <dt>Taille</dt>
                        <dd>{formatBytes(torrent.size_bytes)}</dd>
                      </div>
                      <div>
                        <dt>Download</dt>
                        <dd>{formatSpeed(torrent.download_speed_bytes)}</dd>
                      </div>
                      <div>
                        <dt>Upload</dt>
                        <dd>{formatSpeed(torrent.upload_speed_bytes)}</dd>
                      </div>
                      <div>
                        <dt>Ratio</dt>
                        <dd>{torrent.ratio.toLocaleString("fr-FR", { maximumFractionDigits: 2 })}</dd>
                      </div>
                      <div>
                        <dt>Temps restant</dt>
                        <dd>{formatEta(torrent.eta_seconds)}</dd>
                      </div>
                      <div>
                        <dt>NewGreedy</dt>
                        <dd>
                          <NewGreedyTorrentStatus
                            available={newgreedyAvailable}
                            torrent={linkedNewGreedy}
                          />
                        </dd>
                      </div>
                    </dl>
                  </article>
                </li>
              );
            })}
          </ol>
          {visibleCount < torrents.length && (
            <button
              type="button"
              className="secondary-button torrent-show-more"
              onClick={() => setVisibleCount((current) => current + PAGE_SIZE)}
            >
              Afficher les {Math.min(PAGE_SIZE, torrents.length - visibleCount)} suivants
            </button>
          )}
          {listing.truncated && (
            <p className="torrent-truncated-notice">
              La vue est limitée aux 1 000 torrents les plus récents.
            </p>
          )}
        </>
      )}
      {lastUpdated !== null && (
        <p className="services-last-check">
          Activité mise à jour à {lastUpdated.toLocaleTimeString("fr-FR")}
        </p>
      )}
    </section>
  );
}
