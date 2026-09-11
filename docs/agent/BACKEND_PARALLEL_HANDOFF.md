# Backend 2.1 — contrat de handoff frontend

- `BASE_SHA`: `d40aecc0732bae3de8e14e8027cbe782887cac60`
- Branche d’intégration: `integration/backend-2.1`
- Migrations: `20260911_26_grace_period_seeding.py` après
  `20260910_25_subscription_lifecycle.py`

## Cycle de vie d’un abonnement

`TorrentRequest` est l’abonnement d’un utilisateur. `ManagedTorrent` reste la copie physique
unique d’un infohash.

- Nouvelle option: `WOS_TORRENT_AUTO_UNSUBSCRIBE_HOURS`, catégorie `retention`, défaut `48`,
  minimum `1`, maximum `2160`, modifiable sans redémarrage.
- `WOS_TORRENT_RETENTION_HOURS` désigne désormais uniquement la grâce physique après le dernier
  désabonnement. Elle ne constitue jamais une échéance utilisateur.
- À chaque passage d’un abonnement à `ready`, `ready_at` et `unsubscribe_at` sont propres à cet
  abonnement. Un utilisateur qui rejoint un contenu physique déjà READY reçoit une nouvelle
  période complète.
- `expires_at` est l’instant où l’expiration a réellement été appliquée. `unsubscribe_at` est
  l’échéance future.
- La migration ajoute `torrent_requests.unsubscribe_at` et son index partiel
  `(state, unsubscribe_at, id)`. Les anciennes lignes READY sont reprises par lots bornés depuis
  l’heure du rollout, jamais depuis leur ancien `ready_at`.
- Le `DELETE /api/v2/torrents/{request_id}` désabonne seulement l’utilisateur. Tant qu’un autre
  abonnement actif existe, le torrent physique reste READY. Le dernier désabonnement crée la
  grâce physique et un job durable de purge.
- Le listing standard ne renvoie que `requested`, `active` et `ready`; `cancelled`, `expired` et
  les lignes READY déjà arrivées à échéance sont masquées.
- Une lease déjà engagée peut finir après désabonnement/expiration. Aucune nouvelle lease ne peut
  démarrer à ou après `unsubscribe_at`.

## Grace period seeding semantics

- La suppression ou l’expiration du dernier abonnement ne modifie pas immédiatement l’état
  physique du torrent. Elle renseigne seulement `purge_after` et crée un job `PURGE_TORRENT`
  durable, disponible à cette échéance.
- Pendant la grâce, un torrent READY continue de seeder et de se synchroniser avec qBittorrent.
  Un torrent DOWNLOADING déjà actif continue également son téléchargement avec ses consignes
  qB existantes. Aucun arrêt qB ou NewGreedy n’est déclenché par le désabonnement.
- Une nouvelle souscription pendant la grâce réutilise la même copie physique, efface
  `purge_after` et annule le job de purge, sans nouvel ajout qB ni duplication du stockage.
- À l’échéance seulement, le worker verrouille le torrent et recompte les abonnements actifs. Si
  l’un d’eux existe, il annule la purge. Sinon il passe en `PURGE_PENDING`, demande l’arrêt durable
  au scheduler, attend les leases actives, puis supprime qB et le contenu partagé.
- Le verrou SQL sérialise la course entre réabonnement et activation de purge : le gagnant fixe
  l’état durable, sans suppression physique si un abonnement actif est visible.

### Réponse torrent

```json
{
  "id": "uuid",
  "state": "ready",
  "unsubscribe_at": "2026-09-12T12:00:00Z",
  "retention_expires_at": "2026-09-12T12:00:00Z"
}
```

`retention_expires_at` reste temporairement présent pour compatibilité, mais alias exactement
`unsubscribe_at`. Le frontend doit lire `unsubscribe_at` et ne jamais interpréter ce champ comme
la purge physique.

## Dossiers téléchargeables

Nouvelle option: `WOS_FOLDER_ARCHIVE_MAX_CONCURRENT_GLOBAL`, catégorie `downloads`, défaut `4`,
minimum `1`, maximum `16`. Elle borne les générations ZIP simultanées par processus API.

### Lister les sous-dossiers immédiats

```http
GET /api/v2/torrents/{request_id}/download-directories
GET /api/v2/torrents/{request_id}/download-directories?parent=Series/Saison%201
```

```json
{
  "snapshot_id": "64 caractères hexadécimaux",
  "path": "Series",
  "directories": [
    {
      "name": "Saison 1",
      "relative_path": "Series/Saison 1",
      "file_count": 12,
      "total_size": 987654321,
      "archive_available": true
    }
  ]
}
```

Les résultats proviennent exclusivement du manifeste SQL `TorrentFile.relative_path`. Aucune
adresse physique, infohash, clé de stockage ou inspection récursive du filesystem n’est exposée.

### Télécharger un sous-dossier

```http
GET /api/v2/torrents/{request_id}/download-folder-archive?path=Series/Saison%201&snapshot={snapshot_id}
```

La réponse est un ZIP streaming `ZIP_STORED`, Zip64, dont la racine est le dernier dossier demandé
(`Saison 1/...` dans cet exemple). Les limites de taille et de nombre de fichiers sont calculées
sur ce sous-arbre, indépendamment de la disponibilité du ZIP global du torrent.

Codes métier principaux:

- `404 download_directories_not_found`: droit ou contenu indisponible;
- `404 download_directory_not_found`: dossier absent ou vide;
- `404 folder_archive_not_found`: droit expiré/révoqué ou contenu non READY;
- `409 torrent_manifest_unavailable`: manifeste non exploitable;
- `409 download_snapshot_changed`: le manifeste a changé;
- `413 folder_archive_too_large`: limite d’octets du sous-dossier dépassée;
- `413 folder_archive_too_many_entries`: limite de 50 000 fichiers dépassée;
- `422 download_directory_path_invalid`: chemin absolu, traversal, backslash, NUL ou normalisation
  ambiguë;
- `429 download_concurrency_reached`: limite de leases utilisateur;
- `429 folder_archive_busy`: concurrence ZIP globale atteinte;
- `503 download_options_unavailable`: options SQL indisponibles.

## Adaptations frontend attendues

1. Utiliser `unsubscribe_at` pour le compte à rebours et l’état téléchargeable.
2. Retirer immédiatement du listing une demande supprimée ou arrivée à échéance; ne pas afficher
   l’historique terminal dans la vue standard.
3. Après le manifeste, proposer le ZIP global uniquement si son `archive_available` vaut `true`.
4. Appeler `download-directories` pour afficher les dossiers; utiliser `parent` pour naviguer.
5. Afficher `file_count`, `total_size` et l’action ZIP par dossier seulement quand
   `archive_available` vaut `true`.
6. Réutiliser le `snapshot_id` du listing dans `download-folder-archive` et rafraîchir le listing
   sur `download_snapshot_changed`.
7. Encoder `path`/`parent` comme paramètres de query sans les normaliser ni remplacer les espaces,
   caractères Unicode, `%` ou `_`.
8. Traduire les codes 404/409/413/422/429/503 ci-dessus; un 413 de ZIP global n’interdit pas les
   ZIP de sous-dossiers disponibles.
