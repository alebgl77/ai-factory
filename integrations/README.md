# Intégrations (sous-modules git)

Ce répertoire contient **uniquement des sous-modules git** pointant vers des projets externes,
épinglés sur le commit qui a été analysé (licence, dépendances, secrets). Aucun code n'y est copié :
chaque projet reste sous sa propre licence (MIT ou Apache-2.0) et dans son propre dépôt.

| Fichier | Rôle |
|---|---|
| `inventory.yaml` | liste des dépôts GitHub inventoriés et classement humain par pilier de l'usine |
| `secrets-triage.yaml` | tri manuel des résultats de gitleaks (faux positifs justifiés, sans aucune valeur) |
| `<dépôt>/` | sous-module épinglé, décrit par sa fiche `config/catalog/github/<dépôt>.yaml` |

```bash
git submodule update --init                    # récupérer les sous-modules après un clone
python scripts/inventory_github.py --help      # inventaire, scans et décisions
python scripts/check_licenses.py               # contrôle des licences (sous-modules compris)
```

Rapport complet, décisions et dépôts à relicencier : [docs/GITHUB_INTEGRATION.md](../docs/GITHUB_INTEGRATION.md).
Les sous-modules ne sont ni construits ni exécutés par `docker compose` : ils alimentent le
catalogue en libre-service.
