# Notifications d'alertes Rise2 (Alertmanager)

Prometheus évalue les règles de `monitoring/prometheus/alerts.yml` et les transmet à
Alertmanager, qui notifie :

| Gravité | Destination | Rappel tant que l'alerte dure |
|---|---|---|
| `critical` | Discord **et** e-mail | toutes les 4 h |
| `warning` | Discord | toutes les 12 h |
| `none` (`Watchdog`) | nulle part, voir plus bas | — |

Les alertes résolues sont aussi notifiées. Les alertes sont regroupées par nom et par instance.

## Ce que le dépôt contient, et ce qui reste privé

Le routage est versionné dans `monitoring/alertmanager/alertmanager.yml.template`, sans
aucune valeur personnelle. Sur Rise2, `scripts/rise2_v2_alertmanager_render.py` produit la
configuration réelle dans `/etc/world-of-seeds-v2/alertmanager/alertmanager.yml` à partir de
trois fichiers privés du même dossier :

| Fichier | Contenu |
|---|---|
| `settings.json` | serveur SMTP, expéditeur, identifiant SMTP, destinataires |
| `discord_webhook_url` | l'URL du webhook Discord, **sans retour à la ligne final** |
| `smtp_password` | le mot de passe SMTP, **sans retour à la ligne final** |

Le webhook et le mot de passe ne sont jamais recopiés dans la configuration : Alertmanager
les lit dans leur fichier au moment d'envoyer. Le générateur refuse une valeur mal formée,
un fichier lisible par tous et un lien symbolique ; il donne ensuite au groupe `nobody`
(65534), celui du conteneur, la lecture seule sur le dossier.

Alertmanager tourne dans l'overlay d'observabilité, sur le réseau interne `monitoring` (pour
recevoir les alertes de Prometheus) et sur un réseau de sortie dédié `alerting-egress` (pour
joindre Discord et le serveur SMTP). Il ne publie aucun port sur l'hôte.

## Mise en place sur Rise2

1. Créer un webhook dans un salon Discord privé (Paramètres du salon → Intégrations →
   Webhooks) et un compte SMTP dédié aux alertes.
2. En root, préparer le dossier privé :

   ```bash
   install -d -m 0750 /etc/world-of-seeds-v2/alertmanager
   cd /etc/world-of-seeds-v2/alertmanager
   umask 077
   printf '%s' 'https://discord.com/api/webhooks/…' > discord_webhook_url
   printf '%s' 'mot-de-passe-smtp' > smtp_password
   cat > settings.json <<'JSON'
   {
     "smtp_smarthost": "smtp.exemple.fr:587",
     "smtp_from": "World of Seeds <alertes@exemple.fr>",
     "smtp_auth_username": "alertes@exemple.fr",
     "email_to": ["toi@exemple.fr"]
   }
   JSON
   ```

   Le serveur SMTP doit accepter STARTTLS : `smtp_require_tls` est forcé à `true`.
3. Appliquer l'observabilité : `sudo scripts/rise2_v2_observability_apply.sh`. Le script
   génère la configuration, la fait valider par `amtool` dans l'image épinglée, puis recrée
   Alertmanager, Prometheus et les exporters. Sans le dossier privé, il s'arrête avant toute
   modification des conteneurs.
4. Tester la réception de bout en bout depuis le conteneur :

   ```bash
   cd /opt/world-of-seeds-v2
   docker compose --env-file /etc/world-of-seeds-v2/environment \
     -f deploy/compose.rise2.v2.yaml -f deploy/compose.rise2.observability.v2.yaml \
     exec alertmanager amtool alert add WOSTestNotification severity=critical \
     --annotation=summary="Test de notification" --alertmanager.url=http://127.0.0.1:9093
   ```

   Le message doit arriver sur Discord et par e-mail dans la minute (après `group_wait`),
   puis une notification de résolution environ cinq minutes plus tard.

Pour changer un destinataire ou le webhook, modifier le fichier privé puis relancer le
script d'application.

## Surveiller le surveillant

- `WOSAlertmanagerNotificationsFailing` (warning) signale une intégration qui n'arrive plus
  à envoyer ; si Discord tombe, elle part encore par e-mail pour les alertes critiques.
- `Watchdog` est toujours active : tant que l'on reçoit son signal, la chaîne Prometheus →
  Alertmanager fonctionne. Elle est routée vers un récepteur muet tant qu'aucun service de
  heartbeat externe (par exemple healthchecks.io) n'est branché ; c'est la seule façon d'être
  prévenu si Rise2, Prometheus ou Alertmanager s'arrêtent entièrement. Le brancher demande
  un récepteur `webhook_configs` vers l'URL de ping du service, dans le modèle de routage.

## Vérifications en CI

`scripts/check_alertmanager_config.sh` génère la configuration avec des valeurs d'exemple,
la valide avec `amtool check-config` dans l'image épinglée et vérifie que `critical`,
`warning` et `Watchdog` arrivent aux bons récepteurs.
