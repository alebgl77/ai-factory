#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# Print the CHANGELOG.md section of one version, without its heading (release notes of
# .github/workflows/release.yml).
#
#   .github/scripts/changelog_section.sh VERSION [CHANGELOG]
#
# A section starts at a level-2 heading whose first word is the version, with or without a
# leading "v" or square brackets ("## 1.0.0 (2026-11)", "## [v1.0.0] - 2026-11-04"), and ends at
# the next level-2 heading.
# Exit code: 0 = section found and not empty, 1 = missing or empty section, 2 = usage error.
set -euo pipefail

version="${1:-}"
file="${2:-CHANGELOG.md}"
if [ -z "$version" ]; then
  echo "usage: $0 VERSION [CHANGELOG]" >&2
  exit 2
fi
if [ ! -f "$file" ]; then
  echo "no such file: $file" >&2
  exit 2
fi
version="${version#v}"

section="$(awk -v want="$version" '
  /^## / {
    if (inside) { exit }
    heading = $0
    sub(/^##[[:space:]]+/, "", heading)
    sub(/^\[/, "", heading)
    sub(/^v/, "", heading)
    split(heading, words, /[][:space:]()]/)
    if (words[1] == want) { inside = 1 }
    next
  }
  inside { print }
' "$file")"

# Drop leading and trailing blank lines.
section="$(printf '%s\n' "$section" | sed -e '/./,$!d')"
if [ -z "${section//[[:space:]]/}" ]; then
  echo "CHANGELOG: no section for version $version in $file" >&2
  exit 1
fi
printf '%s\n' "$section"
