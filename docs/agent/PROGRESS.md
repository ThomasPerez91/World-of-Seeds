# World of Seeds — Progress

## Etat courant — 22 octobre 2026

World of Seeds V2 est désormais la ligne de production active.

- Version de production vérifiée : `2.4.2`. Cible en préparation : 2.4.3, S1.

## Campagne audit 2.4.x — 2.4.2 déployée, préparation de 2.4.3

Journal complet : `docs/audits/CORRECTIFS_2.4.md`. Audit PDF lu intégralement.
2.4.1 : #396, #398 et #399 intégrées ; #397 promue sur master
`92a16cefb47c9b9f2ac8898eba8b78262b0d50e6`. CI 39848967140 verte
(1190 tests backend réussis / 2 ignorés), déploiement 39849514346 réussi.
Contrôles publics indépendants : live/ready, page et bundle HTTP 200, version 2.4.1.
Helper root installé et vérifié par l’opérateur ; ne pas répéter le bootstrap.
Déploiement verrouillé, rollback conditionné au schéma, aucun downgrade/restauration
automatique. Sauvegarde chiffrée et restauration isolée avant changement de head ;
pas d’Alembic si head inchangé. Fenêtre SSH adaptée à la sauvegarde vérifiée.
C3/C4 (hors hôte/alertes reçues) restent à traiter.

2.4.2 déployée : T1/M5 (reset et deux appareils conservant leurs cookies),
T2/M9 (toutes les mutations admin avec CSRF valide/invalide et rôles), T2/M11
(seed d’un compte supprimé encore actif). Protections existantes vérifiées par
les tests. #400 et #401 fusionnées ; master `0be962b3f183a621631b1bba1addc229a2602086`.
CI master 39852047968 : 1291 tests backend réussis / 2 ignorés, six contrôles verts.
Déploiement 39852799747 réussi ; contrôles publics indépendants live/ready/page/bundle
HTTP 200, version 2.4.2. Voir le journal pour le digest et les preuves complètes.

2.4.3 en préparation, reprise le 23 octobre : scan quadratique remplacé par un
arbre de chemins partageant les préfixes. Pool dédié (2 threads, 4 travaux admis par processus)
pour validation/staging/lecture spool/normalisation C411, avec capacité conservée lors
d’annulations jusqu’à la fin effective. API 503 temporaire FR/EN ; worker RETRY_WAIT
et payload conservé. 92 tests ciblés après correction mémoire de revue ; première suite locale backend 1240 / 72 ignorés, frontend 209 / 3 ignorés,
Ruff/mypy/TypeScript/build verts. CI et déploiement à suivre.
L’environnement est de nouveau disponible ; pas de bootstrap root ou migration requis.
La version ne sera déclarée déployée qu’après les contrôles réels sur Rise2.

## Release 2.3.29 — téléchargements et alertes privées Discord

- Revue avant publication : priorité de l’échéance personnelle même si le torrent est
  passé en ERROR, test de régression des deux cas ; guide du bot corrigé pour exiger API 2.3.29.

- Nouveau scope explicite downloads:read-discord, lecture personnelle par liaison Discord
  et flux READY paginé par UUID pour le bot de confiance. Les anciennes clés restent inchangées.
- Liste privée paginée, positions estimées identiques au site, états du cycle de vie ; aucun
  titre d'un autre utilisateur, aucune seed stockée par le bot. Erreur physique ne masque
  plus un désabonnement/une expiration ; échéance dépassée visible avant passage du worker.
- Alerte verte READY, rouge au dernier tiers de ready_at → unsubscribe_at de chaque demande.
  Préférences et déduplication persistantes, bouton de désactivation des MP, rattrapage sans
  double message, aucune notification publique. Livraison au mieux : une tentative durable
  par alerte ; MP bloqués/crash ambigu peuvent perdre une alerte. Voir README du bot.
- Pas de migration, changement de scheduler, quota, infrastructure ou tâche de purge.
  Le conteneur bot reste une installation séparée avec nouvelle clé à quatre scopes.
- Validation locale : backend 1097 réussis / 72 ignorés (services absents), frontend
  209 réussis / 3 ignorés, bot 36 réussis ; Ruff, mypy (252 sources), TypeScript et build verts.

## Release 2.3.28 — mot de passe oublié sur le site et via Discord

- Login : aide Mot de passe oublié vers le bon salon Discord ; lien privé du bot vers
  un formulaire Forest FR/EN. Jeton uniquement dans le fragment, retiré immédiatement
  de l’historique, y compris dans un onglet déjà chargé ; aucun stockage navigateur.
  Confirmation du mot de passe, erreurs et réussite sans connexion automatique.
- Nouvelle permission explicite `users:reset-password` et délivrance par Discord ID
  vérifié par le bot. Anciennes clés non élargies ; client émetteur revalidé lors de
  l’utilisation, même après calcul crypto/attente des verrous. `users:recover` reste
  strictement limité à l’ancien parcours initial. Aucun reset anonyme par username.
- Grant haché, durée 15 minutes, usage unique, émission limitée à une par minute et
  par compte. Nouveau lien remplaçant le précédent, révocation de clé, changement de
  password et suspension invalidant le lien. Comptes admin/supprimés/suspendus refusés.
  Toutes les sessions révoquées à la validation ; seed et username conservés.
- Migration additive 20261022_37 ; verrous client → User → grant et crypto hors SQL.
  Tests PostgreSQL : double consommation, émission concurrente et expiration pendant
  le calcul du mot de passe. Validation CI des migrations et rollback avant promotion.
- Bot versionné dans `discord-bot/`, tests sans Discord réel et smoke Docker non-root.
  Seed expliquée dans le message privé. Mode d’inscription après validation via rôle
  Discord optionnel ; inscription libre si non configuré. Un ID Discord ne garantit
  pas une personne unique. Installation du bot et nouveau scope requis côté opérateur.
- Validation locale : 209 tests frontend réussis, trois ignorés, build TypeScript/Vite ;
  1 091 tests backend réussis et 72 ignorés faute de services externes, plus régression
  suspension/réactivation ciblée ; 27 tests bot. Rendu Chromium FR/EN de 320 à 1440 px,
  aide/formulaire/erreur/succès. Ruff et mypy sur le périmètre applicatif.
- +0.0.1 à 2.3.28 ; aucun changement d’infrastructure Rise2, qB, scheduler ou slots.
  Sauvegardes et alertes opérateur toujours reportées ; 3.0.0 après validation finale.

## Release 2.3.27 — première connexion harmonisée avec le login

- La personnalisation obligatoire du premier accès réutilise la carte Forest du login,
  son logo complet `/title.webp`, le même en-tête partagé et le sélecteur de langue
  compact à drapeau. Suppression du petit ancien logo et du sélecteur pleine largeur.
- Titres, espacements et aides resserrés ; quatre champs sur deux rangées dès 600 px,
  une colonne en dessous. Aucun masquage du formulaire pour réduire sa hauteur.
  Champs et bouton principal reprennent les surfaces et styles de connexion ;
  visibilité indépendante des mots de passe, labels et aides accessibles conservés.
- Le changement FR/EN conserve les saisies et persiste la préférence existante ;
  le bouton de langue est désactivé pendant l’enregistrement. Validation des mots
  de passe, erreurs API et passage au Dashboard après succès conservent le contrat.
- Validation : 205 tests frontend réussis, trois ignorés, build TypeScript/Vite et
  16 tests des miroirs de version. Contrôle Chromium du build de production en
  FR/EN à 320, 390, 820 et 1440 px sans débordement horizontal ; à 320 px, le
  formulaire français reste défilable verticalement sans couper les contrôles.
- +0.0.1 à 2.3.27, miroirs de version synchronisés. Aucun changement API,
  provisioning Discord, migration ou infrastructure. Création de compte Discord
  confirmée fonctionnelle par Thomas sur Rise2.
- Le contrôle CI a détecté GHSA-6q48-6c7w-9gw2 dans le bundle de certificats
  existant : mise à jour ciblée de certifi 2026.7.22 vers 2026.10.19 dans uv.lock
  et l’export Docker haché. Aucun autre paquet modifié, aucun contournement du
  contrôle de sécurité ; correction incluse dans la même livraison 2.3.27.

## Release 2.3.26 — finalisation de l’API Discord

