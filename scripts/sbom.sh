#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# Software bills of materials (SBOM) of the Virtual AI Factory, generated with syft (Apache-2.0)
# in CycloneDX JSON and SPDX 2.3 JSON: the same tool and version as CI and releases
# (.github/workflows/security.yml, release.yml).
#
#   scripts/sbom.sh [--images] [--tag TAG] [--out DIR]
#
#   (default)   SBOM of the source tree: every dependency the repository declares (Python
#               requirements of the API, simulators and scripts, CI tool locks)
#   --images    also one SBOM per image built from this repository, ai-factory/<name>:TAG
#               (build them first: docker compose --profile sim build)
#   --tag TAG   tag of those images (default: $FACTORY_IMAGE_TAG, else "local")
#   --out DIR   output directory (default: dist/sbom; it ignores itself in git)
#
# syft runs from its official image pinned by digest, with no network (--network none), as the
# calling user, with the repository mounted read-only. Images are exported with
# "docker image save" first, so the scanner never gets access to the Docker socket. Without
# Docker, a local syft binary is used for the source tree.
# Exit code: 0 = SBOMs written, 1 = an image is missing, 2 = usage or runtime error.
set -euo pipefail

SYFT_IMAGE="${SYFT_IMAGE:-anchore/syft:v1.55.0@sha256:4cc92b64ca74ffca4c94d4c8fcc01f217731375300be14d0b6eeb2e82eaa110a}"
IMAGES=(api dashboard mock-llm traffic gpu-sim grafana-bootstrap)
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="$ROOT/dist/sbom"
TAG="${FACTORY_IMAGE_TAG:-local}"
WITH_IMAGES=0

while [ $# -gt 0 ]; do
  case "$1" in
    --images) WITH_IMAGES=1; shift ;;
    --tag) TAG="${2:?--tag needs a value}"; shift 2 ;;
    --out) OUT="${2:?--out needs a directory}"; shift 2 ;;
    -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done
if ! [[ "$TAG" =~ ^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$ ]]; then
  echo "invalid image tag: $TAG" >&2
  exit 2
fi

mkdir -p "$OUT"
OUT="$(cd "$OUT" && pwd)"
# Generated files never end up in a commit.
[ -f "$OUT/.gitignore" ] || printf '*\n' > "$OUT/.gitignore"

VERSION="$(git -C "$ROOT" describe --tags --always --dirty 2>/dev/null || echo unknown)"
# Paths that are not part of the product: submodules (inventoried separately), local
# environments, caches, generated output, and the local .env (never read).
EXCLUDES=(
  --exclude './.git/**' --exclude './integrations/**' --exclude './dist/**' --exclude './.env'
  --exclude './**/node_modules/**' --exclude './**/.venv/**' --exclude './**/__pycache__/**'
  --exclude './**/.pytest_cache/**' --exclude './**/.ruff_cache/**'
)

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
chmod 0755 "$WORK"

syft_docker() {
  # $1: read-only volume to scan (host:container), rest: syft arguments
  local volume="$1"; shift
  docker run --rm --network none --user "$(id -u):$(id -g)" --read-only --tmpfs /tmp \
    -e HOME=/tmp -e SYFT_CHECK_FOR_APP_UPDATE=false \
    -v "$OUT:/out" -v "$volume:ro" "$SYFT_IMAGE" "$@"
}

if command -v docker >/dev/null 2>&1; then
  echo "syft ($SYFT_IMAGE): source tree"
  syft_docker "$ROOT:/ai-factory" scan dir:/ai-factory "${EXCLUDES[@]}" \
    --source-name ai-factory --source-version "$VERSION" \
    -o cyclonedx-json=/out/source.cdx.json -o spdx-json=/out/source.spdx.json -q
elif command -v syft >/dev/null 2>&1; then
  echo "syft (local binary $(syft version 2>/dev/null | awk '/^Version:/{print $2}')): source tree"
  (cd "$ROOT" && SYFT_CHECK_FOR_APP_UPDATE=false syft scan dir:. "${EXCLUDES[@]}" \
    --source-name ai-factory --source-version "$VERSION" \
    -o cyclonedx-json="$OUT/source.cdx.json" -o spdx-json="$OUT/source.spdx.json" -q)
else
  echo "neither docker nor syft is available" >&2
  exit 2
fi

status=0
if [ "$WITH_IMAGES" -eq 1 ]; then
  if ! command -v docker >/dev/null 2>&1; then
    echo "--images needs docker" >&2
    exit 2
  fi
  for name in "${IMAGES[@]}"; do
    ref="ai-factory/$name:$TAG"
    if ! docker image inspect "$ref" >/dev/null 2>&1; then
      echo "missing image $ref (docker compose --profile sim build)" >&2
      status=1
      continue
    fi
    echo "syft: $ref"
    docker image save -o "$WORK/$name.tar" "$ref"
    chmod 0644 "$WORK/$name.tar"
    syft_docker "$WORK:/work" scan "docker-archive:/work/$name.tar" \
      --source-name "ai-factory/$name" --source-version "$TAG" \
      -o cyclonedx-json="/out/$name.cdx.json" -o spdx-json="/out/$name.spdx.json" -q
    rm -f "$WORK/$name.tar"
  done
fi

(cd "$OUT" && find . -maxdepth 1 -name '*.json' -printf '%f\n' | sort | xargs sha256sum > SHA256SUMS)
echo "SBOMs in $OUT:"
(cd "$OUT" && cat SHA256SUMS)
exit "$status"
