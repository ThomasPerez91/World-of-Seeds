# World of Seeds — Correctifs de l’audit 2.4.x

- Audit : version 2.3.28, 22 octobre 2026, commit `2e507fc6a1f36987d6e852dd2469f5764b2b0500`.
- Début : 22 octobre 2026. Base réelle : production 2.3.29, commit `db1fa6a545b513a433beee4841405905162dc701` ; ajouts Discord conservés.
- Objectif : traiter les constats confirmés, infirmer les faux positifs avec preuves et expliciter les dépendances opérateur.
- Versions : 2.4.1 puis incrément de patch par livraison réellement déployée ; pas de version livrée sur la seule base d’une CI verte.
- Cycle : tests isolés → PR develop → CI → merge → PR master → CI → merge/déploiement → contrôles réels → mise à jour du journal → livraison suivante.
- Tests : comportementaux, PostgreSQL/Redis/Docker en CI selon disponibilité ; aucun test destructif sur production. Limites métier users, protections techniques pour tous, exemptions admin vérifiées côté serveur.
- Audit lu intégralement (15 pages) : les constats [V], [S] et les preuves runtime manquantes restent distingués.
- État vérifié : 2.4.1 déployée sur Rise2 ; 2.4.5 déployée et vérifiée le 23 octobre ; S1, S2 et S3 livrés ; ZIP1 livré en 2.4.6 ; S4 reste le prochain correctif.

## Matrice de suivi

| Référence | Problème | Priorité | Statut | Version | PR | Tests |
|---|---|---|---|---|---|---|
| S1 | Validation torrent quadratique | P0 | Corrigé, testé et déployé | 2.4.3 | #403 / #405 / #406 / #404 | 21 tests ajoutés ; collisions, mémoire, budget/cancel, async, relance, 30k |
| S2 | Limiteur externe non borné | P0 | Corrigé, testé et déployé | 2.4.4 | #408 / #410 / #409 | 100k IPv6, expiration, non-éviction, budgets/concurrence/HTTP |
| S3 | Saturation login/reset | P0 | Corrigé, testé et déployé | 2.4.5 | #412 / #414 / #413 | Connexion/reset avec table pleine, budgets IP/identité/global, IPv6, mémoire, expiration |
| ZIP1 | Lease ZIP expirée en FIFO et nettoyage MissingGreenlet | P0 | Corrigé, testé et déployé ; ZIP privé production à confirmer | 2.4.6 | #416 / #418 / #417 | Attente > TTL, send bloqué, purge, UUID expiré, annulation, 8/9 leases et PostgreSQL |
| S4 | Métadonnées réseau et périmètre C411 | P0 | À traiter (confirmation sur branche actuelle requise) | — | — | — |
| S5 | Passkeys exposées en clair | P0 | À traiter (confirmation sur branche actuelle requise) | — | — | — |
| S6 | Limite authentifiée et cache santé | P1 | À traiter (confirmation sur branche actuelle requise) | — | — | — |
| B1 | 150 Go users et exemptions administrateur | P0 | À traiter (confirmation sur branche actuelle requise) | — | — | — |
| B2 | Isolation des contrôles scheduler défectueux | P0 | À traiter (confirmation sur branche actuelle requise) | — | — | — |
| B3 | Rétention SYNC et snapshots inchangés | P0 | À traiter (confirmation sur branche actuelle requise) | — | — | — |
| B4 | Découplage slots C411 et identité qB | P0 | À traiter (confirmation sur branche actuelle requise) | — | — | — |
| B5 | Durée sans progression et libération des quotas | P1 | À traiter (confirmation sur branche actuelle requise) | — | — | — |
| B6 | Jobs FAILED, relances et options de retry | P1 | À traiter (confirmation sur branche actuelle requise) | — | — | — |
| B7 | Longs téléchargements, 429, session et Range | P1 | À traiter (confirmation sur branche actuelle requise) | — | — | — |
| B8 | Plafonds serveur par user et exemption admin | P0 | À traiter (confirmation sur branche actuelle requise) | — | — | — |
| B9 | Navigation/archives SQL et verrous de lecture | P1 | À traiter (confirmation sur branche actuelle requise) | — | — | — |
| B10 | I/O disque hors verrou global | P1 | À traiter (confirmation sur branche actuelle requise) | — | — | — |
| B11 | Rétention sessions/throttles et réconciliation | P2 | À traiter (confirmation sur branche actuelle requise) | — | — | — |
| B12 | URL Alembic, dates et downgrade peuplé | P1 | À traiter (confirmation sur branche actuelle requise) | — | — | — |
| C1 | Déploiement interrompu après stop/migration | P0 | Corrigé, testé et déployé | 2.4.1 | #396 / #397 / #398 / #399 | 21 tests exécutables de déploiement |
| C2 | Sauvegarde préalable et rollback non destructif | P0 | Corrigé, testé et déployé | 2.4.1 | #396 / #397 / #398 / #399 | 21 tests exécutables de déploiement |
| C3 | Sauvegardes configuration et hors hôte | P0 | À traiter (confirmation sur branche actuelle requise) | — | — | — |
| C4 | Alertmanager, routage et réception prouvée | P0 | À traiter (confirmation sur branche actuelle requise) | — | — | — |
| C5 | Durcissement infrastructure et workflows | P2 | À traiter (confirmation sur branche actuelle requise) | — | — | — |
| A1 | Purge admin, confirmation, préconditions et audit | P1 | À traiter (confirmation sur branche actuelle requise) | — | — | — |
| F1 | Confirmation React en deux clics | P2 | À traiter (confirmation sur branche actuelle requise) | — | — | — |
| F2 | WebSocket, backoff et session inter-onglets | P1 | À traiter (confirmation sur branche actuelle requise) | — | — | — |
| F3 | Dashboard, formatBytes, refresh admin | P2 | À traiter (confirmation sur branche actuelle requise) | — | — | — |
| R1 | Options effectives production | P0 | À traiter (confirmation sur branche actuelle requise) | — | — | — |
| R2 | DHT/PeX/LSD et web seeds qB réel isolé | P0 | À traiter (confirmation sur branche actuelle requise) | — | — | — |
| R3 | Timers backup et copie hors hôte | P0 | À traiter (confirmation sur branche actuelle requise) | — | — | — |
| R4 | Protections GitHub rise2-production | P1 | À traiter (confirmation sur branche actuelle requise) | — | — | — |
| R5 | Propriétaires checkout/hôte et RAID réel | P1 | À traiter (confirmation sur branche actuelle requise) | — | — | — |
| R6 | Absence ponctuelle qB et ERROR définitif | P1 | À traiter (confirmation sur branche actuelle requise) | — | — | — |
| R7 | 100k fichiers SQL et profil navigateur | P1 | À traiter (confirmation sur branche actuelle requise) | — | — | — |
| R8 | createWritable sur fichier partiel volumineux | P2 | À traiter (confirmation sur branche actuelle requise) | — | — | — |
| R9 | Chemins longs et index PostgreSQL | P1 | À traiter (confirmation sur branche actuelle requise) | — | — | — |
| R10 | Ordre des verrous, retard quota et secrets au repos | P1 | À traiter (confirmation sur branche actuelle requise) | — | — | — |
| T1 | Mutations M5 : révocation seconde session | P0 | Corrigé, testé et déployé | 2.4.2 | #400 / #401 | Deux appareils et reset sans cookies |
| T2 | Mutations M9/M11 : rôles et compte supprimé | P0 | Corrigé, testé et déployé | 2.4.2 | #400 / #401 | 14 mutations admin × 7 états, seed supprimée active |
| T3 | Derniers désabonnements et claims PostgreSQL | P0 | À traiter (confirmation sur branche actuelle requise) | — | — | — |
| T4 | Isolation manifestes/archives/fichiers/WebSocket | P0 | À traiter (confirmation sur branche actuelle requise) | — | — | — |
| T5 | Lease renouvelée et limites temporelles exactes | P1 | À traiter (confirmation sur branche actuelle requise) | — | — | — |
| T6 | Migrations peuplées et alembic check | P1 | À traiter (confirmation sur branche actuelle requise) | — | — | — |
| T7 | Client API, CSRF, 401/409/429 et reprise | P1 | À traiter (confirmation sur branche actuelle requise) | — | — | — |
| T8 | Bot/API scopes, révocation et confidentialité | P0 | À traiter (confirmation sur branche actuelle requise) | — | — | — |
| D1 | Documentation et écarts versions/règles | P2 | À traiter (confirmation sur branche actuelle requise) | — | — | — |

