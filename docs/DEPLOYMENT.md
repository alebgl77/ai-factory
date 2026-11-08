# Exploiter AI Factory sur une infrastructure privée

[English](DEPLOYMENT.en.md) · **Français** · [Documentation sur GitHub](https://github.com/alebgl77/ai-factory/blob/main/docs/fr/README.md)

- [Périmètre de prise en charge](#périmètre-de-prise-en-charge)
- [Vérifier et installer une release](#vérifier-et-installer-une-release)
- [Qualifier depuis les sources](#qualifier-depuis-les-sources)
- [Qualifier l'environnement de production](#qualifier-lenvironnement-de-production)
- [Identifier l'état de la pile et protéger les volumes](#identifier-létat-de-la-pile-et-protéger-les-volumes)
- [Répéter la migration PostgreSQL 18](#répéter-la-migration-postgresql-18)
- [Répéter la migration Langfuse 4](#répéter-la-migration-langfuse-4)
- [Préparer la bascule et le retour arrière](#préparer-la-bascule-et-le-retour-arrière)

## Périmètre de prise en charge

| Parcours | Périmètre |
|---|---|
| Démo publique | Tableau de bord et Compagnon de décision sur données synthétiques. La simulation de décisions reste dans le navigateur. [Workbench](https://github.com/alebgl77/ai-factory/blob/main/docs/fr/WORKBENCH.md) |
| Laboratoire de flux | Simulations et réglages dans le navigateur. [Laboratoire de flux](https://alebgl77.github.io/ai-factory/docs/flow-lab.html?lang=fr) |
| Déploiement privé | Une organisation, authentification SSO et périmètres par unité métier. Les profils optionnels sont activés par l'exploitant selon leurs prérequis. [Périmètres BU](BU_SECURITY.md) |
| Aide locale facultative | Les suggestions passent par le modèle interne avec les droits de l'unité métier. Le brief de marché prépare la recherche à partir des éléments fournis par l'utilisateur. [Guide de l’assistance](https://github.com/alebgl77/ai-factory/blob/main/docs/fr/ASSISTANCE_ET_LABORATOIRE.md) |
| Intégrations Workbench | Le SDK fournit l'ingestion CRM et le serveur MCP local prépare des dossiers. Un ERP externe exige un connecteur de brouillons propre à la cible, une clé d'idempotence appliquée par l'ERP et une relecture indépendante. [Contrat d'intégration](https://github.com/alebgl77/ai-factory/blob/main/docs/fr/ADAPTIVE_WORKBENCH.md) |
| Mise en service | Répétez sur la cible une restauration, un passage en file puis une reprise et la réception au SIEM. Conservez les résultats dans le dossier de changement. [Qualification](#qualifier-lenvironnement-de-production) |

Pour la pile simulée, suivez le [démarrage rapide Compose](https://github.com/alebgl77/ai-factory/blob/main/README.fr.md#démarrage-local-sans-aucune-clé). Pour Helm, suivez le [guide Kubernetes](fr/KUBERNETES.md).

## Vérifier et installer une release

Lorsqu'une release fournit le fichier **ai-factory-deploy-${TAG}.tar.gz**, récupérez tous ses fichiers d'actifs dans un même répertoire. Le bundle contient les fichiers Compose et les configurations du commit de la release, ainsi que **release-stack.json** et **docker-compose.release-lock.yml**. L'inventaire indique le commit source, le tag, la plateforme et l'image exacte de chaque service, y compris dans les profils optionnels. L'overlay verrouillé utilise les six images AI Factory signées et les digests de manifeste résolus pour les images des éditeurs tiers. La signature du bundle engage cet inventaire. Les signatures et attestations propres aux images AI Factory se vérifient séparément.

Définissez **TAG** à la version téléchargée. Utilisez Bash, Cosign v3, GNU Coreutils et la CLI GitHub avec `gh attestation`. Connectez-vous avec `gh auth login` ou fournissez `GH_TOKEN` au script de vérification d’images via votre gestionnaire de secrets.

```sh
set -euo pipefail
cosign verify-blob SHA256SUMS --bundle SHA256SUMS.sigstore.json \
  --certificate-identity "https://github.com/alebgl77/ai-factory/.github/workflows/release.yml@refs/tags/$TAG" \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com
sha256sum -c SHA256SUMS
mkdir -- "ai-factory-$TAG"
tar -xzf "ai-factory-deploy-$TAG.tar.gz" -C "ai-factory-$TAG"
cd "ai-factory-$TAG"
while IFS= read -r image; do
  bash .github/scripts/verify_image.sh "$image"
done < ../images.txt
```

Les images du bundle sont publiées pour **linux/amd64**. Vérifiez l'architecture du moteur cible avec **docker version --format '{{.Server.Os}}/{{.Server.Arch}}'** avant de qualifier l'installation.

Préparez **production.env** hors du répertoire d'installation, avec des droits **0600**. La configuration de l'exploitant peut ajouter un overlay de politiques montées en lecture seule. Ajoutez **docker-compose.portfolio.yml** seulement si vous avez configuré le moteur de sous-agents et le fichier privé de son identité. Placez tous les overlays de politiques et du Portefeuille avant **docker-compose.release-lock.yml**. Chargez ce dernier avec l’ultime option **-f** pour donner la priorité aux références d’images signées. Utilisez les mêmes overlays, profils, nom de projet et variables pour le précontrôle, le démarrage et le diagnostic. Pour une installation existante, relevez le nom exact du projet actif avec **docker compose ls --all**. Confirmez-le au besoin sur le conteneur API actif avec le label **com.docker.compose.project**. Affectez ce nom à **FACTORY_COMPOSE_PROJECT** pour conserver les volumes. Pour une première installation, choisissez un nom stable, par exemple **ai-factory**.

```sh
: "${FACTORY_COMPOSE_PROJECT:?Définissez le nom stable du projet Compose}"
factory=(docker compose --project-name "$FACTORY_COMPOSE_PROJECT"
  --env-file /secure/ai-factory/production.env --profile sso
  -f docker-compose.yml
  # Insérez ici les options -f des overlays de politiques et du Portefeuille.
  -f docker-compose.release-lock.yml)
"${factory[@]}" config --format json | python3 scripts/check_production.py
"${factory[@]}" pull
"${factory[@]}" up -d --no-build --wait
"${factory[@]}" ps -a
```

Consignez le résultat du précontrôle sans le JSON de Compose, qui peut contenir des secrets. Si vous changez une image pour un miroir d'entreprise, vérifiez le digest correspondant et documentez l'écart de référence. Les [artefacts publiés de la v1.1.1](https://github.com/alebgl77/ai-factory/releases/tag/v1.1.1) servent de référence historique. Ils précèdent les contrôles actuels de disponibilité et de sortie réseau et ne contiennent pas le bundle de déploiement actuel.

## Qualifier depuis les sources

Avant la publication du bundle signé correspondant, qualifiez le commit source exact dans un environnement isolé. Le commit `27800aa798d7bf21e7bec4ab57ffa4461e0465c3` est le socle testé pour Compose et Kubernetes. Pour un candidat plus récent, épinglez le clone au commit exact à qualifier. Conservez le commit et les références d’images dans le dossier de preuves. Construisez les images depuis ce clone et appliquez les mêmes politiques en lecture seule et le même précontrôle que sur la cible. [CI du socle](https://github.com/alebgl77/ai-factory/actions/runs/42431505134)

```sh
set -euo pipefail
git switch --detach 27800aa798d7bf21e7bec4ab57ffa4461e0465c3
: "${FACTORY_COMPOSE_PROJECT:?Définissez le nom stable du projet Compose}"
factory=(docker compose --project-name "$FACTORY_COMPOSE_PROJECT"
  --env-file /secure/ai-factory/production.env --profile sso
  -f docker-compose.yml)
"${factory[@]}" config --format json | python3 scripts/check_production.py
"${factory[@]}" up -d --build --wait
"${factory[@]}" ps -a
```

Partez d’un clone dédié. Ajoutez vos overlays d’exploitation et vos profils au tableau `factory` avant le précontrôle et le démarrage. Pour installer depuis une release signée, utilisez [le bundle correspondant](#vérifier-et-installer-une-release) une fois la release publiée.

## Qualifier l'environnement de production

Utilisez une VM ou un moteur Docker dédié à une seule frontière de confiance. Alloy découvre les journaux via le proxy Docker. Le proxy autorise certaines lectures par route et le filtre de projet s'exécute dans Alloy. Un Alloy compromis peut demander les métadonnées ou journaux d'autres conteneurs que le moteur expose par ces routes. Sur la cible, inspectez **docker ps -a** et vérifiez que tous les conteneurs appartiennent à la même frontière de confiance. Conservez cette vérification dans les contrôles de capacité et de mise en service. Une installation sur un moteur Docker partagé exige un collecteur ou une frontière de journalisation qui applique l'isolation hors d'Alloy. [Guide de durcissement](SECURITY.md#14-production-hardening-guide)

Le précontrôle vérifie les URL des fournisseurs cloud et VPS, ainsi que les destinations privées pour les modèles sensibles. Utilisez HTTPS pour le cloud et le VPS. L'exception VPS interne concerne le service Compose **ollama-vps** actif sur le réseau interne. Pour un nom DNS externe de GPU privé explicitement approuvé, ajoutez `--private-gpu-host HOST` à `scripts/check_production.py` pour chaque nom autorisé. Qualifiez la résolution DNS, le filtrage sortant et les identités de service sur votre propre infrastructure. [Contrôles de la passerelle](GATEWAY_SECURITY.md)

Dans un environnement de qualification avec des données synthétiques, vérifiez le SSO et le refus d'une identité inter-BU, les chemins de décision et les journaux, les alertes et la réception au SIEM. Vérifiez les routes modèle privées et leurs restrictions réseau sur la cible. Enregistrez les digests déployés, le commit, l'état des services, le résultat du précontrôle, le point de sauvegarde et les mesures RPO/RTO issues d'une restauration. Les jobs CI jetables utilisent un fournisseur OIDC de test et des volumes neufs. La vérification du montage PostgreSQL force une recréation du conteneur, relit une marque synthétique, puis la supprime. Cette preuve couvre la persistance du volume du test.

Contrôlez la disponibilité locale de l'API depuis le conteneur. La sonde vérifie l'accès aux chemins de configuration et d'état ainsi que les réglages du proxy d'identité lorsqu'il est utilisé. Elle répond en HTTP 200 avec **ready** ou en HTTP 503 avec **not_ready** dans le champ **status**. Le démarrage applique les prérequis des profils activés. Les services facultatifs désactivés et les connexions distantes restent hors du périmètre de cette sonde. Utilisez **/api/v1/health** pour le suivi de la vitalité et de la posture existante.

```sh
"${factory[@]}" exec -T api python -c \
  "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/api/v1/ready', timeout=3).read().decode())"
```

Quand le moteur Portfolio est activé, vérifiez la jauge interne **factory_portfolio_runner_oldest_queue_age_seconds** sur une tâche synthétique éligible. L'âge augmente à partir de la dernière mise à jour de la tâche. La jauge revient à zéro quand la file éligible se vide. Une série absente indique que le moteur est désactivé ou que sa file n'a pas pu être lue. Pour le Workbench en mode **protected_demo**, un administrateur global peut consulter le diagnostic en lecture seule **GET /api/v1/workbench/recovery**. Une réconciliation ERP requise se traite selon le contrat du connecteur. [Diagnostic Workbench](https://github.com/alebgl77/ai-factory/blob/main/docs/fr/WORKBENCH.md)

## Identifier l'état de la pile et protéger les volumes

Le socle utilise **PostgreSQL 17.11** avec le montage **postgres-data:/var/lib/postgresql/data** et des images **Langfuse web et worker v3** à la même version. Relevez les références exactes de ClickHouse, Valkey et Grafana dans le `docker-compose.yml` du commit choisi ou dans l’inventaire signé `release-stack.json` d’une installation par release. Conservez le même nom de projet Compose pour retrouver vos volumes existants. Relevez les images réellement exécutées et leurs montages, ainsi que la version des bases et les migrations déjà appliquées. Si PostgreSQL 18, Langfuse 4 ou leurs dépendances ont déjà écrit des données, suspendez le retour aux versions antérieures et utilisez le plan de restauration ou de poursuite de migration issu de la qualification.

Figez les écritures avant une sauvegarde cohérente de référence. Capturez les deux bases de service et les rôles PostgreSQL, les données et métadonnées ClickHouse, le stockage SeaweedFS/S3, les files Valkey et l'état **api-state**. Pour une sauvegarde de volumes, arrêtez les services qui y écrivent ou utilisez des instantanés cohérents du système de stockage. Incluez les données des autres services selon leur politique de reprise, les fichiers Compose et d'exploitation, ainsi que les secrets de chiffrement et d'authentification dans un coffre distinct. Contrôlez le chiffrement, l'intégrité, les droits et la restauration de chaque élément. Les données réelles éventuellement copiées en qualification restent dans une enclave isolée et suivent les règles de rétention de l'entreprise.

Le stockage de production ne sert jamais de cible d'essai. Les commandes de remise en service réutilisent le nom du projet et ses volumes existants. Les commandes de suppression de volumes et de nettoyage du moteur sont exclues du retour au socle.

## Répéter la migration PostgreSQL 18

1. Depuis la pile 17 et après le gel des écritures, produisez un export logique des bases et des rôles. **factory** reprend les arguments Compose exacts de la pile source. **BACKUP_RECIPIENT** contient la clé publique de sauvegarde Age.

   ```sh
   set -euo pipefail
   umask 077
   "${factory[@]}" exec -T postgres pg_dumpall -U postgres --quote-all-identifiers |
     age -r "$BACKUP_RECIPIENT" -o /secure/backup/factory-pg17.sql.age
   sha256sum /secure/backup/factory-pg17.sql.age
   ```

2. Sur le moteur de qualification, démarrez un serveur 18 à partir du digest candidat approuvé et d'un volume neuf, monté sur **/var/lib/postgresql**. Pour l'image officielle 18, vérifiez **PGDATA=/var/lib/postgresql/18/docker**. Utilisez un rôle d'amorçage distinct absent de l'export, par exemple **factory_restore_admin**, et une base initiale **postgres**. Écartez le script d'initialisation Factory lors de ce premier démarrage afin que la restauration crée les rôles et bases d'origine. Vérifiez le chemin avec **SHOW data_directory** avant d'injecter l'export. [Documentation de l'image PostgreSQL](https://github.com/docker-library/docs/blob/master/postgres/README.md#pgdata)
3. Une fois la cible prête et isolée, restaurez l'export. **PG18_REHEARSAL_CONTAINER** désigne le conteneur jetable sur le moteur de qualification. La clé privée Age reste dans le coffre de l'environnement.

   ```sh
   set -euo pipefail
   age -d -i /secure/keys/backup.age /secure/backup/factory-pg17.sql.age |
     docker exec -i "$PG18_REHEARSAL_CONTAINER"        psql -X -v ON_ERROR_STOP=1 -U factory_restore_admin -d postgres
   ```

4. Comparez les bases, propriétaires, droits et volumes de données avec le relevé source. Démarrez LiteLLM et Langfuse dans la pile de qualification connectée au nouveau volume. Vérifiez leur authentification, les traces anciennes et nouvelles, puis la persistance après recréation du conteneur. Mesurez la durée totale de restauration. [Procédure PostgreSQL de changement de version](https://www.postgresql.org/docs/18/upgrading.html)

## Répéter la migration Langfuse 4

1. Utilisez la dernière v3 et vérifiez que toutes les entrées de **Background Migrations** sont terminées. Suspendez l’ingestion et arrêtez les écritures, puis prenez une sauvegarde cohérente de PostgreSQL, ClickHouse, S3 et Valkey. Restaurez-la sur des volumes isolés dans un projet Compose distinct. Vérifiez que Langfuse v3 y relit les traces historiques. Contrôlez les versions minimales et les droits ClickHouse selon le guide éditeur. Réservez des fenêtres distinctes pour PostgreSQL 18 et Valkey.
2. Depuis le commit source exact, utilisez l’overlay facultatif pour passer ensemble **langfuse-web** et **langfuse-worker** en **4.72.0**, épinglée par digest. Il sélectionne le mode d’écriture **legacy** avec propagation OTel native et désactive les parcours de lecture v4 et la reprise historique. Conservez le verrou de release vérifié pour les installations signées. Répétez la migration depuis les sources avec le fichier d’environnement du projet isolé.

   ```bash
   : "${LANGFUSE_REHEARSAL_ENV:?set the isolated Compose environment file}"
   : "${LANGFUSE_REHEARSAL_PROJECT:?set the isolated Compose project name}"
   lf_v4=(docker compose --env-file "$LANGFUSE_REHEARSAL_ENV" --project-name "$LANGFUSE_REHEARSAL_PROJECT"
     -f docker-compose.yml -f docker-compose.langfuse-v4.yml)
   "${lf_v4[@]}" config --quiet
   "${lf_v4[@]}" pull langfuse-web langfuse-worker
   "${lf_v4[@]}" stop langfuse-web langfuse-worker
   "${lf_v4[@]}" up -d --wait --wait-timeout 600 langfuse-web langfuse-worker
   ```

3. Vérifiez les traces historiques et nouvelles du projet restauré avant de planifier le passage en mode **dual**. Contrôlez l’ingestion LiteLLM, la lecture **/api/public/observations** d’AI Factory, **/api/public/traces**, le masquage C3/C4 et les métriques de tokens. Surveillez la propagation du worker et l’espace ClickHouse.
4. Migrez les producteurs et les lecteurs vers les API et SDK compatibles avec **events_only**. Comparez les métriques et des traces représentatives. Choisissez un backfill ou une période de recouvrement selon la rétention et la capacité mesurée. Qualifiez **events_only** après ces contrôles. Une fois les schémas modifiés par v4, restaurez la sauvegarde cohérente antérieure ou appliquez une procédure de retour de schéma éprouvée sur une copie avant de redémarrer v3. [Guide de migration Langfuse v3 vers v4](https://langfuse.com/self-hosting/upgrade/upgrade-guides/upgrade-v3-to-v4)

Sur un hôte Docker jetable, le scénario de CI démarre un projet v3 synthétique vide, migre les deux services et contrôle que les lecteurs historiques retrouvent toujours les traces. Il supprime les volumes de ce projet jetable à la fin.

```bash
COMPOSE_PROJECT_NAME="factory-ci-langfuse-v4-$(date +%s)" \
  bash scripts/smoke_stack.sh --verify-langfuse-v4 --down
```

## Préparer la bascule et le retour arrière

Faites approuver séparément chaque migration avec son plan, la preuve de restauration, la durée d'interruption et les critères d'arrêt. À la bascule, maintenez les écritures gelées jusqu'à la validation des données et du flux synthétique. Si la qualification échoue, reprenez avec les versions vérifiées et les volumes d'origine intacts ou restaurez le point cohérent précédent. Un retour après reprise des écritures demande un plan de réconciliation. Après les migrations de schéma Langfuse 4, appliquez une procédure de restauration ou de rétrogradation validée sur la copie et vérifiez les traces historiques avant la réouverture. Publiez la preuve de validation dans le dossier de changement.