- Association Discord/WoS créée atomiquement avec le compte, unique par Discord et par compte, conservée après suspension/suppression. ID snowflake transmis comme chaîne, distinct du username modifiable. Création concurrente sérialisée par le mutex existant ; ancien contrat sans ID Discord et hash d’idempotence conservés. Migration additive `20261015_36`, réversible.
- `GET /api/external/v1/capacity` et `GET /api/external/v1/users/discord/{id}` sous nouveau scope `users:read`. Capacité indicative, contrôle transactionnel définitif conservé dans POST users. État sans secrets, y compris comptes désactivés/supprimés.
- `POST /api/external/v1/users/discord/{id}/initial-credentials` sous scope `users:recover` : nouveau mot de passe temporaire uniquement avant toute connexion, pour un compte actif non administrateur créé par ce même client. Autorisation client revérifiée après hachage, compte rafraîchi/verrouillé comme le login ; sessions révoquées et audit sans secrets. Aucun reset de compte déjà utilisé, aucune réactivation ni liaison implicite d’anciens comptes. Une nouvelle récupération invalide la précédente.
- Permissions sélectionnables dans l’administration, aucun élargissement automatique des clés existantes. Documentation du parcours Discord, confidentialité des messages, retries, seed et absence de webhook. Le bot reste responsable de vérifier l’auteur Discord et de sérialiser les livraisons par membre ; aucun message Discord envoyé par cette mise à jour.
- Tests d’intégration API et concurrence PostgreSQL : doublons inter-clients, capacité épuisée, états et secrets, récupérations répétées, première connexion, révocation, ID invalides et mutations pendant attente de verrou.
- Validation locale : 1 075 tests backend réussis, 69 ignorés (services PostgreSQL/Redis/Docker absents) ; Ruff/format/mypy 247 sources, frontend 204 tests réussis et build production. Concurrence PostgreSQL et migrations vérifiées en CI avant promotion.
- +0.0.1 à 2.3.26 ; 3.0.0 reste réservée à la validation finale. Aucun changement Compose/infra ; sauvegardes et alertes opérateur toujours reportées.

## Release 2.3.25 — arrêt des téléchargements sans abonné

- Le délai de rétention conserve les données, pas un slot qBittorrent : suppression de l’exception RUNNING des torrents actifs avec purge_after future. Au prochain cycle du scheduler, le torrent sans demande active reçoit STOPPED et libère la capacité ; la commande passe toujours hors transaction via le gateway propriétaire existant. Aucun arrêt direct HTTP/SQL ni suppression disque immédiate.
- Après la dernière demande, une ancienne activation forcée reste un marqueur d’arrêt prioritaire jusqu’à confirmation STOP par qBittorrent, sans contourner le curseur de 200 torrents. Le scheduler ne la traite plus comme une autorisation RUNNING ; elle est retirée après succès avec revalidation de génération et du marqueur d’arrêt. Une relance administrative postérieure efface ce marqueur, même lorsqu’un job de purge existe déjà et que la génération reste identique ; son intention est donc conservée et peut reprendre au cycle suivant. Un réabonnement annule la purge et cette ancienne activation avant reprise normale. Si un autre abonné actif reste présent, son téléchargement et son quota sont conservés.
- Données partielles, progress et storage_key conservés jusqu’à la purge différée. Un réabonnement pendant la rétention annule le job de purge et réutilise le même contenu, avec reprise par le scheduler. Les délais de stall 60 s puis 3/5/10 minutes des torrents encore demandés restent inchangés ; les orphelins ne contournent plus ces pauses pour occuper un slot.
- Onze scénarios supplémentaires : dernier désabonnement avec/sans stall et ancienne activation forcée, capacité libérée sous plafond unique, abonné survivant, réabonnement et arrêt réappliqué après panne gateway, relance forcée sans desired_active dans une file de 201 torrents avec panne/reprise, réabonnement avant confirmation d’arrêt, nouvelle relance administrative pendant la confirmation STOP. Ancien test de maintien RUNNING pendant la grâce remplacé par l’arrêt attendu. Six régressions reproduites avec l’ancien code.
- +0.0.1 à 2.3.25 ; aucun changement Compose/migration/slots HTTP. Sauvegardes et alertes opérateur reportées. Validation finale avant 3.0.0 après ces vérifications produit supplémentaires.

## Release 2.3.24 — leases HTTP face au lifecycle

- Admission HTTP : verrou ManagedTorrent explicite avant TorrentRequest puis TorrentFile, comme le lifecycle qui prend le parent avant les droits. Compte, association propriétaire/torrent/fichier, état READY et deadline du droit revérifiés sous les verrous. Horloge lue après toutes les attentes, avant nettoyage des leases expirés, comptage et création du lease.
- PostgreSQL : le compte HTTP est verrouillé FOR NO KEY UPDATE, toujours incompatible avec les mutations/suspensions mais compatible avec les vérifications KEY SHARE des clés étrangères lors de l’expiration d’un droit. Cycle supplémentaire reproduit par les tests PostgreSQL avant correction.
- Renouvellement : compte, parent, droit puis lease verrouillés/rechargés ; heure lue après les attentes. Un lease dont expires_at est atteint n’est plus ressuscité. Trois régressions de deadline reproduites avec l’ancien code (admission après échéance, renouvellement à échéance déjà atteinte ou atteinte pendant l’attente).
- Les transferts valides conservent leur lease jusqu’à la fin/déconnexion tant que les heartbeats le renouvellent : un droit EXPIRED/CANCELLED sur READY/PURGE_PENDING permet de finir, mais aucune nouvelle admission. PURGING/PURGED ou révocation du compte refusent le renouvellement. Pas de rotation périodique ni de changement des slots/poids/bande passante.
- Onze cas nouveaux : sept SQLite (échéances et continuité après désabonnement/expiration) et quatre PostgreSQL (admission attendant le parent sans retenir le droit ; renouvellement après attente avec lease expiré, droit expiré ou passage PURGING). Test existant de purge différée par un lease actif conservé.
- +0.0.1 à 2.3.24 ; 3.0.0 après validation finale de l’audit. Pas de migration/Compose. Sauvegardes et alertes opérateur toujours reportées. Prochaine étape : passe finale de validation des parcours et bilan des limites connues avant 3.0.0.

## Release 2.3.23 — comptes révoqués et torrents partagés

- Le partage reste le fonctionnement prévu : supprimer un compte annule ses droits et libère son quota logique, sans supprimer le torrent demandé par un autre compte. La suspension conserve ses droits/quota pour la reprise, tout en bloquant ses accès ; droits et téléchargements des autres comptes conservés.
- Admission d’un slot HTTP : le compte est désormais chargé, rafraîchi et revérifié sous verrou après le contrôle initial de la route. Une suspension/suppression committée pendant l’attente interdit la création du lease. Renouvellement : compte, torrent, droit et lease rechargés sous leurs verrous ; aucune continuation fondée sur une identité ORM périmée. Admission torrent et mutations administratives rafraîchissent également le compte verrouillé.
- Suppression multi-torrents : tous les parents sont verrouillés en ordre UUID avant les annulations et leur verrou de quota. Cela évite de retenir un quota en attendant un autre parent qu’une expiration possède déjà. Le quota annulé est rafraîchi sous verrou.
- Dix cas SQLite (révocations face à un compte préchargé, compte déjà supprimé, partage avec un téléchargement survivant) et trois cas PostgreSQL réels (slot attendant un compte révoqué, suppression attendant un second torrent sans retenir le quota). Ancien code rejette mal les admissions suspendues, reproduit localement.
- +0.0.1 à 2.3.23 ; 3.0.0 réservée à la fin de l’audit. Pas de migration/Compose ni de changement de déduplication ou de politique de slots. Backups et alertes opérateur toujours différés. Prochain audit : ordre des verrous et renouvellement des leases de téléchargement HTTP face au lifecycle.

## Release 2.3.22 — finalisation worker et lifecycle

- La finalisation d’un échec nécessitant une transition torrent prend désormais le verrou ManagedTorrent avant le verrou TorrentJob, comme l’activation de purge. Les chemins job seul (claim, heartbeat, récupération, succès et échec sans transition torrent) n’acquièrent pas de verrou parent inutile.
- Un worker attendant le torrent ne retient plus son job : heartbeat, récupération de claim et annulation de purge peuvent progresser. Propriétaire, tentative et échéances restent revérifiés après l’attente ; l’horloge de finalisation est lue après acquisition des verrous. Identité ORM du job rechargée sous verrou.
- Un job finalisé CANCELLED ne modifie plus l’état, les échéances ou la génération du torrent et ne publie pas d’échec de lifecycle. Régression reproduite localement avec l’ancien worker, qui remplaçait notamment PURGED/READY par RETRY_WAIT après annulation.
- Douze cas : huit SQLite (échecs permanent/transitoire contre quatre états de lifecycle annulé) et quatre PostgreSQL réels (heartbeat/annulation pendant l’attente parent, claim expiré ou tentative remplacée avant finalisation). Les limites de claim et l’absence de replay externe 2.3.12/15 restent conservées.
- +0.0.1 à 2.3.22 ; 3.0.0 réservée à la fin de l’audit. Pas de migration/Compose/slots HTTP. Activation backups et chargement opérateur des alertes différés. Prochain audit : suppression et suspension de comptes face aux droits torrent partagés.

## Release 2.3.21 — ordre des verrous sur les droits torrent

