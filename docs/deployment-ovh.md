# Déploiement privé sur OVH avec GitHub Actions

## Modèle de sécurité

Le workflow `Deploy to OVH` est lancé manuellement depuis la branche `master` :

1. GitHub Actions construit l'image de production ;
2. l'image est publiée dans GHCR avec sa provenance et son SBOM ;
3. GitHub transmet à OVH le digest immuable de l'image et un jeton GHCR éphémère ;
4. une clé SSH restreinte autorise uniquement la commande de déploiement ;
5. OVH tire l'image, applique les migrations, démarre les services et vérifie leur santé.

Les secrets PostgreSQL et applicatifs restent exclusivement dans
`/opt/world-of-seeds/.env` sur OVH. L'application reste publiée uniquement sur
`127.0.0.1:18081` et PostgreSQL n'a aucun port hôte.

## 1. Inventaire préalable

Depuis le Mac :

```bash
ssh ovh
```

Puis sur OVH :

```bash
uname -m
lsb_release -ds
docker --version
docker compose version
id
sudo ss -lntp | grep ':18081 ' || true
sudo ufw status verbose
stat -c '%A %a %U:%G %u:%g %n' /srv /srv/seedbox
sudo find /srv/seedbox -mindepth 1 -maxdepth 1 \
  -printf '%M %u:%g %p\n' | sort
docker ps --format 'table {{.Names}}\t{{.Image}}\t{{.Ports}}'
```

Ne pas appliquer de `chown -R` ou de `chmod -R`. `APP_UID:APP_GID` sera choisi à
partir de cet inventaire pour conserver l'accès de qBittorrent aux données existantes.
Le port SSH doit accepter les connexions des runners hébergés par GitHub, dont les
adresses IP sont variables ; aucun port HTTP supplémentaire n'est nécessaire.

## 2. Créer l'identité de déploiement

Sur le Mac, créer une clé sans phrase secrète réservée à GitHub Actions :

```bash
ssh-keygen -t ed25519 -a 100 \
  -f "$HOME/.ssh/world_of_seeds_deploy" \
  -C github-actions-world-of-seeds \
  -N ''
scp "$HOME/.ssh/world_of_seeds_deploy.pub" ovh:/tmp/world_of_seeds_deploy.pub
```

Sur OVH :

```bash
sudo adduser --disabled-password --gecos '' wos-deploy
sudo install -d -o wos-deploy -g wos-deploy -m 700 /home/wos-deploy/.ssh
sed 's#^#restrict,command="/usr/local/sbin/world-of-seeds-deploy-command" #' \
  /tmp/world_of_seeds_deploy.pub \
  | sudo tee /home/wos-deploy/.ssh/authorized_keys >/dev/null
sudo chown wos-deploy:wos-deploy /home/wos-deploy/.ssh/authorized_keys
sudo chmod 600 /home/wos-deploy/.ssh/authorized_keys
rm /tmp/world_of_seeds_deploy.pub
```

`restrict` interdit notamment le terminal interactif et les redirections SSH. La commande
forcée valide le digest puis appelle uniquement le script de déploiement autorisé par
`sudoers`.

## 3. Installer les fichiers de déploiement

Après fusion de la PR de déploiement, sur OVH :

```bash
WOS_SETUP_DIR=$(mktemp -d)
git clone --depth 1 \
  https://github.com/ThomasPerez91/World-of-Seeds.git \
  "$WOS_SETUP_DIR/repository"

sudo install -d -o root -g root -m 700 /opt/world-of-seeds
sudo install -o root -g root -m 644 \
  "$WOS_SETUP_DIR/repository/deploy/compose.production.yaml" \
  /opt/world-of-seeds/compose.production.yaml
sudo install -o root -g root -m 755 \
  "$WOS_SETUP_DIR/repository/deploy/deploy-world-of-seeds" \
  /usr/local/sbin/deploy-world-of-seeds
sudo install -o root -g root -m 755 \
  "$WOS_SETUP_DIR/repository/deploy/world-of-seeds-deploy-command" \
  /usr/local/sbin/world-of-seeds-deploy-command
sudo install -o root -g root -m 440 \
  "$WOS_SETUP_DIR/repository/deploy/world-of-seeds.sudoers" \
  /etc/sudoers.d/world-of-seeds
sudo visudo -cf /etc/sudoers.d/world-of-seeds

rm -rf -- "$WOS_SETUP_DIR"
```

