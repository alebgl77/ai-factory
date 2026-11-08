#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
set -euo pipefail

destination="$(realpath -m "${1:?Usage: install_kubernetes_tools.sh DIRECTORY helm|kind|kubectl ...}")"
shift
mkdir -p "$destination"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

download() {
  local url="$1" digest="$2" file="$3"
  curl --fail --silent --show-error --location --retry 3 --proto '=https' "$url" -o "$work/$file"
  printf '%s  %s\n' "$digest" "$work/$file" | sha256sum --check --strict
}

for tool in "$@"; do
  case "$tool" in
    helm)
      # https://github.com/helm/helm/releases/tag/v3.22.1
      archive=helm-v3.22.1-linux-amd64.tar.gz
      download "https://get.helm.sh/$archive" \
        896cf63684a42a723d61c980369d6e588330927deb13676ca0d85006dd6da0ea "$archive"
      tar -xzf "$work/$archive" -C "$work" linux-amd64/helm
      install -m 755 "$work/linux-amd64/helm" "$destination/helm"
      ;;
    kind)
      # https://github.com/kubernetes-sigs/kind/releases/expanded_assets/v0.33.0
      download https://github.com/kubernetes-sigs/kind/releases/download/v0.33.0/kind-linux-amd64 \
        aee6151561422756b764a4ae28e7f44cda5af5a9eead3cc9985112b1de8d8e0d kind
      install -m 755 "$work/kind" "$destination/kind"
      ;;
    kubectl)
      url=https://dl.k8s.io/release/v1.37.0/bin/linux/amd64/kubectl
      digest="$(curl --fail --silent --show-error --location --retry 3 --proto '=https' "$url.sha256")"
      [[ "$digest" =~ ^[0-9a-f]{64}$ ]]
      download "$url" "$digest" kubectl
      install -m 755 "$work/kubectl" "$destination/kubectl"
      ;;
    *)
      echo "Unsupported Kubernetes tool: $tool" >&2
      exit 2
      ;;
  esac
done
