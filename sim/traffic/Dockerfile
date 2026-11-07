# syntax=docker/dockerfile:1@sha256:4edf897a3ffa55b89f906fc8cc78afdb3f1834cc9c7083565e611a8a7d5fe99e
# SPDX-License-Identifier: MIT
# Synthetic traffic generator (fictional use cases uc-01..uc-08) for the key-less demo.
# Base image pinned by digest (multi-arch index); the tag stays for Dependabot, which updates both.
FROM python:3.12.16-slim@sha256:e695ab189fa2d082b1c61c69a6b888a5adeca61ceeccad107b1ed2a125721a9e

# Apply Debian DSA-6613-1 to the pinned Trixie base.
RUN apt-get update \
 && apt-get install -y --no-install-recommends --only-upgrade 'zlib1g=1:1.3.dfsg+really1.3.1-1+deb13u1' \
 && rm -rf /var/lib/apt/lists/*

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
# Hash-pinned lock (direct + transitive) compiled from requirements.in: pip refuses any file
# whose hash differs and any dependency missing from the lock.
COPY requirements.txt .
# Optional build secret "build_ca": extra CA certificates for TLS-intercepting proxies.
# pip and its bundled bootstrap wheel are build tools only.
RUN --mount=type=secret,id=build_ca,required=false \
    set -eu; \
    if [ -s /run/secrets/build_ca ]; then \
      cat /etc/ssl/certs/ca-certificates.crt /run/secrets/build_ca > /tmp/ca.pem; \
      export PIP_CERT=/tmp/ca.pem; \
    fi; \
    pip install --no-compile --require-hashes -r requirements.txt; \
    python -m pip uninstall --yes pip; \
    rm -f /usr/local/lib/python3.*/ensurepip/_bundled/pip-*.whl; \
    rm -f /tmp/ca.pem; \
    useradd --system --uid 10001 --no-create-home --shell /usr/sbin/nologin traffic

COPY --chown=root:root traffic.py .
USER 10001
HEALTHCHECK --interval=30s --timeout=3s --start-period=30s --retries=3 \
  CMD ["python", "-c", "import os,sys,time; sys.exit(0 if time.time()-os.path.getmtime('/tmp/traffic.heartbeat') < 120 else 1)"]
CMD ["python", "traffic.py"]
