import type { Locale } from "../../i18n";

interface OptionCopy { label: string; description: string }

const sections: Record<string, Record<Locale, string>> = {
  downloads: { fr: "Téléchargements", en: "Downloads" },
  torrents: { fr: "Torrents", en: "Torrents" },
  performance: { fr: "Performance", en: "Performance" },
  retention: { fr: "Rétention", en: "Retention" },
  storage: { fr: "Stockage", en: "Storage" },
  cache: { fr: "Cache", en: "Cache" },
  security: { fr: "Sécurité", en: "Security" },
  interface: { fr: "Interface", en: "Interface" },
};

const englishOptions: Record<string, OptionCopy> = {
  WOS_DOWNLOAD_MAX_BYTES_PER_SECOND_PER_USER: { label: "Maximum rate per user", description: "HTTP download rate cap per user; 0 disables the limit." },
  WOS_DOWNLOAD_MAX_BYTES_PER_SECOND_GLOBAL: { label: "Maximum global rate", description: "Combined HTTP download rate cap; 0 disables the limit." },
  WOS_DOWNLOAD_MAX_CONCURRENT_PER_USER: { label: "Concurrent downloads per user", description: "Maximum number of file streams open for one account." },
  WOS_DOWNLOAD_LEASE_SECONDS: { label: "Download lease duration", description: "How long content remains protected while it is being downloaded." },
  WOS_FOLDER_ARCHIVE_MAX_BYTES: { label: "Maximum folder archive size", description: "Maximum source size accepted for a folder ZIP download." },
  WOS_TORRENT_MAX_ACTIVE_PER_USER: { label: "Active torrents per user", description: "Maximum active requests for one account." },
  WOS_QB_DOWNLOAD_MAX_BYTES_PER_SECOND_GLOBAL: { label: "Maximum global qBittorrent rate", description: "Rate cap shared by admitted V2 torrents; 0 disables the limit." },
  WOS_SCHEDULER_MAX_ACTIVE_GLOBAL: { label: "Global active torrents", description: "Maximum physical torrents admitted simultaneously by the V2 scheduler." },
  WOS_SCHEDULER_MAX_ACTIVE_PER_USER: { label: "Active scheduler torrents per user", description: "Maximum physical torrents assigned simultaneously to one account." },
  WOS_SCHEDULER_SMALL_TORRENT_BYTES: { label: "Small torrent threshold", description: "Maximum remaining size for the favored small-torrent class." },
  WOS_SCHEDULER_MEDIUM_TORRENT_BYTES: { label: "Medium torrent threshold", description: "Maximum remaining size for the scheduler's intermediate class." },
  WOS_SCHEDULER_DEFICIT_QUANTUM: { label: "Fairness quantum", description: "Credit added each round, multiplied by the account weight." },
  WOS_SCHEDULER_AGING_INTERVAL_SECONDS: { label: "Aging interval", description: "Waiting time required to earn an additional anti-starvation credit." },
  WOS_SCHEDULER_AGING_MAX_BONUS: { label: "Maximum aging bonus", description: "Maximum bounded cost reduction for a waiting torrent." },
  WOS_TORRENT_MAX_SIZE_BYTES: { label: "Maximum content size", description: "Maximum total size accepted after parsing the torrent file." },
  WOS_TORRENT_UPLOAD_MAX_FILE_BYTES: { label: "Maximum .torrent file size", description: "Maximum HTTP size for an uploaded bencode manifest." },
  WOS_QB_SYNC_INTERVAL_SECONDS: { label: "qBittorrent synchronization interval", description: "Delay between centralized worker synchronizations." },
  WOS_TORRENT_MAX_RETRY_ATTEMPTS: { label: "Maximum retry attempts", description: "Automatic retries before a durable error is recorded." },
  WOS_TORRENT_RETRY_BASE_SECONDS: { label: "Retry delay base", description: "Initial delay for the worker's exponential backoff." },
  WOS_WORKER_CONCURRENCY: { label: "Worker concurrency", description: "Number of torrent operations processed in parallel by the worker." },
  WOS_TORRENT_RETENTION_HOURS: { label: "Ready-content retention", description: "Minimum delay before unreferenced content becomes purgeable." },
  WOS_STORAGE_MANAGED_MAX_BYTES: { label: "Total managed storage", description: "Shared WOS content cap; 0 relies only on the disk reserve." },
  WOS_STORAGE_USER_MAX_BYTES: { label: "Logical quota per user", description: "Maximum volume referenced by one user; 0 disables this quota." },
  WOS_STORAGE_MIN_FREE_BYTES: { label: "Minimum free disk reserve", description: "Space that must remain free before accepting a new request." },
  WOS_STORAGE_MIN_FREE_PERCENT: { label: "Minimum free disk percentage", description: "Percentage of the disk that must remain free." },
  WOS_STORAGE_PRESSURE_WARNING_PERCENT: { label: "Storage warning threshold", description: "Usage level at which administration displays a warning." },
  WOS_STORAGE_PRESSURE_CRITICAL_PERCENT: { label: "Critical storage threshold", description: "Usage level at which new requests are blocked." },
  WOS_CACHE_DEFAULT_TTL_SECONDS: { label: "Default cache TTL", description: "Lifetime of Redis entries unrelated to progress." },
  WOS_CACHE_PROGRESS_TTL_SECONDS: { label: "Progress cache TTL", description: "Short lifetime of torrent progress snapshots." },
  WOS_CACHE_CONNECTION_POOL_SIZE: { label: "Redis connections", description: "Redis pool size used by the API and worker." },
  WOS_REQUEST_RATE_LIMIT_PER_MINUTE: { label: "Business requests per minute", description: "Functional limit applied to an authenticated account." },
  WOS_FILES_LIST_MAX_ENTRIES: { label: "Entries per folder", description: "Safety cap for a filesystem listing." },
  WOS_DIRECTORY_SIZE_MAX_ENTRIES: { label: "Legacy directory-size budget", description: "Compatibility option; directory sizes are no longer calculated." },
  WOS_DIRECTORY_SIZE_CACHE_SECONDS: { label: "Legacy directory-size cache", description: "Compatibility option; recursive scans are no longer cached." },
  WOS_HTTP_STREAM_CHUNK_BYTES: { label: "HTTP chunk size", description: "Maximum in-memory block size while streaming files." },
  WOS_ADMIN_REFRESH_INTERVAL_SECONDS: { label: "Administration refresh interval", description: "Default delay between automatic administration refreshes." },
};

