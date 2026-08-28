# Validation locale V2 sur macOS

Ce profil exécute localement l'API et son frontend, PostgreSQL, Redis, le worker durable,
le scheduler, qBittorrent et une fixture NewGreedy limitée au mode développement. Une couche
optionnelle ajoute Prometheus, Grafana, node-exporter et cAdvisor. L'ensemble est
séparé de V1 par le projet Compose `world-of-seeds-v2-local`, ses réseaux et ses volumes.

## Prérequis

- macOS sur Apple Silicon (`arm64`) ou Intel (`amd64`) ;
- Docker Desktop avec Compose V2 ;
- Python 3 disponible sur l'hôte pour les validations et le scénario smoke ;
- ports `28081` et `23000` libres sur la boucle locale.

Docker Desktop doit disposer d'au moins 6 Gio de mémoire avec la supervision active. Aucun chemin `/srv`, UID/GID
`1000`, secret réel, compte administrateur réel ou passkey C411 réelle n'est nécessaire.

## Démarrage et validation

Depuis un clone propre de `develop_V2` :

```sh
scripts/local_v2.sh up
scripts/local_v2.sh smoke
scripts/local_v2.sh monitoring-up
scripts/local_v2.sh monitoring-smoke
```

L'interface est ensuite disponible sur <http://127.0.0.1:28081>. Seul ce port API/frontend
est publié sur la boucle locale. Grafana est disponible sur <http://127.0.0.1:23000> avec les
identifiants jetables de `.env.v2.local`. Son réseau d'accès dédié n'est partagé avec aucun
service applicatif. PostgreSQL, Redis, qBittorrent, NewGreedy, Prometheus et les exporters restent
sur des réseaux Compose internes.
Le hostname Compose `api` est autorisé uniquement dans ce profil afin que Prometheus puisse
collecter `/api/v2/metrics`; il n'est ni publié ni ajouté à la configuration de production.

`up` valide la configuration normalisée, construit les images, applique les migrations
Alembic de manière idempotente et attend les healthchecks. `smoke` crée un utilisateur local
non-administrateur avec un mot de passe aléatoire éphémère, soumet une fixture `.torrent`,
et vérifie :

- la file durable pendant l'arrêt du worker ;
- le traitement par le worker et l'état authentifié retourné à l'UI ;
- la présence unique de l'infohash dans qBittorrent ;
- une génération appliquée par le scheduler ;
- l'absence de doublon après redémarrage du worker ;
- un contenu `READY` contrôlé, son manifeste, une reprise HTTP Range et le ZIP streamé ;
- l'annulation CSRF de la dernière référence et sa purge durable mise en attente de rétention ;
- l’exposition des métriques API/jobs/scheduler/leases/qB/Redis/stockage sans identifiant métier ;
- la présence de la vue « Mes téléchargements » dans le bundle frontend servi.

Le scénario peut être relancé : chaque exécution produit une fixture distincte, tout en
vérifiant l'idempotence du job créé. Pour repartir d'un état vierge, utilisez le nettoyage
ci-dessous.

`monitoring-smoke` attend que Prometheus collecte l'API, Prometheus lui-même, node-exporter et
cAdvisor, vérifie les treize alertes opérationnelles, puis contrôle l'API de santé Grafana et le
dashboard provisionné. `local_v2.sh` sélectionne automatiquement l'overlay versionné Docker
Desktop sur macOS afin de ne pas demander la propagation `rslave`, indisponible pour le bind `/`.
Linux conserve `rslave` afin que node-exporter observe les changements de montages de l'hôte.
`WOS_V2_MONITORING_PLATFORM=linux|docker-desktop` permet uniquement de tester explicitement l'un
des deux contrats ; aucun fichier local ignoré n'est nécessaire.

Sur macOS Intel comme Apple Silicon, node-exporter et cAdvisor décrivent principalement la machine
virtuelle Linux de Docker Desktop et les conteneurs qui y tournent, pas le noyau, la mémoire et les
disques physiques exacts du Mac. Les seuils Grafana/Prometheus locaux doivent donc être interprétés
comme des seuils de capacité allouée à Docker Desktop.

## Nettoyage isolé

```sh
scripts/local_v2.sh down
```

Cette commande cible explicitement `world-of-seeds-v2-local` et supprime uniquement ses
conteneurs, réseau et volumes nommés. Elle ne cible ni V1, ni un autre projet Compose, ni
un répertoire de l'hôte.

## Checklist manuelle Docker Desktop

Consigner pour chaque architecture testée la version macOS et la version Docker Desktop.

| Contrôle | Apple Silicon arm64 | Intel amd64 |
|---|---:|---:|
| `scripts/local_v2.sh up` termine sans émulation forcée | ☐ | ☐ |
| `scripts/local_v2.sh smoke` termine avec zéro doublon | ☐ | ☐ |
| l'UI s'ouvre sur `127.0.0.1:28081` | ☐ | ☐ |
| `scripts/local_v2.sh monitoring-up` puis `monitoring-smoke` réussissent | ☐ | ☐ |
| Grafana s'ouvre sur `127.0.0.1:23000` et affiche le dashboard V2 | ☐ | ☐ |
| `scripts/local_v2.sh down` ne laisse aucun volume local V2 | ☐ | ☐ |

La CI Linux valide la politique Compose et exécute le même smoke. Les deux cases macOS
restent une validation manuelle, car GitHub Actions ne fournit pas Docker Desktop macOS.

## Limites volontaires

Ce profil n'est pas un déploiement de production : il n'inclut ni ingress/TLS, ni
Alertmanager/notifications externes, ni secrets de production, ni import V1. La fixture NewGreedy expose uniquement
le contrat de santé requis par le worker et refuse de démarrer hors du mode développement.
