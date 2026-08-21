# Préparation du déploiement World of Seeds V2 sur Rise2

## Portée

Ce runbook décrit la cible et les points de contrôle futurs. Il n'autorise pas encore un
déploiement. La V1 `1.3.3` reste en production et n'est ni arrêtée ni migrée pendant la
construction de la V2.

## Isolation obligatoire

Rise2 utilise un projet Compose, un domaine, des secrets, des réseaux, des volumes et un
répertoire hôte propres à la V2. Aucun volume PostgreSQL, qBittorrent, NewGreedy ou stockage
de la V1 n'est monté, même en lecture seule, pendant les premières étapes.

Nommage indicatif, à confirmer après inventaire de l'hôte :

```text
/opt/world-of-seeds-v2/       # compose et fichiers de déploiement
/srv/world-of-seeds-v2/       # contenu géré et contrôle
volumes Compose wos-v2-*      # PostgreSQL, Redis, qB, Grafana, Prometheus
réseaux Compose wos-v2-*      # ingress, backend, torrent, monitoring
```

Les chemins exacts sont des variables d'infrastructure validées avant création. Aucun
script ne doit appliquer récursivement `chown` ou `chmod` à un chemin existant.

## Phases

### 1. Inventaire et prérequis

- relever OS, noyau, Docker/Compose, CPU, RAM, swap, disques, filesystem et IOPS ;
- vérifier DNS, certificats, ports occupés, pare-feu et politique de sauvegarde ;
- dimensionner stockage données, PostgreSQL, Redis, Prometheus et marge de restauration ;
- fixer UID/GID dédiés et permissions minimales ;
- documenter les limites qBittorrent/NewGreedy compatibles avec les versions épinglées.

### 2. Préparation hors trafic

- créer les secrets V2 avec des valeurs distinctes de V1 ;
- déployer ingress, PostgreSQL, Redis et monitoring sans exposer les services internes ;
- restaurer une sauvegarde de test dans une base jetable et mesurer la durée ;
- démarrer API/workers avec stockage vide et exécuter les migrations Rise2 ;
- vérifier health, readiness, métriques, logs expurgés et alertes.

### 3. qBittorrent et NewGreedy isolés

- déployer des profils et volumes V2 ne contenant aucun état V1 ;
- limiter leurs APIs au réseau torrent interne ;
- vérifier catégorie WOS, save path fixe et absence de port WebUI public ;
- tester ajout, réponse ambiguë, redémarrage, réconciliation et suppression contrôlée ;
- valider qu'aucune passkey n'apparaît dans logs, métriques ou diagnostics.

### 4. Validation technique

- tests de charge API, scheduler, worker, Range et transfert récursif ;
- panne/reprise de PostgreSQL, Redis, worker, qB, NewGreedy et ingress ;
- pression CPU, RAM, I/O et disque avec admission critique ;
- restauration complète dans un environnement vierge ;
- scan de sécurité des images et revue des mounts/capabilities/réseaux.

### 5. Pilote et bascule

- créer des comptes pilotes sans déplacer les données V1 ;
- observer erreurs, âge des files, débit, quotas et pression disque pendant une fenêtre fixée ;
- exécuter l'import V1 en dry-run si celui-ci a été approuvé ;
- basculer le trafic par étapes, avec TTL DNS réduit et critères go/no-go écrits ;
- conserver la V1 intacte et non destructive pendant toute la fenêtre de retour arrière.

## Sauvegardes

| Élément | Méthode minimale | Test obligatoire |
| --- | --- | --- |
| PostgreSQL | dump cohérent + sauvegarde volume selon RPO/RTO | restauration et migrations sur hôte vierge |
| Secrets/config | archive chiffrée hors hôte, accès audité | recréation de la pile sans valeur en clair |
| qBittorrent/NewGreedy | profils arrêtés ou snapshot cohérent | reprise et réconciliation par infohash |
| Prometheus/Grafana | provisioning versionné + données selon rétention | dashboards/alertes reconstruits |
| Contenu torrent | politique explicite selon coût/volume | inventaire manifeste et échantillon restauré |

Les sauvegardes ne sont considérées valides qu'après une restauration réussie. Les passkeys
et mots de passe ne doivent jamais apparaître dans le rapport de test.

## Rollback

Avant chaque bascule, conserver : digest de l'image précédente, dump PostgreSQL, versions
des migrations, sauvegarde des configurations et procédure DNS/ingress inverse.

1. suspendre l'admission de nouvelles demandes V2 ;
2. laisser finir ou remettre en file les jobs à un point idempotent ;
3. capturer l'état DB/qB/filesystem et les identifiants de corrélation ;
4. redéployer le dernier digest compatible ou restaurer la base si la migration est
   explicitement incompatible ;
5. rétablir l'ingress vers la V1 ;
6. vérifier health, authentification, lecture de fichiers et absence d'écriture V2 ;
7. conserver les volumes V2 pour analyse, sans les supprimer automatiquement.

Les migrations destructives suivent expand/contract sur plusieurs releases. Si le digest
précédent ne peut plus lire le schéma courant, la release ne doit pas atteindre le pilote.

## Critères de go/no-go

- CI, tests de charge, sécurité et restauration verts ;
- aucun secret ou tracker complet dans les sorties observables ;
- aucune file durable perdue après redémarrage de Redis/worker ;
- absence de famine dans les scénarios scheduler convenus ;
- quotas, pression disque, leases et purge validés en concurrence ;
- rollback chronométré sous le RTO accepté ;
- approbation explicite avant DNS, import réel ou release `2.0.0`.