## 2.4.1 — sécurisation du déploiement (déployée)

### Cause racine

C1 : le marqueur de runtime est posé après migration alors que les services sont déjà arrêtés.
C2 : downgrade implicite sous `set +e`, sans sauvegarde préalable obligatoire ni validation de compatibilité.
Les copies root installées dans `/usr/local/sbin` sont indépendantes du checkout : une fusion ne les met pas à jour.

### Correction et stratégie

Helper : `deploy/deploy-world-of-seeds-v2-rise2` ; installateur opérateur épinglé :
`scripts/rise2_v2_deploy_helper_install.sh` ; politique de migrations explicite :
`deploy/migration-policy.json` ; tests exécutables : `backend/tests/test_rise2_deploy_failures.py`.
Voir les changements du helper et les tests d’échec. Aucun downgrade automatique destructif ; remise en service de l’ancienne application seulement sur schéma identique ou compatibilité explicitement déclarée et vérifiée.
La sauvegarde avant changement de head doit être chiffrée et restaurée en isolation avant arrêt/migration.

### Bootstrap opérateur obligatoire

La commande SSH de CI est forcée et n’autorise pas l’installation d’un helper root.
Il faut installer la copie corrigée depuis un commit validé, par un accès opérateur déjà autorisé, avant la première promotion master.
Ne pas profiter du preflight exécuté en root pour contourner cette restriction. Tant que cette installation n’est pas prouvée, ne pas annoncer C1/C2 actifs sur Rise2 et ne pas promouvoir la 2.4.1.

Le job SSH dispose de 240 minutes pour inclure la sauvegarde synchrone (dump,
restauration isolée et chiffrement, opérations externes bornées à 15 minutes chacune),
puis le déploiement et une éventuelle récupération applicative. La limite précédente
était de 20 minutes et pouvait interrompre une sauvegarde valide.

### Vérifications et statut

