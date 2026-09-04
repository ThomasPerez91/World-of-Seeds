# V2-33 — pilote limité sur Rise2

## Portée et autorisations

Ce runbook valide la V2 sur la pile Rise2 isolée avant toute release candidate. Il ne modifie ni
la V1, ni `master`, ni `develop`, ne déplace aucune donnée V1 et n'autorise aucune bascule DNS,
aucun import V1 réel et aucune release `2.0.0`. Ces trois actions nécessitent une approbation
explicite distincte.

Le pilote porte d'abord sur des données de test jetables, puis sur un petit nombre de comptes
pilotes créés sans déplacer leurs données V1. La V1 reste disponible pendant toute la fenêtre de
retour arrière. Un test incomplet n'est jamais transformé en succès documentaire : la décision
finale est `go`, `go_limited` ou `no_go` et toutes les étapes doivent être enregistrées.

## Preuve expurgée

Le registre `scripts/rise2_v2_pilot.py` lie les résultats au SHA Git complet et au digest immuable
de l'image. Il accepte uniquement des métriques numériques ou booléennes et conserve le SHA-256 de
chaque artefact de preuve, jamais son chemin ni son contenu. Les fichiers de preuve restent dans le
répertoire privé de l'hôte et ne sont pas ajoutés à Git.

Préparer un répertoire par révision :

```bash
revision="$(git rev-parse HEAD)"
image_digest='sha256:REMPLACER_PAR_LE_DIGEST_VERIFIE'
pilot_root="/var/lib/world-of-seeds-v2/pilot/$revision"
install -d -m 0700 -- "$pilot_root"

scripts/rise2_v2_pilot.py init "$pilot_root/ledger.json" \
  --revision "$revision" \
  --image-digest "$image_digest"
```

Le clone doit être propre, positionné sur le SHA testé de `develop_V2`, et l'image déclarée dans
`/etc/world-of-seeds-v2/environment` doit correspondre exactement au digest enregistré. Les
artefacts bruts peuvent contenir uniquement les sorties expurgées prévues par les outils ; ne pas
y rediriger l'environnement, les configurations, les commandes avec credentials, les réponses
tracker ou les listes de fichiers.

Une étape s'enregistre ainsi :

```bash
scripts/rise2_v2_pilot.py record "$pilot_root/ledger.json" preflight \
  --status passed \
  --duration-seconds 42 \
  --evidence "$pilot_root/preflight.aggregate.json" \
  --metric newgreedy_readable=true \
  --metric isolated_v2_storage=true \
  --metric policy_failures=0 \
  --metric v1_mounts=0 \
  --metric public_internal_ports=0
```

Le registre refuse un `passed` qui ne respecte pas les invariants et seuils de la matrice. Une
étape réellement en échec doit être enregistrée `failed`; ses métriques restent agrégées.

## Séquence obligatoire

### 1. Préflight et isolation

1. Inventorier sans secret OS, noyau, Docker/Compose, CPU, RAM, swap, disques, filesystem, marge
   de restauration, ports occupés et pare-feu.
2. Vérifier que le stockage, les volumes, réseaux, profils qB/NewGreedy, secrets et domaines V2
   sont distincts de la V1.
3. Exécuter :

   ```bash
   scripts/rise2_v2_preflight.sh /etc/world-of-seeds-v2/environment
   ```

4. Confirmer que seuls 80/443 sont publiés, que `/api/v2/metrics` est refusé par l'ingress public
   et reste lisible par Prometheus sur le réseau privé.
5. Enregistrer `preflight` avec `newgreedy_readable`, `isolated_v2_storage`, `policy_failures`,
   `v1_mounts` et `public_internal_ports`.

Tout montage, secret, réseau ou profil partagé avec la V1 est un **no-go immédiat**.

### 2. Sauvegarde et restauration vierge

Exécuter la procédure complète de
[`backup-restore-rise2-v2.md`](backup-restore-rise2-v2.md) : archive chiffrée, snapshot externe,
vérification, staging, restauration PostgreSQL dans un volume jetable puis reconstruction sur une
cible V2 vierge. Vérifier au moins un canari de contenu par taille et SHA-256 sans écrire son nom
dans la preuve.

Enregistrer `backup_restore` avec `postgres_restored`, `content_canary_verified`,
`restore_failures`, `secret_findings`, `existing_target_writes` et `rto_seconds`. La durée réelle
doit rester sous le RTO enregistré. Une cible déjà existante est un no-go, jamais une restauration
en place.

### 3. Charge soutenue à un puis deux slots

Avec 100 comptes de test jetables, un backlog supérieur à 200 et les tailles/états définis dans
[`security-load-v2.md`](security-load-v2.md), exécuter séparément :

- 5 minutes de chauffe puis 30 minutes mesurées avec un slot ;
- 5 minutes de chauffe puis 30 minutes mesurées avec deux slots.

