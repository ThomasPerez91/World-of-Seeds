# Roadmap World of Seeds V2

## Etat de la roadmap

La roadmap de construction initiale V2 est **terminée**.

World of Seeds V2 `2.0.0` est en production sur Rise2. Les étapes V2-00 à V2-35 ont conduit du socle CI/Compose jusqu'au pilote, à la release candidate, à la release stable et au cutover production.

L'unique dette nommée de cette séquence qui reste volontairement ouverte est **V2-32D**, bloquée par l'API NewGreedy v1.7.5 et non bloquante pour la production actuelle.

Cette roadmap reste le résumé historique de la construction 2.0. Les nouveaux développements ne doivent pas continuer artificiellement la numérotation V2-36, V2-37, etc. sauf décision explicite ; ils partent comme tâches post-2.0 orientées fonctionnalité, maintenance, sécurité ou exploitation.

## Règles d'exécution post-2.0

Le flux historique basé sur `develop_V2` est clos.

Pour chaque nouveau changement :

1. partir du dernier `develop` ;
2. créer une branche dédiée (`feat/*`, `fix/*`, `ops/*`, `docs/*`, etc.) ;
3. limiter la PR au scope annoncé ;
4. lancer les tests ciblés pendant le développement ;
5. ouvrir une PR vers `develop` ;
6. attendre les checks requis et résoudre les conversations ;
7. merger dans `develop` ;
8. promouvoir vers la production avec une PR `develop -> master` ;
9. après merge `master`, le CI doit être vert avant tout déploiement automatique Rise2.

`master` et `develop` sont protégées. Aucun push direct, force-push ou suppression de ces branches.

Checks requis :

- `backend` ;
- `frontend` ;
- `Container image` ;
- `Dependency and image security` ;
- `Validate restricted Rise2 deploy path`.

## Historique de construction 2.0

| Phase | Statut | Résultat |
| --- | --- | --- |
| V2-00 à V2-06 | TERMINE | Architecture, CI/versioning, Compose local, schéma partagé, jobs durables, Redis tolérant aux pannes et options PostgreSQL. |
| V2-07 à V2-13C | TERMINE | Déduplication, workers, gateway qB, C411/NewGreedy, activité tracker, scheduler équitable, contrôles qB et routage multi-comptes. |
| V2-14 à V2-18A | TERMINE | Stockage partagé, quotas, manifestes, API torrent, UI téléchargements et smoke local reproductible. |
| V2-19 à V2-28 | TERMINE | HTTP Range, transferts récursifs, fallback ZIP, lifecycle, UX responsive, admin, réconciliation, métriques et observabilité. |
| V2-28A à V2-28H | TERMINE | Autorité scheduler, anti-stall, backlog partagé, WebSocket, transferts scalables, optimisation SQL/métriques, hardening runtime et portabilité monitoring. |
| V2-29 | TERMINE | Compose Rise2 complet et isolé. |
| V2-30 | TERMINE | Sauvegarde/restauration et procédures de restore. |
| V2-31 | TERMINE | Outillage d'import V1 optionnel et réconciliation. |
| V2-32 | TERMINE | Sécurité, charge, scans, pannes/latences, WebSockets et charge 100 comptes. |
| V2-32A | TERMINE | Internationalisation FR/EN. |
| V2-32B | TERMINE | UX/UI/mobile, toasts, confirmations et multi-upload borné. |
| V2-32C | TERMINE | Rétention READY automatique et purge durable. |
| V2-32D | BLOQUE / NON BLOQUANT | NewGreedy v1.7.5 ne permet pas une suppression exacte et durable par SHA-1 complet. |
| V2-32E | TERMINE | Avertissements visuels avant expiration READY. |
| V2-32F | TERMINE | Visibilité des files/rangs et récupération locale. |
| V2-33 | TERMINE | Pilote réel Rise2, critères GO/NO-GO et rollback. |
| V2-34 | TERMINE | Release candidate `2.0.0-rc.1`, validation Rise2 et compatibilité rollback. |
| V2-35 | TERMINE | Stable `2.0.0`, promotion du digest testé, Git tag/release, cutover Rise2. |

## Refonte UX post-2.0

### Direction produit validée

L'interface utilisateur doit devenir **torrent-centric**.

Le parcours principal visé est :

```text
Connexion
  -> Dashboard
     -> ajout d'un .torrent
     -> suivi de la file et du téléchargement
     -> contenu READY
     -> récupération sur le poste local
     -> suppression / désabonnement
```

Le navigateur de fichiers utilisateur, la corbeille utilisateur et les actions de création/gestion libre de dossiers ne doivent plus constituer l'expérience principale. Leur retrait technique doit toutefois être précédé d'un audit de dépendances : l'UI peut être retirée avant certaines briques backend si celles-ci restent nécessaires aux opérations admin, à la création/suppression de comptes ou à des invariants filesystem.

La refonte doit conserver les invariants V2 : PostgreSQL reste autoritaire, Redis reste non autoritaire, le frontend ne pilote jamais qBittorrent ou NewGreedy directement, les droits utilisateur restent portés par `TorrentRequest`, le stockage physique partagé reste dédupliqué, les leases/règles de rétention restent applicables et aucun chemin hôte n'est exposé au client.

