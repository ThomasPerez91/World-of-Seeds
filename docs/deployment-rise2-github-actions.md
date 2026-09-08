# Déploiement GitHub Actions de la V2 vers Rise2

## Objectif

Ce canal remplace définitivement l'ancien déploiement V1/OVH. La V1 reste figée par son tag et sa
release Git, mais aucune clé GitHub destinée à Rise2 ne doit permettre un shell général sur le
serveur. Le déploiement V2 passe par une clé SSH dédiée et une commande forcée installée par root.

La première étape est volontairement **manuelle uniquement** (`workflow_dispatch`). Une seconde PR
n'activera le déploiement automatique après CI vert sur `master` qu'après un premier passage manuel
réussi sur Rise2.

## Contrat de sécurité

- le runner construit l'image depuis le SHA exact de `master` et publie uniquement
  `ghcr.io/thomasperez91/world-of-seeds-v2:sha-<sha>` ;
- le déploiement utilise ensuite le digest immuable retourné par GHCR, jamais un tag mutable ;
- la commande Rise2 vérifie que le SHA demandé est encore le HEAD de `origin/master` ;
- seuls les historiques fast-forward sont acceptés ;
- PostgreSQL, Redis, qBittorrent et NewGreedy ne sont jamais recréés par le déploiement applicatif ;
- une modification des fichiers qB/NewGreedy ou de la topologie Compose Rise2 bloque le canal
  automatique et exige une procédure opérateur dédiée ;
- migrations, API, workers, scheduler et ingress sont validés avant le verdict de succès ;
- la clé GitHub ne dispose pas d'un shell interactif et n'appelle qu'une commande root allowlistée.

## GitHub Environment `rise2-production`

Créer l'environment GitHub `rise2-production` avec :

Variables :

- `RISE2_SSH_HOST=141.94.132.229`
- `RISE2_SSH_PORT=22`
- `RISE2_SSH_USER=wosdeploy`

Secrets :

- `RISE2_SSH_PRIVATE_KEY` : clé privée Ed25519 dédiée à GitHub Actions ;
- `RISE2_SSH_KNOWN_HOSTS` : ligne `known_hosts` Rise2 vérifiée hors bande.

Ne jamais réutiliser une clé SSH personnelle ou la clé de l'utilisateur `sysadmin`.

## Préparation Rise2

Les deux fichiers versionnés suivants doivent être installés par root et non exécutés directement
à partir d'un checkout modifiable :

```text
deploy/world-of-seeds-v2-deploy-command
  -> /usr/local/sbin/world-of-seeds-v2-deploy-command

deploy/deploy-world-of-seeds-v2-rise2
  -> /usr/local/sbin/deploy-world-of-seeds-v2-rise2
```

Le compte `wosdeploy` doit avoir un mot de passe verrouillé. Son `authorized_keys` est root-owned et
force `/usr/local/sbin/world-of-seeds-v2-deploy-command`, sans PTY, forwarding, agent, X11 ou user rc.
Le seul droit sudo NOPASSWD autorisé est `/usr/local/sbin/deploy-world-of-seeds-v2-rise2`.

La commande forcée accepte exactement :

```text
deploy-world-of-seeds-v2 <40-hex-sha> <sha256:digest> <github-user>
```

Le token GHCR arrive uniquement sur stdin et n'est jamais passé dans les arguments SSH.

## Premier passage

1. garder le workflow en mode `workflow_dispatch` seulement ;
2. installer la clé/commande restreinte sur Rise2 ;
3. créer les variables et secrets de `rise2-production` ;
4. lancer manuellement `Deploy V2 to Rise2` depuis `master` ;
5. vérifier le digest en cours, la santé API, 2 workers, scheduler, HTTPS et l'identité inchangée de
   PostgreSQL/Redis/qBittorrent/NewGreedy ;
6. seulement après ce PASS, activer le déclenchement automatique après CI vert sur `master`.

## Changements d'infrastructure sensibles

Le déploiement standard refuse actuellement les changements touchant :

- `deploy/compose.rise2.v2.yaml` ;
- `deploy/qbittorrent.rise2.conf` ;
- `deploy/world-of-seeds-v2-rise2.service` ;
- les scripts qB bootstrap/probe/reconcile ;
- les scripts de politique/smoke NewGreedy.

Ces changements doivent rester des opérations explicites, car ils peuvent nécessiter un recreate
qB/NewGreedy ou une modification de mounts/volumes, ce que le déploiement applicatif n'a pas le
droit de faire.
