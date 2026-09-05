# V2-33 — runner des gates de charge Rise2

Ce runner automatise uniquement les gates `load_1_slot` et `load_2_slots` de V2-33. Il ne modifie
ni le runtime applicatif, ni le ledger du pilote, ni la V1. Il doit être exécuté depuis un tooling
extrait du commit exact et vert de la draft #103, tandis que la checkout opérationnelle Rise2 reste
sur le SHA `develop_V2` réellement testé.

## Pourquoi un runner dédié

Les fixtures du test scheduler sont volontairement synthétiques et n'ont pas de vraie source de
téléchargement. Les workers V2 exécutent aussi le sync périodique qBittorrent et peuvent donc leur
appliquer la logique anti-stall si on laisse la campagne vivre entre la préparation et la mesure.
Le runner évite cette contamination en gardant `worker` et `scheduler` officiels arrêtés depuis
avant `prepare` jusqu'à la fin des deux gates, tout en laissant PostgreSQL, Redis, qBittorrent,
NewGreedy, l'API et l'observabilité disponibles.

## Extraction du tooling vert

Depuis `/opt/world-of-seeds-v2` :

```bash
runtime_revision="$(git rev-parse HEAD)"
git fetch origin feat/v2-rise2-pilot
tool_revision="$(git rev-parse FETCH_HEAD)"
tool_root="/var/lib/world-of-seeds-v2/pilot-load-tools/$tool_revision"

sudo install -d -m 0755 -o root -g root "$tool_root"

for file in \
  rise2_v2_scheduler_load.py \
  rise2_v2_scheduler_load_runtime.py \
  rise2_v2_run_load_gates.py
do
  git show "$tool_revision:scripts/$file" | sudo tee "$tool_root/$file" >/dev/null
  sudo chmod 0555 "$tool_root/$file"
  test "$(git hash-object "$tool_root/$file")" = \
    "$(git rev-parse "$tool_revision:scripts/$file")"
done

test "$(git rev-parse HEAD)" = "$runtime_revision"
```

Ne pas exécuter cette procédure tant que la CI du `tool_revision` n'est pas verte.

## Exécution

Le runner génère un identifiant de campagne borné si `--campaign` n'est pas fourni. Il vérifie le
SHA runtime, la propreté de la checkout, le ledger `preflight` + `backup_restore`, la révision OCI de
l'API et les blobs exacts des trois outils avant toute mutation.

```bash
sudo python3 "$tool_root/rise2_v2_run_load_gates.py" \
  --tool-revision "$tool_revision" \
  --runtime-revision "$runtime_revision"
```

La séquence est ensuite automatique : arrêt des workers et du scheduler officiels, vérification que
la campagne n'existe pas, préparation de 100 comptes / 209 torrents, 5 minutes de chauffe + 30
minutes mesurées à un slot, collecte Prometheus, puis même séquence à deux slots. Les workers et le
scheduler sont redémarrés dans le chemin normal comme dans le chemin d'erreur.

Les preuves privées sont écrites sous
`/var/lib/world-of-seeds-v2/pilot/<runtime_revision>/load-<campaign>/` : rapports bruts des deux
gates, agrégats Prometheus, agrégats combinés, fenêtres temporelles et résumé final. Le runner
n'enregistre volontairement pas le ledger ; le résultat doit être relu avant `record`.

Un gate de charge n'est `passed` que si les durées minimales sont respectées, les compteurs
`famine`, `duplicate`, `corruption` et `unexpected_transition` restent à zéro et le p95 scheduler
reste strictement inférieur à son intervalle. Les seuils CPU > 80 % et iowait > 20 % restent des
signaux d'investigation, pas des échecs automatiques du gate.

## En cas d'interruption

Ne pas relancer aveuglément la même campagne. Le runner préserve les preuves déjà écrites et remet
le contrôle normal en service. Une campagne préparée mais incomplète doit d'abord être inspectée et
nettoyée de façon déterministe avant une nouvelle campagne, afin qu'elle ne soit pas détectée comme
candidat scheduler hors campagne lors du prochain essai.

Ne jamais utiliser `--remove-orphans` : les exporters de l'overlay observabilité sont des
conteneurs légitimes du projet Rise2.
