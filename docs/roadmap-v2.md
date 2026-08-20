# Roadmap de réalisation de la V2

## Règles de livraison

- Chaque PR fonctionnelle part du dernier `develop` et cible `develop`.
- Une PR reste petite, cohérente, testée et réversible ; aucune feature ne cible directement
  `master`.
- Après cinq PR V2 mergées, `develop` est fusionnée dans `master`, la version est mise à jour,
  une release stable est publiée et `develop` est resynchronisée.
- La PR de réconciliation #35 précède la V2 et n’entre pas dans ce compteur.
- Une PR documente systématiquement migrations, options, variables `.env`, clés Redis, TTL,
  indexes, sécurité, tests, risques et rollback.

## Lot 1 — fondations d’orchestration

| PR | Contenu | Données/configuration | Validation principale |
| --- | --- | --- | --- |
| V2-00 | Architecture, machines d’état, versioning, tagline | Aucune migration ; `VERSION` canonique | Cohérence release/image, CI complète |
| V2-01 | Registre `.options`, API/UI admin, restart WOS, messages | `.options.example`, canal systemd WOS | Validation typée, écriture atomique, restart borné |
| V2-02 | Redis privé, cache-aside, health et audit SQL | Redis non publié, TTL `wos:v2:*`, indexes ciblés | Flush/restart Redis, fallback PostgreSQL, N+1 |
| V2-03 | `ManagedTorrent`, `TorrentRequest`, `TorrentFile` | Migration Alembic, contraintes et repositories | Concurrence et transitions |
| V2-04 | Parser bencode, upload et déduplication | Manifestes SQL, cache info-hash | Bencode hostile, passkey masquée, cache miss |

Après V2-04 : version `1.3.0`, merge `develop` vers `master`, release, déploiement et resync.

## Lot 2 — téléchargement partagé

| PR | Contenu | Point de contrôle |
| --- | --- | --- |
| V2-05 | Gateway qB en écriture | 204, `Ok.`, `Fails.`, 401, timeout ; save path fixe |
| V2-06 | Worker et synchronisation cache | Polling centralisé, retry, reprise après crash |
| V2-07 | Drop zone accessible | Drag/drop, clavier, mobile, validation `.torrent` |
| V2-08 | Vue « Mes téléchargements » | Isolation par utilisateur, pagination, snapshots cache |
| V2-09 | Téléchargements de contenu partagé | HTTP Range conservé, ownership, manifeste |

Après V2-09 : version `1.4.0`, release stable, déploiement et resync.

## Lot 3 — lifecycle et exploitation

| PR | Contenu | Point de contrôle |
| --- | --- | --- |
| V2-10 | Annulation et références partagées | Une annulation ne casse jamais une autre demande |
| V2-11 | Rétention, `DownloadLease`, purge | Aucune purge pendant un flux actif |
| V2-12 | Quotas, pression disque, limites de débit | Toutes les limites viennent de `.options` |
| V2-13 | Torrent Manager et réconciliation admin | Externes en lecture seule, WOS-only pour mutations |
| V2-14 | Sécurité reverse proxy et refactor frontend | Trusted proxy, CSP/HSTS/noindex, UX cohérente |

Après V2-14 : version `1.5.0`, release stable, déploiement et resync.

## Finalisation

| PR | Contenu | Critère de sortie |
| --- | --- | --- |
| V2-15 | Performance des téléchargements | Mesures 10/20 flux, CPU/RAM/throughput ; Nginx seulement si justifié |
| V2-16 | Hardening, charge et final V2 | 100 comptes, pannes Redis/Postgres/worker/qB, audit et rollback |

Après V2-16 : version `2.0.0`, release finale, déploiement et resynchronisation de
`develop`.

## Registre de configuration prévu

Les clés exactes et leurs bornes seront fixées en V2-01. Le registre doit au minimum couvrir
les limites par utilisateur et globales de téléchargement, la concurrence, la taille des
uploads `.torrent`, le nombre de torrents actifs, la rétention, l’espace géré, l’espace libre
minimal, les seuils de pression disque, les intervalles de sync/admin, les retries, les
leases, le rate-limit et les TTL cache.

Une option inconnue, sensible, hors bornes ou d’un type incorrect est rejetée. Les chemins,
URLs d’infrastructure, secrets et commandes ne sont pas des options fonctionnelles.

## Namespaces Redis prévus

| Namespace | Usage | Autorité | TTL initial à décider en V2-02 |
| --- | --- | --- | --- |
| `wos:v2:torrent:hash:<hash>` | info-hash vers UUID | PostgreSQL | Oui |
| `wos:v2:torrent:<uuid>:snapshot` | progression/vitesse/ETA | qB + snapshot SQL | Court |
| `wos:v2:user:<uuid>:requests` | IDs de demandes visibles | PostgreSQL | Oui |
| `wos:v2:request:<uuid>` | résumé de demande | PostgreSQL | Oui |
| `wos:v2:ratelimit:<scope>` | compteurs de requêtes | Redis volatil | Fenêtre |
| `wos:v2:download:<user>:leases` | accélération des leases | PostgreSQL si purge | Court |

Les TTL ne sont pas figés dans cette PR : ils seront des options typées et testées. Aucune
clé critique ne sera nécessaire pour reconstruire le métier.

## Gabarit de compte rendu de PR

1. Objectif et périmètre.
2. Changements d’architecture et contrats.
3. Migrations et compatibilité rollback.
4. Options ajoutées ou modifiées.
5. Variables `.env` ajoutées ou modifiées.
6. Clés Redis, TTL et stratégie d’invalidation.
7. Indexes et requêtes SQL.
8. Analyse de sécurité.
9. Tests locaux et CI.
10. Risques résiduels et rollback.
11. État de la roadmap et prochaine PR.

## Gabarit de compte rendu de release

Le rapport indique les PR incluses, version, SHA `master`, tag, URL de release, CI, résultat
du déploiement, versions frontend/backend, migrations Redis/`.options` et SHA de resync
`develop`.
