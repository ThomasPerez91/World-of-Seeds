# V2-33 — pilote limité sur Rise2

## Portée et autorisations

Ce runbook valide la V2 sur la pile Rise2 isolée avant toute release candidate. Il ne modifie ni
la V1, ni `master`, ni `develop`, ne déplace aucune donnée V1 et n'autorise aucune bascule DNS,
aucun import V1 réel et aucune release stable `2.0.0`. Ces actions nécessitent des approbations
séparées.

Le pilote utilise d'abord des données de test jetables, puis un nombre limité de comptes pilotes.
La V1 reste disponible pendant toute la fenêtre de retour arrière. La décision finale est
`go`, `go_limited` ou `no_go`; aucune étape manquante n'est transformée en succès documentaire.

## Registre de preuve et provenance

`scripts/rise2_v2_pilot.py` conserve un registre mode `0600` lié au SHA runtime complet et au
digest immuable de l'image. Chaque écriture du registre est sérialisée par un verrou exclusif,
les douze gates doivent être enregistrées dans l'ordre, leurs timestamps doivent être strictement
croissants et une gate déjà enregistrée ne peut pas être remplacée silencieusement.

Pour `init` et chaque `record`, l'outil vérifie directement sur `rise2-01` :

- le hostname approuvé ;
- la checkout Git propre et exactement égale au SHA du ledger ;
- l'image normalisée des services `api`, `worker` et `scheduler` ;
- l'unique conteneur API en cours d'exécution ;
- son image réellement configurée et son label OCI de révision.

Une preuve `passed` ne peut donc pas être enregistrée depuis un autre hôte, une autre checkout ou
une autre image. Le tooling de la draft peut rester extrait séparément par blob Git vérifié : la
checkout opérationnelle ne bascule jamais sur la branche de tooling.

L'artefact de chaque gate doit être un JSON régulier mode `0600`, borné à 2 MiB, secret-free, avec
le schéma attendu pour la gate. Les métriques du ledger sont whitelisted par gate et doivent être
présentes avec exactement les mêmes valeurs dans la preuve. Pour les deux gates de charge,
le sous-objet `load` doit porter le schéma
`world-of-seeds-v2-rise2-scheduler-load/v1`; les autres gates utilisent leur schéma dédié
`world-of-seeds-v2-rise2-<gate>/v1`. Une preuve vide, sans schéma, sans métriques correspondantes,
symlinkée ou portant une métrique arbitraire est refusée.

Le RTO de lancement est une constante de politique : **4 heures / 14 400 secondes**. Les preuves
`backup_restore` et `rollback` doivent enregistrer exactement ce plafond et leur durée réelle doit
y rester inférieure ou égale. L'opérateur ne peut pas relever le RTO en modifiant une métrique.

Les références d'approbation sont limitées aux namespaces opérationnels datés
`ops-approval-YYYYMMDD` ou `v2-33-{go|go-limited|no-go}-YYYYMMDD`; une valeur ressemblant à un
token générique n'est pas acceptée.

## Séquence obligatoire

L'ordre est immuable :

1. `preflight`
2. `backup_restore`
3. `load_1_slot`
4. `load_2_slots`
5. `websocket_recovery`
6. `transfer_manifest`
7. `dependency_failures`
8. `resource_pressure`
9. `security_observability`
10. `test_data_cleanup`
11. `pilot_accounts`
12. `rollback`

Un `no_go` peut continuer à enregistrer les gates suivantes pour produire une matrice complète,
mais il ne peut pas réordonner les exercices. `rollback` est donc toujours postérieur à la création
des comptes pilotes qu'il doit réellement couvrir.

### 1. Préflight et isolation

Inventorier OS, noyau, Docker/Compose, CPU, RAM, swap, disques, filesystem, ports et pare-feu sans
imprimer de secret. Vérifier que stockage, volumes, réseaux, profils qB/NewGreedy et secrets V2
sont distincts de la V1. Exécuter `scripts/rise2_v2_preflight.sh` avec l'environnement Rise2 et
confirmer que seuls 80/443 sont publiés, que les métriques applicatives sont refusées par l'ingress
public et restent disponibles pour Prometheus sur le réseau privé.

`preflight` exige `newgreedy_readable=true`, `isolated_v2_storage=true` et zéro
`policy_failures`, `v1_mounts`, `public_internal_ports`.

### 2. Sauvegarde et restauration vierge

Exécuter `docs/backup-restore-rise2-v2.md` : archive chiffrée, copie/snapshot de contenu hors hôte,
staging, restauration PostgreSQL et reconstruction sur une cible V2 absente avant l'exercice.
Vérifier les canaris par taille et SHA-256 sans inscrire de nom métier dans la preuve.

Sur l'ext4/RAID0 Rise2 sans snapshot filesystem natif, une copie complète off-host cohérente est
acceptée uniquement si `worker`, `scheduler`, qBittorrent et NewGreedy sont arrêtés pendant toute
la copie, si la cible externe est indépendante et neuve, et si un manifeste SHA-256 complet lie
le point de restauration à l'archive d'état. Une copie locale sur le RAID0 ou une restauration en
place est interdite.

`backup_restore` exige restauration PostgreSQL et canari valides, zéro échec/secret/écriture sur
cible préexistante et un RTO exactement égal à 14 400 secondes.

### 3. Charge à un puis deux slots

