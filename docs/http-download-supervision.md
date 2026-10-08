# Supervision des récupérations HTTP READY — 2.3.7

Le tableau Grafana **World of Seeds V2 — Récupérations HTTP READY** complète les métriques
introduites en 2.3.4. Il se trouve dans le dossier Grafana World of Seeds V2, sous l’UID
`wos-v2-http-downloads`. Ce tableau d'exploitation reste distinct du Dashboard des utilisateurs.
Il contient exclusivement des agrégats ; aucun nom de film, compte, IP utilisateur ou hash.

Les panneaux couvrent flux rapides et attentes, cible des admissions futures, plus ancienne
attente, débit ASGI par voie, P50/P95 du délai de voie rapide et du premier corps, upload total de
l’hôte, capacité configurée, fraîcheur/âge réseau, admissions/refus, issues des réponses et temps
sans progression des flux rapides. Les jauges principales utilisent des requêtes instantanées :
une cible disparue affiche « Sans données », pas son ancienne valeur dans la période du graphique.

## Lecture

- Un slot rapide reste acquis jusqu’à la fin/déconnexion. La cible des futures admissions peut
  baisser sous le nombre de flux déjà engagés, sans rotation ni rétrogradation.
- Le débit ASGI compte les octets acceptés par le serveur HTTP ; il ne mesure pas l’enregistrement
  du fichier sur le disque client. L’upload hôte inclut qBittorrent et les autres services.
- Une mesure réseau périmée est masquée sur le graphique de débit hôte, jamais remplacée par zéro.
  Fraîcheur zéro et âge zéro indiquent qu’aucun échantillon valide n’a été reçu.
- Les histogrammes couvrent les observations terminées. La plus ancienne attente décrit les
  transferts toujours en attente ; l’absence d’observations n’est pas une latence nulle.
- `completed` signifie corps envoyé intégralement à ASGI (y compris Range) ; `interrupted` désigne
  une annulation/déconnexion et `error` une erreur du flux. Une reprise est une nouvelle réponse.
- Un flux rapide sans progression peut simplement subir la contre-pression d’un client lent.
  Ce compteur seul ne déclenche aucune alerte et ne libère pas son slot.

## Alertes d’investigation

| Règle | Condition persistante |
| --- | --- |
| `WOSHttpDownloadTelemetryStale` | Mesure réseau périmée/absente avec au moins une récupération active pendant 2 min |
| `WOSHttpDownloadWaitingTooLong` | Plus ancienne attente >30 min et file non vide pendant 5 min |
| `WOSHttpDownloadErrors` | ≥3 erreurs sur 5 min et >10 % des réponses terminées/erreurs, pendant 5 min |

Ces alertes sont de sévérité warning et ne modifient pas l’ordonnanceur. Le ratio d’erreurs exclut
les interruptions des clients, qui ne doivent ni fabriquer ni masquer les erreurs serveur.
Les conditions sont évaluées par cible API, pour éviter de combiner l’activité d’une instance
avec la mesure réseau d’une autre. Une attente prolongée peut rester explicable par de longs
transferts engagés ; l’alerte invite à examiner les courbes, sans promettre un délai maximum.

## Chargement sur Rise2

Le déploiement applicatif met à jour les fichiers du dépôt. Grafana surveille le répertoire de
dashboards déjà monté et doit provisionner le nouveau tableau après son prochain scan. Vérifier
sa présence dans Grafana ; aucun accès serveur direct n’a été utilisé comme preuve ici.

Les règles Prometheus sont un bind de fichier : un checkout Git peut remplacer son inode.
Un simple rechargement ne suffit donc pas à garantir la nouvelle version. Lors de la prochaine
intervention OPS, recréer **uniquement** Prometheus avec le profil Rise2 existant :

```bash
cd /opt/world-of-seeds-v2
sudo docker compose --env-file /etc/world-of-seeds-v2/environment \
  -f deploy/compose.rise2.v2.yaml -f deploy/compose.rise2.observability.v2.yaml \
  up -d --no-deps --force-recreate prometheus
```

Cette commande suppose que la couche observability Rise2 est déjà installée, conformément au
profil de production. Ne pas remplacer le profil actuel par le profil local. Elle ne recrée
ni l’API, ni qBittorrent/NewGreedy et ne supprime aucun volume. Confirmer ensuite les trois noms
de règles dans l’interface Prometheus. Le dépôt ne configure pas de destinataire externe :
notifications et sauvegardes restent à activer plus tard, conformément au choix de Thomas.
Le correctif n’annonce ni réception de notifications ni chargement des nouvelles règles sur Rise2
sans preuve opérateur.

## Validation

`promtool test rules monitoring/prometheus/http-download-alerts.test.yml` couvre déclenchement,
persistance, retour à la normale, inactivité, cibles différentes, seuils, interruptions clients et
remise à zéro des compteurs. La CI emploie le même Prometheus 3.14.0 que le profil existant.
Le smoke de supervision vérifie le provisioning réel du tableau et des règles, puis exécute
chaque requête du tableau via l’API Prometheus. La charge Rise2 avec 50 clients reste une campagne
opérateur distincte ; voir `http-download-load.md`.
