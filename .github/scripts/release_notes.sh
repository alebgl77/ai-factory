#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# Release notes of .github/workflows/release.yml: the CHANGELOG.md section of the version, the
# published images (pinned by digest) and how to verify them, in English then French.
#
#   .github/scripts/release_notes.sh TAG IMAGES_FILE [REPOSITORY]
#
#   TAG          release tag, e.g. v1.0.0
#   IMAGES_FILE  one "registry/name@sha256:digest" per line (images.txt of the release)
#   REPOSITORY   owner/name of the source repository (default: $GITHUB_REPOSITORY)
# Exit code: 0 notes printed, 1 missing changelog section or empty image list, 2 usage error.
set -euo pipefail

tag="${1:-}"
images_file="${2:-}"
repo="${3:-${GITHUB_REPOSITORY:-}}"
if [ -z "$tag" ] || [ -z "$images_file" ] || [ -z "$repo" ]; then
  echo "usage: $0 TAG IMAGES_FILE [REPOSITORY]" >&2
  exit 2
fi
if [ ! -f "$images_file" ]; then
  echo "no such file: $images_file" >&2
  exit 2
fi
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
root="$(cd "$here/../.." && pwd)"
version="${tag#v}"

changes="$("$here/changelog_section.sh" "$version" "$root/CHANGELOG.md")"
mapfile -t images < <(grep -E '^[a-z0-9.-]+(:[0-9]+)?/[a-z0-9._/-]+@sha256:[0-9a-f]{64}$' "$images_file" | sort)
if [ "${#images[@]}" -eq 0 ]; then
  echo "no image reference in $images_file" >&2
  exit 1
fi
first="${images[0]}"
first_tagged="${first%@*}:$version"

cat <<EOF
$changes

## Container images

All images are built by this workflow on GitHub-hosted runners, scanned (no known fixable HIGH or
CRITICAL vulnerability at release time), signed keyless with Sigstore cosign, and carry a SLSA
build provenance attestation and SBOM attestations (SPDX 2.3 and CycloneDX). Pull them by digest:

| Image | Reference |
|---|---|
EOF
for ref in "${images[@]}"; do
  name="${ref%@*}"
  # shellcheck disable=SC2016  # the backquotes are Markdown, not command substitution
  printf '| `%s` | `%s` |\n' "${name##*/}" "$ref"
done

cat <<EOF

## Verify before you run

\`\`\`sh
# 1. Signature: made by the release workflow of $repo, on a v* tag (cosign v3)
cosign verify $first_tagged \\
  --certificate-identity-regexp '^https://github\\.com/${repo//./\\.}/\\.github/workflows/release\\.yml@refs/tags/v' \\
  --certificate-oidc-issuer https://token.actions.githubusercontent.com

# 2. SLSA build provenance, then the SBOM attestations (GitHub CLI)
gh attestation verify oci://$first_tagged --repo $repo
gh attestation verify oci://$first_tagged --repo $repo --predicate-type https://spdx.dev/Document/v2.3
gh attestation verify oci://$first_tagged --repo $repo --predicate-type https://cyclonedx.org/bom

# 3. Release assets (SBOM files, attestation bundles): checksums signed keyless
cosign verify-blob SHA256SUMS --bundle SHA256SUMS.sigstore.json \\
  --certificate-identity-regexp '^https://github\\.com/${repo//./\\.}/\\.github/workflows/release\\.yml@refs/tags/v' \\
  --certificate-oidc-issuer https://token.actions.githubusercontent.com
sha256sum -c SHA256SUMS
\`\`\`

All checks at once, for every image: \`.github/scripts/verify_image.sh <image>@sha256:<digest>\`
(the same script the release workflow runs before publishing these notes).

### Vérifier avant d'exécuter (français)

Chaque image est construite par ce workflow sur des runners hébergés par GitHub, analysée (aucune
vulnérabilité HIGH ou CRITICAL corrigeable connue au moment de la publication), signée sans clé avec
Sigstore cosign, et porte une attestation de provenance SLSA et des attestations SBOM (SPDX 2.3 et
CycloneDX). Utilisez les références par digest du tableau ci-dessus, puis vérifiez avec les commandes
ci-dessus ou en une fois : \`.github/scripts/verify_image.sh <image>@sha256:<digest>\`.
EOF
