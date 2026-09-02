# Préparation du déploiement World of Seeds V2 sur Rise2

## Portée

Ce runbook décrit la pile versionnée `deploy/compose.rise2.v2.yaml` et ses points de contrôle.
Il n'autorise aucune bascule DNS. Un import V1 n'est possible que par la procédure optionnelle,
read-only côté V1 et dry-run par défaut, décrite dans [`import-v1-v2.md`](import-v1-v2.md). La V1
`1.3.3` reste en production et n'est jamais modifiée par cet import.

## Préparation de la pile versionnée

1. Copier `deploy/.env.rise2.v2.example` vers `/etc/world-of-seeds-v2/environment`, remplacer
   toutes les valeurs et appliquer le mode `0600`.
2. Utiliser uniquement des images WOS/NewGreedy épinglées par digest et des secrets distincts de
   V1. Le JSON des comptes d'intégration reste dans ce fichier non versionné et ne doit jamais être
   affiché dans les journaux ou commandes de diagnostic.
3. Créer `/srv/world-of-seeds-v2/data` sans lien symbolique, avec l'UID/GID WOS V2 dédiés. Ce chemin
   doit être le point de montage actif du filesystem de données ; un simple répertoire présent sur
   le filesystem racine n'est jamais un stockage V2 valide.
4. Conserver le registre `WOS_V2_INTEGRATION_ACCOUNTS_JSON` dans le fichier d'environnement
   privé, avec des quotes simples autour du JSON pour préserver les `$` littéraux. Les routes
   visant cette instance doivent partager exactement les mêmes credentials et l'URL
   `http://qbittorrent:8080`. Le username utilise 1–128 caractères ASCII alphanumériques ou
   `_.@-`, le password 20–1024 caractères UTF-8. Préparer uniquement le répertoire parent de
   `WOS_V2_QBITTORRENT_CONFIG_PATH` : le préflight génère le fichier privé lui-même.
5. Installer `config.ini` NewGreedy en `0640`, propriété de l'UID applicatif WOS et du groupe GID
   NewGreedy. Créer le répertoire `WOS_V2_NEWGREEDY_STATE_HOST_PATH` en `0700`, propriété de
   `root:root`, sans préparer ses fichiers à la main.
6. Exécuter `scripts/rise2_v2_preflight.sh /etc/world-of-seeds-v2/environment`. Le préflight
   refuse maintenant le démarrage si le stockage configuré n'est pas un point de montage actif,
   valide ensuite la pile normalisée, initialise idempotemment `stats.json`, `torrent_registry.json`,
   `newgreedy.log` et `purge_pending.json` en `0600`, puis vérifie leurs accès réels ainsi que
   celui du `config.ini` monté en lecture seule.
7. Pour un démarrage supervisé, installer `deploy/world-of-seeds-v2-rise2.service` sous
   `/etc/systemd/system/`, exécuter `systemctl daemon-reload`, puis n'activer l'unité qu'après
   autorisation du démarrage complet. L'unité ajoute `RequiresMountsFor=/srv/world-of-seeds-v2/data`,
   un contrôle `mountpoint` avant tout Compose et le préflight complet avant `docker compose up`.
   Pendant la qualification manuelle, l'unité peut rester désactivée.

L'image NewGreedy 1.7.5 publiée est validée avec son utilisateur root par défaut, car mitmproxy
génère et conserve sa CA sous `/root/.mitmproxy`. Le service NewGreedy ne force donc plus l'UID
`10003`; il ajoute uniquement le GID NewGreedy pour lire le `config.ini` en `0640`. Il conserve
`cap_drop: ALL`, `no-new-privileges`, un rootfs en lecture seule et aucun port hôte. Les quatre
fichiers d'état sont des binds persistants explicites et la CA utilise le volume nommé
`newgreedy_v2_ca`; aucun volume n'est monté sur `/app` et l'ancien `/app/data` n'est plus utilisé.

La pile publie uniquement Caddy sur 80/443. API, PostgreSQL, Redis, qBittorrent, NewGreedy,
Prometheus, Grafana et les exporters n'ont aucun port hôte. Grafana est routé par son hostname TLS
distinct. Les réseaux `backend`, `torrent`, `monitoring` et `monitoring-edge` sont internes ; seul
Caddy relie l'edge public aux deux destinations autorisées.

## Bootstrap qB reproductible, sans WebUI manuelle

