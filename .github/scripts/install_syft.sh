#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
set -euo pipefail

destination="${1:?Usage: install_syft.sh DIRECTORY}"
version=1.55.0
archive="syft_${version}_linux_amd64.tar.gz"
digest=bb65249542db5fedeeb4d3f9b018b75e7264dbfbbb10486f044581a92f90490d
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

curl_options=(--disable --fail --silent --show-error --location --retry 3
  --max-time 120 --retry-max-time 120 --proto '=https' --proto-redir '=https'
  --output "$work/$archive")
if ! curl "${curl_options[@]}" "https://github.com/anchore/syft/releases/download/v$version/$archive"; then
  echo "Syft download failed. Retrying via the official GitHub release asset API." >&2
  curl "${curl_options[@]}" --header 'Accept: application/octet-stream' \
    https://api.github.com/repos/anchore/syft/releases/assets/657153873
fi
printf '%s  %s\n' "$digest" "$work/$archive" | sha256sum --check --strict
tar -xzf "$work/$archive" -C "$work" syft
install -D -m 755 "$work/syft" "$destination/syft"
