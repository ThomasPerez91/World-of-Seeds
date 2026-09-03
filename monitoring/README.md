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

## Sources Prometheus actuelles

- métriques applicatives WOS (`/api/v2/metrics`) ;
- Prometheus lui-même ;
- `node-exporter` pour l'hôte Linux et le RAID logiciel ;
- `cAdvisor` pour Docker.

Les prochains enrichissements peuvent ajouter des exporters dédiés PostgreSQL, Redis, SMART et des probes HTTP sans modifier la structure des dashboards existants.

## Rétention

Le profil Rise2 recommande 30 jours via `WOS_V2_PROMETHEUS_RETENTION=30d`. La valeur reste configurable dans l'environnement hors Git.

## Alertes

Les règles Prometheus couvrent notamment : indisponibilité de cible, queue de jobs bloquée, erreurs/retries, dérive scheduler, pression stockage, qBittorrent/Redis, erreurs HTTP 5xx, latences anormales, CPU/RAM/swap, remplissage disques/inodes, I/O, processus bloqués, RAID `md10`, erreurs réseau, redémarrages répétés et disparition du conteneur NewGreedy.
