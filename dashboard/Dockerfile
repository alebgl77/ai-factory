# syntax=docker/dockerfile:1@sha256:4edf897a3ffa55b89f906fc8cc78afdb3f1834cc9c7083565e611a8a7d5fe99e
# SPDX-License-Identifier: MIT
# Static 3D dashboard served by nginx (BSD-2-Clause), with Three.js (MIT) vendored at build
# time so that the browser loads nothing from a CDN.
#
#   docker build -t ai-factory/dashboard dashboard/
#   --build-arg DASHBOARD_THREE_SOURCE=cdn  keeps the jsDelivr importmap (the edge CSP must
#                                            then allow https://cdn.jsdelivr.net)

ARG THREE_VERSION=0.186.1
# Subresource integrity of three-0.186.1.tgz as published by the npm registry.
ARG THREE_INTEGRITY=sha512-blFeqb49wRCSGUGj7gtpfnSGHy2lwDk94RhUmS1c/hTby70kvChbWpkJ4Pm1390LqzzvTmzgXKHPEafJwCb8jA==

# Base images pinned by digest (multi-arch index); the tags stay for Dependabot, which updates both.
FROM node:26.13.0-alpine@sha256:c6ad9e9c1d80f6eb607c26b4b0a132ff7a2344fad1c78ea3baa2a7d4c8de6d94 AS vendor
ARG THREE_VERSION
ARG THREE_INTEGRITY
ARG DASHBOARD_THREE_SOURCE=vendored
WORKDIR /build
# Optional build secret "build_ca": extra CA certificates for TLS-intercepting proxies.
RUN --mount=type=secret,id=build_ca,required=false \
    set -eu; \
    if [ -s /run/secrets/build_ca ]; then export NODE_EXTRA_CA_CERTS=/run/secrets/build_ca; fi; \
    # npm verifies the tarball against the registry integrity; we also pin it explicitly.
    npm pack "three@${THREE_VERSION}" --silent --no-audit --no-fund; \
    tgz="three-${THREE_VERSION}.tgz"; \
    actual="sha512-$(node -e "process.stdout.write(require('crypto').createHash('sha512').update(require('fs').readFileSync(process.argv[1])).digest('base64'))" "$tgz")"; \
    if [ "$actual" != "$THREE_INTEGRITY" ]; then echo "three: integrity mismatch ($actual)" >&2; exit 1; fi; \
    mkdir pkg && tar -xzf "$tgz" -C pkg --strip-components=1; \
    v=/vendor/three; \
    mkdir -p "$v/build"; \
    cp pkg/build/three.module.js pkg/build/three.core.js "$v/build/"; \
    # Addons imported by index.html (keep in sync with its import statements)
    # (the postprocessing, shaders and math files are the GTAOPass chain, loaded only in 'Haute' quality)
    for f in controls/OrbitControls.js renderers/CSS2DRenderer.js environments/RoomEnvironment.js \
             geometries/RoundedBoxGeometry.js utils/BufferGeometryUtils.js \
             postprocessing/GTAOPass.js postprocessing/Pass.js shaders/GTAOShader.js \
             shaders/PoissonDenoiseShader.js shaders/CopyShader.js math/SimplexNoise.js; do \
      mkdir -p "$v/examples/jsm/$(dirname "$f")"; cp "pkg/examples/jsm/$f" "$v/examples/jsm/$f"; \
    done; \
    cp pkg/LICENSE "$v/LICENSE"; \
    printf 'three@%s (MIT) %s\n' "$THREE_VERSION" "$THREE_INTEGRITY" > "$v/VERSION"

COPY index.html /build/site/index.html
COPY exercise/ /build/site/exercise/
COPY companion/ /build/site/companion/
# Optional visual pack (data-URI bundle built by scripts/build_assets.py; empty when no image was generated)
COPY assets/assets.js /build/site/assets/assets.js
RUN set -eu; \
    if [ "$DASHBOARD_THREE_SOURCE" = "vendored" ]; then \
      sed -i \
        -e "s#https://cdn.jsdelivr.net/npm/three@${THREE_VERSION}/#/vendor/three/#g" \
        -e "s#depuis le CDN (cdn.jsdelivr.net). Vérifiez la connexion Internet puis rechargez la page.#(copie locale /vendor/three/). Rechargez la page.#" \
        /build/site/index.html; \
      if grep -q "cdn.jsdelivr.net/npm/three@" /build/site/index.html; then \
        echo "importmap still points to the CDN (three version mismatch?)" >&2; exit 1; \
      fi; \
      grep -q '"three": "/vendor/three/build/three.module.js"' /build/site/index.html; \
      mkdir -p /build/site/vendor && cp -r /vendor/three /build/site/vendor/three; \
    fi

# This unprivileged nginx image uses UID 101 and includes Alpine fixes for CVE-2026-107891
# (musl and musl-utils) and CVE-2026-85091 (zlib).
FROM nginx/nginx-unprivileged:1.31.8-alpine-slim@sha256:633f37ad70b9fd48d4da573db1ebc9111a9ad174e5178e83bd2ef45381fde5e2
COPY --chown=root:root nginx.conf /etc/nginx/nginx.conf
COPY --from=vendor --chown=root:root /build/site/ /usr/share/nginx/html/
# Listens on port 80 as uid 101: docker-compose.yml sets
# net.ipv4.ip_unprivileged_port_start=0 for this container.
EXPOSE 80
USER 101
HEALTHCHECK --interval=15s --timeout=3s --start-period=5s --retries=3 \
  CMD ["wget", "-q", "-O", "/dev/null", "http://127.0.0.1/healthz"]
CMD ["nginx", "-g", "daemon off;"]
