# HTTP READY — métriques et campagne de charge

Cette campagne valide la récupération HTTP des fichiers READY. Elle ne remplace pas les anciens
tests du scheduler qBittorrent. Un slot rapide reste acquis jusqu’à la fin du corps HTTP ou à la
déconnexion ; aucune rotation ni préemption temporelle n’est introduite.

## Métriques disponibles

`/api/v2/metrics` expose les métriques agrégées du processus API unique :

| Préfixe `wos_http_download_` | Sens |
| --- | --- |
| `fast_streams`, `waiting_streams` | Flux courants par voie |
| `fast_admission_target` | Cible des admissions futures ; peut être inférieure aux flux rapides déjà engagés |
| `peak_active_streams`, `peak_waiting_streams` | Pics depuis le démarrage du processus |
| `waiting_oldest_age_seconds` | Ancienneté maximale des attentes courantes |
| `fast_max_idle_seconds` | Temps maximal sans nouveau corps accepté par ASGI pour les flux rapides |
| `host_upload_bytes_per_second`, `uplink_capacity_bytes_per_second` | Dernière mesure hôte et capacité configurée |
| `telemetry_age_seconds`, `telemetry_fresh` | Âge du dernier échantillon valide, fraîcheur à 45 secondes ; fraîcheur nulle sans mesure |
| `admitted_total`, `waiting_rejected_total` | Admissions et refus du plafond de deux attentes par compte |
| `bytes_total{lane="fast\|waiting"}` | Octets du corps acceptés par `ASGI send` |
| `ended_total{outcome="completed\|interrupted\|error"}` | Fin du corps envoyé, interruption ou erreur |
| `fast_grant_seconds`, `first_byte_seconds` | Histogrammes cumulatifs du délai de voie rapide et du premier corps accepté par ASGI |

Les labels sont fixes. Aucun compte, IP, chemin, nom de fichier, lease ou hash de torrent n’est
exporté. Les histogrammes et compteurs utilisent une mémoire bornée et se réinitialisent avec
le processus ; utiliser `rate()`/`increase()` dans Prometheus. L’âge de télémétrie tient compte
de la date de l’échantillon réseau, pas seulement de la date de récupération de la requête.

`completed` désigne l’envoi intégral du corps à ASGI, y compris une réponse Range. Le client peut
encore avoir des données dans les buffers TCP ou annuler sa lecture. Ce compteur n’atteste donc
pas que le fichier a été enregistré sur son disque. La campagne distingue les octets lus et les
SHA-256 vérifiés côté client. De même, une voie rapide inactive ne prouve pas à elle seule une panne :
un client lent peut appliquer une contre-pression normale.

## Protection des leases sous contre-pression

Le renouvellement des leases de fichiers READY est exécuté par une tâche indépendante du corps
HTTP, avec une courte session SQL distincte à chaque renouvellement. Un `send` bloqué ne suspend
plus le heartbeat. Une perte de lease arrête la réponse ; les slots, descripteurs et tâches sont
libérés dans les chemins fin, erreur et annulation. Aucune connexion SQL n’est conservée entre
les renouvellements. Les ZIP gardent leur mécanisme distinct.

Le test de régression reproduit un envoi ASGI bloqué : l’implémentation précédente ne renouvelle
pas pendant cette attente ; la nouvelle continue et nettoie les ressources à la déconnexion.

## Reproduction locale

```bash
cd backend
uv sync --frozen --dev
uv run python -m app.local_http_download_load --clients 50 --seconds 45 > /tmp/http-load-local.json
```

Le runner crée une base SQLite WAL, 50 comptes et droits READY temporaires, deux fichiers réellement
écrits (2 Mio et 128 Mio), puis un serveur Uvicorn de loopback. Les cinq premiers clients lisent
de gros fichiers à 500 000 octets/s ; après une seconde, les autres arrivent avec un mélange de
fichiers, vitesses et reprises Range. Les observations loopback alimentent la décision toutes les
15 secondes. Les requêtes en cours sont annulées à la fin du budget ; on vérifie ensuite que
slots et leases ont disparu. Toutes les fixtures sont retirées à la fermeture du runner.

L’environnement n’exécute ni qBittorrent ni NewGreedy, n’utilise pas PostgreSQL, et ne reproduit
pas les performances du disque ou de la carte réseau Rise2. Le CPU/RSS du rapport couvre le
processus Python complet, serveur **et clients**, et les lectures peuvent profiter du cache disque.

