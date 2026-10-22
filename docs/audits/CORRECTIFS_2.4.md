# World of Seeds — Correctifs de l’audit 2.4.x

- Audit : version 2.3.28, 22 octobre 2026, commit `2e507fc6a1f36987d6e852dd2469f5764b2b0500`.
- Début : 22 octobre 2026. Base réelle : production 2.3.29, commit `db1fa6a545b513a433beee4841405905162dc701` ; ajouts Discord conservés.
- Objectif : traiter les constats confirmés, infirmer les faux positifs avec preuves et expliciter les dépendances opérateur.
- Versions : 2.4.1 puis incrément de patch par livraison réellement déployée ; pas de version livrée sur la seule base d’une CI verte.
- Cycle : tests isolés → PR develop → CI → merge → PR master → CI → merge/déploiement → contrôles réels → mise à jour du journal → livraison suivante.
- Tests : comportementaux, PostgreSQL/Redis/Docker en CI selon disponibilité ; aucun test destructif sur production. Limites métier users, protections techniques pour tous, exemptions admin vérifiées côté serveur.
- Audit lu intégralement (15 pages) : les constats [V], [S] et les preuves runtime manquantes restent distingués.
- État vérifié : 2.4.1 déployée sur Rise2 ; préparation de 2.4.2 après contrôles réels réussis.

## Matrice de suivi

| Référence | Problème | Priorité | Statut | Version | PR | Tests |
|---|---|---|---|---|---|---|
| S1 | Validation torrent quadratique | P0 | À traiter (confirmation sur branche actuelle requise) | — | — | — |
| S2 | Limiteur externe non borné | P0 | À traiter (confirmation sur branche actuelle requise) | — | — | — |
| S3 | Saturation login/reset | P0 | À traiter (confirmation sur branche actuelle requise) | — | — | — |
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
| T1 | Mutations M5 : révocation seconde session | P0 | Tests validés localement, CI/déploiement à suivre | 2.4.2 | À publier | Deux appareils et reset sans cookies |
| T2 | Mutations M9/M11 : rôles et compte supprimé | P0 | Tests validés localement, CI/déploiement à suivre | 2.4.2 | À publier | 14 mutations admin × 7 états, seed supprimée active |
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


## 2.4.2 — régressions d’authentification (en préparation)

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
Tests et CI, PR et preuve de déploiement à renseigner au fil de l’exécution.

Validation locale 2.4.2 : 149 tests ciblés réussis / 1 ignoré (dépendance PostgreSQL),
Ruff et formatage 292 fichiers, mypy strict 254 sources, test de version frontend,
TypeScript et build réussis. Trois essais de mutation temporaires, restaurés avant
publication : M5 sans révocation, M9 sans contrôle is_admin, M11 sans contrôle deleted_at ;
chaque test ciblé échoue avec AssertionError quand sa protection est retirée.
La suite ciblée repasse après restauration ; aucun fichier d’authentification runtime modifié.
