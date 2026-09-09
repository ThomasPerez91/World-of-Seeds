# Monitoring World of Seeds V2

Le monitoring V2 est provisionné depuis Git afin qu'un reset complet du pilote ou une réinstallation propre puisse reconstruire les dashboards et les règles d'alerte sans configuration manuelle dans Grafana.

## Persistance

- `grafana_v2_data` conserve la base interne Grafana entre les recréations ordinaires de conteneur.
- `prometheus_v2_data` conserve l'historique Prometheus entre les recréations ordinaires de conteneur.
- un `docker compose down --volumes` ou un nettoyage explicite des volumes détruit ces données locales ; les dashboards et alertes versionnés dans Git seront néanmoins reprovisionnés au prochain démarrage.
- les dashboards provisionnés sont volontairement non éditables depuis l'UI (`allowUiUpdates: false`) afin que Git reste la source de vérité.

## Dashboards provisionnés

- **World of Seeds V2 — Vue opérationnelle** : état général, API, jobs, scheduler, stockage et dépendances.
- **Rise2 — Serveur, stockage & réseau** : CPU, mémoire, swap, load, systèmes de fichiers, inodes, I/O, RAID `md10`, réseau et processus bloqués.
- **Rise2 — Docker & conteneurs** : CPU, RAM, réseau, I/O, présence et redémarrages de chaque conteneur.
- **World of Seeds V2 — Jobs, stockage & dépendances** : files de jobs, retries, leases, stockage, qBittorrent, Redis et ressources qB/NewGreedy vues par cAdvisor.
- **Rise2 — PostgreSQL, Redis, SMART & probes** : connexions/transactions PostgreSQL, mémoire/clients Redis, disponibilité HTTP interne et santé SMART des disques physiques.
- **World of Seeds V2 — Torrents & NewGreedy** : inventaire qB borné, états et progression par hash court, débits DL/UL, statistiques NewGreedy par hash, cumul tracker et âge des announces.

## Sources Prometheus

- métriques applicatives WOS (`/api/v2/metrics`) ;
- Prometheus lui-même ;
- `node-exporter` pour l'hôte Linux, le RAID logiciel et le textfile SMART ;
- `cAdvisor` pour Docker ;
- `postgres-exporter` pour PostgreSQL ;
- `redis_exporter` pour Redis ;
- `blackbox_exporter` pour les probes HTTP internes API, NewGreedy et qBittorrent ;
- `torrent-metrics` sur Rise2 pour les snapshots qBittorrent et le `stats.json` NewGreedy.

La couche exporters Rise2 est définie dans `deploy/compose.rise2.observability.v2.yaml`. Elle complète le compose principal sans exposer de nouveau port sur l'hôte. `scripts/rise2_v2_observability_apply.sh` applique uniquement cette couche et recrée les services de monitoring concernés.

`torrent-metrics` est un sidecar en lecture seule, sans port hôte. Il lit le registre d'intégration privé uniquement pour authentifier les lectures qBittorrent et monte uniquement `stats.json` côté NewGreedy. Aucune passkey, aucun mot de passe et aucune URL d'annonce n'est exporté. L'inventaire détaillé est borné à 200 torrents et n'expose que les huit premiers caractères de l'infohash afin de limiter la cardinalité Prometheus. La configuration Prometheus spécifique Rise2 est `monitoring/prometheus/prometheus.rise2.yml`; le profil local conserve `prometheus.yml` et n'a pas besoin des secrets de production.

## SMART sans conteneur privilégié supplémentaire

La collecte SMART utilise `smartctl` sur l'hôte via `scripts/rise2_v2_smart_metrics.py`, puis écrit un fichier Prometheus dans le textfile collector de `node-exporter`. Les unités `world-of-seeds-v2-smart-metrics.service` et `.timer` sont versionnées dans `deploy/`.

Le helper d'application installe ces unités et démarre le timer pour la session courante, mais ne l'active pas automatiquement au boot. L'activation persistante reste une décision OPS explicite.

## Rétention

Le profil Rise2 recommande 30 jours via `WOS_V2_PROMETHEUS_RETENTION=30d`. La valeur reste configurable dans l'environnement hors Git.

## Alertes

Les règles Prometheus couvrent notamment : indisponibilité de cible, queue de jobs bloquée, erreurs/retries, dérive scheduler, pression stockage, qBittorrent/Redis, erreurs HTTP 5xx, latences anormales, CPU/RAM/swap, remplissage disques/inodes, I/O, processus bloqués, RAID `md10`, erreurs réseau, redémarrages répétés, disparition du conteneur NewGreedy, disponibilité PostgreSQL/Redis exporters, deadlocks PostgreSQL, évictions Redis, échec des probes HTTP internes et anomalies SMART.