- Relance : la prélecture matérialise aussi l’identité canonique du torrent. La transaction verrouille maintenant ManagedTorrent avant TorrentRequest, comme ajout/annulation/READY, puis revérifie propriétaire, association, état, génération et payload. Elle ne retient plus un droit en attendant son torrent.
- Expiration READY : torrents ayant des droits dus sélectionnés/verrouillés selon leur première échéance avec SKIP LOCKED, puis droits encore dus revérifiés/verrouillés, et usages utilisateurs verrouillés en un ordre global avant traitement. La limite s’applique après exclusion des torrents occupés, puis aux droits effectivement traités ; un torrent occupé partagé par de nombreux comptes ne bloque pas les candidats libres suivants. Les torrents occupés sont laissés au passage suivant ; la maintenance ne verrouille pas leurs droits avant d’attendre leur torrent. Rétention, plafonds de batch, quotas, événements et idempotence conservés.
- Annulation : torrent et droit sont explicitement rafraîchis sous verrou. Une expiration survenue pendant l’attente ne peut plus être remplacée par une annulation fondée sur l’identité ORM périmée.
- Six scénarios PostgreSQL isolés : relance attendant un torrent pendant son annulation ; expiration face à un torrent occupé avec annulation, réupload ou simple libération ; droit expiré pendant l’attente d’une annulation ; page remplie par les droits d’un torrent occupé suivie d’un torrent libre. Vérifications des états, quotas, jobs et absence de signal de relance invalide.
- +0.0.1 à 2.3.21 ; 3.0.0 réservée à la fin de l’audit. Pas de migration/Compose/slots HTTP. Activation backups et chargement opérateur des alertes différés. Prochain audit : ordre des verrous entre lifecycle et claims/finalisations des jobs worker.

## Release 2.3.20 — sérialisation des admissions de stockage

- Réconciliation : le verrou du ledger est désormais acquis avant l’agrégat des octets gérés. Un upload ou une purge ayant committé pendant l’attente du verrou ne peut plus être écrasé par un total calculé avant ce commit.
- Relances : même verrou de ledger que les uploads et la purge, acquis avant l’agrégat des réservations actives et conservé jusqu’au commit. Plusieurs torrents ERROR distincts ne peuvent plus décider simultanément avec le même ancien total. Les états/comptages et plafonds existants sont conservés.
- La primitive commune de verrou recharge également le compteur avec populate_existing : une identité ORM chargée avant un commit concurrent ne doit pas garder son ancienne valeur. Régression de compteur périmé reproduite localement sans ce rafraîchissement.
- Six cas : compteur chargé puis modifié en transaction indépendante (SQLite/PostgreSQL), réconciliation concurrente avec upload ou purge, rafales de deux/huit relances via HTTP avec résultats SQL volontairement retardés. Les cas de verrouillage réel utilisent le service PostgreSQL isolé de CI.
- +0.0.1 à 2.3.20 ; 3.0.0 réservée à la fin de l’audit. Pas de migration/Compose/slots HTTP. Activation backups et chargement opérateur des alertes différés. Prochain audit : ordre des verrous entre ajout, annulation et lifecycle partagé.

## Release 2.3.19 — propriété et nettoyage des uploads interrompus

- Annulation avant commit : le thread de staging est protégé et terminé avant rollback/libération des verrous, puis sa publication est nettoyée. Des annulations répétées ne détachent pas le writer. Aucun job/signal Redis n’est créé pour une tentative annulée.
- Staging sous UUID privé de tentative puis publication atomique vers la clé stockage inchangée. Un descripteur ouvert identifie l’inode de cette publication et empêche sa réutilisation jusqu’à la fin de la tentative. Le nettoyage compare device/inode sous verrou de publication avant unlink ; il préserve une publication plus récente. Descripteurs fermés dans tous les chemins.
- Publication et nettoyage conditionnel coordonnés par verrous bornés dans le runtime API mono-processus. Le staging conserve sa transaction actuelle : une annulation attend la fin réelle du syscall/thread, sans prétendre l’interrompre. Une panne du processus/disque peut encore laisser des reliquats ; erreurs de nettoyage journalisées sans détails privés et sans masquer l’erreur de requête.
- Frontière de commit conservée : erreurs/annulations après le dernier flush du corps ne suppriment pas le payload dont le commit peut avoir réussi. Préparation privée nettoyée même après une erreur de fsync/publication.
- Tests d’annulation avant écriture/après publication/avant commit sur création et réactivation, double annulation, nettoyage ancien versus publication nouvelle, fsync échoué et commit réellement effectué dont l’accusé est annulé. Tests d’indisponibilité SQL et commit ambigu 2.3.15 conservés.
- +0.0.1 à 2.3.19 ; 3.0.0 réservée à la fin de l’audit. Activation backups et chargement opérateur des alertes différés. Prochain audit : sérialisation des admissions et réservations de stockage concurrentes.

## Release 2.3.18 — relance sans transaction pendant la lecture du torrent

- Régression reproduite avec un pool réel à une connexion : la lecture du payload conservait la connexion SQL et les verrous métier ; une deuxième opération échouait pendant une attente disque.
- La relance copie les identifiants scalaires d’une demande appartenant au compte, termine sa lecture SQL, puis lit et valide le payload hors transaction. Les droits actifs, l’état ERROR, la clé stockage et la génération sont revérifiés sous verrou avant toute mutation ; métadonnées, jobs et capacité sont ensuite validés dans cette transaction.
- Éligibilité du dernier ADD (ordre création puis UUID), exigé FAILED, vérifiée aussi dans le précontrôle SQL sans verrou : une erreur de synchronisation après ADD réussi (payload supprimé par le worker), sans job ADD, ou avec un ancien échec suivi d’un ADD réussi, est refusée sans lecture du payload. Revalidation transactionnelle conservée.
- Une lecture de payload par clé stockage et application API (runtime mono-processus), sans attente des doublons : refus 409 torrent_retry_in_progress. L’annulation du demandeur conserve l’admission jusqu’à la fin réelle du thread ; erreurs tardives consommées, drainage shutdown borné à 2 s. Clés et applications indépendantes.
- Quatorze cas SQLite/PostgreSQL avec disque suspendu : succès, erreur disque, annulation du demandeur, désabonnement concurrent, changement de génération, de clé et de métadonnées. Rafales concurrentes refusées sans seconde lecture, y compris après annulation ; tests de reprise après succès/erreur tardifs. Aucun job ni signal Redis en cas de refus/annulation. Les erreurs de disponibilité SQL restent 503 sans replay.
- +0.0.1 à 2.3.18 ; 3.0.0 réservée à la fin de l’audit. Activation backups et chargement opérateur des alertes différés. Prochain audit : staging et nettoyage des uploads interrompus, puis sérialisation des admissions de stockage concurrentes.

## Release 2.3.17 — admission des lectures disque de réconciliation

- Trois régressions reproduites : deux rapports pouvaient lancer leurs probes/inventaires en parallèle ; l’inventaire appelait explicitement stat pour toutes les entrées, même les noms non UUID.
- Une seule opération disque de réconciliation en cours par application API, sans file d’attente : concurrente refusée en 503 reconciliation_busy, Retry-After 5, no-store. L’annulation du demandeur ne libère pas prématurément le slot : le thread réellement en cours doit finir. Erreurs propagées et consommées si le demandeur est parti. Attente de nettoyage au shutdown bornée à 2 s ; aucune prétention d’interrompre un syscall bloqué.
- Inventaire : noms invalides éliminés avant métadonnées, type de répertoire via DirEntry.is_dir(follow_symlinks=False), fermeture explicite de scandir. Pas de stat imposé sur les filesystems fournissant le type ; fallback possible pour DT_UNKNOWN. Tri/cursor/compteur d’entrées invalides et mémoire limit+1 conservés. La pagination déterministe exige toujours un parcours complet du répertoire ; aucun cache de preuves disque périmées.
- Tests des deux routes/phases occupées, rafale de vingt refus sans nouvelle lecture, annulation avec succès/erreur tardifs, reprise, isolation des applications et coût des métadonnées avec liens/fichiers/noms invalides. Tests 2.3.16 conservés : SQL reste libre pendant les opérations disque.
- +0.0.1 à 2.3.17 ; 3.0.0 réservée à la fin de l’audit. Pas de changement Compose/migration/slots HTTP de téléchargement ni des effets worker. Activation backups et chargement opérateur des alertes différés. Prochain audit : contrôles de stockage dans les parcours d’upload et de relance torrent.

## Release 2.3.16 — réconciliation sans transaction pendant les lectures disque

- Audit : les appels HTTP qBittorrent/NewGreedy du worker et du scheduler sont déjà hors transaction. Deux régressions confirmées dans le rapport admin : vérification des fichiers et inventaire disque retenaient la connexion de lecture SQL ; un second accès échouait avec un pool à une seule connexion.
- La page base et les identités qBittorrent sont matérialisées en valeurs scalaires, puis rollback avant chaque phase disque. Aucun accès à une entité ORM expirée ne réouvre la transaction pendant les probes. La vérification de rétention des snapshots est terminée avant l’inventaire physique ; la comparaison SQL des clés reste après cet inventaire. Lecture finale également libérée.
- 16 cas SQLite/PostgreSQL avec disque réellement suspendu et deuxième connexion : succès, erreur stockage, annulation et modification SQL concurrente, sur les deux phases. Pagination, anomalies, refus des snapshots expirés et revalidation des actions de récupération conservés. Le rapport reste une observation best-effort, pas une transaction atomique entre base et disque.
- +0.0.1 à 2.3.16 ; 3.0.0 réservée à la fin de l’audit. Pas de migration/Compose/slots HTTP ni de modification des effets externes. Activation backups et chargement opérateur des alertes différés. Prochain audit : coût et concurrence des vérifications disque dans les rapports admin.

