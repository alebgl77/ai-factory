#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# Prepares .env for a GitHub Codespace: public hosts of the forwarded ports, random secrets.
set -euo pipefail
cd "$(dirname "$0")/.."
test -f .env || cp .env.example .env
set_env() { if grep -q "^$1=" .env; then sed -i "s|^$1=.*|$1=$2|" .env; else printf '%s=%s\n' "$1" "$2" >> .env; fi; }
# Replace every "change-me" placeholder by a random value (LiteLLM keys must start with "sk-")
for v in $(grep -E '^[A-Z0-9_]+=.*change-me' .env | cut -d= -f1); do
  case "$v" in LITELLM_*) set_env "$v" "sk-$(openssl rand -hex 24)" ;; *) set_env "$v" "$(openssl rand -hex 24)" ;; esac
done
if [ -n "${CODESPACE_NAME:-}" ]; then
  d="${GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN:-app.github.dev}"
  set_env FACTORY_HOST "${CODESPACE_NAME}-80.${d}"
  set_env GRAFANA_PUBLIC_URL "https://${CODESPACE_NAME}-3001.${d}"
  set_env LANGFUSE_PUBLIC_URL "https://${CODESPACE_NAME}-3002.${d}"
  set_env GRAFANA_CSRF_TRUSTED_ORIGINS "${CODESPACE_NAME}-3001.${d}"
fi
echo "Virtual AI Factory: .env ready. The stack starts with: docker compose --profile sim up -d --build"
