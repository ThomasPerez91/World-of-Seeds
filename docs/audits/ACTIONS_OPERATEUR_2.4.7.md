# Actions d’exploitation issues de l’audit 2.4.6

Ce document liste ce que la livraison 2.4.7 ne pouvait pas traiter par le canal de déploiement
automatique : fichiers protégés (compose, configuration qBittorrent, preflight, scripts
qB/NewGreedy), migrations, accès à l’hôte ou décisions de politique. Chaque action renvoie au
constat du rapport `Rapport_audit_World_of_Seeds_2.4.6.pdf`. Aucune n’a été exécutée.

Les modifications de fichiers protégés suivent le flux manuel habituel : branche, revue,
déploiement manuel documenté dans `docs/deployment-rise2-v2.md`.

## 1. Priorité haute

| Constat | Action | Où |
|---|---|---|
| IN-01 (critique) | Copie quotidienne hors hôte des paires `.dump.age` + `.json` et d’une archive chiffrée de `/etc/world-of-seeds-v2` ; clé privée `age` hors hôte ; `OnFailure=` sur le service de sauvegarde ; test de restauration mensuel sur un hôte distinct | hôte, `docs/backup-restore-rise2-v2.md` |
| IN-03 | Ajouter un service Alertmanager (récepteur Discord ou e-mail, secret fichier), bloc `alerting` dans les `prometheus*.yml`, règle watchdog et heartbeat externe | compose, Prometheus |
| IN-04 | `mem_limit`/`cpus` sur qBittorrent, Prometheus, Grafana, cAdvisor, API/workers ; `oom_score_adj` négatif sur PostgreSQL et Redis | compose |
| IN-06 | Healthchecks (heartbeat fichier) pour worker/scheduler, sondes pour ingress/Prometheus/Grafana ; `docker compose up --wait` dans le helper | compose, helper |
| IN-07 | Rôles PostgreSQL distincts (migrateur, application, exporter, sauvegarde) | init SQL, compose |
| SE-12, IN-16 | La liste de changements bloqués du dépôt couvre maintenant les scripts et unités exécutés en root ; **réinstaller le helper racine** (`scripts/rise2_v2_deploy_helper_install.sh`) pour qu’elle s’applique. Étape suivante : passer la liste de changements bloqués du helper en liste autorisée (`backend/`, `frontend/`, `docs/`, `Dockerfile`, `VERSION`) ; contrôler la propriété `root:root` du checkout ; revues obligatoires sur l’environnement `rise2-production` | helper root, GitHub |

## 2. Périmètre C411 et ingress

| Constat | Action | Où |
|---|---|---|
| SE-02 | Désactiver DHT, PeX et LSD dans la configuration qBittorrent (la normalisation des métainfos retire déjà web seeds et nœuds) | `deploy/qbittorrent.rise2.conf` |
| SE-05, OP-18 | Limite de corps spécifique à `POST /api/v2/torrents` (≈ 5 Mo) et `read_body` côté Caddy ; aligner le maximum de `WOS_TORRENT_UPLOAD_MAX_FILE_BYTES` (32 Mio) sur l’ingress (20 Mo) | `deploy/Caddyfile.rise2.v2` |
| LS-23 | Exclure les routes de téléchargement de `encode` (les réponses portent déjà `no-transform`) | Caddyfile |
| IN-19, SE-14 | Grafana derrière une liste d’IP ou une authentification Caddy ; cAdvisor via un proxy de socket Docker en lecture seule | Caddyfile, compose |
| IN-20 | Rotation des journaux (`json-file`, `max-size`) et journal d’accès Caddy | compose, Caddyfile |
| IN-25 | `stop_grace_period`, `maxmemory` Redis, taille de pool SQL explicite, certificat CA qBittorrent chargé une seule fois | compose, scripts |

## 3. Base de données (migrations à préparer avec une entrée dans `deploy/migration-policy.json`)

| Constat | Migration |
|---|---|
| IN-05 | Index `torrent_jobs (finished_at) WHERE finished_at IS NOT NULL`, `torrent_jobs (managed_torrent_id, job_type, state, created_at DESC)`, `download_leases (torrent_file_id)` ; retirer les index redondants. La purge de 2.4.7 réduit d’abord la table : indexer ensuite |
| IN-08 | 15 colonnes `timestamp` vers `timestamptz` (`USING col AT TIME ZONE 'UTC'`) ; ajouter `alembic check` à la CI ; centraliser `as_utc()` |
| OP-07, OP-11 | Supprimer les options mortes (liste `INACTIVE_OPTION_KEYS`) et ajouter `CHECK` des valeurs dans leurs bornes |
| SE-01, IN-21 | Chiffrer les passkeys au repos (clé applicative hors base) |

Chaque migration qui change le head exige la sauvegarde chiffrée préalable et la restauration
isolée décrites dans `docs/deployment-rise2-v2.md`.

## 4. Décisions de politique à prendre

- Plafond de voies rapides par compte et exemption administrateur sur les fichiers (LS-02,
  OP-04) : aujourd’hui un compte peut occuper toutes les voies rapides.
- Quota de stockage par utilisateur : `WOS_STORAGE_USER_MAX_BYTES` vaut 0 par défaut et aucun
  seed n’installe 150 Go ; vérifier la valeur réelle en base (OP-05).
- Exemption administrateur du nombre de torrents actifs et du quota ; lot maximal côté serveur.
- Limite de requêtes pour l’API authentifiée : l’option `WOS_REQUEST_RATE_LIMIT_PER_MINUTE`
  n’a jamais été branchée et est masquée ; choisir les seuils avant de l’implémenter (SE-06).
- Authentification renforcée des administrateurs (SE-13).

## 5. Vérifications après déploiement de 2.4.7

- `/api/v1/health/live` et `/ready` : HTTP 200, version 2.4.7.
- `curl -I` sur `/` : `Cache-Control: no-cache` ; sur `/assets/<bundle>` : `immutable`.
- Écran d’administration : champs de passkey vides avec « Configurée », sections cache et
  options mortes absentes.
- Taille de `torrent_jobs` après la première heure : décroissante (purge des synchronisations).
- Dépôt d’un lot de `.torrent` pendant qu’un second compte en dépose un autre : les erreurs
  « validation occupée » se résorbent par reprise automatique.
