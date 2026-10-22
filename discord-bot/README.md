# WoS — bot Discord 2.3.29

Paquet autonome, même serveur et salon que le bot initial. Aucun secret dans les sources.
Il faut d’abord déployer l’API WoS 2.3.29 et sa migration additive des liens de récupération.

## Clé API et installation

Dans Administration → Services → API externes, créer une clé dédiée avec :

- `users:create` ;
- `users:read` ;
- `users:reset-password` ;
- **`downloads:read-discord`**.

Cette dernière permission autorise le bot de confiance à délivrer des liens pour les comptes
Discord associés, y compris ceux créés par une ancienne clé. Les clés existantes ne sont pas
étendues automatiquement. `users:recover` reste l’ancien contrat de livraison initiale ; le
nouveau panneau n’en dépend plus. Le bot transmet toujours l’ID de l’auteur réel du clic,
jamais un pseudo ni un identifiant saisi par un membre.

Sur Rise2, conserver `~/wos-discord-bot/.env` et le volume `bot-data` existants. Remplacer
uniquement `WOS_API_KEY` dans `.env` avec la nouvelle clé. Ne jamais partager les tokens.
Transférer l’archive dans ce répertoire puis :

```bash
cd ~/wos-discord-bot
sudo docker compose stop bot
python3 -m zipfile -e wos-discord-bot-2.3.29.zip .
chmod 600 .env
sudo docker compose config --quiet
sudo docker compose build
sudo docker compose run --rm bot python app.py --check
sudo docker compose up -d
sudo docker compose logs --tail=30 bot
```

Le contrôle `--check` lit la capacité sans créer de compte ni contacter Discord. Il vérifie
`users:read` ; vérifier les autres scopes dans le panneau administrateur. Le démarrage publie
ou met à jour le panneau enregistré. Ne pas supprimer le volume pour une mise à jour.
Permissions du salon : Voir, Envoyer des messages, Intégrer des liens. Aucun intent privilégié.

## Récupération du mot de passe

Le panneau affiche Créer mon compte, Mon compte et Réinitialiser mon mot de passe.
Après confirmation privée, le dernier bouton vérifie le compte Discord associé et demande
un lien personnel valable 15 minutes, utilisable une seule fois. Il fonctionne après une
première connexion et après modification du mot de passe. Le membre choisit le nouveau mot
de passe sur le site ; le bot ne reçoit jamais ce nouveau mot de passe.

Le lien est envoyé en MP, ou dans une réponse éphémère visible uniquement par l’auteur si
les MP échouent. La même livraison est réutilisée après échec, sans demander un autre lien.
Le jeton se trouve dans le fragment `#reset-password=…`, qui n’est pas envoyé au serveur web,
et le frontend l’efface immédiatement de l’historique. Aucun token conservé par le bot.

La demande ne change pas le mot de passe ni les sessions. Après validation : toutes les
sessions sont révoquées, pas de connexion automatique. Username et seed conservés. Un
compte qui doit encore personnaliser son premier accès conserve cette obligation.

Un nouveau lien invalide le précédent. Une minute entre deux émissions, contrôle persistant
par compte et limites API/bot. Expiration, réutilisation, changement de mot de passe, compte
suspendu/supprimé/admin et clé émettrice révoquée rendent la récupération indisponible.
Sans accès au compte Discord associé, ou pour un compte WoS ancien sans liaison Discord,
contacter l’administrateur. Aucune liaison automatique par username.

Le message des accès explique la seed : secret personnel pour les intégrations API, inutile
pour se connecter au site et distinct d’un code de récupération.

## Inscriptions après validation administrative (optionnel)

La limite native est **un compte WoS par ID Discord**, pas une personne physique. Dix comptes
Discord distincts peuvent demander dix comptes WoS si l’inscription reste libre.

Pour contrôler les nouvelles inscriptions :

