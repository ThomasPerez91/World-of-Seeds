# Reprise des comptes WoS depuis Google Cloud Storage

Périmètre : comptes, hachages de mot de passe, état des comptes et liens Discord.
Les sessions, réinitialisations en cours, clés API, torrents et abonnements sont exclus.
Ce format JSON versionné complète la sauvegarde PostgreSQL complète existante.

## Préparation hors de Rise2

Dans WSL sur le PC opérateur, générer une identité avec `age-keygen -o wos-accounts.agekey`
dans un répertoire privé. Garder une copie hors ligne. Seul le destinataire public
affiché par `age-keygen -y wos-accounts.agekey` va sur Rise2.

Le bucket reste en classe Archive. L'identité Google utilisée sur Rise2 doit uniquement
avoir le rôle Storage Object Creator sur le bucket ; l'opérateur récupère les objets avec
une identité distincte. Les objets sont créés sous `wos-accounts/v1/` sans écrasement.
Une suppression avant 365 jours peut entraîner une facturation anticipée. Ne pas appliquer
de règle de suppression au bucket entier s'il accueille d'autres données.

## Fonctionnement sur Rise2

`scripts/rise2_accounts_gcs.sh` tourne en root, comme la sauvegarde PostgreSQL, via
`world-of-seeds-v2-accounts-backup.service` et son timer quotidien (04:30 UTC, après la
sauvegarde PostgreSQL de 03:30). PostgreSQL n'est joignable que sur le réseau Docker interne :
le script lance donc l'export dans le conteneur `api` déjà en service
(`docker compose exec -T api python -m app.account_backup dump`), qui utilise les identifiants
de l'application dans une transaction en lecture seule. Le JSON en clair passe uniquement par
le pipe ; `age` le chiffre sur l'hôte avant toute écriture sur disque. Aucun port PostgreSQL
n'est publié et aucun environnement Python n'est installé sur l'hôte.

Chaque exécution met à jour `wos_accounts_backup.prom` dans le textfile collector de
node-exporter. Les alertes `WOSAccountsBackupMissing`, `WOSAccountsBackupStale` (26 heures) et
`WOSAccountsBackupFailed` s'appuient sur ces métriques ; elles ne préviennent personne tant
qu'Alertmanager n'est pas en place (IN-03). Garder en plus une supervision indépendante de
Rise2 qui alerte si aucun objet récent n'est visible dans le bucket depuis 26 heures.

Le script et les deux unités font partie des fichiers protégés du helper de déploiement : toute
révision qui les modifie exige un déploiement manuel. Après la fusion de cette révision,
réinstaller le helper racine avec `scripts/rise2_v2_deploy_helper_install.sh` pour que la
nouvelle liste s'applique.

## Installation sur Rise2

En root, une fois la révision déployée dans `/opt/world-of-seeds-v2` :

1. Installer `age` (déjà présent pour la sauvegarde PostgreSQL) et le CLI `gcloud`.
2. Créer une configuration `gcloud` dédiée et y activer le compte de service qui n'a que le
   rôle Storage Object Creator sur le bucket, puis supprimer la clé téléchargée :

   ```bash
   install -d -m 0700 /etc/world-of-seeds-v2/gcloud-accounts-backup
   CLOUDSDK_CONFIG=/etc/world-of-seeds-v2/gcloud-accounts-backup \
     gcloud auth activate-service-account --key-file=/root/cle-compte-de-service.json
   shred -u /root/cle-compte-de-service.json
   ```

3. Déposer le destinataire public `age` (une seule ligne `age1…`) :

   ```bash
   install -m 0600 /dev/null /etc/world-of-seeds-v2/accounts-backup-recipient
   printf '%s\n' 'age1…' > /etc/world-of-seeds-v2/accounts-backup-recipient
   ```

4. Installer les unités et lancer une première exécution, sans activer le timer :

   ```bash
   install -m 0644 /opt/world-of-seeds-v2/deploy/world-of-seeds-v2-accounts-backup.service \
     /opt/world-of-seeds-v2/deploy/world-of-seeds-v2-accounts-backup.timer /etc/systemd/system/
   systemctl daemon-reload
   systemctl start world-of-seeds-v2-accounts-backup.service
   journalctl -u world-of-seeds-v2-accounts-backup.service -n 20 --no-pager
   ```

5. Vérifier l'objet dans `wos-accounts/v1/` avec l'identité opérateur, faire l'essai de
   restauration ci-dessous, puis seulement activer le timer :

   ```bash
   systemctl enable --now world-of-seeds-v2-accounts-backup.timer
   ```

Variables facultatives du script, pour un essai hors des chemins par défaut :
`WOS_V2_ENV_FILE`, `WOS_V2_REPOSITORY`, `WOS_BACKUP_RECIPIENT_FILE`, `WOS_BACKUP_WORKDIR`
(par défaut `/var/lib/world-of-seeds-v2/accounts-backup`, qui contient `last-success`),
`WOS_BACKUP_METRICS` et `WOS_BACKUP_BUCKET` (par défaut `gs://world_of_seeds_bucket`).

## Essai de restauration

Télécharger une archive avec le compte opérateur. Sur une instance PostgreSQL isolée,
appliquer les migrations WoS sans créer de compte utilisateur. Préparer de nouveaux
clients API Discord sur cette instance. Depuis le dossier backend :

```bash
python -m app.account_backup inspect --identity /chemin/identite archive.json.age
python -m app.account_backup restore --empty-target \
  --identity /chemin/identite \
  --client UUID_CLIENT_SOURCE=UUID_NOUVEAU_CLIENT archive.json.age
```

Pour la restauration, pointer explicitement `WOS_DATABASE_URL` vers l'instance isolée.
Répéter `--client` pour chaque UUID source affiché par `inspect`, ou omettre l'option
si aucun lien n'est présent. La restauration refuse une table utilisateurs non vide.
Vérifier le nombre de comptes, la connexion avec un compte de test connu et le
rattachement Discord sur l'instance isolée. Consigner la date et le nom de l'objet
testé, sans conserver de données en clair.