Preuve locale de la préparation 2.3.4 : [rapport JSON](evidence/http-load-local-2.3.4.json).
Sur 45 secondes : pic de 50 flux et 45 attentes, 42 fichiers terminés avec SHA-256 valide,
4 reprises Range, 8 gros transferts arrêtés au budget, zéro erreur et zéro slot/lease restant.
P95 liveness 4,1 ms ; readiness SQL 47,8 ms. Ces chiffres ne constituent pas un GO de capacité Rise2.

## Campagne sur Rise2

Le runner externe effectue exclusivement des GET de fichiers READY et de sondes de santé. Il
ne crée/supprime pas de compte, ne modifie pas de torrent et ne touche pas au scheduler qB.
Il peut fonctionner depuis un poste Linux distinct pour éviter de confondre CPU client et serveur.

Préparer un manifeste **privé, appartenant à l’opérateur, en mode 0600**. Utiliser des sessions
valides de comptes de test distincts avec des droits READY, et les tailles et SHA-256 connus des
fichiers. Les sessions sont émises par le parcours normal de connexion ; ne pas désactiver ses
limites pour constituer le manifeste. Les cookies ne doivent jamais entrer dans Git ni les rapports.

Structure du manifeste :

```json
{
  "base_url": "https://world-of-seeds.fr",
  "cookie_name": "wos_session",
  "targets": [
    {
      "path": "/api/v2/torrents/UUID-DU-DROIT/files/UUID-DU-FICHIER/download",
      "session_token": "COOKIE-DE-SESSION-PRIVE",
      "expected_bytes": 21474836480,
      "expected_sha256": "SHA256-HEX-DE-64-CARACTERES",
      "read_bytes_per_second": 500000,
      "cancel_after_bytes": 0,
      "resume_after_cancel": false
    }
  ]
}
```

Remplacer les valeurs d’exemple et ajouter 50 entrées : petits fichiers, gros fichiers (par
exemple 20 Go), clients lents à 500 ko/s et rapides, annulations avec reprise partielle.
Les requêtes 429 ont des retries bornés respectant `Retry-After`. Les reprises exigent une réponse
206, le `Content-Range` attendu et le même ETag ; un contenu différent n’est jamais concaténé.

```bash
chmod 0600 /chemin/prive/http-load-manifest.json
cd backend
uv run python -m app.benchmark_http_downloads \
  --manifest /chemin/prive/http-load-manifest.json \
  --seconds 300 > /chemin/prive/http-load-report.json
```

Si le runner est exécuté depuis un accès interne Rise2 au Prometheus existant, ajouter
`--prometheus-url http://127.0.0.1:9090` **seulement si cette adresse est réellement accessible**.
Ne pas publier de nouveau port. Depuis un conteneur applicatif sur le réseau monitoring,
l’adresse configurée peut être `http://prometheus:9090`. Sans accès Prometheus depuis le client,
collecter les mêmes courbes dans Grafana pendant la fenêtre horodatée du test.

Le rapport récupère CPU, iowait, utilisation disque, débit qB et métriques HTTP par requêtes
Prometheus agrégées. Ce Prometheus doit couvrir le seul hôte Rise2 pour ces agrégats. Un résultat
absent est conservé comme inconnu ; il n’est pas remplacé par zéro. Le rapport ne journalise
aucune URL cible, session ou exception contenant des identifiants.

## Critères de lecture de la campagne réelle

- Au moins 50 flux simultanés observés, avec assez de gros fichiers pour maintenir la charge.
- qB actif pendant la fenêtre : débit DL/UL observé, pas uniquement processus healthy.
- Télémétrie réseau fraîche ; CPU, iowait et saturation disque documentés pendant la fenêtre.
- Zéro erreur HTTP inattendue ou corruption ; SHA-256 des fichiers terminés corrects.
- Reprises Range/ETag correctes ; annulations distinguées des erreurs et des terminaisons.
- P95 des sondes live/ready inférieur à 1 seconde comme seuil initial, avec maxima et erreurs joints.
- Aucun slot perdu lors d’une hausse de charge ; gros fichiers effectivement promus avec l’ancienneté.
- Zéro slot et lease résiduel après annulation/fin, en tenant compte du délai de scrape.

L’absence d’un prérequis interdit de conclure que la capacité production est validée. Le champ
`production_capacity_validated` reste faux : la décision exige la revue des preuves serveur.
À la livraison 2.3.4, cette campagne réelle reste à exécuter avec l’accès opérateur Rise2.