### Règles de scope UX

Pour les premiers écrans de la refonte, réutiliser les contrats et données déjà disponibles avant d'ajouter de la télémétrie backend.

En particulier, UX-02 à UX-04 ne doivent pas ajouter de backend uniquement pour exposer des informations décoratives ou de confort telles que seeders, peers, ETA qBittorrent, ratio, débit qBittorrent ou télémétrie globale de récupération. Si une information n'est pas déjà disponible, elle est omise et peut devenir une amélioration ultérieure.

La file de récupération présentée pendant cette première refonte est celle gérée localement par le contrôleur navigateur existant. Elle peut afficher le nombre de récupérations actives, la concurrence maximale locale et la position des éléments en attente disponibles dans ce contrôleur. Elle ne doit pas être présentée comme une file globale autoritaire multi-appareils tant qu'aucun contrat backend dédié n'existe.

### Plan de PR

| Tâche | Risque | Dépendances | Statut | Scope |
| --- | --- | --- | --- | --- |
| UX-00 | RAPIDE | aucune | TERMINE | Formaliser la direction produit, le découpage des PR et les contraintes de scope dans `roadmap-v2.md`, `PROGRESS.md` et `CONTEXT.md`. |
| UX-01 | MOYEN | UX-00 | TERMINE | Design system léger, nouvelles palettes claire/sombre, composants UI modernes, préférence `light/dark/system` persistée par utilisateur, cartouche Préférences langue/thème, nouveau shell et login cohérent. |
| UX-02 | MOYEN | UX-01 | A FAIRE | Nouveau Dashboard utilisateur comme accueil : cartouches torrents, récupération locale et stockage en composant les API/données existantes, sans nouveau backend de télémétrie. |
| UX-03 | MOYEN | UX-02 | A FAIRE | Remplacer la table actuelle par un gestionnaire de torrents en accordéons ; conserver drag/drop, multi-upload borné, états, progression, queue, WebSocket, annulation/désabonnement et rétention en utilisant le contrat existant. |
| UX-04 | MOYEN | UX-03 | A FAIRE | Recomposer l'expérience READY : fichier unique, manifeste dossier, téléchargement fichier par fichier ou complet, fallback existant, progression/file locale et affichage actif/max sans nouvelle télémétrie backend. |
| UX-05 | ELEVE | UX-04 | A FAIRE | Retirer l'ancien espace utilisateur Fichiers/Corbeille/création de dossiers et nettoyer le code mort après audit complet des dépendances backend, admin, workspaces, trash et routes `/api/v1/files`. |
| UX-06 | MOYEN | UX-05 | A FAIRE | Harmoniser l'administration avec le design system, finaliser responsive/accessibilité, supprimer CSS/i18n/tests morts et effectuer le nettoyage final de la refonte. |

### UX-01 — Design system, thèmes et préférences

Objectifs :

- remplacer la palette actuelle trop sombre par deux palettes plus lisibles et plus douces ;
- ajouter `light`, `dark` et `system` ;
- persister la préférence de thème avec le compte utilisateur, comme préférence durable ;
- conserver la préférence de langue FR/EN existante ;
- fournir les deux réglages dans un cartouche Préférences et le thème dans le menu du compte ;
- introduire des primitives réutilisables : boutons, icon-buttons, cartes, badges, accordéons, progressions et états loading/empty/error ;
- réserver les couleurs sémantiques fortes aux vrais avertissements/destructions au lieu de colorer les actions ordinaires ;
- adapter login, changement initial de credentials, shell et paramètres au même langage visuel.

Une migration additive simple est acceptable pour la préférence de thème. Elle doit être testée avec son downgrade si les pratiques Alembic du projet l'exigent.

### UX-02 — Dashboard utilisateur

Le Dashboard devient la page d'accueil authentifiée.

Cartouches cibles :

1. **Torrents** : actifs, terminés/READY et en attente/file à partir des données torrent déjà exposées ; si nécessaire, agréger côté frontend les pages de l'API existante plutôt que créer un endpoint uniquement pour ces compteurs.
2. **Récupérations** : état du contrôleur local de récupération déjà existant, avec nombre actif / maximum local et position disponible des éléments en attente. Ne pas prétendre fournir une vue globale inter-utilisateurs ou multi-appareils.
3. **Stockage** : espace disponible/total à partir d'une donnée déjà exposée par les contrats actuels. Ne pas créer un nouveau backend pour ce cartouche dans cette phase.

Le Dashboard doit être compact, responsive et éviter les grands titres/espacements qui dominent actuellement l'écran.

### UX-03 — Gestionnaire de torrents en accordéons

Réutiliser la mécanique existante de `UserDownloadsPage` plutôt que la réécrire.

Accordéon fermé :

- nom ;
- taille ;
- état ;
- progression lorsque disponible ;
- position/statut de queue déjà exposé ;
- actions pertinentes, dont annulation avant READY et téléchargement/suppression lorsque READY.

Accordéon ouvert :