Conserver les résultats agrégés du générateur de charge et de Prometheus. Enregistrer
`load_1_slot` puis `load_2_slots` avec : `slots`, `warmup_seconds`, `measurement_seconds`,
`famine_count`, `duplicate_count`, `corruption_count`, `unexpected_transition_count`,
`scheduler_cycle_p95_seconds` et `scheduler_interval_seconds`.

Les quatre compteurs d'erreur doivent rester à zéro et le p95 du cycle scheduler doit rester
strictement inférieur à son intervalle configuré. Une mesure plus courte est refusée.

### 4. WebSocket, transferts et manifestes

Exécuter les paliers 10/25/50/100 WebSockets, puis 25 comptes avec quatre onglets et 25
reconnexions. Redémarrer l'API, interrompre/rétablir Redis et perdre volontairement un événement ;
la reconnexion doit effectuer une resynchronisation GET sans transaction PostgreSQL inactive.
Enregistrer `websocket_recovery`.

Tester ensuite Range/reprise, client lent, annulation, déconnexion et limites sur petits/gros
fichiers, puis manifestes petits, paginés, plusieurs milliers et 50 000 entrées. Enregistrer
`transfer_manifest`; il exige démarrage progressif, pause/reprise/annulation, intégrité, zéro lease
résiduelle et zéro dépassement de limite.

### 5. Pannes et pression de ressources

Après snapshot et une panne à la fois, couvrir au minimum les huit familles suivantes : Redis,
PostgreSQL lent, qBittorrent lent/indisponible, NewGreedy lent/indisponible, worker, scheduler,
reset qB et ingress/API. Utiliser `docker compose stop/start/restart` uniquement sur le projet
`world-of-seeds-v2-rise2`; ne jamais cibler un nom de conteneur V1. Vérifier backoff, état sûr,
absence de faux succès, aucun job perdu et reprise idempotente. Enregistrer
`dependency_failures` avec `scenarios` supérieur ou égal à 8.

Appliquer ensuite une pression bornée CPU/RAM/I/O/disque à la seule pile V2, avec seuil de fin et
espace de restauration réservé. Vérifier que la pression disque ferme l'admission de façon sûre et
qu'aucun seuil dépassé ne reste inexpliqué. Enregistrer `resource_pressure`. Ne jamais remplir un
filesystem, appliquer une charge non bornée ou supprimer des fichiers pour simuler la pression.

### 6. Sécurité et observabilité

Rejouer audits de dépendances, configuration et image. Rechercher dans les sorties observables les
secrets, trackers complets et identifiants métier, sans copier les occurrences dans le rapport.
Enregistrer uniquement les compteurs dans `security_observability`, avec la preuve que les métriques
sont privées et disponibles pour Prometheus.

### 7. Nettoyage et comptes pilotes

Supprimer les seuls comptes, torrents et fichiers marqués par l'identifiant de campagne de test,
via les actions métier prévues. Vérifier par compteurs qu'il ne reste aucune donnée de test et que
la V1 est inchangée, puis enregistrer `test_data_cleanup`.

Créer ensuite un nombre limité de comptes pilotes via l'administration V2. Conserver les
credentials hors logs et rapports, imposer leur changement à la première connexion et ne déplacer
aucune donnée V1. Enregistrer `pilot_accounts` avec uniquement le nombre de comptes et les
invariants booléens attendus.

### 8. Rollback chronométré

Sans supprimer les volumes V2 : suspendre l'admission, laisser finir ou remettre les jobs à un
point idempotent, capturer les compteurs agrégés, retirer l'ingress pilote V2, confirmer que la V1
reste disponible, puis vérifier health, authentification et absence d'écriture V2. Redémarrer le
digest V2 précédent uniquement si sa compatibilité avec le schéma courant a déjà été prouvée.

Enregistrer `rollback` avec la durée, le RTO, les échecs health/authentification, les écritures V1
et les preuves que V1 est disponible, l'admission V2 suspendue et les volumes V2 préservés.

## Décision

Après revue des douze étapes, finaliser avec une référence d'approbation non secrète :

```bash
scripts/rise2_v2_pilot.py finalize "$pilot_root/ledger.json" \
  --decision go \
  --approval-ref ops-approval-YYYYMMDD

scripts/rise2_v2_pilot.py validate "$pilot_root/ledger.json" --require-final
```

`go` et `go_limited` exigent douze étapes réussies. `no_go` exige les douze étapes enregistrées et
au moins un échec explicite. Le registre final, son SHA-256 et une synthèse sans secret sont relus
avant d'être mentionnés dans `PROGRESS.md`; les artefacts bruts restent privés sur Rise2.

La décision V2-33 n'autorise pas V2-34 si elle est `no_go`. Elle n'autorise jamais à elle seule un
DNS public, un import V1 réel ou la release stable.
