# Sauvegarde PostgreSQL quotidienne — 2.3.6

## Ce qui est livré, et ce qui doit être activé

La procédure complète `backup-restore-rise2-v2.md` reste nécessaire pour une reprise de toute la
pile (contenu, configs, qB et NewGreedy). Elle ne constituait pas une sauvegarde planifiée.
Le nouvel outil couvre **la base WoS uniquement**, en ligne : aucune pause des téléchargements,
aucune écriture dans PostgreSQL de production et aucun nouveau port réseau.

Un dump custom est restauré entièrement avec `pg_restore --exit-on-error` dans un PostgreSQL
jetable utilisant l'image exacte de la source, un volume unique, aucun réseau, 1 CPU et 1 Gio RAM.
Il contrôle les tables essentielles et la révision Alembic avant de chiffrer avec `age`. Un contrôle préalable réserve deux tailles de base + 1 Gio libres pour le staging/chiffrement.
Le dump clair temporaire est supprimé ; archive et manifeste sont privés (0600), dans un répertoire 0700.
Le manifeste contient uniquement empreinte, image, date, révision et preuve de restauration.

Le timer systemd tourne à **03:30 UTC**, avec au plus dix minutes de décalage et rattrapage après
redémarrage. Un verrou empêche les exécutions concurrentes. La rétention conserve tous les points
pendant 14 jours et le dernier point de chacune des huit dernières semaines présentes. Elle ne
supprime que des paires archive/manifeste connues, complètes et vérifiées ; jamais un arbre.
Les points supplémentaires avant migration suivent la même rétention.

**Le déploiement applicatif n'installe pas d'unités sur l'hôte.** Tant que la commande ci-dessous
n'a pas été exécutée sur Rise2 et son résultat vérifié, aucune sauvegarde réelle n'est annoncée.
La CI teste une base jetable ; elle n'est pas une preuve de sauvegarde de production.

## Première activation sur Rise2

À exécuter en SSH sur Rise2, après livraison du checkout 2.3.6 :

```bash
sudo apt-get update
sudo apt-get install -y age
sudo /opt/world-of-seeds-v2/scripts/rise2_v2_database_backup_install.sh
```

Le helper conserve un destinataire existant. Sinon il crée une identité `age` privée locale dans
`/etc/world-of-seeds-v2/backup-identity`, ainsi que son destinataire public `backup-recipient`.
Il exige un premier dump **et une restauration réussis avant d'activer le timer au boot**.
Il n'arrête ni ne recrée aucun service de production. Sur un serveur avec une clé de récupération
existante, installer son destinataire public dans `backup-recipient` avant cette commande.

```bash
sudo systemctl list-timers world-of-seeds-v2-database-backup.timer --no-pager
sudo journalctl -u world-of-seeds-v2-database-backup.service --since today --no-pager
sudo cat /var/backups/world-of-seeds-v2/postgres/status.json
sudo ls -lh /var/backups/world-of-seeds-v2/postgres/
```

Ne jamais publier le fichier `backup-identity` ou l'environnement. Copier l'identité dans un
coffre hors serveur, et les paires `.dump.age` / `.dump.age.json` sur un stockage hors hôte.
Sans ces deux copies, une perte de Rise2 peut perdre aussi les sauvegardes et leur clé.
La copie distante n'est pas configurée automatiquement : aucune destination n'a été fournie.

## Test complet d'une archive copiée

Sur un hôte de test avec Docker, Python 3.12 et age, sans production montée :

```bash
chmod 600 /chemin/prive/backup-identity
python3 scripts/rise2_v2_database_backup.py restore-check \
  /chemin/prive/wos-db-YYYYMMDDTHHMMSSZ-XXXXXXXXXXXX.dump.age \
  --identity /chemin/prive/backup-identity \
  --image postgres:17.11-alpine3.24
```

L'archive et son manifeste doivent être côte à côte. La commande vérifie SHA-256, déchiffre,
restaure réellement la base puis retire uniquement son conteneur et son volume jetables.
Le résultat JSON doit indiquer `result: pass`. Ce test confirme également que la clé conservée
hors hôte déchiffre bien l'archive. Il ne restaure jamais une base live et ne restaure pas les films.
Une interruption forcée/SIGKILL peut laisser un staging privé ou une ressource jetable ; inspecter
les ressources `wos-db-restore-*` et leur label avant tout nettoyage manuel, sans toucher aux
volumes de production. Une erreur de nettoyage fait échouer la sauvegarde.

## Supervision et notification

Le fichier `wos_database_backup.prom` rejoint le textfile collector Rise2 existant. Les règles
Prometheus signalent absence de métrique (30 min), dernière réussite datant de plus de 26 heures,
ou dernière tentative échouée (5 min). La date de dernière réussite n'avance qu'après le dump,
la restauration et la publication de l'archive. Un échec conserve la réussite précédente.

Ces règles ne sont **pas un canal de notification**. Le dépôt ne provisionne actuellement ni
Alertmanager ni destination externe. Vérifier dans Grafana/Prometheus que les nouvelles règles
sont chargées après relecture de la configuration. Configurer un destinataire choisi par
l'administrateur puis déclencher une notification de test et confirmer sa réception. Tant que la
réception n'est pas observée, les alertes ne sont pas considérées opérationnelles. Ne pas provoquer
un échec en corrompant ou supprimant une sauvegarde de production.

## Validation reproductible

Les tests unitaires couvrent rétention, corruption, liens, isolation et publication d'un échec.
La CI backend installe age et active `WOS_TEST_DATABASE_BACKUP_DOCKER=1` : base source jetable,
dump, restauration, chiffrement, déchiffrement, deuxième restauration, corruption refusée et
absence de conteneur/volume jetable résiduel. Aucun projet Compose de production n'est démarré.