## Release 2.3.15 — réponses HTTP en indisponibilité SQL

- Quatre régressions reproduites : connexions asyncpg refusées et pool réellement saturé renvoyaient HTTP 500, sur API interne/externe. Normalisation au seul bord du driver SQL et classification des déconnexions/SQLSTATE de disponibilité ; pool TimeoutError traité spécifiquement. OSError de fichiers, erreurs de contraintes, syntaxe et credentials SQL ne deviennent pas des indisponibilités.
- Un timeout/erreur système sur driver déjà établi termine aussi la connexion ; signal de déconnexion explicitement transmis à SQLAlchemy pour invalider la connexion incertaine sans purger les connexions saines du pool. Les coupures TCP établies restent couvertes.
- Réponse 503 `database_unavailable`, Retry-After 5 s et no-store ; enveloppe interne `detail` conservée, enveloppe externe `error` avec request_id. Journal constant sans SQL, identifiant métier, secret ni traceback. Readiness garde son contrat 503 et ajoute les mêmes headers ; liveness reste indépendante de SQL.
- Tests de refus/handshake silencieux/pool occupé et reprise, fermeture des sessions, login sans cookie, classification, rollback sans replay d’une mutation, plus pool PostgreSQL saturé et connexion rompue en CI. Aucun retry automatique, aucun budget de pool augmenté, aucune garantie d’absence d’effet si le commit a été interrompu.
- Ajout/annulation/retry de torrents relaient les indisponibilités avant leurs catches SQL génériques, avec tests des six combinaisons panne/pool. Si le commit d’un ajout a réussi mais son accusé est perdu, le payload déjà préparé est conservé pour la récupération durable, sans publication/replay supposé ; test SQL réellement committé.
- Frontière de commit explicite après le dernier flush du corps transactionnel : une panne avant cette frontière nettoie le payload préparé après rollback, y compris sur tentatives répétées ; une panne lors du commit conserve le payload. Tests des deux états de disponibilité et du commit confirmé mais accusé perdu.
- Middleware request_id externe ne fabrique plus de 500 après le début d’un flux : interruption propagée et cleanup normal, jamais de seconde réponse HTTP.
- +0.0.1 à 2.3.15 ; 3.0.0 réservée à la fin de l’audit. Pas de modification Compose/migration/slots HTTP. Activation backups et chargement opérateur des alertes différés. Prochain audit : transactions conservées pendant les appels externes et leur effet sur l’occupation du pool.

## Release 2.3.14 — délais SQL bornés

- Fabrique commune de l'engine API/worker : attente du pool 5 s, connexion asyncpg 5 s, commandes 15 s (dont pré-ping, transactions et attentes de verrous), garde externe à 16 s puis drainage de cancellation au maximum 1 s. Capacité du pool inchangée. Politique runtime fixe, positive et finie (DatabaseTimeouts) ; aucune variable Compose/env annoncée sans transmission. La politique est raccourcie seulement dans les tests de panne ; aucun argument PostgreSQL transmis aux tests SQLite.
- Serveur TCP silencieux : timeout réel du driver et nouvelles tentatives possibles, y compris récupération/claim worker sans exception fatale ni détail privé. Régressions confirmées avec la fabrique précédente : délais non transmis et handshake ne terminant pas avant le watchdog. Tests PostgreSQL CI de requête lente, attente de verrou et pool épuisé, suivis de rollback/reprise.
- Une commande interrompue n'implique ni réussite ni absence d'effet : finalisation et récupération durables 2.3.12/13 conservées. Ces délais sont par opération, pas un budget global de transaction ou de téléchargement. Migrations et scripts de sauvegarde restent sur leurs connexions propres.
- La revue a identifié le cas d’une connexion établie perdant toute réponse, y compris annulation. Garde externe appliquée à tous les awaits de l’adaptateur : pré-ping, commandes, commit/rollback, fermeture et initialisation des codecs. Tests proxy TCP PostgreSQL coupant les deux directions après connexion, puis reprise du pool ; arrêt/annulation et erreurs inattendues conservés.
- +0.0.1 à 2.3.14 ; 3.0.0 réservée à la fin de l'audit. Sauvegardes et chargement opérateur des alertes toujours différés. Prochain audit : gestion de la pression sur le pool et réponses HTTP en cas d'indisponibilité SQL.

## Release 2.3.13 — worker résilient aux pannes de connexion SQL

- Trois régressions reproduites avec de vraies connexions asyncpg refusées : récupération, claim et boucle worker/TaskGroup voisin. Les boucles de polling, synchronisation et rétention prennent désormais en charge SQLAlchemyError et OSError brutes, sans traceback/SQL/secrets dans les journaux.
- Initialisation des options worker retentée toutes les cinq secondes, sans valeurs de secours inventées ; SIGINT/SIGTERM installés avant la première connexion. Arrêt/annulation annule et attend la lecture SQL en cours ; Le même événement d'arrêt pilote le démarrage puis les tâches runtime, sans remplacement de handler signal ni fenêtre de perte d'arrêt. Redis/engine sont nettoyés par une seule sortie commune. Configuration invalide et erreurs inattendues restent visibles.
- Échec de heartbeat traité comme perte de claim et annulation du handler. Échec/ambiguïté de finalisation laisse le claim durable à la récupération après expiration/backoff ; aucun replay immédiat ni libération non confirmée. Idempotence/réconciliation restent nécessaires pour les effets déjà exécutés.
- Rétention : après une erreur SQL, retry après cinq secondes au lieu d'attendre une heure ; cadence horaire conservée après succès. Pas de chevauchement ni boucle rapide. Tests d'indisponibilité réelle puis reprise SQL, cadence, TaskGroup voisin, finalisation, heartbeat, démarrage, arrêt/annulation et erreurs inattendues.
- +0.0.1 à 2.3.13 ; 3.0.0 réservée à la fin de l'audit. Sauvegardes et chargement opérateur des nouvelles alertes toujours reportés. Prochaine vérification : bornes de connexion/requête SQL pour limiter les attentes pendant les pannes silencieuses.

## Release 2.3.12 — claims expirés et tentatives obsolètes

- Expiration stricte : renouveler, compléter, retenter, échouer ou annuler un job exige un claim et un délai d'exécution encore valides, y compris exactement à l'échéance et avec timestamps SQL naïfs. Un claim expiré ne peut plus être ressuscité avant le passage de la récupération.
- Le worker vérifie également le numéro de tentative sous verrou, pour chaque heartbeat/finalisation : un ancien handler ne peut pas modifier une nouvelle tentative reprise par le même processus. L'heure de finalisation d'échec est lue après acquisition du verrou. Perte de claim traitée par code constant, sans transition ni écriture de torrent depuis l'ancien résultat.
- Régressions reproduites : 18 transitions périmées acceptées auparavant ; trois résultats d'une ancienne tentative finalisaient auparavant le job repris. Tests de frontières, heartbeat annulant le handler puis reprise par un autre worker, résultats tardifs succès/transitoire/permanent, et heartbeat d'une tentative obsolète.
- Régression de récupération signalée en revue et confirmée : une annulation demandée avant expiration prime désormais sur retry/exhaustion, en terminant le job CANCELLED sans réexécuter le handler. Tests des deux échéances, tentatives restantes/épuisées et worker sans replay.
- +0.0.1 à 2.3.12 ; 3.0.0 réservée à la fin de l'audit. Politique de slots de téléchargement inchangée. Les effets externes restent réconciliés/idempotents ; ce correctif n'annule pas un effet déjà exécuté côté qBittorrent. Sauvegardes/chargement opérateur des alertes toujours reportés. Prochaine vérification : résilience de la boucle worker lors de pannes de connexion PostgreSQL.

## Release 2.3.11 — reprise de la supervision après erreur SQL

- Course de panne reproduite dans un TaskGroup : une erreur SQL lors d'une écriture de santé ou de la finalisation du cycle sortait de la boucle de supervision et annulait le scheduler voisin. Les erreurs SQLAlchemy et les erreurs système de connexion (OSError/socket/DNS/timeout asyncpg) sont désormais journalisées par un code constant, puis le cycle est retenté à la cadence configurée.
- Les contextes de session assurent rollback et fermeture avant l'attente. Pas de chevauchement, pas de boucle rapide, pas de renouvellement artificiel de la fraîcheur des observations. Annulation et arrêt pendant l'attente restent immédiats ; une exception inattendue continue de remonter.
- Tests avec transactions réelles et injection d'erreur sur les écritures, reprise d'une santé cohérente et de l'inventaire, TaskGroup voisin conservé, cadence, logs sans détail privé, annulation et arrêt pendant le délai de reprise. Une vraie connexion asyncpg refusée sur un port local réservé confirme le cas de connexion brute signalé en revue.
- +0.0.1 à 2.3.11 ; 3.0.0 réservée à la fin de l'audit. Sauvegardes/chargement opérateur des alertes restent reportés. Prochaine étape : vérifier les chemins de reprise des jobs durables lors d'une perte de claim ou d'une interruption worker.