Pour chaque gate : 5 minutes de chauffe puis 30 minutes mesurées. `load_1_slot` exige exactement
un slot, `load_2_slots` exactement deux. Les compteurs famine, doublon, corruption et transition
inattendue doivent rester à zéro. Le p95 du cycle scheduler doit rester strictement inférieur à
son intervalle configuré. Une durée totale inférieure à 2 100 secondes est refusée.

### 4. WebSocket, transferts et manifestes

Couvrir au moins 100 WebSockets et 25 reconnexions, avec redémarrage API, perte/rétablissement Redis
et resynchronisation GET. Zéro transaction SQL inactive, zéro resync en échec et zéro événement
perdu après resync; la mémoire doit revenir à son plateau.

Tester ensuite Range/reprise, client lent, annulation, déconnexion et limites, puis un manifeste de
50 000 fichiers. Le transfert doit démarrer progressivement, pause/reprise/annulation doivent être
validées, sans erreur d'intégrité, lease résiduelle ni dépassement de limite.

### 5. Pannes et pression de ressources

Couvrir au minimum huit familles : Redis, PostgreSQL lent, qBittorrent indisponible, NewGreedy
indisponible, worker, scheduler, reset qB et ingress/API. Vérifier backoff, absence de faux succès,
aucun job perdu et reprise idempotente.

Appliquer ensuite une pression bornée CPU/RAM/I/O/disque uniquement à la pile V2. L'admission doit
fermer de façon sûre sous pression disque critique et aucun seuil dépassé ne doit rester inexpliqué.
Ne jamais remplir un filesystem ni utiliser `--remove-orphans`.

### 6. Sécurité et observabilité

Rejouer audits de dépendances, configuration et image. Scanner logs et métriques pour secrets,
URL trackers complètes et identifiants métier sans recopier les occurrences. Exiger zéro
HIGH/CRITICAL corrigeable, zéro secret, zéro identifiant métier, métriques publiques bloquées et
métriques privées disponibles.

### 7. Nettoyage et comptes pilotes

Supprimer uniquement les données portant l'identifiant de campagne de test. Vérifier zéro compte,
torrent ou fichier de test résiduel et aucune modification V1. Créer ensuite un nombre limité de
comptes pilotes avec credentials hors logs, changement forcé à la première connexion et zéro
déplacement de données V1.

### 8. Rollback chronométré

Le rollback V2-33 **suspend la nouvelle admission publique V2** en retirant l'ingress pilote, mais
il ne force pas à zéro toute écriture interne V2 : worker et scheduler peuvent terminer ou
réconcilier un effet durable déjà engagé pour atteindre un point idempotent sûr. Mesurer un
compteur global de writes PostgreSQL V2 serait donc contraire à la stratégie de drain et pourrait
transformer une reprise sûre en faux échec.

Le contrat vérifié est précisément :

- aucune écriture V1 (`v1_writes=0`) ;
- V1 reste disponible ;
- l'admission publique V2 est suspendue ;
- health et authentification pilote restent valides depuis le réseau interne de contrôle ;
- aucun volume V2 n'est supprimé ou remplacé ;
- la durée reste sous le RTO immuable de 14 400 secondes ;
- le runtime V2 est restauré après l'exercice.

Ne jamais exécuter `down --volumes` ni `--remove-orphans` dans cet exercice.

## Décision

Après les douze gates, finaliser avec une référence non secrète :

```bash
pilot_root="/var/lib/world-of-seeds-v2/pilot/$(git rev-parse HEAD)"
pilot_tool="/var/lib/world-of-seeds-v2/pilot-gates-7-12-tools/$(git rev-parse feat/v2-rise2-pilot)/rise2_v2_pilot.py"

sudo python3 "$pilot_tool" finalize "$pilot_root/ledger.json" \
  --decision go \
  --approval-ref v2-33-go-20260907

sudo python3 "$pilot_tool" validate "$pilot_root/ledger.json" --require-final
```

`go` et `go_limited` exigent 12/12 gates réussies. `no_go` exige 12/12 gates enregistrées et au
moins un échec explicite. Une décision V2-33 n'autorise jamais à elle seule une bascule DNS, un
import V1 réel ou la release stable.

## Résultat opérationnel du 7 septembre 2026

Le pilote réel Rise2 est terminé :

- runtime testé : `adcf67d5ea92b72c2a2210f8cdafb29669a940d8` ;
- tooling utilisé pour les gates 7→12 et la finalisation :
  `087c5fa54d7793258113a0e3e51ac7c969e9e928` ;
- image WOS :
  `ghcr.io/thomasperez91/world-of-seeds-v2@sha256:d0e817283ad95ba1792b4e16e7241bddabbce2190272382e2c339b4c291a947e` ;
- gates : **12/12 PASSED** ;
- décision : **GO** ;
- référence : `v2-33-go-20260907` ;
- décision enregistrée : `2026-09-07T09:29:13.249423Z` ;
- SHA-256 du ledger final :
  `38c94b41aed849a754053470e4a1eba8834157c64c57c6fb2e7d79dcca19d70b` ;
- validation finale `--require-final` : PASS.

Gate 9 a initialement détecté des passkeys dans les URL d'annonce affichées par `mitmdump` dans
les logs NewGreedy. Rise2 a été durci avec `flow_detail: 0`; un flux synthétique a ensuite donné
`synthetic_marker_log_occurrences=0`, puis Gate 9 a passé avec `runtime_log_secret_findings=0`.
Ce réglage doit être rendu déclaratif/reproductible pour un volume NewGreedy neuf avant V2-34;
le runtime historique testé n'est pas réécrit rétroactivement.
