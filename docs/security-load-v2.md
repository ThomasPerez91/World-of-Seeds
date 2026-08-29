# V2-32 — sécurité et charge

## Portée et niveaux de preuve

Cette validation ne déploie rien sur Rise2 et ne touche ni la V1, ni ses volumes, ni ses
secrets. Elle distingue trois niveaux afin de ne pas transformer une mesure dépendante du
matériel GitHub en garantie de production.

| Niveau | Bloquant | Preuve |
|---|---:|---|
| CI déterministe | oui | règles scheduler, backlog borné, WS sans session SQL persistante, Redis fail-closed, Range/reprise/limites, manifeste 50 000 fichiers, CSP/auth/CSRF/path/symlink, audits dépendances et image |
| profil local Compose | oui | API mono-processus, PostgreSQL, Redis, worker, scheduler, qBittorrent, NewGreedy et stockage réels ; charge bornée 1/10/25/50/100 comptes |
| Rise2 représentatif | avant pilote | durée soutenue, CPU/RAM/I/O, latences externes, pression disque et pannes réelles selon la checklist ci-dessous |

Les latences du smoke local sont publiées comme mesures indicatives. Ses invariants de
correction restent bloquants : aucun statut inattendu, aucune fuite de lease, aucune
transaction PostgreSQL laissée idle, budget de connexions borné et aucun identifiant métier
ou secret dans le rapport.

## Matrice automatisée

| Domaine | Charge ou panne | Invariants attendus |
|---|---|---|
| Scheduler | 1/10/25/50/100 comptes, 1 et 2 slots, petites/moyennes/grosses tailles, backlog 199 à 1 000 | plafond global et utilisateur, service de chaque compte, aging/déficit persisté, fenêtres de contrôle de 200 maximum |
| Scheduler | torrent partagé, stalled/cooldown, READY en seed, échec qB et reprise de leader | rotation déterministe, aucun slot consommé par stalled/READY, génération désirée rejouable sans doublon |
| API/HTTP | 1/10/25/50/100 comptes, liste + manifeste + Range concurrent | authentification et ownership, `206`, contenu exact, zéro lease résiduelle |
| WebSocket | 10/25/50/100 connexions, 25 comptes × 4 onglets, 25 reconnexions | session SQL fermée avant attente réseau, heartbeat réseau, fermeture propre, resync autoritaire si Redis tombe |
| PostgreSQL | charge HTTP et 100 WS | zéro `idle in transaction`, connexions du profil mono-processus inférieures ou égales à 40, requêtes longues contrôlées sur Rise2 |
| Manifeste | petit, paginé, 50 000 fichiers | pagination bornée à 500, démarrage progressif, pause/reprise/annulation sans matérialiser toute l'arborescence |
| Téléchargement | Range, reprise, gros fichier sparse, utilisateur lent/déconnecté, limites globales/utilisateur | pas de lecture intégrale, descripteur et lease libérés, ETag/snapshot stables, changement physique rejeté |
| Coordination | Redis indisponible puis retour, worker/scheduler redémarrés, qB lent/indisponible | PostgreSQL reste autorité, backoff, pas de succès inventé, pas de mutation externe sous transaction SQL |

Commande pleine pile jetable :

```sh
scripts/local_v2.sh up
scripts/local_v2.sh smoke
scripts/local_v2.sh load
scripts/local_v2.sh monitoring-up
scripts/local_v2.sh monitoring-smoke
scripts/local_v2.sh down
```

`app.local_load_smoke` refuse tout environnement autre que `development` avec le profil
`v2`. Il crée 100 sessions aléatoires en mémoire, ne les affiche jamais, et son rapport ne
contient que des agrégats.

## Revue sécurité applicative

La revue est alignée sur les risques OWASP pertinents, sans prétendre à une certification :

- contrôle d'accès : ownership opaque pour API, manifeste, fichier, ZIP, purge,
  réconciliation et import ; endpoints admin protégés ; WebSocket authentifié avant accept ;
- cryptographie et sessions : mots de passe hachés, jetons stockés hachés, cookies `HttpOnly`
  et `SameSite=Strict`, `Secure` obligatoire en production, CSRF sur chaque mutation ;
- injection et chemins : requêtes SQLAlchemy paramétrées, chemins relatifs validés, ouverture
  `O_NOFOLLOW`, refus des symlinks et traversées, racines V1/V2 séparées ;
- conception sûre : PostgreSQL est l'autorité ; Redis ne transporte que des signaux ; jobs,
  générations scheduler et leases rendent les effets rejouables et bornés ;