## Release 2.3.10 — révocation pendant le provisioning externe

- Corrige une course confirmée : une clé désactivée/révoquée ou privée de scope pendant la préparation du mot de passe pouvait encore créer un compte. L'empreinte authentifiée est figée, puis l'autorisation relue sous verrou avant création/commit ; replays rapides également revalidés sans recalcul crypto.
- Aucune transaction/verrou conservé pendant Argon2. Ordre quota puis client, verrou client jusqu'au commit ; refus 401/403 avec rollback, sans compte/credentials/audits/idempotence. Contrats de quotas, générations, scopes et isolation des téléchargements conservés.
- Quatre régressions reproduites sur le code précédent (201 malgré droits modifiés) puis refus corrects, avec identité ORM volontairement périmée. Deux scénarios PostgreSQL à sessions indépendantes vérifient révocation avant création et révocation attendant une création déjà verrouillée. Les scénarios PostgreSQL sont requis en CI, ignorés localement sans ce service.
- +0.0.1 à 2.3.10 ; 3.0.0 réservée à la fin de l'audit. Ce correctif fiabilise l'API existante, sans activer de bot ni inventer une liaison d'identité Discord. Sauvegardes/chargement opérateur des alertes restent reportés. Prochaine correction : audit des jobs périodiques et de leur reprise.

## Release 2.3.9 — lectures frontend sans courses ni chevauchement

- Hook commun par source/composant pour Dashboard et supervision admin : une lecture à la fois, rafale manuelle regroupée, timer n'annulant plus une lecture lente, abort/cleanup et callbacks tardifs ignorés. Cadences, routes et visibilité par utilisateur conservées.
- GET d'aperçu/statut NewGreedy suspendus pendant les mutations correspondantes puis relus ; l'ancien résultat ne peut pas annuler l'affichage du redémarrage demandé. Propagation AbortSignal jusqu'à fetch pour six lectures admin.
- Deux régressions réellement reproduites sur l'ancien Dashboard : trois lectures/annulations en trente secondes pour une réponse lente et expiration de session après navigation sur 401 tardif. Passent après correction, avec tests de rafale, StrictMode, démontage, changement de source/callback, reprise et lecture concurrente à une mutation NewGreedy.
- Version +0.0.1 à 2.3.9 ; 3.0.0 reste réservée à la fin de l'audit. Sauvegardes/activation opérateur des alertes restent reportées. Prochaine étape : vérifier l'intégration API Discord/provisioning avant correction, puis les jobs périodiques.

## Release 2.3.8 — mutualisation des collectes réseau

- Le Dashboard et le suivi de capacité HTTP partagent les deux requêtes Prometheus réseau par processus API, via une connexion persistante et un cache serveur de quinze secondes au maximum. Les demandes simultanées attendent une seule collecte ; aucune donnée utilisateur dans le cache.
- Expiration plafonnée par la fraîcheur réelle des échantillons et âge transmis inchangé au scheduler ; panne temporisée cinq secondes sans réutilisation des valeurs expirées, annulation sans verrou résiduel et fermeture du client au shutdown.
- Régressions : cinquante lecteurs ne produisent que deux requêtes réseau ; expiration, perte/reprise, limite de fraîcheur, annulation, isolation entre applications et partage effectif avec le monitor. Contrat API/authentification/no-store et règles de slots conservés.
- +0.0.1 à 2.3.8 ; 3.0.0 réservée à la fin de l'audit. Sauvegardes et chargement opérateur des nouvelles alertes restent reportés. Prochaine tâche : consolidation du frontend après inspection des rafraîchissements et du traitement des requêtes concurrentes.

## Release 2.3.7 — supervision des récupérations HTTP READY

- Tableau Grafana dédié aux métriques 2.3.4 : flux rapides/attentes, cible future, délai de promotion/premier corps, débits ASGI/hôte, fraîcheur, interruptions/erreurs et refus. Aucun identifiant métier ; Dashboard utilisateur inchangé.
- Jauges instantanées, débit hôte masqué si périmé, collecte désactivée distinguée des mesures périmées via indicateur agrégé de configuration, sens des compteurs/latences explicité. Le temps sans progression d’un client lent n’est pas assimilé à une panne.
- Alertes warning par cible pour télémétrie périmée avec activité, attente >30 min persistante, et erreurs répétées >10 %, sans compter les annulations comme erreurs ni modifier les slots.
- Tests promtool de comportement (dont profil local sans URL Prometheus) et smoke de provisioning Grafana avec exécution de toutes les requêtes Prometheus. Procédure `docs/http-download-supervision.md`.
- Activation backups/notifications reportée à la demande de Thomas. Le nouveau tableau est provisionné depuis le répertoire monté ; nouvelles règles Prometheus à charger/confirmer lors d’une intervention OPS, sans prétendre une activation observée sur Rise2.
- +0.0.1 à 2.3.7 ; 3.0.0 réservée à la fin de l’audit. Prochaine amélioration technique : réduire les collectes répétées Dashboard/Prometheus après vérification du code courant.

## Release 2.3.6 — sauvegarde PostgreSQL quotidienne

- Nouveau dump en ligne, restauration obligatoire sur PostgreSQL jetable sans réseau avec image exacte de la source, chiffrement age, manifeste SHA-256, rétention 14 jours + huit points hebdomadaires et verrou de concurrence.
- Helper opérateur : première sauvegarde/restauration avant activation persistante du timer quotidien 03:30 UTC. Clé locale privée créée uniquement si aucun destinataire n'existe. Aucune unité hôte installée implicitement par le déploiement applicatif.
- Métriques textfile et règles Prometheus pour absence, ancienneté >26 h et échec ; les règles seules ne livrent pas de notifications. Aucun destinataire externe ni stockage hors hôte connu/configuré.
- Tests unitaires et scénario Docker/age de CI sur source isolée, double restauration et corruption refusée. L'activation et une restauration d'une archive Rise2 restent à confirmer avec accès opérateur ; procédure `docs/database-backups-rise2.md`.
- Limites : base uniquement, copie de clé/archives hors serveur et réception de notification encore requises. La reprise complète contenu/config/qB/NewGreedy garde sa procédure distincte. +0.0.1 ; 3.0.0 réservée à la validation finale.

## Release 2.3.5 — arrêt API borné et reprise des fichiers READY

- Entrypoint API avec drain SIGTERM/SIGINT : refus temporaire 503/Retry-After des nouvelles requêtes, readiness retirée, cinq secondes de grâce puis annulation. Le lifespan attend jusqu’à deux secondes le nettoyage des réponses avant fermeture Redis/SQL. Aucune modification de Compose ni des services persistants.
- Reprise automatique des transferts fichiers/dossiers gérés par WoS : réseau/5xx/EOF prématuré, fermeture du writer partiel, taille locale revalidée, Range et snapshot constant. Huit retries au maximum, backoff plafonné à quinze secondes, pause/annulation immédiates. Les pages de manifeste réessaient également les indisponibilités.
- Régression TCP réelle : SIGTERM au milieu d’un fichier de 128 Mio, arrêt en moins de neuf secondes, zéro lease, redémarrage puis Range/ETag/Content-Range et SHA-256 valides. Régressions navigateur sur pannes, limites, annulation et erreurs locales non retryables.
- Les téléchargements natifs restent sous le contrôle du navigateur, les ZIP gardent leur contrat distinct et aucun état artificiel ne revient au Dashboard. Le test opérateur pendant un déploiement Rise2 réel reste à exécuter avec l’accès serveur ; preuve locale et procédure dans `docs/download-deploy-resume.md`.
- Version +0.0.1 à 2.3.5 ; 3.0.0 reste réservée à la fin et à la validation de l’audit.

## Release 2.3.4 — observabilité et charge HTTP READY

- Métriques HTTP à cardinalité bornée : flux rapides/attentes/pics, admissions, délais de voie rapide et premier corps ASGI, ancienneté, absence de progression, octets ASGI, issues, mesure montante et fraîcheur réelle des échantillons. Compteurs/histogrammes cumulatifs sans identifiants métier.
- Régression de contre-pression reproduite : l’ancien heartbeat inline ne renouvelle pas pendant un `ASGI send` bloqué. Les fichiers READY renouvellent désormais indépendamment, via de courtes sessions SQL ; la perte de lease termine le flux et les chemins fin/erreur/déconnexion nettoient les ressources.
- Runner externe en GET uniquement, lecteurs lents cadencés par bloc sans crédit accumulé pendant l’attente/retry/reconnexion, manifeste de sessions privé, SHA-256, retries 429 bornés, reprises Range/ETag, sondes live/ready et collecte Prometheus optionnelle CPU/disque/qB/HTTP. Runner local isolé sur vraies routes READY avec SQLite et Uvicorn.
- Preuve locale de 45 s : 50 flux simultanés, 45 attentes au pic, 42 fichiers terminés et SHA-256 valides, 4 reprises, 8 gros transferts arrêtés au budget, aucune erreur et aucun slot/lease résiduel. P95 live 4,1 ms et ready 47,8 ms. Rapport dans `docs/evidence/http-load-local-2.3.4.json`.
- Limite explicite : aucun accès d’exécution Rise2 dans cet environnement. La campagne réelle avec PostgreSQL, disque/hôte et qB actif reste requise ; ne pas annoncer une validation de capacité production. Procédure et critères dans `docs/http-download-load.md`.
- Aucun changement de règle d’admission, de conservation des slots rapides, de cadence de 15 secondes ou d’infrastructure. Version +0.0.1 ; 3.0.0 attend la fin des améliorations et validations.