- uniquement les informations déjà disponibles dans le contrat frontend courant ;
- état détaillé, progression, queue, dates déjà exposées, rétention READY et contenu du manifeste lorsque pertinent ;
- pas de développement backend pour seeders, peers, ETA, vitesse qB, ratio ou autres diagnostics non exposés.

L'annulation doit conserver la sémantique V2 actuelle : si d'autres demandes actives existent pour le `ManagedTorrent`, l'utilisateur est désabonné ; si la dernière demande active disparaît, le lifecycle de purge existant s'applique. Ne pas contourner V2-32D pour prétendre supprimer précisément l'état NewGreedy.

### UX-04 — READY et récupération locale

Conserver et recomposer les capacités existantes :

- fichier unique téléchargeable directement ;
- torrent multi-fichiers consultable depuis son accordéon ;
- téléchargement de chaque fichier ;
- téléchargement complet via le mécanisme existant adapté au navigateur ;
- manifeste paginé ;
- fallback ZIP/compatibilité lorsque nécessaire ;
- pause/reprise/annulation et progression locale déjà supportées ;
- concurrence bornée et file locale existante.

Aucune télémétrie backend supplémentaire n'est requise dans cette PR pour mesurer un débit, une file globale ou des téléchargements lancés nativement dans d'autres onglets/appareils.

### UX-05 — Retrait du legacy utilisateur fichiers/corbeille

Avant suppression, auditer au minimum :

- `frontend/src/features/files/*` ;
- les routes `/api/v1/files` ;
- `WorkspaceManager` et la structure de workspace ;
- `.trash`, `TrashEntry` et l'admin trash ;
- la création, le renommage et la suppression des utilisateurs ;
- les scripts/imports/tests qui dépendent encore des anciens chemins.

Ne pas supprimer une brique backend uniquement parce que son écran utilisateur disparaît. Retirer ce qui est réellement mort, conserver ou isoler ce qui reste requis par l'administration ou les invariants de sécurité.

### UX-06 — Harmonisation et finition

- appliquer le design system à l'administration ;
- vérifier mobile/tablette/desktop ;
- conserver la navigation clavier, les labels accessibles et les tests axe ;
- nettoyer CSS, traductions, composants et tests devenus morts ;
- faire un audit final des liens/actions user-facing afin qu'aucune entrée Fichiers/Corbeille supprimée ne subsiste.

## Jalons de validation désormais acquis

### Runtime

- API V2, workers et scheduler en production Rise2 ;
- PostgreSQL/Redis persistants ;
- qBittorrent/NewGreedy intégrés sans ports hôte publics ;
- stockage média partagé sous `/srv/world-of-seeds-v2/data` ;
- monitoring et alerting actifs.

### Sécurité / qualité

- CI backend/frontend/container/security ;
- scans de dépendances/image ;
- migrations et rollback validés ;
- secret handling borné ;
- protections de branches GitHub actives.

### Performance / robustesse

- charge CI 100 comptes ;
- WebSockets multi-tabs/reconnect ;
- jobs durables et reprise après crash ;
- scheduler borné/anti-starvation ;
- sauvegarde off-host et restore drill ;
- rollback applicatif réel Rise2 validé.

### Release / production

- stable `2.0.0` publiée ;
- DNS production sur Rise2 ;
- HTTPS production ;
- déploiement manuel GitHub Actions validé ;
- déploiement automatique après CI `master` vert validé.

## Dette connue

### V2-32D — NewGreedy purge cleanup

Ne pas contourner le blocage actuel par :

- suppression sur préfixe 8 caractères ;
- reset global ;
- édition manuelle de `stats.json` comme pseudo-source autoritaire ;
- mutation qui peut être réécrasée par l'état en mémoire NewGreedy.

Critère de réouverture : NewGreedy expose une suppression exacte par full SHA-1, idempotente, cohérente entre mémoire et persistance, ou la dépendance est remplacée par un composant offrant ce contrat.

## Backlog post-2.0

La priorité produit post-2.0 est désormais la refonte UX `UX-01` à `UX-06` définie ci-dessus.

Les autres sujets doivent être créés explicitement à partir d'un besoin produit ou opérateur, puis classés par risque :

- **RAPIDE** : changement local, pas de migration/topologie ;
- **MOYEN** : plusieurs couches ou migration additive simple ;
- **ELEVE** : concurrence, stockage, sécurité, migration importante ou infrastructure production.

Pour une modification d'infrastructure sensible Rise2 (Compose, qBittorrent, NewGreedy, volumes, réseaux, ingress), ne pas compter sur le canal de déploiement applicatif automatique : préparer une procédure opérateur dédiée et un rollback explicite.

## Definition of Done post-2.0

Une tâche est terminée lorsque :

- le scope annoncé est respecté ;
- les tests ciblés passent ;
- les migrations/rollback sont couverts si concernés ;
- aucune donnée sensible n'est exposée ;
- les checks requis de PR sont verts ;
- les conversations sont résolues ;
- `PROGRESS.md` est mis à jour si l'état opérationnel change ;
- `CONTEXT.md` est mis à jour seulement pour une décision durable ;
- la promotion `develop -> master` est séparée de la PR de feature si la mise en production doit être contrôlée.
