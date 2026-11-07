# Exploiter AI Factory sur une infrastructure privée

- [Périmètre de prise en charge](#périmètre-de-prise-en-charge)
- [Vérifier et installer une release](#vérifier-et-installer-une-release)
- [Qualifier l'environnement de production](#qualifier-lenvironnement-de-production)
- [Identifier l'état de la pile et protéger les volumes](#identifier-létat-de-la-pile-et-protéger-les-volumes)
- [Répéter la migration PostgreSQL 18](#répéter-la-migration-postgresql-18)
- [Répéter la migration Langfuse 4](#répéter-la-migration-langfuse-4)
- [Préparer la bascule et le retour arrière](#préparer-la-bascule-et-le-retour-arrière)

## Périmètre de prise en charge

| Parcours | Périmètre |
|---|---|
| Démo publique | Tableau de bord et Decision Companion sur données synthétiques. La simulation de décisions reste dans le navigateur. [Workbench](WORKBENCH.md#open-the-mobile-companion) |
| Flow Lab | Simulations et réglages dans le navigateur. [Flow Lab](../dashboard/docs/flow-lab.html) |
| Déploiement privé | Une organisation, authentification SSO et périmètres par unité métier. Les profils optionnels sont activés par l'exploitant selon leurs prérequis. [Périmètres BU](BU_SECURITY.md) |
| Aide locale facultative | Les suggestions passent par le modèle interne avec les droits de l'unité métier. Pour un cadrage à partir de sources publiques, préparez un brief de marché à partir de faits fournis par l'utilisateur. [Portfolio](fr/PORTFOLIO.md) |
| Intégrations Workbench | Le SDK fournit l'ingestion CRM et le serveur MCP local prépare des dossiers. Un ERP externe exige un connecteur de brouillons propre à la cible, une clé d'idempotence appliquée par l'ERP et une relecture indépendante. [Contrat d'intégration](ADAPTIVE_WORKBENCH.md) |
| Mise en service | Répétez sur la cible une restauration, un passage en file puis une reprise et la réception au SIEM. Conservez les résultats dans le dossier de changement. [Qualification](#qualifier-lenvironnement-de-production) |

## Vérifier et installer une release

Lorsqu'une release fournit le fichier **ai-factory-deploy-${TAG}.tar.gz**, récupérez tous ses fichiers d'actifs dans un même répertoire. Le bundle contient les fichiers Compose et les configurations du commit de la release, ainsi que **release-stack.json** et **docker-compose.release-lock.yml**. L'inventaire indique le commit source, le tag, la plateforme et l'image exacte de chaque service, y compris dans les profils optionnels. L'overlay verrouillé utilise les six images AI Factory signées et les digests de manifeste résolus pour les images des éditeurs tiers. La signature du bundle engage cet inventaire. Les signatures et attestations propres aux images AI Factory se vérifient séparément.

Définissez **TAG** à la version téléchargée. Exécutez ces commandes avec Bash, Cosign v3 et GNU Coreutils.

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

Préparez **production.env** hors du répertoire d'installation, avec des droits **0600**. Utilisez **docker-compose.release-lock.yml** comme overlay d'images. La configuration de l'exploitant peut ajouter un overlay de politiques montées en lecture seule. Ajoutez **docker-compose.portfolio.yml** seulement si vous avez configuré le moteur de sous-agents et le fichier privé de son identité. Utilisez les mêmes overlays, profils, nom de projet et variables pour le précontrôle, le démarrage et le diagnostic. Pour une installation existante, relevez le nom exact du projet actif avec **docker compose ls --all**. Confirmez-le au besoin sur le conteneur API actif avec le label **com.docker.compose.project**. Affectez ce nom à **FACTORY_COMPOSE_PROJECT** pour conserver les volumes. Pour une première installation, choisissez un nom stable, par exemple **ai-factory**.

```sh
# Ajoutez vos overlays privés et profils à la liste avant de poursuivre.
: "${FACTORY_COMPOSE_PROJECT:?Définissez le nom stable du projet Compose}"
factory=(docker compose --project-name "$FACTORY_COMPOSE_PROJECT"
  --env-file /secure/ai-factory/production.env --profile sso
  -f docker-compose.yml -f docker-compose.release-lock.yml)
"${factory[@]}" config --format json | python3 scripts/check_production.py
"${factory[@]}" pull
"${factory[@]}" up -d --no-build --wait
"${factory[@]}" ps -a
```

Consignez le résultat du précontrôle sans le JSON de Compose, qui peut contenir des secrets. Si vous changez une image pour un miroir d'entreprise, vérifiez le digest correspondant et documentez l'écart de référence. Pour une release antérieure qui fournit uniquement **images.txt**, prenez le commit indiqué par son tag et appliquez [l'overlay historique](fr/PORTFOLIO.md#exploitation).

## Qualifier l'environnement de production

Utilisez une VM ou un moteur Docker dédié à une seule frontière de confiance. Alloy découvre les journaux via le proxy Docker. Le proxy autorise certaines lectures par route et le filtre de projet s'exécute dans Alloy. Un Alloy compromis peut demander les métadonnées ou journaux d'autres conteneurs que le moteur expose par ces routes. Sur la cible, inspectez **docker ps -a** et vérifiez que tous les conteneurs appartiennent à la même frontière de confiance. Conservez cette vérification dans les contrôles de capacité et de mise en service. Une installation sur un moteur Docker partagé exige un collecteur ou une frontière de journalisation qui applique l'isolation hors d'Alloy. [Guide de durcissement](SECURITY.md#14-production-hardening-guide)

Le précontrôle vérifie les URL des fournisseurs cloud et VPS, ainsi que les destinations privées pour les modèles sensibles. Utilisez HTTPS pour le cloud et le VPS. L'exception VPS interne concerne le service Compose **ollama-vps** actif sur le réseau interne. Qualifiez la résolution DNS, le filtrage sortant et les identités de service sur votre propre infrastructure. [Contrôles de la passerelle](GATEWAY_SECURITY.md)

Dans un environnement de qualification avec des données synthétiques, vérifiez le SSO et le refus d'une identité inter-BU, les chemins de décision et les journaux, les alertes et la réception au SIEM. Vérifiez les routes modèle privées et leurs restrictions réseau sur la cible. Enregistrez les digests déployés, le commit, l'état des services, le résultat du précontrôle, le point de sauvegarde et les mesures RPO/RTO issues d'une restauration. Les jobs CI jetables utilisent un fournisseur OIDC de test et des volumes neufs. La vérification du montage PostgreSQL force une recréation du conteneur, relit une marque synthétique, puis la supprime. Cette preuve couvre la persistance du volume du test.

Contrôlez la disponibilité locale de l'API depuis le conteneur. La sonde vérifie l'accès aux chemins de configuration et d'état ainsi que les réglages du proxy d'identité lorsqu'il est utilisé. Elle répond en HTTP 200 avec **ready** ou en HTTP 503 avec **not_ready** dans le champ **status**. Le démarrage applique les prérequis des profils activés. Les services facultatifs désactivés et les connexions distantes restent hors du périmètre de cette sonde. Utilisez **/api/v1/health** pour le suivi de la vitalité et de la posture existante.

```sh
"${factory[@]}" exec -T api python -c \
  "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/api/v1/ready', timeout=3).read().decode())"
```

Quand le moteur Portfolio est activé, vérifiez la jauge interne **factory_portfolio_runner_oldest_queue_age_seconds** sur une tâche synthétique éligible. L'âge augmente à partir de la dernière mise à jour de la tâche. La jauge revient à zéro quand la file éligible se vide. Une série absente indique que le moteur est désactivé ou que sa file n'a pas pu être lue. Pour le Workbench en mode **protected_demo**, un administrateur global peut consulter le diagnostic en lecture seule **GET /api/v1/workbench/recovery**. Une réconciliation ERP requise se traite selon le contrat du connecteur. [Diagnostic Workbench](WORKBENCH.md#api-and-integration-contract)

## Identifier l'état de la pile et protéger les volumes

Le retour au socle éprouvé concerne **PostgreSQL 17.11** et le montage **postgres-data:/var/lib/postgresql/data**, **Langfuse web et worker 3.227.0**, **ClickHouse 26.8.36.3** et **Valkey 8.1.11**. **Grafana 13.0.12** reprend aussi son épinglage précédent. Conservez le même nom de projet Compose pour retrouver vos volumes existants. Relevez les images réellement exécutées et leurs montages, ainsi que la version des bases et les migrations déjà appliquées. Si PostgreSQL 18, Langfuse 4 ou leurs dépendances ont déjà écrit des données, suspendez le retour aux versions antérieures et utilisez le plan de restauration ou de poursuite de migration issu de la qualification.

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

1. Dans une pile isolée, restaurez l'instantané cohérent de PostgreSQL, ClickHouse, S3 et Valkey et vérifiez d'abord que Langfuse 3 relit les traces historiques. Évaluez la version minimale de chaque dépendance selon le guide éditeur et gardez le passage PostgreSQL 18 et le changement de version Valkey dans des fenêtres distinctes.
2. Montez ensemble **langfuse-web** et **langfuse-worker** vers le même tag v4 vérifié. Commencez en mode **legacy** sur les deux services pour vérifier les migrations et les usages v3. Conservez la propagation native en double écriture et gardez le parcours v4 et le backfill désactivés à cette étape, conformément au guide éditeur.
3. Passez en mode **dual** lors d'une étape dédiée. Vérifiez l'ingestion LiteLLM, la lecture **/api/public/observations** utilisée par AI Factory, la lecture **/api/public/traces** utilisée par le smoke test, le masquage C3/C4 et les métriques de tokens. Surveillez la propagation du worker et l'espace ClickHouse.
4. Migrez les producteurs et les lecteurs vers les API et SDK compatibles avec **events_only**, y compris les deux consommateurs mentionnés. Validez la comparabilité des métriques et des échantillons de traces. Choisissez un backfill ou une période de recouvrement d'après la rétention et la capacité mesurée. Qualifiez le passage à **events_only** uniquement après ces vérifications. [Guide de migration Langfuse v3 vers v4](https://langfuse.com/self-hosting/upgrade/upgrade-guides/upgrade-v3-to-v4)

## Préparer la bascule et le retour arrière

Faites approuver séparément chaque migration avec son plan, la preuve de restauration, la durée d'interruption et les critères d'arrêt. À la bascule, maintenez les écritures gelées jusqu'à la validation des données et du flux synthétique. Si la qualification échoue, reprenez avec les versions vérifiées et les volumes d'origine intacts ou restaurez le point cohérent précédent. Un retour après reprise des écritures demande un plan de réconciliation. Après les migrations de schéma Langfuse 4, appliquez une procédure de restauration ou de rétrogradation validée sur la copie et vérifiez les traces historiques avant la réouverture. Publiez la preuve de validation dans le dossier de changement.
