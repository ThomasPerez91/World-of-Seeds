import type { Locale } from "../../i18n";

interface Copy { label: string; description: string }
type BilingualCopy = Record<Locale, Copy>;

const sectionLabels: Record<string, Record<Locale, string>> = {
  proxy: { fr: "Proxy", en: "Proxy" },
  spoofing: { fr: "Simulation", en: "Simulation" },
  anti_detection: { fr: "Anti-détection", en: "Anti-detection" },
  ssl: { fr: "TLS", en: "TLS" },
  stats: { fr: "Statistiques", en: "Statistics" },
  web: { fr: "Interface Web", en: "Web interface" },
  advanced: { fr: "Avancé", en: "Advanced" },
};

function both(frLabel: string, frDescription: string, enLabel: string, enDescription: string): BilingualCopy {
  return { fr: { label: frLabel, description: frDescription }, en: { label: enLabel, description: enDescription } };
}

const fields: Record<string, BilingualCopy> = {
  "proxy.listen_port": both("Port du proxy", "Port interne écouté par NewGreedy.", "Proxy port", "Internal port used by NewGreedy."),
  "proxy.tracker_timeout": both("Délai des trackers", "Délai maximal d’une requête tracker, en secondes.", "Tracker timeout", "Maximum tracker request duration in seconds."),
  "spoofing.upload_mode": both("Mode d’upload", "Algorithme utilisé pour simuler l’upload.", "Upload mode", "Algorithm used to simulate upload."),
  "spoofing.target_ratio": both("Ratio cible", "Ratio upload/download visé.", "Target ratio", "Target upload/download ratio."),
  "spoofing.target_ratio_buffer": both("Marge du ratio", "Marge interne ajoutée au ratio cible.", "Ratio buffer", "Margin added to the target ratio."),
  "spoofing.anti_clustering": both("Espacer les annonces", "Évite de regrouper les annonces simulées.", "Space announces", "Avoids grouping simulated announces."),
  "spoofing.max_ratio_per_torrent": both("Ratio maximal par torrent", "Plafond de ratio pour un torrent.", "Maximum ratio per torrent", "Ratio cap for one torrent."),
  "spoofing.max_global_ratio_per_tracker": both("Ratio maximal par tracker", "Plafond cumulé pour un domaine de tracker.", "Maximum ratio per tracker", "Combined cap for one tracker domain."),
  "spoofing.catch_up_factor": both("Facteur de rattrapage", "Agressivité du rattrapage vers le ratio cible.", "Catch-up factor", "Aggressiveness when catching up to the target ratio."),
  "spoofing.seed_credit_mb": both("Crédit seed", "Mégaoctets crédités par annonce en seed pur.", "Seed credit", "Megabytes credited per pure-seeding announce."),
  "spoofing.seed_target_mb": both("Objectif seed", "Objectif d’upload simulé en mégaoctets.", "Seed target", "Simulated upload target in megabytes."),
  "spoofing.seeding_dl_ratio": both("Ratio seed réservé", "Paramètre réservé par NewGreedy.", "Reserved seed ratio", "Reserved NewGreedy setting."),
  "spoofing.max_simulated_speed_mbps": both("Débit simulé maximal", "Débit maximal simulé en Mo/s.", "Maximum simulated rate", "Maximum simulated rate in MB/s."),
  "spoofing.upload_noise_pct": both("Variation de l’upload", "Bruit appliqué à chaque incrément, en pourcentage.", "Upload variation", "Noise applied to each increment as a percentage."),
  "spoofing.stagnation_probability": both("Probabilité de stagnation", "Probabilité d’une stagnation volontaire par annonce.", "Stagnation probability", "Probability of intentional stagnation per announce."),
  "spoofing.auto_stop_at_target": both("Arrêt au ratio cible", "Arrête l’injection une fois le ratio cible atteint.", "Stop at target ratio", "Stops injection once the target ratio is reached."),
  "anti_detection.user_agent_mode": both("Mode du User-Agent", "Rotation automatique ou valeur fixe.", "User-Agent mode", "Automatic rotation or a fixed value."),
  "anti_detection.user_agent_value": both("User-Agent fixe", "Valeur utilisée en mode fixe.", "Fixed User-Agent", "Value used in fixed mode."),
  "anti_detection.spoof_user_agent": both("Simuler le User-Agent", "Aligne le User-Agent sur l’identifiant du pair.", "Simulate User-Agent", "Aligns the User-Agent with the peer identifier."),
  "anti_detection.spoof_peer_id": both("Simuler le peer ID", "Génère un identifiant de pair par session.", "Simulate peer ID", "Generates one peer identifier per session."),
  "anti_detection.spoof_peers": both("Faire varier les pairs", "Fait varier le nombre de pairs demandé.", "Vary peers", "Varies the requested peer count."),
  "anti_detection.peer_variance": both("Variation des pairs", "Amplitude de variation du nombre de pairs.", "Peer variation", "Range of peer-count variation."),
  "anti_detection.spoof_port": both("Simuler le port", "Attribue un port stable par torrent.", "Simulate port", "Assigns a stable port per torrent."),
  "anti_detection.port_range": both("Plage de ports", "Plage inclusive au format 6881-6999.", "Port range", "Inclusive range in 6881-6999 format."),
  "anti_detection.spoof_headers": both("Simuler les en-têtes", "Ajoute des en-têtes HTTP cohérents.", "Simulate headers", "Adds consistent HTTP headers."),
  "anti_detection.intercept_scrape": both("Intercepter les scrapes", "Ignore les requêtes scrape.", "Intercept scrapes", "Ignores scrape requests."),
  "anti_detection.tracker_whitelist": both("Trackers autorisés", "Domaines séparés par des virgules ; vide pour tous.", "Allowed trackers", "Comma-separated domains; empty allows all."),
  "anti_detection.tracker_blacklist": both("Trackers ignorés", "Domaines séparés par des virgules.", "Ignored trackers", "Comma-separated domains."),
  "ssl.ssl_verify_trackers": both("Vérifier les certificats", "Vérifie les certificats TLS des trackers.", "Verify certificates", "Verifies tracker TLS certificates."),
  "stats.persist_stats": both("Conserver les statistiques", "Enregistre les statistiques sur disque.", "Persist statistics", "Stores statistics on disk."),
  "stats.auto_purge_stopped": both("Purger les torrents arrêtés", "Retire les statistiques à la réception de l’événement stopped.", "Purge stopped torrents", "Removes statistics when a stopped event is received."),
  "web.web_enabled": both("Interface Web NewGreedy", "Active l’API et l’interface internes.", "NewGreedy Web interface", "Enables the internal API and interface."),
  "web.web_host": both("Adresse d’écoute Web", "Adresse interne de l’API NewGreedy.", "Web listen address", "Internal NewGreedy API address."),
  "web.web_port": both("Port Web", "Port interne de l’API NewGreedy.", "Web port", "Internal NewGreedy API port."),
  "advanced.min_announce_interval": both("Intervalle minimal", "Délai minimal entre deux annonces, en secondes.", "Minimum interval", "Minimum delay between announces in seconds."),
  "advanced.log_level": both("Niveau des logs", "Niveau de verbosité de NewGreedy.", "Log level", "NewGreedy logging verbosity."),
  "advanced.multi_tracker_delay_min": both("Délai tracker minimal réservé", "Paramètre réservé par NewGreedy.", "Reserved minimum tracker delay", "Reserved NewGreedy setting."),
  "advanced.multi_tracker_delay_max": both("Délai tracker maximal réservé", "Paramètre réservé par NewGreedy.", "Reserved maximum tracker delay", "Reserved NewGreedy setting."),
  "advanced.event_anomaly_probability": both("Probabilité d’événement", "Probabilité d’injecter un événement started.", "Event probability", "Probability of injecting a started event."),
  "advanced.corrupt_field_probability": both("Probabilité de corrupt", "Probabilité d’ajouter le champ corrupt.", "Corrupt field probability", "Probability of adding the corrupt field."),
  "advanced.stall_announce_threshold": both("Seuil de blocage réseau", "Nombre d’annonces sans download avant le signalement.", "Network stall threshold", "Announces without download before reporting a stall."),
  "advanced.min_announces_before_stagnation": both("Seuil de stagnation", "Nombre minimal d’annonces avant une stagnation.", "Stagnation threshold", "Minimum announces before stagnation."),
  "advanced.interval_jitter_pct": both("Variation de l’intervalle", "Variation appliquée à l’intervalle d’annonce.", "Interval variation", "Variation applied to the announce interval."),
  "advanced.inject_hours": both("Plage d’injection", "Heures inclusives au format 0-23.", "Injection hours", "Inclusive hours in 0-23 format."),
};

export function newGreedySectionLabel(id: string, locale: Locale, fallback: string): string {
  return sectionLabels[id]?.[locale] ?? fallback;
}

export function newGreedyFieldCopy(id: string, locale: Locale, fallback: Copy): Copy {
  return fields[id]?.[locale] ?? fallback;
}

export const translatedNewGreedyFieldIds = new Set(Object.keys(fields));
