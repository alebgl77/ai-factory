# syntax=docker/dockerfile:1@sha256:4edf897a3ffa55b89f906fc8cc78afdb3f1834cc9c7083565e611a8a7d5fe99e
# SPDX-License-Identifier: MIT
# Aggregation API of the Virtual AI Factory.
# Base image pinned by digest (multi-arch index); the tag stays for Dependabot, which updates both.
FROM python:3.12.16-slim@sha256:e695ab189fa2d082b1c61c69a6b888a5adeca61ceeccad107b1ed2a125721a9e

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    FACTORY_CONFIG_DIR=/config \
    FACTORY_STATE_DIR=/data

WORKDIR /app
# Hash-pinned lock (direct + transitive) compiled from requirements.in: pip refuses any file
# whose hash differs and any dependency missing from the lock.
COPY requirements.txt ./
# The optional build secret "build_ca" holds extra CA certificates for TLS-intercepting
# corporate proxies; it is never written to an image layer.
RUN --mount=type=secret,id=build_ca,required=false \
    set -eu; \
    if [ -s /run/secrets/build_ca ]; then \
      cat /etc/ssl/certs/ca-certificates.crt /run/secrets/build_ca > /tmp/ca.pem; \
      export PIP_CERT=/tmp/ca.pem; \
    fi; \
    pip install --no-compile --require-hashes -r requirements.txt; \
    rm -f /tmp/ca.pem; \
    groupadd --system --gid 10001 factory; \
    useradd --system --uid 10001 --gid factory --no-create-home --shell /usr/sbin/nologin factory; \
    mkdir -p /data /config; \
    chown factory:factory /data

COPY --chown=root:root app ./app

USER 10001
EXPOSE 8000
VOLUME ["/data"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health', timeout=4).status == 200 else 1)"]

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-server-header"]
