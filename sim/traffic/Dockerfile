# syntax=docker/dockerfile:1@sha256:4edf897a3ffa55b89f906fc8cc78afdb3f1834cc9c7083565e611a8a7d5fe99e
# SPDX-License-Identifier: MIT
# Synthetic traffic generator (fictional use cases uc-01..uc-08) for the key-less demo.
# Base image pinned by digest (multi-arch index); the tag stays for Dependabot, which updates both.
FROM python:3.15.0-slim@sha256:f5b92f2a669a4c347044f29367f31983280b648250ca94dea66f98c53bc87e58

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
# Hash-pinned lock (direct + transitive) compiled from requirements.in: pip refuses any file
# whose hash differs and any dependency missing from the lock.
COPY requirements.txt .
# Optional build secret "build_ca": extra CA certificates for TLS-intercepting proxies.
RUN --mount=type=secret,id=build_ca,required=false \
    set -eu; \
    if [ -s /run/secrets/build_ca ]; then \
      cat /etc/ssl/certs/ca-certificates.crt /run/secrets/build_ca > /tmp/ca.pem; \
      export PIP_CERT=/tmp/ca.pem; \
    fi; \
    pip install --no-compile --require-hashes -r requirements.txt; \
    rm -f /tmp/ca.pem; \
    useradd --system --uid 10001 --no-create-home --shell /usr/sbin/nologin traffic

COPY --chown=root:root traffic.py .
USER 10001
HEALTHCHECK --interval=30s --timeout=3s --start-period=30s --retries=3 \
  CMD ["python", "-c", "import os,sys,time; sys.exit(0 if time.time()-os.path.getmtime('/tmp/traffic.heartbeat') < 120 else 1)"]
CMD ["python", "traffic.py"]
