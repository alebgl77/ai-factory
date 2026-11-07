# syntax=docker/dockerfile:1@sha256:4edf897a3ffa55b89f906fc8cc78afdb3f1834cc9c7083565e611a8a7d5fe99e
# SPDX-License-Identifier: MIT
# DCGM-compatible GPU telemetry simulator (standard library only).
# Base image pinned by digest (multi-arch index); the tag stays for Dependabot, which updates both.
FROM python:3.12.16-slim@sha256:e695ab189fa2d082b1c61c69a6b888a5adeca61ceeccad107b1ed2a125721a9e

# Apply Debian DSA-6613-1 to the pinned Trixie base.
RUN apt-get update \
 && apt-get install -y --no-install-recommends --only-upgrade 'zlib1g=1:1.3.dfsg+really1.3.1-1+deb13u1' \
 && rm -rf /var/lib/apt/lists/*

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
WORKDIR /app
# The simulator uses the standard library only.
RUN python -m pip uninstall --yes pip \
 && rm -f /usr/local/lib/python3.*/ensurepip/_bundled/pip-*.whl \
 && useradd --system --uid 10001 --no-create-home --shell /usr/sbin/nologin gpusim
COPY --chown=root:root gpu_sim.py .
USER 10001
EXPOSE 9400
HEALTHCHECK --interval=15s --timeout=3s --start-period=10s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:9400/healthz', timeout=2)"]
CMD ["python", "gpu_sim.py"]
