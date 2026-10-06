# Téléchargements READY pendant un redémarrage API — 2.3.5

## Arrêt de l’API

L’image utilise `python -m app.server`, sur l’unique processus API existant. À réception de
SIGTERM/SIGINT, le drain commence immédiatement : les nouveaux appels HTTP sont refusés avec
503 et `Retry-After: 3`, la readiness ne renvoie plus 200 et les nouveaux WebSockets sont refusés.
La liveness reste disponible jusqu’à la fermeture des connexions par Uvicorn.

Uvicorn ferme son écoute et laisse cinq secondes aux requêtes déjà engagées pour terminer.
Il annule ensuite les requêtes restantes. Le lifespan attend jusqu’à deux secondes que leurs
blocs `finally` se terminent avant de fermer Redis et le moteur SQL. Les réponses READY libèrent
leurs descripteurs, tâches de heartbeat, slots et leases ; les ZIP libèrent aussi leur protection.

Ce budget laisse une marge dans les dix secondes d’arrêt Docker de l’API actuelle. Ni Compose,
ni les services persistants, ni le helper de déploiement ne changent. Il ne s’agit pas d’un
redémarrage sans coupure : les gros fichiers peuvent nécessiter une reconnexion. Un SIGKILL ou
une base indisponible peut empêcher le nettoyage SQL ; les leases restent alors protégées jusqu’à
leur expiration normale, sans suppression aveugle au démarrage.

## Reprise côté navigateur

Les transferts fichiers/dossiers réellement gérés par WoS via File System Access API réessaient
les erreurs réseau de fetch/lecture, les réponses 5xx et les corps terminés avant la taille attendue.
Après une coupure, le writer est fermé pour conserver les octets partiels. Chaque tentative relit
la taille locale et conserve au plus le dernier offset suivi ; elle envoie `Range: bytes=offset-`
avec le même `X-WOS-Download-Snapshot`. Le serveur revalide les droits et le snapshot à chaque GET.
Une réponse de reprise doit être 206, porter la bonne version et commencer au bon offset.
Une réponse 200 à un Range ou un manifeste différent n’est jamais concaténé au fichier partiel.

Les reprises sont bornées à huit retries par fichier (neuf tentatives), avec attentes de
1, 2, 4, 8, 15, 15, 15 et 15 secondes par défaut. Un `Retry-After` numérique de 5xx allonge
l’attente jusqu’au plafond de 15 secondes. Ce budget ne borne pas la durée d’un téléchargement
valide. Les erreurs réseau/5xx des pages de manifeste sont également réessayées sur le même snapshot.
Pause/annulation interrompent immédiatement les timers ; une panne persistante finit en erreur,
avec la reprise manuelle existante disponible. Les erreurs de disque, de droits ou de snapshot
ne déclenchent pas de retry automatique. Les limites existantes de retries 429 restent distinctes.

Les téléchargements natifs appartiennent au navigateur : leur reprise dépend de son gestionnaire,
qui peut utiliser Range/ETag. WoS ne crée aucun état persistant artificiel pour eux. Les ZIP sont
un flux distinct et ne bénéficient pas de la reprise automatique des fichiers READY. Fermer ou
recharger l’onglet retire le contrôleur WoS en mémoire ; aucune restauration automatique n’est promise.

## Vérifications reproductibles

```bash
cd backend
uv sync --frozen --dev
uv run pytest -q tests/test_request_drain.py tests/test_download_restart.py
```

Le test POSIX utilise un vrai processus Uvicorn, une socket TCP et une base SQLite WAL isolée :
lecture partielle d’un fichier de 128 Mio, SIGTERM pendant la contre-pression, sortie en moins de
neuf secondes sans SIGKILL, zéro lease résiduelle, redémarrage, reprise 206 avec ETag identique,
Content-Range exact et SHA-256 du fichier reconstitué égal à l’original. Les fixtures restent
locales et sont retirées à la fin. Aucun torrent ni compte de production n’est créé ou modifié.

```bash
cd frontend
npm ci
npm test -- src/features/torrents/recursiveDownload.test.ts src/features/torrents/downloadManager.test.ts
```

Les régressions couvrent coupure partielle, 503/Retry-After, réseau indisponible, EOF prématuré,
reprise invalide refusée, budget de retries, annulation pendant attente, retry de manifeste et
absence de retry pour les erreurs locales de write/close.

Le test local ne prouve pas à lui seul le comportement du proxy, de PostgreSQL et du stockage
Rise2. Une vérification opérateur consiste à lancer un fichier READY géré par WoS, garder
l’onglet ouvert pendant un déploiement normal, puis vérifier la reprise, le hash final et
l’absence de leases/slots résiduels. Aucun accès d’exécution opérateur Rise2 n’est disponible
dans l’environnement de préparation ; cette vérification réelle reste distincte du succès du
pipeline de déploiement et de la preuve TCP locale.
