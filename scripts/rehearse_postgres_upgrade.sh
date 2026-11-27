#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# Restore synthetic PostgreSQL 17 roles and data into a fresh PostgreSQL 18 volume.
# Usage: scripts/rehearse_postgres_upgrade.sh factory-ci-postgres18-<unique suffix>
set -euo pipefail
umask 077

project="${1:-}"
if [ "$#" != 1 ] || ! [[ "$project" =~ ^factory-ci-postgres18-[a-z0-9][a-z0-9-]{5,40}$ ]]; then
  echo 'Use one unique factory-ci-postgres18-* project name.' >&2
  exit 2
fi
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root"
for tool in docker python3; do
  command -v "$tool" >/dev/null || { echo "Missing dependency: $tool" >&2; exit 2; }
done

# Ignore exported Compose settings and credentials. No existing installation is selected.
docker_cmd=(env -i "PATH=$PATH" "HOME=$HOME" docker --context default)
work="$(mktemp -d)"
created_volumes=()
created_containers=()
cleanup() {
  local status=$?
  for container in "${created_containers[@]}"; do
    "${docker_cmd[@]}" rm -f "$container" >/dev/null 2>&1 || status=1
  done
  for volume in "${created_volumes[@]}"; do
    "${docker_cmd[@]}" volume rm "$volume" >/dev/null 2>&1 || status=1
  done
  rm -rf "$work"
  exit "$status"
}
trap cleanup EXIT

source_container="$project-source"
target_container="$project-target"
source_volume="$project-source-data"
target_volume="$project-target-data"
for name in "$source_container" "$target_container"; do
  if "${docker_cmd[@]}" container inspect "$name" >/dev/null 2>&1; then
    echo 'The rehearsal refuses an existing container.' >&2
    exit 2
  fi
done
for name in "$source_volume" "$target_volume"; do
  if "${docker_cmd[@]}" volume inspect "$name" >/dev/null 2>&1; then
    echo 'The rehearsal refuses an existing volume.' >&2
    exit 2
  fi
done

compose=("${docker_cmd[@]}" compose --env-file .env.example -f docker-compose.yml)
"${compose[@]}" config --format json > "$work/source.json"
"${compose[@]}" -f docker-compose.postgres18.yml config --format json > "$work/target.json"
python3 - "$work" > "$work/images" <<'PY'
import json
import re
import sys
from pathlib import Path

root = Path(sys.argv[1])
for filename, major, volume, target in (
    ("source.json", 17, "postgres-data", "/var/lib/postgresql/data"),
    ("target.json", 18, "postgres18-data", "/var/lib/postgresql"),
):
    service = json.loads((root / filename).read_text())["services"]["postgres"]
    image = service["image"]
    if not re.fullmatch(rf"postgres:{major}\.\d+-alpine(?:@sha256:[a-f0-9]{{64}})?", image):
        raise SystemExit("Unexpected PostgreSQL image in the rehearsal configuration.")
    mounts = [item for item in service["volumes"] if item["type"] == "volume"]
    if len(mounts) != 1 or mounts[0]["source"] != volume or mounts[0]["target"] != target:
        raise SystemExit("Unexpected PostgreSQL volume layout.")
    print(image)
PY
mapfile -t images < "$work/images"
test "${#images[@]}" = 2
for image in "${images[@]}"; do
  "${docker_cmd[@]}" pull "$image" >/dev/null
done
source_image="$("${docker_cmd[@]}" image inspect --format '{{.Id}}' "${images[0]}")"
target_image="$("${docker_cmd[@]}" image inspect --format '{{.Id}}' "${images[1]}")"
password="$(python3 -c 'import secrets; print(secrets.token_hex(24))')"

for volume in "$source_volume" "$target_volume"; do
  "${docker_cmd[@]}" volume create --label "ai-factory.rehearsal=$project" "$volume" >/dev/null
  test "$("${docker_cmd[@]}" volume inspect --format '{{ index .Labels "ai-factory.rehearsal" }}' "$volume")" = "$project"
  created_volumes+=("$volume")
done
start_database() {
  local name=$1 image=$2 volume=$3 mount=$4 user=$5 data=$6
  "${docker_cmd[@]}" run -d --name "$name" --label "ai-factory.rehearsal=$project" \
    --network none --cap-drop ALL --security-opt no-new-privileges:true \
    --user 70:70 --read-only --pids-limit 256 \
    --tmpfs /var/run/postgresql:uid=70,gid=70,mode=0775 --tmpfs /tmp:mode=1777 \
    --mount "type=volume,source=$volume,target=$mount" \
    -e "POSTGRES_PASSWORD=$password" -e "POSTGRES_USER=$user" -e POSTGRES_DB=postgres \
    -e "PGDATA=$data" "$image" >/dev/null
  created_containers+=("$name")
  for attempt in {1..45}; do
    if "${docker_cmd[@]}" exec "$name" pg_isready -q -U "$user" -d postgres; then
      return 0
    fi
    sleep 1
  done
  echo 'PostgreSQL did not become ready.' >&2
  return 1
}
sql() { "${docker_cmd[@]}" exec -i "$1" psql -X -v ON_ERROR_STOP=1 -Atq -U "$2" -d "$3"; }
start_database "$source_container" "$source_image" "$source_volume" /var/lib/postgresql/data postgres /var/lib/postgresql/data
start_database "$target_container" "$target_image" "$target_volume" /var/lib/postgresql factory_restore_admin /var/lib/postgresql/18/docker
test "$(printf 'SHOW server_version_num;' | sql "$source_container" postgres postgres)" -ge 170000
test "$(printf 'SHOW server_version_num;' | sql "$source_container" postgres postgres)" -lt 180000
test "$(printf 'SHOW server_version_num;' | sql "$target_container" factory_restore_admin postgres)" -ge 180000
test "$(printf 'SHOW data_directory;' | sql "$target_container" factory_restore_admin postgres)" = /var/lib/postgresql/18/docker
printf '%s\n' "CREATE ROLE factory_probe LOGIN;" "CREATE DATABASE factory_history OWNER factory_probe;" \
  | sql "$source_container" postgres postgres
printf '%s\n' 'SET ROLE factory_probe; CREATE TABLE migration_evidence (id integer PRIMARY KEY, value text NOT NULL);' \
  "INSERT INTO migration_evidence VALUES (1, 'synthetic-upgrade-marker');" \
  | sql "$source_container" postgres factory_history
"${docker_cmd[@]}" exec "$source_container" pg_dumpall -U postgres --quote-all-identifiers > "$work/export.sql"
sql "$target_container" factory_restore_admin postgres < "$work/export.sql" > /dev/null
verify_target() {
  test "$(printf 'SELECT value FROM migration_evidence WHERE id=1;' | sql "$target_container" factory_restore_admin factory_history)" = synthetic-upgrade-marker
  test "$(printf "SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname='factory_history';" | sql "$target_container" factory_restore_admin postgres)" = factory_probe
  test "$(printf "SELECT rolcanlogin FROM pg_roles WHERE rolname='factory_probe';" | sql "$target_container" factory_restore_admin postgres)" = t
}
verify_target
"${docker_cmd[@]}" rm -f "$target_container" >/dev/null
created_containers=("$source_container")
start_database "$target_container" "$target_image" "$target_volume" /var/lib/postgresql factory_restore_admin /var/lib/postgresql/18/docker
verify_target
test "$(printf 'SELECT value FROM migration_evidence WHERE id=1;' | sql "$source_container" postgres factory_history)" = synthetic-upgrade-marker
echo 'PostgreSQL 17 to 18 synthetic restore and target persistence passed.'
