# Migration des espaces utilisateurs

## Organisation cible

Les espaces World of Seeds sont placés directement sous la racine montée :

```text
/srv/seedbox/
├── downloads/                  # chemin qBittorrent historique, inchangé
├── watch/                      # chemin qBittorrent historique, inchangé
├── admin/
│   └── downloads/
├── <username>/
│   └── downloads/
└── .trash/                    # hors du navigateur, isolée par identifiant utilisateur
```

Le navigateur d’un utilisateur est ancré dans `/data/<username>`. La présence des
répertoires qBittorrent historiques à côté de cet espace ne les rend donc pas accessibles.

## Migration depuis l’ancienne organisation

Les installations ayant déjà créé `/srv/seedbox/users/<username>` doivent exécuter,
avec PostgreSQL démarré et la nouvelle image construite :

```bash
docker compose run --rm app python -m app.cli migrate-workspaces
```

La commande charge la liste des comptes depuis PostgreSQL puis traite chaque espace :

1. validation du nom comme composant unique ;
2. ouverture de `/data`, `/data/users` et du workspace avec `O_NOFOLLOW` ;
3. contrôle de la présence de `downloads` ;
4. refus si `/data/<username>` existe déjà ;
5. renommage atomique sans écrasement vers `/data/<username>` ;
6. vérification du périphérique et de l’inode après déplacement ;
7. suppression du `watch` propre à l'utilisateur uniquement s'il est réellement vide ;
8. suppression de `/data/users` seulement s'il est réellement vide.

La commande peut être rejouée : un espace déjà migré et valide est simplement signalé.
Si l’ancien et le nouveau chemin existent simultanément, elle s’arrête sans choisir ni
supprimer l’un des deux.

## Invariants applicatifs

- le nom du dossier correspond exactement au nom de connexion ;
- l’unicité des noms est vérifiée en base sans tenir compte de la casse ;
- un dossier contient initialement `downloads`, conformément à
  `backend/app/files/workspace_structure.json` ;
- les composants sont ouverts depuis des descripteurs de répertoire avec `O_NOFOLLOW` ;
- un lien symbolique ne peut jamais être adopté comme espace utilisateur ;
- une collision avec un fichier, un dossier ou un lien bloque la création ou le renommage ;
- si l’insertion SQL échoue, seul le nouvel espace encore vide peut être retiré ;
- si le changement de nom SQL échoue, le dossier reprend son ancien nom ;
- aucune compensation ne supprime un dossier contenant des données.
- un ancien `watch` utilisateur non vide est conservé, mais n'est ni listé ni ouvrable
  depuis l'API fichiers ;
- la corbeille reste sous `/data/.trash/<user-id>` et n'est jamais un enfant du workspace.

## Données qBittorrent historiques

La commande ne touche jamais à `/srv/seedbox/downloads` ou `/srv/seedbox/watch`. Leur
migration sera réalisée plus tard avec l’API qBittorrent afin de préserver les chemins de
contenu et le seeding. Un simple déplacement manuel de ces données reste exclu.

## Permissions à vérifier au déploiement

Le couple `APP_UID:APP_GID` doit pouvoir créer et renommer des entrées directement sous
`/srv/seedbox`, sans accès au reste du serveur. Aucun `chown -R` ou `chmod -R` ne doit être
appliqué sans inventaire préalable des permissions et des données existantes.
