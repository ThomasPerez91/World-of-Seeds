# Préparation de la migration du stockage

## État de coexistence

Tant que la migration qBittorrent n'a pas été réalisée, les deux organisations restent
volontairement côte à côte sur l'hôte :

```text
/srv/seedbox/
├── downloads/                  # chemin qBittorrent historique, non déplacé
├── watch/                      # chemin qBittorrent historique, non déplacé
└── users/
    ├── admin/
    │   ├── downloads/
    │   └── watch/
    └── <username>/
        ├── downloads/
        └── watch/
```

World of Seeds crée uniquement les nouvelles racines sous `users/`. Il ne lit, ne déplace
et ne supprime rien dans les chemins historiques pendant cette étape.

## Invariants applicatifs

- le nom du dossier est le nom de connexion normalisé ;
- un dossier contient initialement uniquement `downloads` et `watch` ;
- les composants sont ouverts depuis des descripteurs de répertoire avec `O_NOFOLLOW` ;
- un lien symbolique ne peut jamais être adopté comme espace utilisateur ;
- une collision avec un fichier, un dossier ou un lien existant bloque la création ;
- si l'insertion SQL échoue, seul le nouvel espace encore vide peut être retiré ;
- si le changement de nom SQL échoue, le dossier reprend son ancien nom ;
- aucune compensation ne supprime un dossier contenant des données.

## Migration qBittorrent différée

Le déplacement des téléchargements existants sera fait pendant le déploiement accompagné,
après l'intégration qBittorrent. La procédure devra :

1. inventorier les torrents et leurs chemins de contenu réels ;
2. choisir explicitement le compte propriétaire de chaque contenu ;
3. demander à qBittorrent de déplacer les données vers la nouvelle destination ;
4. attendre la fin du déplacement et forcer une nouvelle vérification si nécessaire ;
5. confirmer que les torrents seedent encore avant de traiter le suivant ;
6. conserver les répertoires historiques tant qu'ils ne sont pas vérifiés vides.

Un simple `mv /srv/seedbox/downloads ...` est exclu : qBittorrent conserverait ses anciens
chemins et les torrents passeraient en erreur.

## Permissions à vérifier au déploiement

Le couple `APP_UID:APP_GID` du conteneur doit pouvoir créer et renommer des entrées sous
`/srv/seedbox/users`, sans recevoir d'accès supplémentaire au reste du serveur. Les droits
seront inspectés avant le premier démarrage ; aucune modification récursive des permissions
de `/srv/seedbox` ne sera faite sans inventaire et sauvegarde.