const frenchOptions: Record<string, OptionCopy> = {
  WOS_DOWNLOAD_MAX_BYTES_PER_SECOND_PER_USER: { label: "Débit maximal par utilisateur", description: "Plafond de téléchargement HTTP par utilisateur ; 0 désactive la limite." },
  WOS_DOWNLOAD_MAX_BYTES_PER_SECOND_GLOBAL: { label: "Débit maximal global", description: "Plafond cumulé des téléchargements HTTP ; 0 désactive la limite." },
  WOS_DOWNLOAD_MAX_CONCURRENT_PER_USER: { label: "Téléchargements simultanés par utilisateur", description: "Nombre maximal de flux de fichiers ouverts par un même compte." },
  WOS_DOWNLOAD_LEASE_SECONDS: { label: "Durée d’une lease", description: "Durée de protection d’un contenu pendant son téléchargement." },
  WOS_FOLDER_ARCHIVE_MAX_BYTES: { label: "Taille maximale d’une archive dossier", description: "Volume source maximal accepté pour un téléchargement ZIP de dossier." },
  WOS_TORRENT_MAX_ACTIVE_PER_USER: { label: "Torrents actifs par utilisateur", description: "Nombre maximal de demandes actives pour un même compte." },
  WOS_QB_DOWNLOAD_MAX_BYTES_PER_SECOND_GLOBAL: { label: "Débit qBittorrent maximal global", description: "Plafond réparti entre les torrents V2 admis ; 0 désactive la limite." },
  WOS_SCHEDULER_MAX_ACTIVE_GLOBAL: { label: "Torrents actifs globaux", description: "Nombre maximal de torrents physiques admis simultanément par le scheduler V2." },
  WOS_SCHEDULER_MAX_ACTIVE_PER_USER: { label: "Torrents actifs par utilisateur dans le scheduler", description: "Nombre maximal de torrents physiques attribués simultanément à un même compte." },
  WOS_SCHEDULER_SMALL_TORRENT_BYTES: { label: "Seuil petit torrent", description: "Taille restante maximale de la classe favorisée des petits torrents." },
  WOS_SCHEDULER_MEDIUM_TORRENT_BYTES: { label: "Seuil torrent moyen", description: "Taille restante maximale de la classe intermédiaire du scheduler." },
  WOS_SCHEDULER_DEFICIT_QUANTUM: { label: "Quantum d’équité", description: "Crédit ajouté à chaque tour, multiplié par le poids du compte." },
  WOS_SCHEDULER_AGING_INTERVAL_SECONDS: { label: "Intervalle de vieillissement", description: "Temps d’attente nécessaire pour obtenir un crédit anti-famine supplémentaire." },
  WOS_SCHEDULER_AGING_MAX_BONUS: { label: "Bonus maximal de vieillissement", description: "Réduction maximale et bornée du coût d’un torrent qui attend." },
  WOS_TORRENT_MAX_SIZE_BYTES: { label: "Taille maximale d’un contenu", description: "Taille totale maximale acceptée après analyse du fichier .torrent." },
  WOS_TORRENT_UPLOAD_MAX_FILE_BYTES: { label: "Taille maximale du fichier .torrent", description: "Taille HTTP maximale du manifeste bencode déposé." },
  WOS_QB_SYNC_INTERVAL_SECONDS: { label: "Intervalle de synchronisation qBittorrent", description: "Délai entre deux synchronisations centralisées du worker." },
  WOS_TORRENT_MAX_RETRY_ATTEMPTS: { label: "Tentatives maximales", description: "Nombre de reprises automatiques avant passage en erreur durable." },
  WOS_TORRENT_RETRY_BASE_SECONDS: { label: "Base du délai de reprise", description: "Délai initial du backoff exponentiel du worker." },
  WOS_WORKER_CONCURRENCY: { label: "Concurrence du worker", description: "Nombre d’opérations torrent traitées en parallèle par le worker." },
  WOS_TORRENT_RETENTION_HOURS: { label: "Rétention des contenus prêts", description: "Durée minimale avant qu’un contenu sans référence devienne purgeable." },
  WOS_STORAGE_MANAGED_MAX_BYTES: { label: "Espace total géré", description: "Plafond des contenus partagés WOS ; 0 utilise uniquement la réserve disque." },
  WOS_STORAGE_USER_MAX_BYTES: { label: "Quota logique par utilisateur", description: "Volume maximal référencé par utilisateur ; 0 désactive ce quota." },
  WOS_STORAGE_MIN_FREE_BYTES: { label: "Réserve disque minimale", description: "Espace qui doit rester libre avant l’acceptation d’une nouvelle demande." },
  WOS_STORAGE_MIN_FREE_PERCENT: { label: "Réserve disque minimale en pourcentage", description: "Pourcentage du disque qui doit rester libre." },
  WOS_STORAGE_PRESSURE_WARNING_PERCENT: { label: "Seuil d’alerte stockage", description: "Occupation à partir de laquelle l’administration affiche un avertissement." },
  WOS_STORAGE_PRESSURE_CRITICAL_PERCENT: { label: "Seuil critique stockage", description: "Occupation à partir de laquelle les nouvelles demandes sont bloquées." },
  WOS_CACHE_DEFAULT_TTL_SECONDS: { label: "TTL cache par défaut", description: "Durée des entrées Redis non liées à la progression." },
  WOS_CACHE_PROGRESS_TTL_SECONDS: { label: "TTL des progressions", description: "Durée courte des snapshots de progression torrent." },
  WOS_CACHE_CONNECTION_POOL_SIZE: { label: "Connexions Redis", description: "Taille du pool Redis utilisé par l’API et le worker." },
  WOS_REQUEST_RATE_LIMIT_PER_MINUTE: { label: "Requêtes métier par minute", description: "Limite fonctionnelle appliquée à un compte authentifié." },
  WOS_FILES_LIST_MAX_ENTRIES: { label: "Éléments par dossier", description: "Plafond de sécurité d’un listing filesystem." },
  WOS_DIRECTORY_SIZE_MAX_ENTRIES: { label: "Ancien budget de calcul des tailles", description: "Option conservée pour compatibilité ; les tailles de dossiers ne sont plus calculées." },
  WOS_DIRECTORY_SIZE_CACHE_SECONDS: { label: "Ancien cache des tailles de dossiers", description: "Option conservée pour compatibilité ; aucun scan récursif n’est mis en cache." },
  WOS_HTTP_STREAM_CHUNK_BYTES: { label: "Taille des blocs HTTP", description: "Taille mémoire maximale d’un bloc lors du streaming de fichiers." },
  WOS_ADMIN_REFRESH_INTERVAL_SECONDS: { label: "Rafraîchissement de l’administration", description: "Délai par défaut entre deux actualisations automatiques." },
};

export function optionSectionLabel(id: string, locale: Locale, frenchFallback: string): string {
  return sections[id]?.[locale] ?? frenchFallback;
}

export function optionFieldCopy(
  key: string,
  locale: Locale,
  frenchFallback: OptionCopy,
): OptionCopy {
  return (locale === "fr" ? frenchOptions[key] : englishOptions[key]) ?? frenchFallback;
}

export const translatedOptionKeys = new Set(
  Object.keys(englishOptions).filter((key) => frenchOptions[key] !== undefined),
);
