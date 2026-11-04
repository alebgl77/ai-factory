#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# Secret scan with gitleaks (MIT), run from its official container image.
#
#   scripts/scan_secrets.sh [PATH] [--git] [--report FILE]
#
#   PATH          directory to scan (default: repository root)
#   --git         scan the git history of PATH instead of the working tree
#   --report FILE also write a JSON report (secrets redacted) to FILE
#
# Exit code: 0 = no finding, 1 = findings, 2 = usage or runtime error.
# Used before integrating any external repository (integrations/) and in CI.
# Placeholders of .env.example ("change-me", "sk-mock-not-a-secret", ...) and vendored or
# generated directories are allow-listed below; a repository-level .gitleaks.toml, when
# present in PATH, is used instead of the built-in configuration.
set -euo pipefail

GITLEAKS_IMAGE="${GITLEAKS_IMAGE:-zricethezav/gitleaks:v8.30.1}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET="$ROOT"
MODE="dir"
REPORT=""

while [ $# -gt 0 ]; do
  case "$1" in
    --git) MODE="git"; shift ;;
    --report) REPORT="${2:?--report needs a file}"; shift 2 ;;
    -h|--help) sed -n '2,16p' "$0"; exit 0 ;;
    -*) echo "unknown option: $1" >&2; exit 2 ;;
    *) TARGET="$1"; shift ;;
  esac
done

if [ ! -d "$TARGET" ]; then
  echo "not a directory: $TARGET" >&2
  exit 2
fi
TARGET="$(cd "$TARGET" && pwd)"

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
chmod 0755 "$WORK"

CONFIG_ARGS=()
if [ -f "$TARGET/.gitleaks.toml" ]; then
  CONFIG_ARGS=(--config /scan/.gitleaks.toml)
else
  cat > "$WORK/gitleaks.toml" <<'TOML'
# Built-in configuration of scripts/scan_secrets.sh: default gitleaks rules plus the
# allow-list of the Virtual AI Factory placeholders.
[extend]
useDefault = true

[[allowlists]]
description = "Placeholders, vendored and generated content"
paths = [
  '''(^|/)\.git/''',
  '''(^|/)node_modules/''',
  '''(^|/)\.venv/''',
  '''(^|/)__pycache__/''',
  '''(^|/)\.pytest_cache/''',
  '''(^|/)dashboard/vendor/''',
  '''(^|/)compose/build/[^/]+\.pem$''',
  '''(^|/)\.env$''',
  # submodules: scanned on their full history by scripts/inventory_github.py (triage in integrations/secrets-triage.yaml)
  '''(^|/)integrations/[^/]+/''',
  # optional visual pack: base64 image data, not credentials
  '''(^|/)dashboard/assets/assets\.js$''',
]
regexes = [
  # snake_case identifiers (contract field names such as tokens_per_task_p95), never secrets
  '''^[a-z][a-z0-9]*(_[a-z0-9]+)+$''',
  '''(?i)change[-_]?me''',
  '''sk-mock-not-a-secret''',
  '''^0{64}$''',
]
TOML
  chmod 0644 "$WORK/gitleaks.toml"
  CONFIG_ARGS=(--config /work/gitleaks.toml)
fi

REPORT_ARGS=()
if [ -n "$REPORT" ]; then
  REPORT_ARGS=(--report-format json --report-path /work/report.json)
fi

if [ "$MODE" = "git" ] && [ ! -d "$TARGET/.git" ]; then
  echo "--git: $TARGET is not a git repository" >&2
  exit 2
fi

run_gitleaks() {
  if command -v docker >/dev/null 2>&1; then
    docker run --rm --network none --user "$(id -u):$(id -g)" \
      -v "$TARGET:/scan:ro" -v "$WORK:/work" \
      "$GITLEAKS_IMAGE" "$MODE" /scan --redact --no-banner --exit-code 1 \
      "${CONFIG_ARGS[@]}" "${REPORT_ARGS[@]}"
  elif command -v gitleaks >/dev/null 2>&1; then
    local cfg=("${CONFIG_ARGS[@]/\/work/$WORK}")
    cfg=("${cfg[@]/\/scan/$TARGET}")
    local rep=("${REPORT_ARGS[@]/\/work/$WORK}")
    gitleaks "$MODE" "$TARGET" --redact --no-banner --exit-code 1 "${cfg[@]}" "${rep[@]}"
  else
    echo "neither docker nor gitleaks is available" >&2
    return 2
  fi
}

echo "gitleaks ($MODE) on $TARGET"
set +e
run_gitleaks
status=$?
set -e

if [ -n "$REPORT" ] && [ -f "$WORK/report.json" ]; then
  cp "$WORK/report.json" "$REPORT"
fi

case "$status" in
  0) echo "secret scan: clean" ;;
  1) echo "secret scan: FINDINGS (see above, values redacted)" >&2 ;;
  *) echo "secret scan: error (exit $status)" >&2; status=2 ;;
esac
exit "$status"
