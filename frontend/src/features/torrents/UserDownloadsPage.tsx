import { type ChangeEvent, type DragEvent, useCallback, useEffect, useRef, useState } from "react";

import { api, ApiError, type UserTorrent, type UserTorrentState } from "../../api/client";
import { DownloadIcon } from "../../components/icons";
import { Notice, type NoticeTone } from "../../components/Notice";
import { formatBytes } from "../../utils/format";

const stateLabels: Record<UserTorrentState, string> = {
  adding: "Ajout",
  pending: "En attente",
  downloading: "Téléchargement",
  stalled: "En attente de sources",
  completed: "Terminé",
  error: "Erreur",
};

function formatSpeed(value: number): string {
  return value <= 0 ? "—" : `${formatBytes(value)}/s`;
}

function formatEta(value: number | null): string {
  if (value === null || value <= 0) return "—";
  const hours = Math.floor(value / 3600);
  const minutes = Math.ceil((value % 3600) / 60);
  return hours > 0 ? `${hours} h ${minutes} min` : `${minutes} min`;
}

function uploadError(error: unknown): string {
  if (!(error instanceof ApiError)) return "Le torrent n’a pas pu être envoyé.";
  if (error.status === 413) return "Le fichier .torrent est trop volumineux.";
  if (error.status === 409 || error.status === 422 || error.status === 503) return error.message;
  return "Le torrent n’a pas pu être envoyé. Réessaie dans quelques instants.";
}

export function UserDownloadsPage({ onSessionExpired }: { onSessionExpired: () => void }) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [torrents, setTorrents] = useState<UserTorrent[]>([]);
  const [loading, setLoading] = useState(true);
  const [dragging, setDragging] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [notice, setNotice] = useState<{ message: string; tone: NoticeTone } | null>(null);

  const load = useCallback(async (signal?: AbortSignal) => {
    try {
      const result = await api.listUserTorrents(signal);
      setTorrents(result.torrents);
      setNotice((current) => current?.tone === "error" ? null : current);
    } catch (caught) {
      if (caught instanceof DOMException && caught.name === "AbortError") return;
      if (caught instanceof ApiError && caught.status === 401) {
        onSessionExpired();
        return;
      }
      setNotice({
        tone: "error",
        message: caught instanceof ApiError ? caught.message : "Le suivi est indisponible.",
      });
    } finally {
      setLoading(false);
    }
  }, [onSessionExpired]);

  useEffect(() => {
    const controller = new AbortController();
    void load(controller.signal);
    const interval = window.setInterval(() => void load(), 5_000);
    return () => {
      controller.abort();
      window.clearInterval(interval);
    };
  }, [load]);

  async function submit(file: File | undefined) {
    if (file === undefined) return;
    if (!file.name.toLowerCase().endsWith(".torrent")) {
      setNotice({ tone: "warning", message: "Sélectionne un fichier portant l’extension .torrent." });
      return;
    }
    setUploading(true);
    setNotice({ tone: "progress", message: "Validation du fichier et ajout au téléchargement…" });
    try {
      const result = await api.uploadTorrent(file);
      setNotice({ tone: "success", message: `« ${result.name} » a été ajouté.` });
      await load();
    } catch (caught) {
      if (caught instanceof ApiError && caught.status === 401) {
        onSessionExpired();
        return;
      }
      setNotice({ tone: "error", message: uploadError(caught) });
    } finally {
      setUploading(false);
      if (inputRef.current !== null) inputRef.current.value = "";
    }
  }

  function drop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    setDragging(false);
    void submit(event.dataTransfer.files.item(0) ?? undefined);
  }

  function select(event: ChangeEvent<HTMLInputElement>) {
    void submit(event.target.files?.item(0) ?? undefined);
  }

  return (
    <section className="user-downloads" aria-labelledby="user-downloads-title">
      <header className="user-downloads-header">
        <p className="eyebrow">Espace personnel</p>
        <h2 id="user-downloads-title">Mes téléchargements</h2>
        <p>Ajoute un fichier .torrent C411 et suis sa progression ici.</p>
      </header>

      <div
        className={`torrent-drop-zone${dragging ? " dragging" : ""}${uploading ? " disabled" : ""}`}
        data-testid="torrent-drop-zone"
        aria-disabled={uploading}
        onDragEnter={(event) => { event.preventDefault(); setDragging(true); }}
        onDragOver={(event) => event.preventDefault()}
        onDragLeave={(event) => { if (!event.currentTarget.contains(event.relatedTarget as Node)) setDragging(false); }}
        onDrop={drop}
      >
        <DownloadIcon />
        <strong>Dépose ton fichier .torrent ici</strong>
        <span>ou sélectionne-le depuis ton appareil</span>
        <button type="button" disabled={uploading} onClick={() => inputRef.current?.click()}>
          {uploading ? "Ajout en cours…" : "Sélectionner un fichier"}
        </button>
        <input
          ref={inputRef}
          className="sr-only"
          type="file"
          accept=".torrent,application/x-bittorrent"
          onChange={select}
          disabled={uploading}
          tabIndex={-1}
        />
      </div>

      {notice !== null && (
        <Notice message={notice.message} tone={notice.tone} onDismiss={() => setNotice(null)} onRetry={notice.tone === "error" ? () => void load() : undefined} />
      )}

      {loading ? (
        <p className="torrent-list-state" role="status">Lecture des téléchargements…</p>
      ) : torrents.length === 0 ? (
        <p className="torrent-list-state">Aucun téléchargement pour le moment.</p>
      ) : (
        <ol className="user-torrent-list">
          {torrents.map((torrent) => (
            <li key={torrent.id}>
              <article className="user-torrent-card">
                <header>
                  <h3 title={torrent.name}>{torrent.name}</h3>
                  <span className={`torrent-primary-state ${torrent.state}`}>{stateLabels[torrent.state]}</span>
                </header>
                <div className="torrent-progress">
                  <progress value={torrent.progress} max={1}>{Math.round(torrent.progress * 100)} %</progress>
                  <strong>{Math.round(torrent.progress * 100)} %</strong>
                </div>
                <dl className="user-torrent-metrics">
                  <div><dt>Taille</dt><dd>{formatBytes(torrent.size_bytes)}</dd></div>
                  <div><dt>Téléchargé</dt><dd>{formatBytes(torrent.downloaded_bytes)}</dd></div>
                  <div><dt>Vitesse</dt><dd>{formatSpeed(torrent.download_speed_bytes)}</dd></div>
                  <div><dt>Temps restant</dt><dd>{formatEta(torrent.eta_seconds)}</dd></div>
                </dl>
                {torrent.error !== null && <p className="torrent-user-error" role="alert">{torrent.error}</p>}
              </article>
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}