- configuration : CSP et en-têtes de sécurité testés, processus non-root, filesystem
  read-only, capacités supprimées, réseaux internes, aucun socket Docker dans l'application ;
- intégrité logicielle : locks Python/npm, versions d'actions épinglées, Trivy 0.74.0 dont
  l'archive est vérifiée par SHA-256 avant les scans Dockerfile/image ; les variantes Compose
  passent leurs validateurs d'isolation dédiés ;
- journalisation : tests dédiés sur métriques, événements, audit et erreurs ; aucun passkey,
  cookie, jeton, URL credentialée, chemin utilisateur ou infohash dans les rapports de charge.

Les rapports JSON Python, frontend, configuration et image sont conservés 14 jours comme
artefact CI. Toute vulnérabilité Python connue bloque ; les vulnérabilités applicables
`HIGH`/`CRITICAL` avec correctif bloquent les audits frontend et image. Pour l'image, les
vulnérabilités sans correctif restent dans le rapport complet mais ne bloquent pas arbitrairement ;
elles doivent être réévaluées à chaque mise à jour d'image. Toute exception future doit nommer le
CVE/check, le composant, la raison, la mitigation et une date de revue.

Exception d'exploitation connue : cAdvisor requiert privilèges et montages hôte en lecture
seule pour observer les conteneurs. Il reste dans le profil `monitoring`, sans réseau backend
de l'application ni secrets applicatifs. Cette exception doit être revalidée sur Rise2 ; elle
ne justifie aucun montage Docker dans l'API, le worker ou le scheduler.

## Checklist d'acceptation Rise2 avant pilote

Exécuter avec des comptes et secrets de test dédiés, après snapshot, sur la pile V2 isolée.
La V1 reste active et non modifiée.

1. Vérifier l'isolation, la sauvegarde et le monitoring, puis enregistrer versions d'images,
   SHA applicatif, configuration scheduler et capacité disque sans copier de secret.
2. Créer 100 comptes test et un backlog supérieur à 200, avec tailles mixtes, contenus
   partagés, éléments stalled/cooldown et contenus READY en seed.
3. Mesurer 30 minutes après 5 minutes de chauffe avec 1 puis 2 slots. Exiger zéro famine,
   doublon, corruption ou transition interdite ; cycle scheduler p95 inférieur à son intervalle
   configuré et fenêtres qB inférieures ou égales à 200.
4. Ouvrir successivement 10/25/50/100 WS puis 25 comptes × 4 onglets. Redémarrer l'API,
   couper/rétablir Redis et perdre volontairement un événement. Exiger reconnexion + GET de
   resynchronisation, zéro transaction idle et retour du pool/mémoire au plateau initial.
5. Télécharger simultanément petits et gros fichiers avec Range/reprise, débit lent,
   annulation et déconnexion. Exiger zéro lease résiduelle et absence de dépassement des
   limites globales/utilisateur. Garder un seul processus API si ce test reste suffisant.
6. Tester manifests de quelques entrées, plusieurs milliers et 50 000 ; vérifier démarrage
   progressif, pagination bornée, pause/reprise et annulation sans pic mémoire proportionnel.
7. Injecter séparément : Redis indisponible, PostgreSQL lent, qB/NewGreedy lents ou
   indisponibles, redémarrage worker/scheduler, reset qB et pression disque contrôlée. Vérifier
   backoff, états sûrs, absence de faux succès et reprise idempotente.
8. Pendant chaque palier, suivre CPU, RSS, connexions/pool PostgreSQL, transactions longues,
   requêtes lentes/N+1, latence qB/NewGreedy, débit et attente I/O, disque et métriques bornées.
   Investiguer tout CPU soutenu supérieur à 80 %, iowait supérieur à 20 %, croissance mémoire
   monotone, API métadonnées p95 supérieure à 2 s ou p99 supérieure à 5 s.
9. Rejouer les audits de dépendances/configuration/image et rechercher secrets/identifiants
   métier dans logs, métriques, audit, événements, erreurs et rapports.
10. Conserver un rapport expurgé avec résultats par palier, anomalies, correctifs et décision
    explicite : pilote autorisé, autorisé avec limites, ou refusé.

Ces seuils sont des garde-fous d'investigation, sauf les invariants de correction explicitement
marqués « exiger ». Un écart de performance ne doit pas être masqué par l'ajout immédiat d'un
second processus API : il faut d'abord identifier PostgreSQL, stockage, réseau ou traitement.
