# Sauvegarde et restauration Rise2 V2

## Contrat de récupération

Cette procédure concerne exclusivement le projet Compose `world-of-seeds-v2-rise2`. Elle ne lit,
ne monte et ne modifie jamais `/srv/seedbox` ni un volume V1.

| Composant | Sauvegarde | Cohérence | Rétention minimale |
| --- | --- | --- | --- |
| PostgreSQL | dump custom `pg_dump` dans l'archive chiffrée | snapshot transactionnel PostgreSQL | 14 quotidiennes + 8 hebdomadaires |
| environnement et configs | copie normalisée dans l'archive `age` | même fenêtre que le dump | identique au dump |
| qBittorrent et NewGreedy | état des volumes copié conteneurs arrêtés | consumers, qB et NewGreedy arrêtés | identique au dump |
| contenu `/srv/world-of-seeds-v2/data` | snapshot bloc/filesystem externe | créé pendant la même fenêtre d'arrêt | 7 points quotidiens minimum |
| monitoring | provisioning Git ; données métriques non bloquantes | reconstruction autorisée | selon la rétention Prometheus |

Le RPO initial est de 24 heures et le RTO initial de 4 heures. Ils restent des seuils de lancement :
chaque exercice enregistre sa durée, puis l'exploitation les resserre ou augmente les ressources si
Rise2 ne les tient pas. Une sauvegarde n'est valide qu'après un exercice de restauration réussi.

Le contenu torrent n'est jamais ajouté implicitement à l'archive : son volume rendrait la copie
longue, coûteuse et difficile à rendre cohérente. Un snapshot externe est obligatoire et son
identifiant est lié cryptographiquement au manifeste chiffré. Le rapport ne contient ni nom de
fichier, ni URL tracker, ni passkey, ni secret.

## Prérequis

- `docker compose`, Python 3.12 et `age` sont installés sur Rise2 ;
- une clé publique `age` de récupération, dont la clé privée est conservée hors hôte, est approuvée ;
- la destination est un stockage hors hôte monté en écriture, hors des arbres V1 et V2 ;
- le snapshotter de `/srv/world-of-seeds-v2/data` fournit un identifiant immuable ;
- l'environnement et les deux configs sont sous `/etc/world-of-seeds-v2`, sans lien symbolique.

L'archive contient des secrets en clair uniquement dans un répertoire temporaire `0700`, supprimé
à la fin. L'artefact `.tar.age` et son sidecar `.sha256` sont créés en `0600`. Les erreurs de
commandes externes sont volontairement expurgées pour ne pas recopier des secrets dans les logs.

## Créer une sauvegarde

Une sauvegarde officielle s'exécute dans une fenêtre de maintenance. Le script refuse de continuer
si `worker`, `scheduler`, `qbittorrent` ou `newgreedy` tourne encore : cela empêche d'associer un
snapshot de contenu antérieur à un état qB plus récent.

```bash
env_file=/etc/world-of-seeds-v2/environment
compose=(docker compose --env-file "$env_file" --file deploy/compose.rise2.v2.yaml)

cleanup() { "${compose[@]}" start worker scheduler qbittorrent newgreedy; }
trap cleanup EXIT
"${compose[@]}" stop worker scheduler qbittorrent newgreedy

# Exécuter ici la commande approuvée du snapshotter Rise2, puis recopier son ID exact.
snapshot_id=rise2-data-YYYYMMDDTHHMMSSZ

scripts/rise2_v2_backup.py backup \
  --env-file "$env_file" \
  --output "/mnt/off-host-backups/wos-v2-$snapshot_id.tar.age" \
  --age-recipient 'age1REPLACE_WITH_APPROVED_PUBLIC_RECIPIENT' \
  --content-snapshot-id "$snapshot_id"
```

Après redémarrage, vérifier API, workers, scheduler, qBittorrent et NewGreedy. Copier l'archive et
son sidecar sur le stockage hors hôte, puis vérifier la copie :

```bash
scripts/rise2_v2_backup.py verify \
  "/mnt/off-host-backups/wos-v2-$snapshot_id.tar.age"
```

Le manifeste chiffré recense les composants et les SHA-256 de chaque fichier. Le sidecar public ne
contient que le SHA-256 du ciphertext et son nom ; l'authenticité du contenu reste assurée par
`age` lors du déchiffrement.

## Restaurer sans toucher à la production

La commande `restore` vérifie d'abord le sidecar, déchiffre l'archive, refuse chemins traversants,
liens et fichiers spéciaux, contrôle tous les SHA-256 internes et exige l'identifiant du snapshot
externe. La cible doit être absente et située hors de `/srv/seedbox` et
`/srv/world-of-seeds-v2`.

```bash
snapshot_id=rise2-data-YYYYMMDDTHHMMSSZ
bundle=/mnt/off-host-backups/wos-v2-$snapshot_id.tar.age
stage=/var/tmp/wos-v2-restore-$snapshot_id

scripts/rise2_v2_backup.py restore "$bundle" \
  --identity /run/keys/wos-v2-backup-age-identity \
  --target "$stage" \
  --content-snapshot-id "$snapshot_id"
```

Le résultat est un staging en clair, privé, et non une écriture dans les volumes live. Il doit être
effacé après l'exercice conformément à la procédure de l'hôte.

## Exercice de restauration obligatoire

L'exercice restaure réellement `postgres.dump` dans un conteneur PostgreSQL dédié, sans réseau et
sur un volume au nom unique. Il vérifie les modes des configs et la présence des états qB/NewGreedy,
puis détruit automatiquement le conteneur et le volume jetables. Il ne démarre aucun service WOS
et ne publie aucun port.

```bash
report=/var/lib/world-of-seeds-v2/restore-reports/$snapshot_id.json
scripts/rise2_v2_restore_drill.sh "$stage" "$snapshot_id" "$report"
```

Compléter l'exercice sur l'environnement de reprise vierge :

1. restaurer le snapshot de contenu exact vers un nouveau chemin V2 isolé ;
2. contrôler un échantillon de canaris par taille et SHA-256, sans écrire leurs noms dans le rapport ;
3. installer les configs restaurées avec leurs modes `0600`/`0640` et les UID/GID dédiés ;
4. créer uniquement les nouveaux volumes Rise2, restaurer PostgreSQL puis les états qB/NewGreedy ;
5. exécuter le préflight, les migrations, la réconciliation par infohash et les tests de health ;
6. chronométrer l'ensemble, comparer au RTO et faire approuver le rapport sans secret ;
7. conserver l'ancienne pile et ses volumes intacts jusqu'à la décision de bascule.

Tout volume, conteneur, fichier de configuration ou chemin de contenu déjà présent sur la cible est
un **no-go**. La restauration doit alors être interrompue et examinée ; elle ne doit jamais devenir
une mise à niveau en place implicite.

## Fréquence, alertes et preuve

- quotidiennement : snapshot contenu puis archive chiffrée cohérente, copie hors hôte et `verify` ;
- avant migration ou changement d'image : point supplémentaire conservé jusqu'à validation ;
- mensuellement : exercice automatisé PostgreSQL/config/qB/NewGreedy ;
- trimestriellement : reconstruction complète sur hôte vierge avec échantillon contenu ;
- alerter si la dernière archive, le dernier snapshot ou le dernier exercice dépasse son échéance ;
- conserver dans le rapport uniquement identifiants techniques, compteurs, durée, résultat et
  approbation, jamais les données ou secrets restaurés.

Les suppressions de rétention ciblent des noms d'artefacts résolus et contrôlés par l'exploitation.
Aucun script de ce dépôt ne supprime récursivement un arbre de sauvegarde ou de contenu.