## Release 2.3.3 — authentification asynchrone et concurrence bornée

- Déporte vérification et hachage Argon2 vers un pool dédié : deux calculs simultanés, huit attentes au maximum, délai de réponse de cinq secondes ; réponses de surcharge génériques et retryables. Les calculs commencés conservent leur budget après déconnexion jusqu’à leur fin réelle.
- Couvre connexion, modification de mot de passe/identifiants, provisioning admin/API externe/CLI et seed local ; préserve les mots de passe, sessions et CSRF existants. Libère les connexions SQL avant les calculs et attentes crypto, puis revalide état du compte et hash sous verrou avant écriture. Prépare le hash avant les verrous de quota/idempotence ; les replays externes restent sans calcul crypto.
- Ajoute trente tentatives par IP par fenêtre fixe de soixante secondes avant crypto, indépendamment des noms soumis, en complément des verrous PostgreSQL existants. Cache borné, expiration et refus sans éviction lorsque saturé ; adresse uniquement issue du proxy de confiance existant.
- Diagnostic reproductible : `cd backend && uv run python -m app.benchmark_password_work`. Sur cet environnement local, quatre vérifications et un ticker à 10 ms : gap maximal de boucle 217,1 ms avant, 16,6 ms après ; durée des vérifications 206,9 ms avant, 135,5 ms après. Ces résultats ne constituent pas une mesure de charge Rise2.
- Version incrémentée de 0.0.1 ; la release 3.0.0 attend la fin et la validation des améliorations de l’audit. Aucun changement de topologie ni des règles de téléchargements.

## Release 2.3.2 — retrait du résumé de récupération native

- Supprime le bloc « Récupération locale » du Dashboard et le suivi persistant des téléchargements natifs du navigateur, y compris la restauration après refresh.
- Les liens natifs READY et ZIP déclenchent toujours le navigateur sans fabriquer de statut ; la file réelle du `BrowserDownloadManager` reste visible uniquement dans « Mes téléchargements » pour les transferts gérés.
- Nettoie callbacks, styles, traductions et tests associés à l'ancien résumé. Aucun historique artificiel n'est restauré depuis le navigateur.

## Release 2.3.1 — thème Forest / Green unique

- Le mode clair et la préférence système sont retirés. Les tokens sombres actuels deviennent la palette canonique ; aucune préférence d'OS ni ancienne valeur de stockage local ne peut sélectionner une autre palette.
- Suppression du bootstrap, du provider, du sélecteur, de l'API et des variantes CSS de thème clair ; les contrôles natifs utilisent un `color-scheme: dark` statique.
- Migration `20260925_34` : suppression de la contrainte et de la colonne de thème des comptes existants ; langue, session et `prefers-reduced-motion` inchangés.

## Release 2.3.0 — fichiers ordinaires dans les torrents

- Supprime la whitelist des extensions des fichiers contenus dans les `.torrent` : les jeux Windows, fichiers sans extension et formats inconnus suivent la même validation que les médias.
- Conserve les rejets liés aux métadonnées malformées, chemins dangereux ou ambigus, symlinks, attributs exécutables `x` et inconnus, trackers et limites de taille/nombre ; le contrôle de l'extension du fichier `.torrent` déposé reste applicable.
- Supprime le code d'erreur obsolète lié aux extensions et ses traductions ; couvre les téléchargements unitaires et les envois successifs du batch par tests d'API.

## Release 2.2.15 — cohérence desktop et mobile

- Fermeture de deux media queries dans `styles.css` qui absorbaient les styles suivants lors de la compilation ; test de parsing strict sur toutes les feuilles CSS.
- Tableaux administratifs à colonnes fixes et noms tronqués avec les tooltips existants ; lignes utilisateurs restaurées en véritables lignes de tableau sur desktop.
- Composition mobile unifiée, tri sous forme de boutons qui reviennent à la ligne, surfaces forêt et cases à cocher émeraude, bouton Enregistrer compact.
- Cards de téléchargements réagencées, arrondis du fond sélectionné, espacement des détails et suppression des largeurs minimales de l’aperçu mobile.
- Validation : build et tests frontend ; inspection visuelle locale indisponible (accès localhost bloqué par le navigateur de validation).

## Release 2.2.14 — récupération locale priorisée et File API explicite

- la File System Access API est l'action principale des contenus multi-fichiers et mémorise une
  destination WoS dans le sélecteur natif ; le ZIP n'est plus affiché en parallèle lorsqu'elle est
  disponible ;
- le diagnostic distingue contexte non sécurisé, API absente et refus d'accès au dossier, avec un
  message exploitable notamment sous Brave ;
- les petits fichiers locaux sont servis en priorité par salves bornées, puis le fichier le plus
  ancien est forcé afin qu'un gros fichier ne soit jamais affamé ;
- les permis de flux du navigateur alternent entre travaux concurrents au lieu de laisser un seul
  dossier monopoliser la file locale ;
- les protections Range, snapshots, leases, limites serveur et reprise restent inchangées.

## Release 2.2.13 — concurrence qBittorrent dynamique

- le scheduler mesure le débit réellement observé sur une fenêtre durable et augmente par paliers
  le nombre de torrents actifs lorsque le débit reste sous la cible et qu'une file attend ;
- le plafond dur, les limites par utilisateur, le scheduler pondéré par taille et les protections
  anti-famine restent autoritaires ;
- une fenêtre de cooldown après achèvement évite de perturber l'ensemble actif pendant la nouvelle
  mesure.

## Release 2.2.6 — supervision qBittorrent et NewGreedy

- deux vues dédiées ajoutées à l’administration pour superviser qBittorrent et NewGreedy ;
- inventaire qBittorrent global en lecture seule avec nom, taille, statut, progression et débits
  download/upload agrégés ;
- statistiques NewGreedy exposées avec hash, nom qBittorrent corrélé, statut, DL, UL et ratio ;
- noms longs tronqués avec tooltip et tableaux responsives alignés sur le design du dashboard ;
- rafraîchissement exclusivement manuel, sans polling automatique sur ces deux écrans ;
- endpoints V2 réservés aux administrateurs, bornés et sans exposition des secrets d’intégration.
- API de production raccordée au registre privé en lecture seule et au réseau interne `torrent`
  afin que les vues qBittorrent/NewGreedy puissent joindre leurs services sans secret inline.

## Release 2.2.5 — équité et fiabilité des transferts

- cadence de décision du scheduler qBittorrent séparée de la synchronisation, avec un quantum de
  contrôle configurable à 120 secondes par défaut pour éviter les rotations toutes les 5 secondes ;
- jobs durables ordonnés par priorité explicite : purge/récupération, ajout, jobs inconnus, puis
  synchronisation, sans perdre l'ordre FIFO à priorité égale ;
- `WOS_WORKER_CONCURRENCY` réellement appliquée au démarrage des workers ;
- limite globale durable des flux HTTP en plus de la limite par utilisateur, y compris pour les
  administrateurs ;
- une file FIFO commune et configurable pour les ZIP complets et les ZIP de dossiers ;
- erreurs de saturation et d'indisponibilité distinguées dans le Download Manager ;
- téléchargement direct vers un dossier proposé avec la File System Access API même lorsqu'un ZIP
  de secours est disponible, notamment sous Brave ;
- parcours des gros manifests effectué en flux pour l'arborescence et limité au sous-arbre demandé
  pour la création d'un ZIP de dossier.

## Release 2.2.3 — quota, auth seeds et API externe

- quota global dynamique `WOS_MAX_USER_ACCOUNTS`, appliqué par un provisioning transactionnel
  sérialisé sous PostgreSQL ;
- seed base62 de 25 caractères pour chaque compte, backfill migratoire et consultation propriétaire
  non cacheable ;
- API tierce stable `/api/external/v1` avec clients hashés, scopes, idempotence, rate limiting,
  création d’utilisateur et téléchargements isolés par seed ;
- gestion des clients API et du quota dans l’administration, seed masquée dans les paramètres du
  compte ;
