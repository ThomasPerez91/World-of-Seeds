# Déploiement GitHub Actions de la V2 vers Rise2

## Etat actuel

Ce canal est le mécanisme de déploiement production officiel de World of Seeds V2 vers Rise2.

La V1 reste figée par son tag/release et l'ancien serveur peut être conservé comme rollback temporaire, mais l'ancien déploiement V1/OVH est désactivé.

Le workflow `Deploy V2 to Rise2` supporte :

- `workflow_dispatch` pour un passage manuel contrôlé ;
- `workflow_run` après le workflow `CI` réussi sur un `push` de `master`.

## Validations réelles

Premier passage manuel :

- run `34204307209` ;
- SHA `2164c4411d84fd4381f7e58370189eef86ecc263` ;
- build immuable : SUCCESS ;
- déploiement Rise2 : SUCCESS.

Premier passage automatique :

- CI `master` run `34205528939` / CI #443 ;
- SHA `7010fa9f2a86cd74961bfc60eaaab5c2f7aa5f14` ;
- workflow de déploiement run `34205834353` ;
- event `workflow_run` ;
- build immuable : SUCCESS ;
- déploiement exact digest sur Rise2 : SUCCESS.

Le canal automatique est donc validé de bout en bout.

## Contrat de sécurité

- le déclenchement automatique dépend de la fin réussie du workflow `CI` sur un événement `push` de `master` ;
- le runner verrouille la révision sur `github.event.workflow_run.head_sha` ;
- si cette révision n'est plus le HEAD de `master`, le run s'arrête avant la publication/deploiement ;
- le runner construit l'image depuis le SHA exact de `master` pour `linux/amd64` ;
- l'image publiée est identifiée par SHA puis déployée par digest immuable ;
- le helper Rise2 vérifie à nouveau que le SHA demandé est le HEAD actuel de `origin/master` ;
- seuls les historiques fast-forward sont acceptés ;
- les labels OCI revision/version doivent correspondre ;
- PostgreSQL, Redis, qBittorrent et NewGreedy ne sont jamais recréés par un déploiement applicatif standard ;
- les changements sensibles de topologie/qB/NewGreedy sont refusés par le canal standard ;
- migrations, API, workers, scheduler, persistance et HTTPS sont vérifiés avant succès ;
- la clé GitHub ne donne aucun shell interactif et n'appelle qu'une commande root allowlistée.

## GitHub Environment `rise2-production`

Variables :

- `RISE2_SSH_HOST` ;
- `RISE2_SSH_PORT` ;
- `RISE2_SSH_USER`.

Secrets :

- `RISE2_SSH_PRIVATE_KEY` : clé privée Ed25519 dédiée GitHub Actions ;
- `RISE2_SSH_KNOWN_HOSTS` : host key Rise2 vérifiée hors bande.

Ne jamais réutiliser une clé SSH personnelle ou la clé de l'utilisateur `sysadmin`.

## Identité SSH restreinte

Les helpers versionnés sont installés par root :

```text
deploy/world-of-seeds-v2-deploy-command
  -> /usr/local/sbin/world-of-seeds-v2-deploy-command

deploy/deploy-world-of-seeds-v2-rise2
  -> /usr/local/sbin/deploy-world-of-seeds-v2-rise2
```

Le compte `wosdeploy` :

- mot de passe verrouillé ;
- `authorized_keys` root-owned ;
- commande forcée ;
- pas de PTY ;
- pas de forwarding/agent/X11/user rc ;
- sudo NOPASSWD limité au helper de déploiement.

Commande autorisée :

```text
deploy-world-of-seeds-v2 <40-hex-sha> <sha256:digest> <github-user>
```

Le token GHCR arrive uniquement sur stdin et n'est jamais passé dans les arguments SSH.

## Séquence de déploiement

1. verrouiller le SHA cible ;
2. checkout exact ;
3. confirmer qu'il est toujours le HEAD `master` ;
4. valider version et helpers ;
5. construire/publier l'image immuable ;
6. vérifier digest et labels OCI ;
7. connecter Rise2 avec la clé restreinte ;
8. sur Rise2, revalider HEAD/fast-forward et chemins sensibles ;
9. sauvegarder temporairement l'environment ;
10. exécuter le preflight ;
11. capturer les IDs qB/NewGreedy/PostgreSQL/Redis ;
12. arrêter API/workers/scheduler ;
13. exécuter les migrations ;
14. recréer API/workers/scheduler puis ingress ;
15. vérifier health/readiness, replicas, services persistants et HTTPS ;
16. vérifier que les quatre services persistants n'ont pas été recréés ;
17. écrire `/var/lib/world-of-seeds-v2/deploy/current.env`.

En cas d'échec après activation du rollback, le helper restaure l'image/environnement précédents et, si nécessaire, le head Alembic précédent avec la migration cible avant de recréer seulement la couche applicative.

## Changements d'infrastructure sensibles

Le déploiement standard refuse notamment les changements sur :

- `deploy/compose.rise2.v2.yaml` ;
- `deploy/qbittorrent.rise2.conf` ;
- `deploy/world-of-seeds-v2-rise2.service` ;
- scripts qB bootstrap/probe/reconcile ;
- scripts de policy/smoke NewGreedy.

Pour ces changements :

- PR dédiée ;
- tests ciblés + CI ;
- plan d'application opérateur ;
- snapshot/backup si nécessaire ;
- rollback explicite ;
- ne jamais contourner le garde-fou du helper automatique.

## Protections de branches

`master` et `develop` sont protégées.

- PR obligatoire ;
- zéro approbation humaine requise dans le dépôt mono-mainteneur ;
- branche à jour avant merge ;
- conversations résolues ;
- force-push et suppression interdits ;
- règles appliquées aussi à l'administrateur.

Checks requis :

- `backend` ;
- `frontend` ;
- `Container image` ;
- `Dependency and image security` ;
- `Validate restricted Rise2 deploy path`.

## Flux normal de release

```text
feature/fix/ops/docs
        |
        v
     develop
        |
        | PR de promotion
        v
      master
        |
        v
      CI vert
        |
        v
Build image immuable
        |
        v
Digest GHCR exact
        |
        v
SSH restreint wosdeploy
        |
        v
      Rise2
```

Un CI rouge, un SHA obsolète, un historique non fast-forward ou un changement d'infrastructure sensible doit empêcher le déploiement.