1. Créer un rôle Discord réservé aux membres validés manuellement, sans permission spéciale.
2. Ne pas permettre aux membres de s’attribuer ce rôle eux-mêmes.
3. Copier son ID et ajouter dans `.env` : `DISCORD_REGISTRATION_ROLE_ID=ID_DU_ROLE`.
4. Recréer le bot : `sudo docker compose up -d --force-recreate bot`.

Le bot refuse alors la création avant tout appel API si l’auteur n’a pas ce rôle. Les boutons
Mon compte et Réinitialiser restent utilisables pour les comptes existants. Ce rôle ne réduit
les abus que si l’attribution est effectivement contrôlée par l’administration. Aucun contrôle
technique ne garantit ici une personne par compte. **Variable vide : inscriptions libres.**

## Validation et exploitation

```bash
python -m unittest discover -s tests -v
sudo docker compose ps
sudo docker compose restart bot
```

Tests avec WoS/Discord simulés, sans compte réel créé. Concurrence, idempotence de création,
confidentialité des réponses, redémarrages du panneau, liens de reset et rôle de validation.
Un test manuel Créer mon compte crée un vrai compte et occupe une place.

Ne pas afficher `docker compose config` sans `--quiet`, ni utiliser `down -v` pour redémarrer.
Le healthcheck contrôle la connexion Discord récente ; il ne garantit pas WoS disponible
à tout instant. Un seul bot par volume, aucun port publié et aucun accès au stockage des torrents.


## Mes téléchargements et alertes privées — 2.3.29

Le bouton **Mes téléchargements** affiche une liste éphémère personnelle, huit demandes par
page, avec Précédent / Suivant / Actualiser. États : attente, téléchargement, absence de
progression, nouvelle tentative différée, prêt, erreur, désabonné et expiré. La position
est une estimation de la file physique partagée, identique au site, pas une promesse de FIFO.
Les titres des autres utilisateurs ne sont jamais affichés. Aucune seed n'est demandée au membre.

La nouvelle permission explicite `downloads:read-discord` est privilégiée : elle permet au
bot de confiance de lire les demandes des comptes Discord associés et leur flux d'alertes.
Elle n'est pas ajoutée automatiquement aux anciennes clés. Les chemins membres utilisent
uniquement l'identité authentifiée de l'auteur du clic ; le flux global est réservé au bot.

Les alertes sont activées par défaut pour les comptes liés actifs non administrateurs :
MP vert quand un fichier est prêt, puis MP rouge quand les deux premiers tiers de la durée
personnelle `ready_at` → `unsubscribe_at` se sont écoulés (exemple : 9 jours → alerte après
6 jours). La liste affiche aussi cet avertissement. Un bouton permet de désactiver/réactiver
ces MP. Les dates sont propres à chaque abonnement, même pour un torrent partagé.

Le bot scrute au maximum 25 abonnements READY par page. Une passe est répétée après
60 secondes, avec 5 secondes entre pages ; un gros backlog allonge donc le délai d'alerte.
Aucun torrent n'est relancé, aucun slot n'est interrompu. Les comptes suspendus/supprimés,
abonnements annulés/expirés et torrents non prêts sont exclus du flux à la lecture.
Les fichiers déjà prêts à l'installation reçoivent aussi un premier rappel ; après une panne,
si l'avertissement est déjà dû, seul le message rouge est envoyé, sans doubler le MP vert.

Le volume existant contient désormais `notifications.sqlite3` : préférences, curseur de
lecture et empreintes de déduplication persistantes, sans seed, mot de passe ou titre.
Une nouvelle période d'accès peut recevoir ses propres alertes. Ne pas effacer ce volume.
Une alerte est réservée durablement avant la tentative Discord pour éviter les doublons après
redémarrage. **Livraison au mieux, une tentative par alerte** : MP bloqués, réponse Discord
ambiguë ou arrêt entre réservation et envoi peuvent empêcher sa réception. Aucun repli public,
aucune boucle de renvoi. La liste privée et le site restent la référence ; autoriser les MP.