- contrat documenté dans `docs/external-api-v1.md`.
- Production : Rise2.
- Branche de production : `master`.
- Branche d'intégration : `develop`.
- Le dernier `develop` doit toujours être vérifié par `fetch` avant de créer une nouvelle branche.
- `develop_V2` est une branche historique/legacy de la phase de construction V2 ; ne plus l'utiliser pour les nouveaux développements.
- La V1 `1.3.3` reste figée par son tag/release et sur l'ancien serveur uniquement comme solution de rollback pendant la fenêtre de conservation ; elle n'est plus la production active et son ancien déploiement GitHub est désactivé.

## Production Rise2

Le cutover V2 est terminé : le trafic public de `world-of-seeds.fr` arrive sur Rise2.

Runtime principal :

- API FastAPI V2 ;
- deux workers durables ;
- scheduler singleton ;
- PostgreSQL ;
- Redis ;
- qBittorrent ;
- NewGreedy ;
- Caddy ;
- Prometheus, Grafana, node-exporter et cAdvisor.

Le stockage média est un bind mount hôte partagé, hors conteneurs :

- hôte : `/srv/world-of-seeds-v2/data` ;
- conteneurs WOS/qB : `/data` ;
- contenu torrent moderne : `/data/content/<storage-key>` via `SharedContentStore`.

Les données persistantes ne sont pas recréées lors d'un déploiement applicatif standard : PostgreSQL, Redis, qBittorrent et NewGreedy sont préservés et leurs IDs de conteneur sont contrôlés pendant le déploiement.

Etat/configuration opérateur :

- checkout : `/opt/world-of-seeds-v2` ;
- environnement/secrets : `/etc/world-of-seeds-v2/environment` ;
- état NewGreedy : `/srv/world-of-seeds-v2/newgreedy-state` ;
- état du dernier déploiement : `/var/lib/world-of-seeds-v2/deploy/current.env`.

Les sauvegardes off-host et le restore drill Rise2 ont été validés avant le passage en production.

## Release V2 et cutover

La chaîne V2-33 → V2-35 est terminée :

- pilote Rise2 validé ;
- release candidate `2.0.0-rc.1` validée sur Rise2 ;
- rollback applicatif réel validé ;
- release stable `2.0.0` publiée ;
- tag Git `v2.0.0` et GitHub Release créés ;
- DNS production basculé vers Rise2 ;
- HTTPS production validé ;
- observation post-cutover sans rollback.

La release stable historique reste ancrée au SHA applicatif `4814d4636e4a14edfa711c2c78da4dd5a8df2300` et au digest stable `sha256:65559c7f38d2be5c38132c0301f3ca0bae1a214e2398b2ff9fcc4fa7f82b236e`. Les commits OPS ultérieurs conservent la version applicative `2.0.0` tant qu'aucune nouvelle version produit n'est décidée.

## CI/CD production

Le canal de déploiement GitHub Actions vers Rise2 est opérationnel.

Contrat courant :

1. un merge/push sur `master` lance le CI ;
2. aucun déploiement n'est autorisé si le CI échoue ;
3. après un CI `master` vert, le workflow verrouille le `head_sha` exact ;
4. il refuse un run obsolète si `master` a déjà bougé ;
5. il construit l'image `linux/amd64` exacte et la publie sur GHCR ;
6. il déploie uniquement le digest immuable via la commande SSH forcée `wosdeploy` ;
7. migrations, API, workers, scheduler, services persistants et HTTPS sont vérifiés avant succès.

`workflow_dispatch` reste disponible pour un déploiement manuel contrôlé.

Les changements d'infrastructure sensibles qBittorrent/NewGreedy/topologie Compose restent explicitement exclus du canal automatique et demandent une opération dédiée.

## Protections GitHub

`master` et `develop` sont protégées et les règles s'appliquent aussi à l'administrateur du dépôt.

Règles :

- passage par pull request ;
- zéro approbation humaine obligatoire, le dépôt étant maintenu par une seule personne ;
- branche à jour avant merge (`strict`) ;
- conversations de review résolues ;
- force-push interdit ;
- suppression de branche protégée interdite.

Checks requis :

- `backend` ;
- `frontend` ;
- `Container image` ;
- `Dependency and image security` ;
- `Validate restricted Rise2 deploy path`.

## Flux de développement courant

Le flux historique `feature/* → develop_V2` est terminé.

Pour tout nouveau travail :

1. partir du dernier `develop` ;
2. créer une branche dédiée ;
3. développer et lancer les tests ciblés ;
4. ouvrir une PR vers `develop` ;
5. laisser passer les checks obligatoires et résoudre les conversations ;
6. merger dans `develop` ;
7. lorsque le changement doit partir en production, ouvrir une PR `develop → master` ;
8. après merge sur `master`, le déploiement Rise2 est automatique uniquement après CI vert.

Ne jamais pousser directement sur `master` ou `develop`.

## Refonte UX post-2.0

La refonte de l'expérience utilisateur autour d'un Dashboard **torrent-centric** est intégrée dans `develop` (UX-00 à UX-06 terminées).

Décisions validées :

- la page d'accueil utilisateur est le Dashboard de suivi des torrents ;
- l'ancien espace utilisateur Fichiers/Corbeille et le filesystem métier personnel sont retirés ;
- le stockage physique torrent est partagé et reste géré par `SharedContentStore` ;
- `ManagedTorrent` porte la copie physique, `TorrentFile` le manifeste et `TorrentRequest` le droit/abonnement utilisateur ;
- le Dashboard présente synthèses torrents, récupération locale et capacité du stockage partagé ;
- le gestionnaire de torrents adopte une présentation en accordéons ;
- l'ajout `.torrent`, la progression, les états de queue, le WebSocket, l'annulation/désabonnement, la rétention et le manifeste READY existants sont réutilisés ;
- la récupération affichée reste celle du contrôleur navigateur local, pas une file globale autoritaire multi-appareils ;
- le frontend ne contacte jamais qBittorrent ou NewGreedy directement ;
- le thème Forest / Green unique remplace les anciens choix visuels depuis 2.3.1 ; la langue FR/EN reste conservée ;
- le mobile-first et le responsive restent un critère de Definition of Done de chaque PR UX.

### Découpage des tâches

- **UX-00 — TERMINE** : planification documentaire de la refonte.
- **UX-01 — TERMINE** : design system, préférence de langue, login/settings/shell ; les anciens thèmes sont retirés depuis 2.3.1.
- **UX-02 — TERMINE** : nouveau Dashboard et ses cartouches.
- **UX-03 — TERMINE** : gestionnaire de torrents en accordéons.
- **UX-04 — TERMINE** : expérience READY et récupération locale intégrées aux accordéons.
- **UX-05 — TERMINE** : retrait de l'espace utilisateur Fichiers/Corbeille.
- **UX-05B — TERMINE** : suppression du filesystem/workspace utilisateur legacy et consolidation sur le stockage partagé torrent.
- **UX-06 — TERMINE** : harmonisation admin, responsive/accessibilité et nettoyage frontend final.

Le détail, les dépendances et la Definition of Done de chaque tâche sont dans `docs/roadmap-v2.md`.

## Dette / points encore ouverts

### V2-32D — nettoyage NewGreedy à la purge

Toujours BLOQUE et non bloquant pour la production actuelle.

NewGreedy v1.7.5 ne fournit pas de suppression exacte, durable et idempotente par SHA-1 complet. WOS ne doit pas contourner cette limite avec une suppression par préfixe, un reset global ou une édition directe non autoritaire de l'état NewGreedy.

Réévaluer uniquement si NewGreedy expose un contrat de suppression full-hash fiable ou si WOS remplace cette dépendance.

### V1

La V1 ne reçoit plus de développement normal. Elle reste seulement une référence historique et une solution de rollback temporaire tant que cette fenêtre n'est pas explicitement fermée. Ne pas réactiver son ancien workflow de déploiement.

### PR legacy

Les PR encore ouvertes contre `develop_V2` sont historiques et ne doivent pas être fusionnées telles quelles dans le nouveau flux. Toute correction encore pertinente doit être réévaluée puis réimplémentée depuis le `develop` courant.

## UX-01 — historique du design system et des préférences (thèmes retirés en 2.3.1)

- Palettes Light/Dark à tokens partagés.
- L'ancien système de thèmes et sa migration `20260908_23` appartiennent à l'historique UX-01 ; la migration `20260925_34` l'a supprimé en 2.3.1.
- Seule la langue FR/EN reste configurable dans les préférences et le menu compte.
- Primitives natives légères Button, IconButton, Card, Badge, Progress, Accordion et StateMessage.
- Login, credentials, shell et paramètres compacts et mobile-first.

## UX-02 — Nouveau Dashboard utilisateur torrent-centric

- Trois cartouches : activité torrent, récupération locale du navigateur et stockage.
- Agrégation torrent paginée côté frontend sans nouvel endpoint décoratif.
- Erreurs et chargements isolés par cartouche.
- Dashboard comme accueil authentifié.
- Layout mobile-first.

## UX-03 — Gestionnaire de torrents en accordéons

- Liste de cartes `details/summary` avec résumé compact, progression, état, queue, rétention et actions.
- Détails limités aux données déjà exposées.
- Pagination, WebSocket/reconnect/resync, annulation/désabonnement, drag/drop et multi-upload conservés.
- Contrôles tactiles et noms longs bornés.

