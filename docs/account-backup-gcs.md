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

## Première sauvegarde

Préparer un environnement Python avec les dépendances verrouillées du backend, `age`
et le CLI `gcloud`. Lancer le script depuis le dossier backend avec un utilisateur
dédié, dans un répertoire de travail privé, avec les variables suivantes exportées :

- `WOS_BACKUP_PYTHON` : chemin absolu du Python du backend.
- `WOS_BACKUP_WORKDIR` : répertoire absolu, privé et accessible à cet utilisateur.
- `WOS_BACKUP_RECIPIENT` : destinataire public.
- `WOS_DATABASE_URL` : connexion PostgreSQL interne avec un rôle dédié ayant SELECT
  sur users et discord_account_links ; ne pas publier de port PostgreSQL pour cet usage.
- `WOS_BACKUP_BUCKET` : facultatif, par défaut `gs://world_of_seeds_bucket`.

Exécuter `bash ../scripts/rise2_accounts_gcs.sh`. Le fichier `last-success` n'avance
qu'après l'envoi. Après l'essai de restauration, planifier une exécution quotidienne.
Configurer une supervision indépendante de Rise2 avec une alerte si aucun objet récent
n'est visible depuis 26 heures. Tester la réception Discord et e-mail séparément ;
ce dépôt ne provisionne pas ces destinataires.

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