Branche : `fix/audit-2.4.1-deployment`. [PR #396](https://github.com/ThomasPerez91/World-of-Seeds/pull/396) intégrée dans develop (`dd258e843ce16c9ca96d58153c59b101d6491ce2`).
Promotion : [PR #397](https://github.com/ThomasPerez91/World-of-Seeds/pull/397), passée prête après confirmation opérateur.

Preuve opérateur reçue le 22 octobre 2026 : installateur validé par `sha256sum --check`,
helper installé depuis `093e79d2029cabda79bef5585ce91e3f472cd8e3` ; empreinte finale
`9d60b2f57f54f61bccb85e0be5caf2a88e588eef4d6813fb7c1a09c6abfc63c4`, identique à la source revue.
Ce résultat confirme l’installation du helper, pas encore le déploiement applicatif.

[CI source](https://github.com/ThomasPerez91/World-of-Seeds/actions/runs/39791637717) et
[CI de promotion](https://github.com/ThomasPerez91/World-of-Seeds/actions/runs/39793037306)
réussies : les six contrôles passent ; backend source 1190 réussis / 2 ignorés.
L’ancien lancement push develop `39792883500` reste marqué en cours sur l’installation du
prérequis de sauvegarde ; GitHub refusait la fusion avec « Required status check backend is in progress ».
Aucune protection n’est désactivée. La présente preuve documentaire repasse le cycle normal de PR et de CI.
Ce bloc décrit le blocage initial ; les preuves de livraison ci-dessous le clôturent.
Validation locale : backend 1120 réussis / 72 ignorés (services non disponibles), frontend
209 réussis / 3 ignorés, bot 36 réussis ; Ruff, mypy strict (253 sources), TypeScript et build réussis.
21 tests exécutables pour C1/C2, dont preflight, configuration, sauvegarde, arrêt partiel,
migration, schéma partiel/incompatible/inaccessible, démarrage, healthcheck, rollback, TERM,
head inchangé, politique absente/non transactionnelle, verrou et installation avec checksum.
Les commandes Docker sont simulées dans ces 21 tests ; les tests de restauration réelle
existants demandent Docker et restent soumis aux résultats de CI.

### Preuve de livraison 2.4.1

Promotion #397 fusionnée après #398 (preuve bootstrap) et #399 (fenêtre de sauvegarde).
Commit master : `92a16cefb47c9b9f2ac8898eba8b78262b0d50e6`.
[CI master](https://github.com/ThomasPerez91/World-of-Seeds/actions/runs/39848967140)
réussie : 1190 tests backend réussis / 2 ignorés, tous les contrôles verts.
[Déploiement Rise2](https://github.com/ThomasPerez91/World-of-Seeds/actions/runs/39849514346)
réussi le 22 octobre 2026 à 20:32:46 UTC. Journal : « World of Seeds V2 déployé avec succès »,
révision exacte et image `ghcr.io/thomasperez91/world-of-seeds-v2@sha256:35be8b910d50e8326c0055c53ffedd7deda631c08b783d8471b183ea35be6c8c`.
Le helper vérifie API, workers, scheduler, services persistants non recréés, image et révision.
Contrôles publics indépendants : `/`, bundle `/assets/index-De7gI_ab.js`,
`/api/v1/health/live` et `/api/v1/health/ready` en HTTP 200 ; santé et bundle annoncent 2.4.1.
Aucun changement de schéma dans cette livraison. Aucun test destructif de production.
C3/C4 : aucune sauvegarde hors hôte ou réception d’alerte n’est présumée active.


## 2.4.2 — régressions d’authentification (déployée)

T1/M5 : deux appareils authentifiés conservent leurs cookies ; un troisième client
sans session consomme le reset. Les deux anciens cookies doivent ensuite recevoir 401,
et les deux lignes UserSession doivent avoir revoked_at. Cela isole la révocation serveur
de l’effacement des cookies de la réponse de reset.

T2/M9 : matrice de toutes les mutations admin V1/V2 (options, comptes, clés API,
services, purge, qB, récupération), comparée à l’inventaire OpenAPI pour détecter une
route oubliée. Chaque route vérifie anonyme, user avec CSRF valide, user sans CSRF,
admin sans CSRF, admin valide, admin suspendu et admin supprimé encore actif.
Le user valide doit atteindre le refus de rôle `forbidden`, indépendamment du CSRF.
Le cas admin atteint le résultat exact de validation/métier (422, 404, 503 pour
services non configurés, ou succès), sans forcer artificiellement un 200.

T2/M11 : la seed d’un compte initialement autorisé devient invalide après soft-delete,
tout en conservant is_active=True pour tester la suppression indépendamment de la suspension.

Le code d’authentification existant applique ces protections ; cette livraison renforce
les tests contre des régressions, sans modifier les limites métier ou les sessions.
PR #400 intégrée dans develop, promotion #401 fusionnée dans master ; preuves ci-dessous.

Validation locale 2.4.2 : 149 tests ciblés réussis / 1 ignoré (dépendance PostgreSQL),
Ruff et formatage 292 fichiers, mypy strict 254 sources, test de version frontend,
TypeScript et build réussis. Trois essais de mutation temporaires, restaurés avant
publication : M5 sans révocation, M9 sans contrôle is_admin, M11 sans contrôle deleted_at ;
chaque test ciblé échoue avec AssertionError quand sa protection est retirée.
La suite ciblée repasse après restauration ; aucun fichier d’authentification runtime modifié.


### Preuve de livraison 2.4.2

[PR #400](https://github.com/ThomasPerez91/World-of-Seeds/pull/400) et
[promotion #401](https://github.com/ThomasPerez91/World-of-Seeds/pull/401) fusionnées après contrôles verts et aucune remarque ouverte.
Commit master : `0be962b3f183a621631b1bba1addc229a2602086`.
[CI source](https://github.com/ThomasPerez91/World-of-Seeds/actions/runs/39850678285) et
[CI master](https://github.com/ThomasPerez91/World-of-Seeds/actions/runs/39852047968)
réussies : 1291 tests backend réussis / 2 ignorés ; six contrôles verts.
[Déploiement Rise2](https://github.com/ThomasPerez91/World-of-Seeds/actions/runs/39852799747)
réussi le 22 octobre 2026 à 20:56:02 UTC, révision exacte et image
`ghcr.io/thomasperez91/world-of-seeds-v2@sha256:a82c0920f5392ad1f57c84725b191e020528dc370533e4df692fda5ae287ce6f`.
Contrôles publics indépendants : live/ready HTTP 200 avec version 2.4.2,
page et bundle `/assets/index-BVzATqB2.js` HTTP 200, version frontend 2.4.2.
Aucune migration ; bot autonome inchangé. C3/C4 restent non validés opérationnellement.

## S1 — point de reprise initial du 22 octobre (historique)

Le scan de tous les chemins précédents dans `metainfo.py` est confirmé quadratique.
La première validation API est déjà déportée par `run_in_threadpool`, de même que la
lecture du spool worker par `asyncio.to_thread` ; la normalisation du gateway C411
reste synchrone dans une méthode async. Le constat doit distinguer ces cas.

Prototype local : ensembles de fichiers/répertoires normalisés à la place du scan,
pool dédié borné pour validation/staging/lecture/normalisation, saturation temporaire,
lifecycle API/worker et message FR/EN. Ce code n’est ni publié, ni testé complètement,
ni déployé ; ne pas considérer S1 corrigé.

Mesures locales sans réseau (infohash préservé) : ancien code 1000 fichiers 0,142246 s,
5000 fichiers 2,325027 s ; prototype 1000 fichiers 0,019671 s, 5000 fichiers 0,132521 s,
10000 fichiers 0,185090 s, 30000 fichiers 0,728226 s. Ce sont des mesures d’une machine,
pas une garantie de latence en production.

Blocage : environnement d’exécution devenu indisponible pendant le travail ; dernière
commande refusée avec « exec-server transport disconnected ». Les tests des collisions,
de la saturation, de l’annulation sans libération prématurée des slots, de la boucle
async et de la propriété des payloads doivent encore être écrits/exécutés. Repartir de
la branche distante validée, vérifier si les changements locaux sont récupérables,
puis reprendre S1 pour la future 2.4.3. Aucun nouveau bootstrap root nécessaire.


## 2.4.3 — validation torrent bornée et chemins sans scan quadratique (déployée)

Cause : chaque entrée comparait son chemin à tous les fichiers déjà rencontrés.
Un arbre de chemins normalisés partage les préfixes et remplace ce scan ; les
collisions fichier/répertoire dans les deux sens, Unicode NFC et casse restent refusées.
Le dictionnaire info brut et l’infohash ne changent pas. Les limites de profondeur,
éléments, chemins et métadonnées restent en place.

La validation initiale API et la lecture du spool worker étaient déjà déportées :
le constat async de l’audit est donc partiellement infirmé. La normalisation C411
était encore exécutée sur la boucle async et est désormais déportée. Validation,
staging possédé, lecture/validation du spool et normalisation partagent un pool
technique par processus : deux threads, quatre travaux admis au total (exécutés ou
mis en file). Ce budget technique s’applique aux administrateurs comme aux users.
L’annulation ne rend pas la capacité avant la fin réelle du thread ou le retrait de
l’entrée annulée de la file ; une rafale d’annulations ne peut pas agrandir cette file.
La protection existante du staging et de son nettoyage après annulation est conservée.

Saturation : API 503 torrent_validation_busy, Retry-After: 1, Cache-Control: no-store,
message FR/EN ; aucun torrent créé. Worker : erreur transitoire RETRY_WAIT, payload
conservé, pas d’ADD qB. Le pool est fermé à l’arrêt de l’API/worker avant fermeture DB.
Aucune migration ou modification d’infrastructure protégée ; version applicative 2.4.3.

Tests ciblés après correction de revue : 92 réussis. Cas ajoutés : collisions inverses/NFC/casse, budget threads
et file, boucle async réactive, annulation active/en attente, saturation user/admin,
gateway sans appel qB, surcharge worker relançable, exceptions/fermeture/redémarrage.
Les tests existants de propriété des payloads et nettoyage après annulation passent.
Un sous-processus borné à 10 secondes valide 30000 fichiers pour détecter une régression
quadratique sans bloquer la suite. Le script de benchmark reproductible se trouve dans
scripts/benchmark_torrent_validation.py ; mesures locales du 23 octobre (secondes, aucune opération réseau) :

| Fichiers | Avant | Après |
|---|---:|---:|
| 1000 | 0,098456 | 0,018167 |
| 5000 | 2,245008 | 0,132538 |
| 10000 | Non mesuré | 0,225523 |
| 30000 | Non mesuré | 0,647664 |

Chaque mesure vérifie nombre de fichiers, taille et infohash conservé. À 5000 fichiers,
le gain observé est d’environ 17 fois ; aucune extrapolation de mesure 30000 avant.
Le benchmark exclut la génération du torrent et ne garantit pas une latence production.

Première validation locale avant correction mémoire : backend 1240 réussis / 72 ignorés (services externes absents),
frontend 209 réussis / 3 ignorés ; Ruff/formatage (294 fichiers), mypy strict (256 sources),
TypeScript et build verts. Script de benchmark linté et exécutable. Le code de validation
retrouve exactement ses octets après la comparaison temporaire à l’ancien algorithme.
Les contrôles CI et le déploiement restent à prouver avant de déclarer S1 livré.


### Revue de promotion — mémoire des chemins profonds

La revue de #404 a relevé que le premier correctif avec tuples de préfixes copiait
encore O(profondeur²) références par chemin. Cas confirmé : 50 chemins distincts de
1000 composants, valides sous les limites existantes, pic Python tracé de 207940561
bytes. Le correctif final utilise un arbre de nœuds partageant les préfixes ; ce même
cas mesure 14452689 bytes, infohash conservé. Il s’agit d’allocations Python tracées,
pas d’une mesure de RSS totale du processus.

Le benchmark accepte --depth et --memory et refuse de générer un cas hors budget
d’éléments du parser. Un nouveau test isolé impose un pic sous 64 MiB, avec une
deadline de 20 secondes. Les 92 tests ciblés passent après correction ; Ruff et mypy
restent verts. Le contrôle complet CI est relancé avant toute promotion master.


### Relectures de relance

La relecture du spool lors d’une relance rejoint le pool technique commun. Le verrou
existant par storage_key et la protection contre l’annulation sont conservés ; des
clés distinctes ou plusieurs instances de TorrentRetryReads ne peuvent pas contourner
le budget de threads. Un test vérifie la saturation entre deux clés/instances, l’absence
d’exécution excédentaire et la récupération de la clé après saturation.


### Preuve de livraison 2.4.3

[PR #403](https://github.com/ThomasPerez91/World-of-Seeds/pull/403),
[correction mémoire #405](https://github.com/ThomasPerez91/World-of-Seeds/pull/405) et
[relances #406](https://github.com/ThomasPerez91/World-of-Seeds/pull/406) intégrées dans develop.
[Promotion #404](https://github.com/ThomasPerez91/World-of-Seeds/pull/404) fusionnée sur master
après contrôles verts et remarque P1 effectivement corrigée/résolue.

Commit master : `69b10c028cfc370c93381f39c2c3724385516663`.
[CI source finale](https://github.com/ThomasPerez91/World-of-Seeds/actions/runs/39909563942)
et [CI master](https://github.com/ThomasPerez91/World-of-Seeds/actions/runs/39910474087)
réussies : 1312 tests backend réussis / 2 ignorés, six contrôles verts.
[Déploiement Rise2](https://github.com/ThomasPerez91/World-of-Seeds/actions/runs/39910869671)
réussi le 23 octobre 2026 à 06:21:54 UTC, image et révision exactes :
`ghcr.io/thomasperez91/world-of-seeds-v2@sha256:c4f89a61f25879c5ed64b61d89fd62bbd4af099b45028780231149868c4845aa`.
Le helper vérifie API/workers/scheduler et les services persistants sans les recréer.
Contrôles publics indépendants après déploiement : live/ready HTTP 200, version 2.4.3 ;
page et bundle `/assets/index-BCSVaby9.js` HTTP 200, version frontend 2.4.3.
Aucun changement de head Alembic, nouvelle installation root ou changement du bot.

Le blocage d’environnement du 22 octobre est résolu. Prochaine priorité S2 : borner
la mémoire et l’expiration du limiteur externe, agréger les préfixes IPv6 et préserver
les budgets sans contournement par éviction. S2 n’est pas encore corrigé. C3/C4
(sauvegardes hors hôte/alertes reçues) restent non validés opérationnellement.


## 2.4.4 — limiteur externe borné (livré)

Cause confirmée : defaultdict(deque) créait une entrée par adresse avant validation
API ; une adresse jamais revue ne bénéficiait d’aucune collecte d’expiration.

Le limiteur conserve un dictionnaire ordonné par dernier événement accepté, et
collecte les identités inactives sur chaque appel sans parcourir tous les budgets
actifs. Les fenêtres restent glissantes, avec expiration exacte et Retry-After entier
positif arrondi au plafond. Un accès refusé ne prolonge pas le budget ni sa rétention.
Aucune entrée active n’est évincée pour faire place à une autre : à capacité, les
nouvelles identités reçoivent 429 jusqu’à libération d’une entrée. Les entrées déjà
présentes conservent leurs compteurs. La rotation d’adresse ne réinitialise pas
le budget d’une clé cliente existante.

Bornes par processus : 10000 identités au maximum, 60 événements par budget IP/client
sur 60 secondes (valeurs existantes), budget global technique de 6000 admissions IP
sur cette fenêtre. Le passage IP avant authentification charge ce budget ; le passage
client après recherche de clé ne le charge pas une deuxième fois. Les requêtes déjà
refusées par leur budget IP ne déclenchent pas la recherche de clé. Ce budget n’est
pas une limite métier par user et s’applique aux intégrations privilégiées aussi.

IPv6 : préfixe /64 commun ; écritures équivalentes et identifiants de zone ne créent
pas de nouveau budget. IPv4-mappée en IPv6 et IPv4 littérale partagent le même budget.
Les adresses invalides/absentes partagent une identité inconnue ; pas de croissance
avec des métadonnées arbitraires. Les identités conservées sont hachées, pas des IP brutes.
Le budget client reste stable après changement de réseau et les scopes ne changent pas.

Tests : bornes invalides, fenêtre/Retry-After aux limites, collecte des adresses jamais
revues, préservation des budgets actifs, absence d’éviction sous pression, IPv6/alias,
budgets IP/client distincts, global avant auth, concurrence, 100000 préfixes IPv6 avec
plafond de pic Python tracé à 32 MiB, récupération après expiration et réponses HTTP.
Première validation ciblée : 33 réussis / 1 ignoré (PostgreSQL), Ruff et mypy strict
257 sources verts. Suite backend complète : 1259 réussis / 72 ignorés (services
indisponibles localement) ; frontend : 209 réussis / 3 ignorés, TypeScript et build
réussis. CI et déploiement validés ci-dessous.

Version 2.4.4, aucune migration ou modification d’infrastructure protégée. Limiteur
local au processus, API unique exigée par le déploiement ; les budgets sont réinitialisés
au redémarrage. Le problème de saturation login/reset (S3) et le réglage métier
WOS_REQUEST_RATE_LIMIT_PER_MINUTE (S6) restent séparés et non déclarés corrigés.

La double CI de promotion a révélé une interaction non déterministe d’un test
PostgreSQL existant : huit relances peuvent recevoir le 503 attendu du pool de
validation (capacité 4), avant le contrôle de stockage qui attend 507. Le test
d’admission stockage dispose désormais d’un pool isolé admettant ses huit
concurrents ; toutes les assertions de stockage et de non-duplication sont
conservées. Les tests dédiés de saturation conservent les bornes de production.
Aucun changement de capacité de production. La revue a corrigé la référence du
pool remplacé : app.torrents.retry_reads.torrent_validation_pool. CI complète verte.

### Preuves de livraison 2.4.4

#408 (correctif S2) et #410 (isolation du test stockage) intégrées ; promotion #409
fusionnée sur master `e43bd5d572c93fe0d78c3960e4143d8b004b7242`.
[CI source finale](https://github.com/ThomasPerez91/World-of-Seeds/actions/runs/39919511022),
[CI promotion](https://github.com/ThomasPerez91/World-of-Seeds/actions/runs/39920032237)
et [CI master](https://github.com/ThomasPerez91/World-of-Seeds/actions/runs/39920526108)
vertes : 1329 tests backend réussis / 2 ignorés ; six contrôles verts.
Le premier passage de promotion avait révélé le conflit du test stockage avec le
pool borné ; il a été corrigé, sans relancer un échec pour le masquer.

[Déploiement Rise2](https://github.com/ThomasPerez91/World-of-Seeds/actions/runs/39920982983)
réussi le 23 octobre 2026 à 07:56:55 UTC. Révision exacte ci-dessus et image :
`ghcr.io/thomasperez91/world-of-seeds-v2@sha256:5c556872d28756999c96a6efc67f1e7f8220ad29dc73cb3615dfe0a7eff1a1ea`.
Contrôles publics indépendants après livraison : live/ready HTTP 200, version 2.4.4 ;
page et bundle `/assets/index-BL36gtjC.js` HTTP 200, version frontend 2.4.4.
Aucune migration, nouvelle installation root ou mise à jour du bot.

Prochaine priorité : S3, saturation login/reset. S6 et C3/C4 restent ouverts.

## 2.4.5 — saturation des protections login/reset (livré)

Cause confirmée : une table IP de 10000 entrées était commune au login et au reset
(publication des tokens exclue). À capacité, chaque nouvelle adresse recevait 429
sans pouvoir vérifier ses identifiants, même valides. Les métadonnées IPv6 n’étaient
pas agrégées par préfixe.

Chaque route dispose désormais de son propre limiteur et de son propre plafond
global de 6000 admissions par fenêtre (60 secondes par défaut). Une saturation du
login ne consomme ni la table ni le plafond global de confirmation de reset, et
réciproquement. Ces plafonds limitent le travail avant SQL/Argon2 ; le pool Argon2
borné existant reste actif. Il ne s’agit pas d’une exemption pour les administrateurs.

La table primaire reste bornée à 10000 réseaux, sans éviction active, avec 30 essais
par fenêtre par défaut (réglages auth_ip_* existants). À capacité, le secours utilise
deux tableaux de 4096 fenêtres chacun : réseau et identité. Les index dérivent d’un
hachage avec secret aléatoire par processus et domaine distinct ; aucune adresse,
aucun username ni token de récupération n’est conservé dans ces tableaux. Le réseau
garde sa limite IP ; l’identité de secours est limitée à auth_max_attempts (5 par
défaut), même si elle change de réseau. Login : username canonique insensible à la
casse/espaces ; reset : token exact. Le verrouillage SQL existant par couple IP/nom
reste inchangé et s’applique aussi au secours.

Les fenêtres de secours restent actives lorsque des places primaires se libèrent,
et une identité déjà limitée ne contourne pas le secours via un réseau primaire.
Les IPv6 partagent leur /64 ; IPv4-mappée et IPv4 littérale sont unifiées. Des
métadonnées de pair invalides partagent le budget inconnu. Les fenêtres expirent à
la borne exacte, sans prolongation sur refus, Retry-After entier positif arrondi
au plafond. Les refus préalables du login portent également Cache-Control: no-store.

Limites explicites : plafonds globaux et collisions de secours peuvent produire un
429, même pour des identifiants valides. Les collisions ne font que restreindre ;
elles ne remettent pas les compteurs à zéro. Cela supprime le refus universel dû à
la seule occupation de la table, pas tout déni de service distribué. Les compteurs
restent locaux au processus et sont remis à zéro au redémarrage. Le déploiement
impose toujours un seul processus API. Pas de migration ou infrastructure protégée.

Tests : paramètres invalides, alias IPv6/IPv4/inconnu, table pleine sans éviction,
rotation IP/identité, maintien du secours après expiration primaire, collision
forcée, expiration exacte/Retry-After, plafond global et 100000 réseaux/identités
avec mémoire tracée bornée à 32 MiB. Intégration HTTP : connexion valide user/admin
sur nouvelle IP, refus avant crypto malgré rotation/casse, budgets login/reset
indépendants, reset valide et token toujours à usage unique avec table pleine.
Première validation ciblée : 64 réussis ; frontend 209 réussis / 3 ignorés,
TypeScript/build verts. Ruff et mypy strict 258 sources verts.
CI, revue et preuve de déploiement validées ci-dessous. Suite backend complète locale :
1280 réussis / 72 ignorés (services absents localement). Le test de charge utilise
une horloge figée pour exclure la vitesse du runner des assertions d’expiration.
S4, S6 et C3/C4 restent ouverts.

La première CI de promotion a révélé une attente synchrone instable dans le test
de restauration de langue : le texte anglais peut être rendu avant l’effet React
qui met à jour html.lang. Le test attend désormais explicitement cette valeur
finale avec waitFor ; son assertion en et les contrôles du menu restent inchangés.
Aucun changement de comportement frontend. Nouvelle CI complète verte avant promotion.

La revue de promotion a relevé un Retry-After trop court lorsque le plafond global
expire avant une limite IP/identité commencée plus tard. Le limiteur calcule désormais
le maximum des délais de tous les blocages applicables, sans charger les budgets
ni atteindre SQL/Argon2 lors d’un refus. Deux tests à horloge injectée couvrent les
fenêtres décalées global/IP et global/identité (secours et réseau primaire).

### Preuves de livraison 2.4.5

#412 (S3) et #414 (revue Retry-After et synchronisation du test de langue) intégrées ;
23 cas backend ajoutés au total. Promotion #413 fusionnée sur master
`11634b8f5183ae299d7a282e2d45be29455646e5`.
[CI source finale](https://github.com/ThomasPerez91/World-of-Seeds/actions/runs/39942930302),
[CI promotion](https://github.com/ThomasPerez91/World-of-Seeds/actions/runs/39943515835)
et [CI master](https://github.com/ThomasPerez91/World-of-Seeds/actions/runs/39944059451)
vertes : 1352 tests backend réussis / 2 ignorés ; six contrôles verts, revue corrigée.
Après ajustements : 55 tests ciblés backend locaux réussis ; App 12 réussis / 2 ignorés,
TypeScript/build, Ruff et mypy strict verts. La suite backend locale initiale 1280/72
précède les deux tests Retry-After ; la CI complète finale couvre la version livrée.

[Déploiement Rise2](https://github.com/ThomasPerez91/World-of-Seeds/actions/runs/39944454779)
réussi le 23 octobre 2026 à 11:13:59 UTC (13:13:59 à Paris), révision exacte ci-dessus :
`ghcr.io/thomasperez91/world-of-seeds-v2@sha256:16373810182da285a00a05b61da9a402bde56667bd2fd96408c32951e92b8019`.
Contrôles publics indépendants après déploiement : live/ready HTTP 200, version 2.4.5 ;
page et bundle `/assets/index-CJACzAQE.js` HTTP 200, version frontend 2.4.5.
Aucun changement de head Alembic, nouvelle installation root ou mise à jour du bot.
Les limites de disponibilité et de stockage des compteurs sont décrites ci-dessus.

Prochaine priorité S4 : métadonnées réseau des torrents et périmètre C411 ; vérifier
les fixtures réelles et la configuration qB avant toute modification sensible.
S6 et C3/C4 restent ouverts, sans présumer de validation opérationnelle.

## 2.4.6 — leases ZIP pendant l’attente FIFO (déployée)

Nouveau P0 ZIP1, demandé avant S4. Workspace propre et branches actualisées ;
base develop 2.4.5 après #415, master 2.4.5 après #413. Aucun test local de Claude
nommé test_archive_queue_lease.py retrouvé ; scénarios recréés dans ce fichier.

Causes confirmées : les routes download-folder-archive et download-archive
acquéraient une lease SQL avant la FIFO, sans heartbeat pendant cette attente.
Le générateur ZIP renouvelait seulement en progressant ; un send bloqué arrêtait
ces renouvellements. Le nettoyage relisait lease.id sur un objet ORM que le rollback
pouvait expirer, introduisant MissingGreenlet à la place de la cause initiale.

La réponse ZIP possède désormais tout son cycle : admission SQL et capture UUID,
puis démarrage immédiat du heartbeat indépendant en sessions SQL courtes, AVANT
l’acquisition du slot FIFO. La lease compte donc toujours dans les plafonds globaux
et par compte pendant l’attente. Ni lease ni tâche de fond sur une réponse non
exécutée. L’attente du slot, le streaming et la déconnexion sont surveillés dans un
TaskGroup ; l’échec du heartbeat annule l’attente/stream. Une validation SQL indépendante
supplémentaire après la FIFO précède les en-têtes de succès. Les renouvellements
redondants du générateur ZIP ont été supprimés.

Les fichiers simples capturent aussi un UUID, employé par le générateur, la télémétrie
et le nettoyage. Aucune modification de l’ordonnanceur ou des 429 sans Retry-After.
L’acquisition SQL capture son UUID avant le commit : en cas d’annulation du commit,
une suppression explicite est tentée sans dépendre d’un attribut ORM expiré.

Le nettoyage tente toutes ses opérations, sous protection AnyIO et asyncio contre
l’annulation, et conserve l’exception initiale avec une note sur l’erreur secondaire.
Un lecteur ZIP déjà exécuté dans un thread est terminé avant la fermeture du générateur
ou la libération du slot. Fin normale, erreur, annulation répétée et déconnexion
libèrent le générateur, le slot et la lease. Les contrôles de purge existants restent
actifs : un test appelle réellement le handler de purge après dépassement du TTL
initial et vérifie torrent_download_active ainsi que la présence du fichier.
Si SQL est durablement indisponible, la suppression ne peut être garantie : heartbeat
arrêté, lease bornée par son expiration, erreur de nettoyage attachée à la cause.

Attente bornée étudiée : la file est déjà limitée par les admissions SQL (défauts
8 archives globales, 2 par user ; slots FIFO configurés séparément). Pas de timeout
de durée ajouté : le TTL sert à détecter une lease abandonnée, pas à interrompre un
client encore connecté et renouvelé. Déconnexion, annulation ou échec de renouvellement
interrompent l’attente. Aucun contournement par acquisition après la FIFO.

Tests recréés : horloge UTC et réveil du vrai heartbeat injectés séparément, chaque
pas attend le commit du renouvellement avant d’avancer. Attentes de 10/40/160 s pour
une lease de 30 s ; HTTP des deux routes avec 80 s d’attente ; send bloqué 80 s ;
ZIP final valide ; erreur de renouvellement avant en-têtes ; arrêt/déconnexion en
FIFO/streaming ; UUID ORM expiré après rollback pour ZIP/fichier ; erreur secondaire
de cleanup ; annulation répétée ; réponse annulée avant démarrage ; lecture disque
en cours ; annulation juste après commit ; huit leases et neuvième refusée, plusieurs
comptes ; admissions concurrentes réelles PostgreSQL exécutées en CI.

Validation locale des sept suites demandées après correction ASGI : 79 réussis /
5 ignorés (services PostgreSQL absents). Après ajout des trois cas de contexte,
test_archive_queue_lease.py : 28 réussis / 1 ignoré. Ruff/format, mypy strict
259 sources et build frontend verts. La suite backend globale locale a été interrompue
sous contention ; le cas d’autorisation concerné repasse seul. Des timeouts frontend
locaux ne sont pas présentés comme une réussite : aucun délai de test n’a été augmenté pour
les masquer. La CI complète, services PostgreSQL/Redis disponibles, fait foi.

CI finale du correctif : source 7730cdcabc064d696104b5291d43b2224401f2a2,
run 39951809442, 1381 réussis / 2 ignorés ; Ruff/format et mypy strict verts.
Frontend, bot, image, audit sécurité et garde-fou de déploiement également verts.
29 cas ont été ajoutés, dont le cas PostgreSQL d’admission simultanée de neuf demandes.
Aucune migration ou infrastructure protégée modifiée ; version 2.4.6.

Revue de promotion : préservation de la traduction Starlette ASGI 2.4, OSError
de send devient ClientDisconnect, sans l’enfermer seul dans un ExceptionGroup.
Les erreurs simultanées restent groupées et les erreurs de lecture ne sont pas
traduites en déconnexion. Quatre tests couvrent en-têtes/corps et ASGI 2.0/2.4,
avec vérification de la libération SQL/FIFO. Aucune modification des fichiers simples.

Revue complémentaire : le dégroupement conserve la cause explicite ou, à défaut,
le contexte implicite non supprimé, conformément au helper Starlette installé.
Trois cas vérifient la chaîne implicite, explicite et from None volontaire, tout en
vérifiant la libération de la lease et du slot.


### Preuves de livraison 2.4.6

Correctif #416 et ajustements de revue #418 fusionnés dans develop
5a78acb953c62217839269afb5605bc382ff5941. Promotion #417 fusionnée sur master
68d66a0a4d2429fec27a6805ff53cee7f717a99d après douze contrôles verts
(CI push 39952720342 et PR 39952725887, plus politiques de déploiement).
Les deux remarques de revue ont été corrigées et leurs fils résolus.

CI master 39953318062 verte : 1381 tests backend réussis / 2 ignorés,
Ruff/format et mypy strict, frontend/tests/build, bot, image et audit sécurité verts.
Garde-fou de déploiement 39953318104 vert.
Déploiement automatique Rise2 39953976497 réussi le 23 octobre 2026 à
12:33:10 UTC (14:33:10 Paris), job 120415521390. Révision exacte :
68d66a0a4d2429fec27a6805ff53cee7f717a99d.
Image : ghcr.io/thomasperez91/world-of-seeds-v2@sha256:7da268cc706891a87aaf5ce2b5f79754390fae8c130a47bd53dfa7d2911d11ab.

Contrôles publics indépendants après livraison, le 23 octobre à 12:33 UTC :
/api/v1/health/live et /api/v1/health/ready HTTP 200, status ok, version 2.4.6.
Page et bundle /assets/index-C9Uz78gi.js HTTP 200, version frontend 2.4.6.
Les deux routes download-archive et download-folder-archive refusent une requête
non authentifiée avec HTTP 401 / not_authenticated, sans en-têtes de succès ZIP.

Limite de preuve : aucun compte/session de production n’est disponible dans cet
environnement. Aucun téléchargement ZIP privé authentifié n’a donc été effectué
sur Rise2 ; ce contrôle utilisateur reste à confirmer. Les transferts authentifiés,
attentes au-delà du TTL et ZIP finaux valides sont vérifiés dans les tests isolés
et la CI, pas présentés comme un transfert privé réellement exécuté en production.
ZIP1 est corrigé et déployé ; S4 reste le prochain sujet. L’ordonnanceur fichiers,
les 429 sans Retry-After, le bot et l’infrastructure n’ont pas été modifiés.


## 2.4.7 — correctifs par lots issus de l’audit complet

Source : rapport `Rapport_audit_World_of_Seeds_2.4.6.pdf` (136 constats : 3 critiques,
24 élevés, 76 moyens, 33 faibles). Les corrections sont livrées par lots, chacun sur sa
propre branche depuis `develop`, fusionné puis supprimé. Aucune migration Alembic, aucun
fichier de déploiement protégé (compose, configuration qBittorrent, preflight, scripts
qB/NewGreedy) n’est modifié : la livraison reste éligible au déploiement automatique Rise2.
Les corrections qui exigent une migration ou une action d’exploitation sont listées plus bas
et dans `docs/audits/ACTIONS_OPERATEUR_2.4.7.md`.

### Lot 1 — résilience du pipeline torrent

- Contrôles qBittorrent isolés par torrent : un torrent absent ou en erreur ne fait plus
  échouer le lot de contrôles du scheduler ; les arrêts préalables aux purges et les torrents
  en erreur restés actifs côté qB sont repris dans le même cycle (SC-07).
- Une limite globale qB plus petite que le nombre de torrents sélectionnés dégrade les parts
  au lieu de lever (OP-01) ; une valeur écrite sous 1 Mio/s est refusée (erreur d’unité).
- Les synchronisations tolèrent les incidents transitoires (qB injoignable, validation
  saturée, torrent absent) jusqu’à la dernière tentative ; les attentes qui ne dépendent pas
  du torrent (qB indisponible, lease active avant purge) ne consomment plus le budget d’essais
  et ont un plafond de 10 minutes, budget total 7 jours (LS-03, partie b).
- `WOS_TORRENT_MAX_RETRY_ATTEMPTS` et `WOS_TORRENT_RETRY_BASE_SECONDS` sont enfin lues : budget
  du job ADD et base du backoff (OP-06). La relance d’un torrent déjà ajouté à qB ne relit plus
  un payload supprimé : elle repasse par l’observation.
- Les torrents READY ne sont resynchronisés qu’une fois par minute, après les téléchargements.

### Lot 2 — croissance de la base

- `torrent_jobs` (IN-02, critique) : purge bornée dans le reaper existant, par lots de 1000,
  budget de 30 s par passe, verrous `SKIP LOCKED` ; synchronisations terminées après 1 h,
  échouées après 7 jours, autres jobs après 180 jours ; les jobs ADD ne sont jamais purgés.
- Sessions expirées/révoquées (7 jours) et verrous de connexion expirés (1 jour) purgés par
  le même mécanisme (IN-12, SE-08).
- `initialize` des options ne verrouille plus toutes les lignes quand rien n’est à écrire
  (OP-10). Métriques : plus ancien job en file mesuré sur les jobs réellement prêts, retries
  = tentatives au-delà de la première (IN-13, partie alerte permanente).

### Lot 3 — secrets et périmètre C411

- Les passkeys ne sont plus jamais renvoyées par l’API d’administration : valeur vide et
  indicateur `configured` ; l’interface ne renvoie une passkey que si elle est saisie ou
  explicitement effacée (SE-01, OP-02). Le chiffrement au repos reste à faire (voir plus bas).
- Les métainfos normalisées retirent `url-list`, `httpseeds`, `nodes`, `comment`, `created by`,
  `publisher*` hors de `info` ; l’infohash est inchangé (SE-02, partie métainfo).
- `POST /api/v2/torrents` n’analyse plus le multipart avant l’authentification (comportement
  confirmé dans FastAPI 0.141.1 : le corps est lu avant les dépendances) : authentification
  d’abord, `Content-Length` obligatoire et borné par l’option, une seule partie fichier
  (SE-05, partie applicative).

### Lot 5 — transferts et leases

- Un client qui ne lit plus libère sa voie, sa lease et son slot ZIP : un chunk accepté doit
  se terminer en moins de 120 s (LS-01, LS-09).
- Le heartbeat de lease retente une erreur SQL/timeout transitoire tant que la lease est
  valide (LS-16). Une erreur inattendue à l’ouverture d’un fichier libère la lease et répond
  503 (LS-14) ; un mtime absurde n’interdit plus le téléchargement et les dates ZIP sont
  bornées (LS-20). `no-transform` sur les réponses de téléchargement (LS-23). Le moniteur de
  débit sortant journalise et survit à une erreur inattendue (LS-24, partie a).
- Vérifié sans changement : uvicorn annonce ASGI 2.3 et son `send` retourne silencieusement
  après déconnexion ; la classification des déconnexions (LS-15) ne se pose donc pas dans la
  configuration livrée.

### Lot 6 — options et limites

- Les options sans consommateur (limite de requêtes, caches, liste de fichiers, rafraîchissement
  admin, budgets de dossiers hérités) sont masquées de l’écran d’administration et un test
  échoue si une option visible n’est lue nulle part (OP-03, OP-07, OP-24).
- Un PATCH correctif est accepté quand l’état stocké est déjà incohérent ; les routes torrent
  répondent 503 et non 500 quand les options stockées sont invalides (OP-11).
- Garde-fous à l’écriture seulement, jamais sur l’état stocké : débits HTTP de 64 Kio/s minimum,
  flux ZIP simultanés ≤ admissions ZIP, réserve disque ≤ marge laissée par le seuil d’alerte
  (OP-12, OP-13). Traductions manquantes, unités `%` et crédits, textes périmés (OP-20).

### Lot 7 — robustesse frontend

- WebSocket des torrents : backoff exponentiel plafonné à 30 s avec jitter (FE-01) ; recherche
  avec anti-rebond de 300 ms (FE-02) ; dépôts : reprise sur 429/503 avec `Retry-After`, arrêt
  du lot sur refus de compte avec un seul message (FE-03).
- 401 pendant un transfert local = session expirée (FE-13, LS-06 partiel) ; avertissement avant
  fermeture de l’onglet pendant un transfert ou un dépôt (FE-16, LS-05) ; progression des
  octets échantillonnée à 4/s (FE-12, LS-07).
- `ErrorBoundary` racine, stockage local et dates tolérants, `formatBytes` robuste, minuteur de
  rétention borné, purge partielle signalée (FE-09, FE-20).
- Fichiers statiques : `index.html` revalidé, bundles hachés `immutable` (FE-08).

### Lot 8 — documentation et petits correctifs

- Chemin de fichier torrent limité à 2048 octets (index unique PostgreSQL, IN-11) ; `env.py`
  d’Alembic accepte un mot de passe contenant `%` (IN-09).
- Documentation alignée sur le comportement réel (secrets, plafonds de flux, options).

### Lots 9 à 13 — compléments

- Lot 9 : le reaper de rétention n’exécute sa passe de maintenance que si la rétention a
  réussi et qu’aucun arrêt n’est demandé.
- Lot 10 (scheduler) : bail renouvelé avant l’application qB et rendu à l’arrêt, génération
  appliquée même si l’application dépasse le TTL (SC-15) ; backoff 5 → 60 s des cycles en échec
  (SC-09, partie cadence) ; attentes Redis sur une connexion dédiée sans timeout de socket de
  1 s (SC-13) ; relance manuelle sans cooldown ni slot forcé hérités (SC-23) ; la réconciliation
  ne signale plus un ajout en cours (SC-21).
- Lot 11 (sécurité) : socket d’événements refusée depuis une origine autre que l’hôte de la page,
  5 sockets par utilisateur et plafond global, session revérifiée à chaque heartbeat (SE-07) ;
  tracker `http://` réécrit en `https://` quand la passkey est injectée (SE-11) ; journal
  `admin_action` pour purge, actions qB, récupération, comptes et clés API (SE-09, partie
  journal ; pas de table d’audit).
- Lot 12 (frontend) : confirmation avant révocation d’une clé API et erreurs d’action qui ne
  masquent plus la liste (FE-18) ; compteurs du Dashboard issus d’une seule requête (FE-19) ;
  listings d’un ancien snapshot oubliés (FE-20, point 4) ; noms locaux compatibles avec les
  systèmes de fichiers (FE-15).
- Lot 13 : PATCH d’options versionné et rétrocompatible, 409 `option_version_conflict` et
  rechargement côté interface (OP-08) ; un ZIP dont le droit est perdu pendant la file répond
  404 avant tout en-tête (LS-11) ; tests HTTP du 429 de plafond ZIP (LS-25) ; texte du toast de
  sauvegarde qui n’annonce plus une application immédiate (OP-09, partie message).

### Non traité dans cette livraison (décision, migration ou exploitation requises)

Politique à trancher : plafond de voies rapides par compte et exemption administrateur sur
les fichiers (LS-02, OP-04 ; les tests actuels encadrent plusieurs voies par compte), quota de
150 Go par défaut, lot de 50 fichiers côté serveur, exemption administrateur des quotas
(OP-05), authentification renforcée des administrateurs (SE-13).
Migration nécessaire : index de `torrent_jobs` et `download_leases` (IN-05), colonnes
`timestamp` vers `timestamptz` (IN-08), suppression réelle des options mortes (OP-07),
chiffrement des passkeys au repos (SE-01/IN-21), contrainte `CHECK` des valeurs d’options.
Exploitation (voir le runbook) : sauvegardes hors hôte (IN-01), Alertmanager (IN-03), limites
mémoire (IN-04), healthchecks (IN-06), rôles PostgreSQL (IN-07), limites Caddy et DHT/PeX/LSD
côté qBittorrent (SE-02, OP-18), liste autorisée du helper de déploiement (SE-12, IN-16).
Développement ultérieur : rotation d’ensemble actif du scheduler (SC-05, l’algorithme
d’équité demande une conception dédiée), limiteur d’API authentifiée (SE-06), verrous de
lecture manifeste/archives (LS-17, LS-18), consolidation CSS (FE-05), caractères
bidirectionnels dans les noms (SE-21), table d’audit de sécurité (SE-09), authentification
renforcée (SE-13), réveil du scheduler sur modification d’option (OP-09).