Le registre secret existant reste l'unique autorité des credentials WOS/qB. Aucun username,
password ou hash de production n'est versionné. `rise2_v2_qb_bootstrap.py`, appelé par le
préflight, vérifie la politique `deploy/qbittorrent.rise2.conf` et dérive le password selon le
[code qB 5.2.3](https://github.com/qbittorrent/qBittorrent/blob/release-5.2.3/src/base/utils/password.cpp) :
PBKDF2-HMAC-SHA512, 100 000 itérations, sel aléatoire de 16 octets, sortie de 64 octets,
`@ByteArray(base64(sel):base64(dérivé))`. Un hash déjà conforme est conservé. Le résultat
est écrit atomiquement en `0600`, avec l'UID/GID qB, sans password en clair.

Le préflight dérive aussi `${WOS_V2_QBITTORRENT_CONFIG_PATH}.integration.json` depuis la même
variable : fichier privé `0600`, UID/GID WOS, jamais une seconde autorité à éditer. Il est
fourni aux seuls workers/scheduler par un secret Compose **file**, puis chargé dans leur
environnement **dans le processus**, sans rebuild WOS. Le type secret `environment` n'est pas
compatible avec les services Compose `read_only`; leur durcissement reste inchangé.
`docker compose config` n'affiche plus le JSON, le username ou le password qB. Le fichier
d'environnement et les sorties `config --environment`, `inspect` de processus et diagnostics
généraux restent sensibles : ne jamais les publier. Aucun secret n'est fourni à l'API.

Sur volume vierge, `qbittorrent-init` prépare le profil complet. Sur volume existant, l'init
ne réécrit pas un profil potentiellement ouvert : le même réconciliateur est exécuté dans le
conteneur qB **avant** son démarrage normal, puis la CA publique NewGreedy est installée et
`tini` lance qB. La réconciliation remplace uniquement les clés gérées, conserve les autres
préférences et ne touche ni aux torrents ni à leurs données. Les symlinks et INI ambigus sont
refusés. Le healthcheck sonde la page de connexion sans désactiver l'authentification locale.
Le seed vierge porte `Meta/MigrationVersion=8`, version des réglages qB 5.2.3 : sans ce marqueur,
sa migration historique réécrit les profils proxy modernes au premier lancement. Le marqueur
d'un profil existant n'est jamais écrasé par le réconciliateur.

Contrat contrôlé (clés vérifiées dans les sources qB 5.2.3 et par son API réelle) :

| Domaine | Contrat |
| --- | --- |
| WebUI | `HostHeaderValidation=true`, `ServerDomains=qbittorrent;localhost`, `CSRFProtection=true` |
| Auth | `LocalHostAuth=true`, `AuthSubnetWhitelistEnabled=false`, aucun password temporaire |
| Proxy HTTP | `[Network] Proxy\Type=HTTP`, `Proxy\IP=newgreedy`, `Proxy\Port=3456` |
| Trafic proxifié | `Proxy\Profiles\BitTorrent=true` ; RSS et Misc désactivés |
| Peers | `[BitTorrent] Session\ProxyPeerConnections=false` |
| CA | NewGreedy healthy → export du seul certificat public → bundle qB ; clé privée jamais montée dans qB |

`WebUI\Address=*` signifie écoute sur les interfaces **internes** du conteneur, pas wildcard
dans les domaines autorisés. Aucun port qB/NewGreedy n'est publié. Il n'y a plus de réglage
manuel WebUI à refaire après installation propre ou wipe autorisé.

Le smoke dédié `sudo python3 scripts/rise2_v2_qb_smoke.py` utilise les images WOS/NewGreedy
déjà publiées, un projet Compose aléatoire distinct et uniquement des credentials TEST générés
en mémoire. Il contrôle l'auth interne, le refus d'un mauvais password/Host/Origin, l'inventaire
vide, les préférences proxy, la CA, puis restart, force-recreate et wipe du seul volume qB du
projet jetable. Il n'est pas une commande de wipe du pilote. Ne l'exécuter que sur un hôte Docker
de test ; il nécessite root pour son stockage tmpfs isolé et nettoie ses propres ressources.

### Reprise du pilote, uniquement après autorisation opérateur

1. `git fetch origin`, puis mettre à jour le checkout de déploiement sur le commit validé de
   `develop_V2` (ne pas utiliser la branche de #103).
2. Conserver les digests WOS/NewGreedy et le registre secret existants. Entourer le JSON de quotes
   simples si nécessaire ; aucune nouvelle variable de credential n'est requise.
3. Lancer `sudo scripts/rise2_v2_preflight.sh /etc/world-of-seeds-v2/environment`.
4. Dans une fenêtre explicitement autorisée, arrêter workers/scheduler puis qB proprement et
   recréer qB, workers et scheduler afin de charger les nouveaux mounts/secrets. Ne pas faire
   `down --volumes` sur Rise2. Un wipe TEST du profil qB demande une autorisation distincte,
   résolution du volume exact et sauvegarde préalable ; aucune commande destructive du pilote
   n'est fournie par ce correctif.
5. Confirmer santé, authentification depuis `http://qbittorrent:8080`, protections Host/CSRF,
   proxy trackers et peers directs. Sur un profil vide, `inventory_items=0` est attendu.

Le backup **schema 2 reste inchangé** : environnement et bootstrap sont déjà archivés chiffrés,
ainsi que le volume `qbittorrent-config`. Deployment + secrets reconstruisent la configuration
fonctionnelle, mais pas les torrents, fastresume ni préférences non gérées : le backup cohérent
du profil qB reste nécessaire pour préserver ces états lors d'une restauration.

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
script ne doit appliquer récursivement `chown` ou `chmod` à un chemin existant. Le stockage de
données doit rester un montage distinct : ni le préflight ni l'unité systemd ne doivent accepter
le répertoire de repli créé sur `/` quand le filesystem de données est absent.

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
- vérifier la persistance des fichiers d'état NewGreedy et de sa CA après restart et recreate ;
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

La procédure exécutable, la politique de rétention et l'exercice isolé sont définis dans
[`backup-restore-rise2-v2.md`](backup-restore-rise2-v2.md). L'archive chiffrée et le snapshot
externe de contenu sont liés par un identifiant immuable ; aucun script ne copie ou supprime le
contenu torrent par défaut.

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
- le préflight et le démarrage systemd refusent le stack si le point de montage de données est absent ;
- rollback chronométré sous le RTO accepté ;
- approbation explicite avant DNS, import réel ou release `2.0.0`.
