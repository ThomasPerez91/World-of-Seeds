import { type ChangeEvent, type DragEvent, useCallback, useEffect, useRef, useState } from "react";

import {
  api,
  ApiError,
  type TorrentRequestV2,
  type TorrentRequestV2State,
} from "../../api/client";
import { DownloadIcon, RefreshIcon } from "../../components/icons";
import { Notice, type NoticeTone } from "../../components/Notice";
import { formatBytes } from "../../utils/format";
import {
  pickDownloadDirectory,
  RecursiveDownloadController,
  type RecursiveTransferProgress,
  supportsRecursiveDirectoryDownload,
} from "./recursiveDownload";

const PAGE_SIZE = 10;

const stateLabels: Record<TorrentRequestV2State, string> = {
  requested: "Demandé",
  active: "En cours",
  ready: "Disponible",
  cancelled: "Annulé",
  expired: "Expiré",
  error: "Erreur",
};

const errorLabels: Record<string, string> = {
  torrent_failed: "Le téléchargement nécessite une intervention.",
};

function uploadError(error: unknown): string {
  if (!(error instanceof ApiError)) return "Le torrent n’a pas pu être envoyé.";
  if ([409, 413, 422, 503, 507].includes(error.status)) return error.message;
  return "Le torrent n’a pas pu être envoyé. Réessaie dans quelques instants.";
}

function formatDate(value: string): string {
  return new Intl.DateTimeFormat("fr-FR", {
    dateStyle: "short",
    timeStyle: "short",
  }).format(new Date(value));
}

function TorrentRow({
  torrent,
  onRefresh,
  onDownload,
  downloadBusy,
}: {
  torrent: TorrentRequestV2;
  onRefresh: () => void;
  onDownload: () => void;
  downloadBusy: boolean;
}) {
  const percent = Math.round(torrent.progress * 100);
  const error = torrent.error_code === null
    ? null
    : errorLabels[torrent.error_code] ?? "Le téléchargement est en erreur.";
  return (
    <tr>
      <td className="torrent-name-cell">
        <span title={torrent.name}>{torrent.name}</span>
        {error !== null && <small role="alert">{error}</small>}
      </td>
      <td>
        <span className={`torrent-primary-state ${torrent.state}`}>
          {stateLabels[torrent.state]}
        </span>
      </td>
      <td className="torrent-size-cell">{formatBytes(torrent.total_size)}</td>
      <td className="torrent-progress-cell">
        <div>
          <progress value={torrent.progress} max={1} aria-label={`Progression de ${torrent.name}`}>
            {percent} %
          </progress>
          <strong>{percent} %</strong>
        </div>
      </td>
      <td className="torrent-date-cell">{formatDate(torrent.updated_at)}</td>
      <td className="torrent-row-actions">
        {torrent.state === "ready" ? (
          <button type="button" disabled={downloadBusy} onClick={onDownload}>
            <DownloadIcon />
            <span>Télécharger</span>
          </button>
        ) : (
          <button type="button" className="secondary-button" onClick={onRefresh}>
            <RefreshIcon />
            <span>Actualiser</span>
          </button>
        )}
      </td>
    </tr>
  );
}