## UX-04 — Expérience READY et récupération locale

- Manifeste READY chargé à la demande et paginé.
- Mono-fichier en téléchargement natif ; multi-fichiers avec liens individuels et téléchargement complet.
- `RecursiveDownloadController` réutilisé pour la récupération locale.
- Pause/reprise/annulation/progression locale conservées.
- Suppression READY distincte de l'annulation d'une récupération locale.

## UX-05 — Retrait du legacy utilisateur Fichiers/Corbeille

- Le shell authentifié ne propose plus Fichiers ni Corbeille.
- Les anciens liens `?path=...` sont ignorés et nettoyés.
- Les composants frontend user files/trash et les actions de mutation associées ont été retirés.
- Cette étape avait volontairement conservé temporairement les routes/workspaces backend nécessaires à l'ancien contrat de stockage du Dashboard avant UX-05B.

## UX-05B — Suppression du filesystem utilisateur legacy et stockage partagé

- `GET /api/v1/files`, les routes de mutation fichier et les routes trash utilisateur ont été retirées du runtime moderne.
- Les services de browsing/mutation/workspace utilisateur legacy ont été supprimés ; le package `app.files` ne conserve plus que la compatibilité minimale des primitives HTTP de téléchargement READY.
- `WorkspaceManager` n'est plus requis par la création, le renommage ou la suppression de comptes.
- `TrashEntry` et la table `trash_entries` sont retirés via migration Alembic `20260909_24_drop_legacy_user_trash.py` avec downgrade couvert.
- Le Dashboard lit désormais la capacité partagée via `GET /api/v2/storage` au lieu d'utiliser le navigateur de fichiers pour obtenir deux métriques.
- `SharedContentStore` reste l'autorité filesystem du contenu torrent et conserve la disposition physique `/data/content/<storage-key>` ; aucun renommage disque n'est introduit par cette PR.
- Le lifecycle torrent et la déduplication ne changent pas : plusieurs `TorrentRequest` peuvent partager un `ManagedTorrent`; la suppression d'un compte/droit ne détruit pas la copie tant qu'une autre demande active existe, et la dernière référence passe par la purge normale.
- Les primitives HTTP Range/stream nécessaires aux téléchargements READY ont été extraites du filesystem legacy afin de conserver les comportements Range/leases/ZIP existants.
- L'administration n'expose plus de navigation ou métriques de corbeille legacy ; les reliquats frontend non accessibles peuvent être supprimés lors du nettoyage final UX-06 sans rouvrir le backend legacy.
- Les smokes/policies Rise2 ont été adaptés au stockage partagé.

Validation de la PR #157 avant finalisation documentaire :

- migrations upgrade/downgrade/upgrade : vert ;
- Ruff check + format : verts ;
- mypy app/tests : vert ;
- pytest backend : vert ;
- frontend check/tests/build : verts ;
- Dependency and image security : vert ;
- Container image + smokes V2 : vert ;
- V2 Rise2 deploy policy : vert.

## UX-06 — Harmonisation administration, responsive et finition

- Le shell administration et les vues Utilisateurs, Services, Paramètres et Stockage utilisent les primitives partagées `Button`, `Card`, `Badge`, `Progress` et `StateMessage` lorsque pertinent.
- La composition admin est isolée dans `frontend/src/features/admin/admin.css`, avec base mobile-first puis enrichissements tablette/desktop.
- La navigation admin conserve `aria-current`, des cibles tactiles d'au moins 2,75 rem et des contenus longs bornés sans overflow horizontal attendu.
- Le dialogue générique a été extrait de l'ancien namespace `features/files` vers `components/Dialog`, avec gestion du focus, fermeture clavier et tests axe conservés.
- Les derniers reliquats frontend du filesystem/trash utilisateur supprimé par UX-05B ont été retirés : module `features/files`, ancien écran admin corbeille devenu sans backend moderne, contrats API associés et route frontend morte.
- Le Dashboard, le gestionnaire torrent, READY, les récupérations locales et les contrats backend torrent restent inchangés.
- La policy responsive interdit la réintroduction du module filesystem frontend et vérifie le contrat mobile-first de l'administration.

Validation du HEAD fonctionnel UX-06 `dad0ff8a09a2029f557fd135ffd8896629e854b5` avant le commit documentaire final :

- frontend `npm run check` : vert ;
- frontend `npm run test` : vert ;
- frontend `npm run build` : vert ;
- backend Ruff / format / mypy / pytest : verts ;
- `Container image` et smokes V2 : verts ;
- `Dependency and image security` : vert ;
- `V2 Rise2 deploy policy` : vert ;
- aucune conversation de review ouverte sur la PR #158.

## Consolidation post-UX-06

Une passe de cohérence post-refonte retire les derniers contrats morts du filesystem utilisateur : namespace backend `app.files`, schémas V1 Files/Torrents non montés, client HTTP stockage dupliqué, traductions et styles de l'ancienne corbeille. La documentation publique/légale est réalignée sur le stockage partagé. La suppression d'un compte admin exige désormais une confirmation qui décrit les vrais effets sur les `TorrentRequest` et le lifecycle partagé.

Cette consolidation ne modifie ni le schéma PostgreSQL, ni `UserTorrent` historique, ni le stockage physique, ni qBittorrent/NewGreedy, ni le lifecycle torrent.

## Release 2.2.2 — récupération native et privilèges administrateur

- La File System Access API devient le mode principal pour récupérer un torrent multi-fichiers ou un sous-dossier : le manifeste READY est streamé fichier par fichier et l'arborescence est reconstruite dans la destination choisie.
- Le ZIP reste disponible uniquement comme fallback lorsque `showDirectoryPicker()` est absent et lorsque les limites d'archive existantes l'autorisent.
- Les torrents mono-fichier conservent `showSaveFilePicker()` ou le téléchargement HTTP standard selon les capacités du navigateur.
- Les comptes administrateurs n'ont plus de plafond applicatif sur la taille d'un lot `.torrent`; la concurrence du pipeline d'upload reste fixée à trois requêtes.
- La policy de récupération expose explicitement `unlimited: true` et une limite `null` pour les administrateurs. Chaque flux conserve sa lease et tous les rate limits/protections globales restent actifs.
- Les comptes standards conservent les plafonds existants de lot et de flux simultanés.

## Ordonnancement des récupérations HTTP de fichiers

- Correction du déploiement Rise2 : suppression du mapping Compose ajouté pour la capacité HTTP. Le backend conserve sa valeur par défaut de 125 000 000 octets/s (1 Gbit/s). Le Compose redevient identique à celui du dernier déploiement réussi (`76f7b15`), afin que le canal applicatif puisse déployer sans modification d'infrastructure ni assouplissement du garde-fou.

- Les fichiers READY n'ont plus de plafond global de huit connexions : un transfert peut commencer sur chaque appareil. Le contrôleur démarre à cinq flux rapides et ajuste sa cible (de 5 à 64) selon le débit montant total du serveur, sondé toutes les 15 secondes via Prometheus. Rise2 annonce 125 Mo/s de capacité configurable ; sous 80 % il ouvre progressivement des voies en attente, au-delà de 95 % il réduit les admissions futures. Chaque transfert rapide garde sa voie jusqu'à la fin ou la déconnexion ; aucune rotation ni rétrogradation. Si la télémétrie manque, il garde sa dernière décision. Les autres progressent à 1 Kio/s ; deux attentes par compte au maximum sont admises. Les places libérées vont aux attentes selon ancienneté et taille restante avec bonus plafonné pour les petits fichiers.
- Une voie rapide est redistribuée seulement quand son flux se ferme et si la cible d'admission autorise une autre promotion. La taille restante donne un bonus plafonné à cinq minutes, puis l'ancienneté départage les attentes. Une limite de débit global configurée est appliquée aux octets réellement transmis sans réserver une fraction fixe aux clients lents.
- L'API expose `/api/v2/downloads/traffic` pour les compteurs réels des flux du compte et de l'instance. `fast_limit` reflète au minimum le nombre de flux rapides déjà actifs, tandis que `fast_admission_target` représente la cible actuelle d'admission. « Mes téléchargements » affiche cet état serveur en complément des jobs locaux ; aucun état fictif de téléchargement natif n'est persisté.
- Les ZIP conservent leurs plafonds spécifiques et leurs leases sont désormais comptées séparément des fichiers (`20260929_35`). L'ordonnanceur des fichiers dépend de l'invariant Rise2 `WOS_API_PROCESS_COUNT=1` ; une interruption du processus coupe les connexions HTTP, que le client peut reprendre avec `Range`.

## Prochaine tâche

La séquence **UX-00 → UX-06 est terminée**. Aucun chantier UX supplémentaire n'est présumé automatiquement.

Le prochain développement doit repartir d'un besoin produit, maintenance, sécurité ou exploitation explicitement défini, depuis le `develop` courant après intégration de la PR #158. La promotion en production reste une PR séparée `develop → master`.
