# Operate AI Factory on private infrastructure

**English** · [Français](DEPLOYMENT.md) · [Documentation on GitHub](https://github.com/alebgl77/ai-factory/blob/main/docs/README.md)

- [Supported paths](#supported-paths)
- [Verify and install a release](#verify-and-install-a-release)
- [Qualify from source](#qualify-from-source)
- [Qualify the target environment](#qualify-the-target-environment)
- [Identify stack versions and protect volumes](#identify-stack-versions-and-protect-volumes)
- [Rehearse PostgreSQL 18 migration](#rehearse-postgresql-18-migration)
- [Rehearse Langfuse 4 migration](#rehearse-langfuse-4-migration)
- [Prepare cutover and rollback](#prepare-cutover-and-rollback)

## Supported paths

| Path | Scope |
|---|---|
| Public demo | Dashboard and Decision Companion with synthetic data and decisions simulated in the browser. [Workbench](https://github.com/alebgl77/ai-factory/blob/main/docs/WORKBENCH.md#open-the-mobile-companion) |
| Flow Lab | Browser-based graph and scenario simulation. [Flow Lab](https://alebgl77.github.io/ai-factory/docs/flow-lab.html?lang=en) |
| Private deployment | One organization, SSO and BU scope. Enable each optional integration after meeting its prerequisites. [BU security](BU_SECURITY.md) |
| Local assistance | Suggestions use the internal model under the requester's BU permissions. A market brief uses supplied facts to prepare research. [Assistance](https://github.com/alebgl77/ai-factory/blob/main/docs/ASSIST_AND_FLOW_LAB.md) |
| Workbench integrations | The SDK polls supported CRMs and local MCP prepares cases. An external ERP requires a target-specific draft connector, target-enforced idempotency and independent read-back. [Integration contract](https://github.com/alebgl77/ai-factory/blob/main/docs/ADAPTIVE_WORKBENCH.md) |
| Go-live qualification | Rehearse restoration, queue and recovery, and SIEM delivery on the target. Record the results in the change record. [Qualification](#qualify-the-target-environment) |

For the simulated stack, follow the [Compose quick start](https://github.com/alebgl77/ai-factory#local-quick-start-no-key-at-all). For Helm, see [Kubernetes installation](KUBERNETES.md).

## Verify and install a release

When the release includes **ai-factory-deploy-${TAG}.tar.gz**, download all its assets into one directory. The archive contains the release commit's Compose files and configuration, **release-stack.json** and **docker-compose.release-lock.yml**. The inventory records the commit, tag, platform and exact service images, including optional profiles. The overlay pins the six signed AI Factory images and the resolved third-party manifest digests. The signed checksums cover the archive and its inventory. Verify image signatures and attestations separately.

Set **TAG** to the downloaded version. Use Bash, Cosign v3, GNU Coreutils and the GitHub CLI with `gh attestation`. Sign in with `gh auth login` or provide `GH_TOKEN` through your secret manager for the image verification script.

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

The bundled images target **linux/amd64**. Check with **docker version --format '{{.Server.Os}}/{{.Server.Arch}}'**. Store **production.env** outside the installation directory with mode **0600**. Add read-only policy overlays as needed, and **docker-compose.portfolio.yml** only when you have configured the runner and its private binding file. Put all operator policy and Portfolio overlays before **docker-compose.release-lock.yml**, which must be the final **-f** file so the signed image pins take precedence. Keep the overlays, profiles, project name and variables consistent through preflight, startup and diagnosis. On an existing installation, find the active project name with **docker compose ls --all** and confirm the API container's **com.docker.compose.project** label if needed. Set **FACTORY_COMPOSE_PROJECT** to that exact name to retain the volumes. For a new installation, pick a stable name such as **ai-factory**.

```sh
: "${FACTORY_COMPOSE_PROJECT:?Set the stable Compose project name}"
factory=(docker compose --project-name "$FACTORY_COMPOSE_PROJECT"
  --env-file /secure/ai-factory/production.env --profile sso
  -f docker-compose.yml
  # Insert optional policy and Portfolio -f overlays here.
  -f docker-compose.release-lock.yml)
"${factory[@]}" config --format json | python3 scripts/check_production.py
"${factory[@]}" pull
"${factory[@]}" up -d --no-build --wait
"${factory[@]}" ps -a
```

Record the result without the resolved Compose JSON, which can contain secrets. If using a company image mirror, verify the corresponding digest and record the change. The published [v1.1.1 assets](https://github.com/alebgl77/ai-factory/releases/tag/v1.1.1) are historical references. They predate current readiness and egress hardening and carry no current deployment bundle.

## Qualify from source

Before a matching signed bundle ships, qualify the exact source commit in an isolated environment. Commit `27800aa798d7bf21e7bec4ab57ffa4461e0465c3` is the demonstrated Compose and Kubernetes baseline. For a newer candidate, pin the checkout to the exact commit being qualified and retain the commit and image identities in the evidence. Build from that checkout and use the same read-only policies and preflight as for the target. [Baseline CI](https://github.com/alebgl77/ai-factory/actions/runs/42431505134)

```sh
set -euo pipefail
git switch --detach 27800aa798d7bf21e7bec4ab57ffa4461e0465c3
: "${FACTORY_COMPOSE_PROJECT:?Set the stable Compose project name}"
factory=(docker compose --project-name "$FACTORY_COMPOSE_PROJECT"
  --env-file /secure/ai-factory/production.env --profile sso
  -f docker-compose.yml)
"${factory[@]}" config --format json | python3 scripts/check_production.py
"${factory[@]}" up -d --build --wait
"${factory[@]}" ps -a
```

Start from a dedicated clone. Add your operator overlays and profiles to the `factory` array before running preflight and startup. For a signed installation, use the [matching bundle procedure](#verify-and-install-a-release) once that release is published.

## Qualify the target environment

Use a VM or Docker engine dedicated to one trust boundary. Alloy discovers logs through a Docker proxy that allows specific read routes. Alloy applies the project filter, so a compromised Alloy instance could read metadata or logs of other containers exposed through the same routes. Inspect **docker ps -a** on the target and verify that all containers share the intended boundary. Record the check. A shared Docker engine requires a collector or logging boundary that enforces isolation outside Alloy. [Hardening](SECURITY.md#14-production-hardening-guide)

Preflight checks the cloud and VPS provider URLs and the destinations for sensitive models. Use HTTPS for cloud and VPS. The exception for the internal VPS applies to the active Compose **ollama-vps** service on its internal network. For an explicitly approved external private GPU DNS name, repeat `--private-gpu-host HOST` as needed on `scripts/check_production.py`. Qualify DNS, egress filtering and service identities on the target. [Gateway controls](GATEWAY_SECURITY.md)

Use synthetic data to test SSO, cross-BU denial, decision paths and their logs, alerting and SIEM receipt. Verify private model routes and their network restrictions. Record deployed digests, source commit, service health, preflight, backup point and measured RPO and RTO. Disposable CI uses a test identity provider and fresh volumes. Its PostgreSQL test recreates the container, reads a synthetic marker and removes it, verifying persistence of the test volume.

Check local API readiness inside the container. The probe checks configuration and state paths and identity proxy settings when enabled. It returns HTTP 200 with **ready** or HTTP 503 with **not_ready** in **status**. Startup checks enabled-profile prerequisites. Test remote dependencies separately and use **/api/v1/health** for liveness and posture.

```sh
"${factory[@]}" exec -T api python -c \
  "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/api/v1/ready', timeout=3).read().decode())"
```

With the Portfolio runner enabled, check **factory_portfolio_runner_oldest_queue_age_seconds** on an eligible synthetic task. Age is measured from the last task update and goes back to zero when the eligible queue drains. A missing series indicates the runner is disabled or its queue could not be read. In Workbench **protected_demo** mode, a global administrator can query **GET /api/v1/workbench/recovery** read-only. Follow the connector contract for a pending ERP reconciliation. [Workbench diagnostics](https://github.com/alebgl77/ai-factory/blob/main/docs/WORKBENCH.md#api-and-integration-contract)

## Identify stack versions and protect volumes

The validated baseline uses **PostgreSQL 17.11** with **postgres-data:/var/lib/postgresql/data**, **Langfuse web and worker 3.227.0**, **ClickHouse 26.8.36.3**, **Valkey 8.1.11** and **Grafana 13.0.12**. Retain the Compose project name to keep the volumes. Record the running images, mounts, database versions and applied migrations. If PostgreSQL 18, Langfuse 4 or their dependencies have already written data, suspend the return to older versions and use the qualified restore or forward migration plan.

Freeze writes before a consistent baseline backup. Capture the two service databases and PostgreSQL roles, ClickHouse data and metadata, SeaweedFS/S3 storage, Valkey queues and **api-state**. Stop writing services or use consistent storage snapshots for volume backups. Preserve other service data per recovery policy, together with Compose and operator configuration. Keep encryption and authentication secrets in a separate vault. Check encryption, integrity, permissions and restoration. Keep any real data used for qualification in an isolated enclave under the organization's retention rules. Rehearse on separate volumes. Leave volume deletion and engine cleanup outside the rollback procedure.

## Rehearse PostgreSQL 18 migration

1. From PostgreSQL 17, freeze writes and export the databases and roles. **factory** uses the exact source Compose arguments. **BACKUP_RECIPIENT** holds the public Age key.

   ```sh
   set -euo pipefail
   umask 077
   "${factory[@]}" exec -T postgres pg_dumpall -U postgres --quote-all-identifiers |
     age -r "$BACKUP_RECIPIENT" -o /secure/backup/factory-pg17.sql.age
   sha256sum /secure/backup/factory-pg17.sql.age
   ```

2. Start PostgreSQL 18 on the isolated qualification engine with the approved candidate digest and a fresh volume mounted on **/var/lib/postgresql**. Verify **PGDATA=/var/lib/postgresql/18/docker** for the official image. Choose a bootstrap role absent from the export, such as **factory_restore_admin**, and database **postgres**. Omit the Factory initialization script at the first startup so the restore creates the original roles and databases. Check **SHOW data_directory** first. [Image documentation](https://github.com/docker-library/docs/blob/master/postgres/README.md#pgdata)
3. Restore into the ready, isolated container named by **PG18_REHEARSAL_CONTAINER**. Keep the private Age key in the qualification vault.

   ```sh
   set -euo pipefail
   age -d -i /secure/keys/backup.age /secure/backup/factory-pg17.sql.age |
     docker exec -i "$PG18_REHEARSAL_CONTAINER"        psql -X -v ON_ERROR_STOP=1 -U factory_restore_admin -d postgres
   ```

4. Compare databases, owners, permissions and data volume to the source. Connect LiteLLM and Langfuse in the qualification stack and test authentication, historical and new traces, and persistence across container recreation. Measure the full restoration time. [PostgreSQL upgrade procedure](https://www.postgresql.org/docs/18/upgrading.html)

## Rehearse Langfuse 4 migration

1. In isolation, restore the consistent PostgreSQL, ClickHouse, S3 and Valkey snapshot. Confirm that Langfuse 3 reads historical traces. Check the vendor's dependency minima. Schedule the PostgreSQL 18 and Valkey changes in separate windows.
2. Upgrade **langfuse-web** and **langfuse-worker** to the same verified v4 tag. Start both in **legacy** mode to verify migrations and v3 usage. Keep native dual-write propagation enabled and leave the v4 path and backfill disabled for this stage.
3. Move to **dual** in a separate stage. Check LiteLLM ingestion, AI Factory's **/api/public/observations** reader and the smoke test's **/api/public/traces** reader, C3/C4 masking and token metrics. Monitor worker propagation and ClickHouse capacity.
4. Move producers and readers, including both above, to API and SDK versions supporting **events_only**. Compare metrics and representative traces. Choose backfill or a transition window based on retention and measured capacity. Qualify **events_only** after these checks. [Langfuse migration guide](https://langfuse.com/self-hosting/upgrade/upgrade-guides/upgrade-v3-to-v4)

## Prepare cutover and rollback

Approve each migration separately with the plan, restore evidence, outage duration and stop criteria. Keep writes frozen until data checks and the synthetic workflow pass. If qualification fails, resume on verified versions and intact original volumes or restore the previous consistent point. A return after writes resume requires reconciliation. After Langfuse 4 schema migration, use a restore or downgrade procedure tested on the copy and verify historical traces before reopening access. Keep the validation evidence in the change record.
