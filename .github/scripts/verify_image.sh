#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# Verify a released image of the Virtual AI Factory before running it, against the public
# Sigstore transparency log and the GitHub attestation store:
#   1. the keyless cosign signature was made by the release workflow of this repository, on a
#      v* tag, with a GitHub Actions OIDC identity;
#   2. the SLSA build provenance attestation (who built it, from which commit, with which
#      workflow);
#   3. the SBOM attestations (SPDX 2.3 and CycloneDX).
# The release workflow runs this same script on every image it publishes.
#
#   .github/scripts/verify_image.sh IMAGE@sha256:DIGEST      (preferred: immutable)
#   .github/scripts/verify_image.sh IMAGE:TAG                (resolved to its digest first)
#
# Example: .github/scripts/verify_image.sh ghcr.io/alebgl77/api:1.0.0
#
# Requirements: cosign v3 (signatures use the Sigstore bundle format) and the GitHub CLI with
# "gh attestation" (tested with 2.89), logged in or with GH_TOKEN set; no Docker daemon is
# needed. Environment:
#   SOURCE_REPO   owner/name of the repository that must have built the image
#                 (default: alebgl77/ai-factory)
# Exit code: 0 verified, 1 verification failed, 2 usage or missing tool.
set -euo pipefail

ref="${1:-}"
repo="${SOURCE_REPO:-alebgl77/ai-factory}"
issuer="https://token.actions.githubusercontent.com"

if [ -z "$ref" ] || [ "$ref" = "-h" ] || [ "$ref" = "--help" ]; then
  sed -n '2,22p' "$0"
  exit 2
fi
for tool in cosign gh; do
  if ! command -v "$tool" >/dev/null 2>&1; then
    echo "missing tool: $tool" >&2
    exit 2
  fi
done
if ! [[ "$repo" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]]; then
  echo "SOURCE_REPO must be owner/name: $repo" >&2
  exit 2
fi

# Identity of the signer: the release workflow of the repository, run for a v* tag.
repo_re="${repo//./\\.}"
identity_re="^https://github\\.com/${repo_re}/\\.github/workflows/release\\.yml@refs/tags/v[0-9]+\\.[0-9]+\\.[0-9]+.*$"

fail() {
  echo "FAILED: $*" >&2
  exit 1
}

echo "image:     $ref"
echo "repository $repo, signer workflow .github/workflows/release.yml on a v* tag"

echo "[1/4] cosign signature (keyless, Sigstore)"
cosign verify \
  --certificate-identity-regexp "$identity_re" \
  --certificate-oidc-issuer "$issuer" \
  --certificate-github-workflow-repository "$repo" \
  "$ref" >/dev/null || fail "cosign signature"

echo "[2/4] SLSA build provenance attestation"
gh attestation verify "oci://$ref" \
  --repo "$repo" \
  --signer-workflow "$repo/.github/workflows/release.yml" \
  --deny-self-hosted-runners \
  --predicate-type https://slsa.dev/provenance/v1 >/dev/null || fail "build provenance"

echo "[3/4] SBOM attestation (SPDX 2.3)"
gh attestation verify "oci://$ref" \
  --repo "$repo" \
  --signer-workflow "$repo/.github/workflows/release.yml" \
  --deny-self-hosted-runners \
  --predicate-type https://spdx.dev/Document/v2.3 >/dev/null || fail "SPDX SBOM attestation"

echo "[4/4] SBOM attestation (CycloneDX)"
gh attestation verify "oci://$ref" \
  --repo "$repo" \
  --signer-workflow "$repo/.github/workflows/release.yml" \
  --deny-self-hosted-runners \
  --predicate-type https://cyclonedx.org/bom >/dev/null || fail "CycloneDX SBOM attestation"

echo "OK: $ref is signed, and its provenance and SBOMs are attested by $repo"
