# Import V1 optionnel vers V2

## Limites et garanties

L'import V1 est une opération volontaire, distincte des migrations Alembic et désactivée par
défaut. Il lit uniquement `users` et `user_torrents` dans une transaction PostgreSQL déclarée
`READ ONLY`. Il ne met jamais à jour, ne verrouille pas pour écriture et ne supprime aucune ligne,
configuration, donnée ou session V1.

La V1 ne connaît ni taille totale fiable, ni manifeste V2, ni route qB/NewGreedy, ni clé de
stockage opaque. L'import ne les invente pas :

- un `UserTorrent` est associé au compte V2 de même nom, sans tenir compte de la casse ;
- un infohash déjà géré avec le même nom reçoit seulement un nouveau `TorrentRequest` ;
- un infohash absent crée un `ManagedTorrent` de taille `0`, en état `ERROR`, marqué
  `v1_import_reconcile_required`, puis un `TorrentRequest` `REQUESTED` ;
- ce placeholder n'est jamais ordonnancé avant une réconciliation explicite qB/filesystem ;
- aucun compte V2 n'est créé et aucun compte inactif n'est réactivé.

Les conflits bloquent toute la transaction. Il n'existe ni import partiel implicite, ni adoption
automatique d'un torrent externe, ni copie de contenu. Le rapport contient seulement les UUID de
source et des codes techniques ; aucun username, nom de torrent ou infohash n'y apparaît.

## Prérequis

1. restaurer et valider une sauvegarde V2 avec la procédure V2-30 ;
2. attribuer à cette sauvegarde un identifiant technique sans espace ;
3. prendre un snapshot immuable de la base V1 ou utiliser un replica ;
4. créer un rôle PostgreSQL V1 limité à `CONNECT` et `SELECT` sur `users`/`user_torrents` ;
5. placer son URL `postgresql+asyncpg://...` seule dans un fichier `0600` ;
6. vérifier que tous les comptes attendus existent déjà et sont actifs en V2.

La V1 reste en production et intacte. L'inventaire est sensible car il contient usernames, noms et
infohashes : fichier `0600`, transfert chiffré, rétention courte et jamais de dépôt Git.

## 1. Produire l'inventaire en lecture seule

```bash
python -m app.cli inventory-v1 \
  --source-url-file /run/keys/wos-v1-read-only-url \
  --snapshot-id v1-YYYYMMDDTHHMMSSZ \
  --output /var/lib/world-of-seeds-v2/import/v1-YYYYMMDDTHHMMSSZ.json
```

L'outil refuse les URLs non PostgreSQL, les fichiers de credentials accessibles au groupe/autres,
les inventaires de plus de 100 000 lignes et toute sortie existante. La requête est bornée,
ordonnée et exécutée après `SET TRANSACTION READ ONLY`. Conserver l'empreinte SHA-256 affichée.

## 2. Exécuter le dry-run

Le dry-run est le comportement par défaut et n'ouvre aucune transaction d'écriture :

```bash
python -m app.cli import-v1 \
  --inventory /var/lib/world-of-seeds-v2/import/v1-YYYYMMDDTHHMMSSZ.json \
  --report /var/lib/world-of-seeds-v2/import/dry-run-YYYYMMDDTHHMMSSZ.json
```

Codes de conflit principaux :

| Code | Décision requise |
| --- | --- |
| `target_user_missing` | créer/approuver le compte V2 ou exclure la ligne dans un nouvel inventaire |
| `target_user_inactive` | faire approuver le traitement du compte, sans réactivation automatique |
| `canonical_name_conflict` | vérifier manuellement que l'infohash et le contenu correspondent |
| `target_torrent_terminal` | terminer/annuler la purge avant un nouveau dry-run |
| `target_request_history_conflict` | décider explicitement si un droit V2 annulé doit être recréé |

Deux lignes V1 pour le même utilisateur/infohash, un hash non canonique, un timestamp sans fuseau
ou un champ inattendu invalident l'inventaire avant toute requête V2.

## 3. Appliquer après approbation

Arrêter temporairement ingress/API, workers et scheduler V2 pour empêcher une création concurrente.
PostgreSQL protège aussi un import identique par verrou transactionnel et empreinte unique. La V1
peut continuer à servir ; l'opération n'y possède aucun droit d'écriture.

```bash
python -m app.cli import-v1 \
  --inventory /var/lib/world-of-seeds-v2/import/v1-YYYYMMDDTHHMMSSZ.json \
  --report /var/lib/world-of-seeds-v2/import/apply-YYYYMMDDTHHMMSSZ.json \
  --apply \
  --confirm-source-fingerprint SHA256_EXACT_DU_DRY_RUN \
  --backup-id rise2-backup-before-v1-YYYYMMDDTHHMMSSZ
```

L'empreinte et l'identifiant de sauvegarde sont obligatoires. Un second apply du même inventaire
retourne le run existant sans créer de doublon. Le ledger `v1_import_runs`/`v1_import_items`
enregistre uniquement les mutations V2 nécessaires au rollback.

Après apply, avant tout pilote :

1. restaurer/copier le contenu via la procédure approuvée, jamais depuis le script d'import ;
2. comparer l'inventaire qB et filesystem par infohash dans la réconciliation admin ;
3. renseigner taille, manifeste, stockage et route de compte depuis des observations vérifiées ;
4. ne quitter l'état `ERROR` qu'après cohérence DB/qB/filesystem et contrôle des droits ;
5. refaire sauvegarde, healthchecks et échantillons de téléchargement.

## 4. Rollback borné

Le rollback exige deux fois l'UUID exact du run :

```bash
python -m app.cli rollback-v1-import \
  --run-id UUID_DU_RUN \
  --confirm-run-id UUID_DU_RUN \
  --report /var/lib/world-of-seeds-v2/import/rollback-UUID_DU_RUN.json
```

Il supprime uniquement les requests créées par ce run et les placeholders que ce run a créés. Il
est atomiquement bloqué si une request a changé, si un job existe, si un autre droit référence le
torrent ou si un état runtime/manifeste/lease/activité a été ajouté. Les torrents V2 préexistants
ne sont jamais supprimés. Les runs sont annulés du plus récent au plus ancien afin qu'un inventaire
ultérieur ne perde pas silencieusement son mapping. Relancer un rollback terminé est un no-op audité.

Si le rollback est bloqué, ne pas forcer de suppression SQL : conserver la V2 hors trafic, analyser
les codes du rapport et restaurer la sauvegarde V2 validée si un retour complet est nécessaire.

## Go/no-go

- sauvegarde V2 restaurée et identifiant enregistré ;
- inventaire V1 issu d'un snapshot/replica et rôle effectivement read-only ;
- dry-run sans conflit, empreinte approuvée et rapport expurgé ;
- fenêtre V2 sans écritures concurrentes ;
- apply idempotent vérifié par une seconde exécution ;
- réconciliation physique complète avant activation ;
- rollback testé sur un environnement jetable ;
- V1 conservée intacte jusqu'à la fin de la fenêtre de retour arrière.