export function UserDownloadsPage({ onSessionExpired }: { onSessionExpired: () => void }) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [torrents, setTorrents] = useState<TorrentRequestV2[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [notice, setNotice] = useState<{ message: string; tone: NoticeTone } | null>(null);
  const controllerRef = useRef<RecursiveDownloadController | null>(null);
  const [transfer, setTransfer] = useState<(
    RecursiveTransferProgress & {
      name: string;
      totalBytes: number;
      fileCount: number;
    }
  ) | null>(null);

  useEffect(() => () => controllerRef.current?.cancel(), []);

  const load = useCallback(async (requestedOffset: number, signal?: AbortSignal) => {
    setRefreshing(true);
    try {
      const result = await api.listTorrentRequestsV2(requestedOffset, PAGE_SIZE, signal);
      setTorrents(result.items);
      setTotal(result.total);
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
      setRefreshing(false);
    }
  }, [onSessionExpired]);

  useEffect(() => {
    const controller = new AbortController();
    void load(offset, controller.signal);
    const interval = window.setInterval(() => void load(offset), 10_000);
    return () => {
      controller.abort();
      window.clearInterval(interval);
    };
  }, [load, offset]);

  async function submit(file: File | undefined) {
    if (file === undefined) return;
    if (!file.name.toLowerCase().endsWith(".torrent")) {
      setNotice({ tone: "warning", message: "Sélectionne un fichier portant l’extension .torrent." });
      return;
    }
    setUploading(true);
    setNotice({ tone: "progress", message: "Validation et enregistrement de la demande…" });
    try {
      const result = await api.createTorrentRequestV2(file);
      const repeated = result.created ? "a été ajouté" : "était déjà présent";
      setNotice({
        tone: result.storage_pressure === "warning" ? "warning" : "success",
        message: `« ${result.name} » ${repeated}.`,
      });
      setOffset(0);
      await load(0);
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

  async function startRecursiveDownload(torrent: TorrentRequestV2) {
    if (!supportsRecursiveDirectoryDownload()) {
      setNotice({
        tone: "warning",
        message: "Ce navigateur ne permet pas encore le téléchargement récursif. Le fallback arrive à l’étape suivante.",
      });
      return;
    }
    setNotice({ tone: "progress", message: `Préparation de « ${torrent.name} »…` });
    try {
      const snapshotPromise = api.getTorrentDownloadSnapshotV2(torrent.id);
      const directoryPromise = pickDownloadDirectory();
      const [snapshot, directory] = await Promise.all([snapshotPromise, directoryPromise]);
      const update = (progress: RecursiveTransferProgress) => {
        setTransfer({
          ...progress,
          name: torrent.name,
          totalBytes: snapshot.total_size,
          fileCount: snapshot.file_count,
        });
        if (progress.status === "completed") {
          setNotice({ tone: "success", message: `« ${torrent.name} » a été téléchargé.` });
        } else if (progress.status === "error") {
          setNotice({ tone: "error", message: progress.error ?? "Le téléchargement a échoué." });
        }
      };
      const controller = new RecursiveDownloadController({
        torrentRequestId: torrent.id,
        snapshot,
        directory,
        onProgress: update,
      });
      controllerRef.current = controller;
      setNotice(null);
      await controller.start();
    } catch (caught) {
      if (caught instanceof DOMException && caught.name === "AbortError") {
        setNotice(null);
        return;
      }
      if (caught instanceof ApiError && caught.status === 401) {
        onSessionExpired();
        return;
      }
      setNotice({
        tone: "error",
        message: caught instanceof Error ? caught.message : "Le téléchargement a échoué.",
      });
    }
  }

  function cancelTransfer() {
    controllerRef.current?.cancel();
    controllerRef.current = null;
  }

  const page = Math.floor(offset / PAGE_SIZE) + 1;
  const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE));

  return (
    <section className="user-downloads" aria-labelledby="user-downloads-title">
      <header className="user-downloads-header">
        <div>
          <p className="eyebrow">Espace personnel</p>
          <h2 id="user-downloads-title">Mes téléchargements</h2>
          <p>Ajoute un fichier .torrent C411 et consulte son état durable.</p>
        </div>
        <div className="torrent-page-actions">
          <button type="button" disabled={uploading} onClick={() => inputRef.current?.click()}>
            <DownloadIcon />
            <span>{uploading ? "Ajout…" : "Ajouter un torrent"}</span>
          </button>
          <button
            type="button"
            className="secondary-button"
            disabled={refreshing}
            onClick={() => void load(offset)}
          >
            <RefreshIcon className={refreshing ? "rotating" : undefined} />
            <span>{refreshing ? "Actualisation…" : "Actualiser"}</span>
          </button>
        </div>
      </header>

      <input
        ref={inputRef}
        className="sr-only"
        type="file"
        aria-label="Fichier torrent"
        accept=".torrent,application/x-bittorrent"
        onChange={select}
        disabled={uploading}
        tabIndex={-1}
      />
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
        <span>La destination et le propriétaire sont déterminés par le serveur.</span>
      </div>

      {notice !== null && (
        <Notice
          message={notice.message}
          tone={notice.tone}
          onDismiss={() => setNotice(null)}
          onRetry={notice.tone === "error" ? () => void load(offset) : undefined}
        />
      )}

      {transfer !== null && (
        <section className={`recursive-transfer ${transfer.status}`} aria-label="Téléchargement local">
          <div>
            <strong title={transfer.name}>{transfer.name}</strong>
            <span aria-live="polite">
              {transfer.completedFiles}/{transfer.fileCount} fichiers · {formatBytes(transfer.downloadedBytes)} sur {formatBytes(transfer.totalBytes)}
            </span>
          </div>
          <progress
            value={transfer.downloadedBytes}
            max={Math.max(1, transfer.totalBytes)}
            aria-label={`Téléchargement local de ${transfer.name}`}
          />
          <div className="recursive-transfer-actions">
            {transfer.status === "running" && (
              <button type="button" className="secondary-button" onClick={() => controllerRef.current?.pause()}>
                Pause
              </button>
            )}
            {(transfer.status === "paused" || transfer.status === "error") && (
              <button type="button" onClick={() => void controllerRef.current?.resume()}>
                Reprendre
              </button>
            )}
            {transfer.status === "completed" || transfer.status === "cancelled" ? (
              <button type="button" className="secondary-button" onClick={() => setTransfer(null)}>
                Fermer
              </button>
            ) : (
              <button type="button" className="danger-button" onClick={cancelTransfer}>
                Annuler
              </button>
            )}
          </div>
        </section>
      )}

      {loading ? (
        <p className="torrent-list-state" role="status">Lecture des téléchargements…</p>
      ) : torrents.length === 0 ? (
        <p className="torrent-list-state">Aucun téléchargement pour le moment.</p>
      ) : (
        <>
          <div className="torrent-table-wrap" aria-busy={refreshing}>
            <table className="torrent-table">
              <caption className="sr-only">Demandes de téléchargement</caption>
              <thead>
                <tr>
                  <th scope="col">Nom</th>
                  <th scope="col">État</th>
                  <th scope="col">Taille</th>
                  <th scope="col">Progression</th>
                  <th scope="col">Mise à jour</th>
                  <th scope="col">Actions</th>
                </tr>
              </thead>
              <tbody>
                {torrents.map((torrent) => (
                  <TorrentRow
                    key={torrent.id}
                    torrent={torrent}
                    onRefresh={() => void load(offset)}
                    onDownload={() => void startRecursiveDownload(torrent)}
                    downloadBusy={transfer?.status === "running" || transfer?.status === "paused"}
                  />
                ))}
              </tbody>
            </table>
          </div>
          <nav className="torrent-pagination" aria-label="Pagination des téléchargements">
            <button
              type="button"
              className="secondary-button"
              disabled={offset === 0 || refreshing}
              onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
            >
              Précédent
            </button>
            <span aria-live="polite">Page {page} sur {pageCount} · {total} demande{total > 1 ? "s" : ""}</span>
            <button
              type="button"
              className="secondary-button"
              disabled={offset + PAGE_SIZE >= total || refreshing}
              onClick={() => setOffset(offset + PAGE_SIZE)}
            >
              Suivant
            </button>
          </nav>
        </>
      )}
    </section>
  );
}
