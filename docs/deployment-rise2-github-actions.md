# Déploiement GitHub Actions de la V2 vers Rise2

## Objectif

Ce canal remplace définitivement l'ancien déploiement V1/OVH. La V1 reste figée par son tag et sa
release Git, mais aucune clé GitHub destinée à Rise2 ne doit permettre un shell général sur le
serveur. Le déploiement V2 passe par une clé SSH dédiée et une commande forcée installée par root.

Le premier passage manuel a été validé le 8 septembre 2026 par le run GitHub Actions
`34204307209` sur le SHA `2164c4411d84fd4381f7e58370189eef86ecc263`. Le canal automatique peut
donc s'exécuter après un CI `master` vert. `workflow_dispatch` reste disponible pour les opérations
manuelles contrôlées.

## Contrat de sécurité

- le déclenchement automatique dépend de la fin réussie du workflow `CI` sur un événement `push`
  de la branche `master` ;
- le runner verrouille la révision sur le `head_sha` du CI qui vient de réussir ;
- si cette révision n'est plus le HEAD courant de `master`, le run est considéré obsolète et s'arrête
  avant le build de production ;
- le runner construit l'image depuis le SHA exact de `master` et publie uniquement
  `ghcr.io/thomasperez91/world-of-seeds-v2:sha-<sha>` ;
- le déploiement utilise ensuite le digest immuable retourné par GHCR, jamais un tag mutable ;
- la commande Rise2 vérifie à nouveau que le SHA demandé est encore le HEAD de `origin/master` ;
- seuls les historiques fast-forward sont acceptés ;
- PostgreSQL, Redis, qBittorrent et NewGreedy ne sont jamais recréés par le déploiement applicatif ;
- une modification des fichiers qB/NewGreedy ou de la topologie Compose Rise2 bloque le canal
  automatique et exige une procédure opérateur dédiée ;
- migrations, API, workers, scheduler et ingress sont validés avant le verdict de succès ;
- la clé GitHub ne dispose pas d'un shell interactif et n'appelle qu'une commande root allowlistée.

## GitHub Environment `rise2-production`

L'environment GitHub `rise2-production` contient :

Variables :

- `RISE2_SSH_HOST=141.94.132.229`
- `RISE2_SSH_PORT=22`
- `RISE2_SSH_USER=wosdeploy`

Secrets :

- `RISE2_SSH_PRIVATE_KEY` : clé privée Ed25519 dédiée à GitHub Actions ;
- `RISE2_SSH_KNOWN_HOSTS` : ligne `known_hosts` Rise2 vérifiée hors bande.

Ne jamais réutiliser une clé SSH personnelle ou la clé de l'utilisateur `sysadmin`.

## Préparation Rise2

Les deux fichiers versionnés suivants sont installés par root et non exécutés directement à partir
d'un checkout modifiable :

```text
deploy/world-of-seeds-v2-deploy-command
  -> /usr/local/sbin/world-of-seeds-v2-deploy-command

deploy/deploy-world-of-seeds-v2-rise2
  -> /usr/local/sbin/deploy-world-of-seeds-v2-rise2
```

Le compte `wosdeploy` a un mot de passe verrouillé. Son `authorized_keys` est root-owned et force
`/usr/local/sbin/world-of-seeds-v2-deploy-command`, sans PTY, forwarding, agent, X11 ou user rc.
Le seul droit sudo NOPASSWD autorisé est `/usr/local/sbin/deploy-world-of-seeds-v2-rise2`.

La commande forcée accepte exactement :

```text
deploy-world-of-seeds-v2 <40-hex-sha> <sha256:digest> <github-user>
```

Le token GHCR arrive uniquement sur stdin et n'est jamais passé dans les arguments SSH.

## Validation du premier passage

Le run `34204307209` a validé :

1. le build et la publication de l'image immuable du SHA exact de `master` ;
2. la vérification des labels OCI révision/version et de l'architecture `linux/amd64` ;
3. la connexion Rise2 via l'identité SSH restreinte ;
4. le déploiement du digest exact ;
5. les contrôles runtime et les garde-fous du script Rise2.

Le résultat GitHub du build et du déploiement était `success`.

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
