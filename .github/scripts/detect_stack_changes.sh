#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# The PR checkout is the test merge commit. On pushes, compare the entire pushed range.
# An absent/unknown base or diff error takes the conservative path and runs all jobs.
set -euo pipefail

base="${1:-}"
changed=true
kubernetes=true
if [[ "$base" =~ ^[0-9a-f]{40}$ ]] && git cat-file -e "$base^{commit}" 2>/dev/null; then
  if git diff --quiet --no-ext-diff "$base" HEAD -- \
    ':(glob)docker-compose*.yml' .env.example compose/ config/ api/ dashboard/ sim/ \
    scripts/smoke_stack.sh scripts/check_production.py .github/workflows/ .github/scripts/ \
    .github/dependabot.yml licenses/; then
    changed=false
  fi
  if git diff --quiet --no-ext-diff "$base" HEAD -- \
    deploy/ scripts/package_helm.py tests/kubernetes/ tests/deployment/ \
    ':(glob)docs/KUBERNETES*.md' compose/ config/ api/ dashboard/ \
    .github/workflows/ .github/scripts/ .github/dependabot.yml; then
    kubernetes=false
  fi
fi
printf 'changed=%s\n' "$changed"
printf 'kubernetes=%s\n' "$kubernetes"