## 4. Créer la configuration privée OVH

```bash
sudo install -o root -g root -m 600 /dev/null /opt/world-of-seeds/.env
sudo nano /opt/world-of-seeds/.env
```

Contenu à adapter :

```dotenv
POSTGRES_DB=world_of_seeds
POSTGRES_USER=world_of_seeds
POSTGRES_PASSWORD=REMPLACER_PAR_UN_SECRET_LONG_ET_ALEATOIRE

APP_UID=1000
APP_GID=1000
SEEDBOX_HOST_PATH=/srv/seedbox

WOS_ENVIRONMENT=production
WOS_LOG_LEVEL=info
WOS_COOKIE_SECURE=false
WOS_ALLOWED_HOSTS=["127.0.0.1","localhost"]
```

Générer le mot de passe sans le publier dans GitHub :

```bash
openssl rand -hex 32
```

Valider les propriétaires et permissions :

```bash
sudo stat -c '%A %a %U:%G %n' /opt/world-of-seeds/.env
sudo test -r /opt/world-of-seeds/.env
```

## 5. Vérifier l'empreinte SSH

Sur OVH, relever l'empreinte officielle :

```bash
sudo ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub
```

Sur le Mac, remplacer `SERVEUR` et `PORT` par le vrai nom DNS ou l'IP et le port SSH :

```bash
ssh-keyscan -p PORT -t ed25519 SERVEUR 2>/dev/null \
  > /tmp/world-of-seeds-known-hosts
ssh-keygen -lf /tmp/world-of-seeds-known-hosts
```

Les deux empreintes doivent être identiques avant de continuer. L'alias local `ovh` ne
doit pas être utilisé dans GitHub : il faut son vrai `HostName`, visible avec :

```bash
ssh -G ovh | awk '/^(hostname|port|user) / { print }'
```

## 6. Configurer l'environnement GitHub

Dans le dépôt : **Settings → Environments → New environment → `production`**.

Limiter les branches de déploiement à `master`. Une approbation manuelle peut aussi être
requise avant que le job accède aux secrets.

Créer les variables d'environnement :

| Variable | Valeur |
|---|---|
| `OVH_SSH_HOST` | vrai nom DNS ou IP du serveur |
| `OVH_SSH_PORT` | port SSH, souvent `22` |
| `OVH_SSH_USER` | `wos-deploy` |

Créer les secrets d'environnement :

| Secret | Valeur |
|---|---|
| `OVH_SSH_PRIVATE_KEY` | contenu de `~/.ssh/world_of_seeds_deploy` |
| `OVH_SSH_KNOWN_HOSTS` | contenu vérifié de `/tmp/world-of-seeds-known-hosts` |

Sur macOS, copier sans afficher les valeurs :

```bash
pbcopy < "$HOME/.ssh/world_of_seeds_deploy"
pbcopy < /tmp/world-of-seeds-known-hosts
```

## 7. Premier déploiement

Dans GitHub : **Actions → Deploy to OVH → Run workflow → master**. Le workflow refuse
de déployer une autre branche et sérialise les déploiements de production.

Après succès, créer l'administrateur de manière interactive sur OVH :

```bash
ssh ovh
sudo -i
cd /opt/world-of-seeds
export WOS_IMAGE="$(cat .deployed-image)"
docker compose --env-file .env -f compose.production.yaml \
  exec app python -m app.cli create-admin --username admin
exit
```

Le mot de passe n'est ni placé dans `.env`, ni écrit dans GitHub Actions.

## 8. Vérifications finales

Sur OVH :

```bash
curl --fail --silent --show-error \
  http://127.0.0.1:18081/api/v1/health/ready
sudo ss -lntp | grep ':18081 '
```

Depuis le Mac :

```bash
ssh -N -L 18081:127.0.0.1:18081 ovh
```

Ouvrir ensuite <http://127.0.0.1:18081>.

Pour revenir à une image précédente, relancer l'ancien run GitHub Actions correspondant.
Cette opération ne rétrograde jamais automatiquement le schéma PostgreSQL.
