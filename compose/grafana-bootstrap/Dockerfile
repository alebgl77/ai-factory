# syntax=docker/dockerfile:1@sha256:4edf897a3ffa55b89f906fc8cc78afdb3f1834cc9c7083565e611a8a7d5fe99e
# SPDX-License-Identifier: MIT
# One-shot job: Viewer service account + token for the aggregation API.
# Python standard library only; the base image is shared with the simulators.
# Base image pinned by digest (multi-arch index); the tag stays for Dependabot, which updates both.
FROM python:3.12.16-slim@sha256:e695ab189fa2d082b1c61c69a6b888a5adeca61ceeccad107b1ed2a125721a9e

# Apply Debian DSA-6613-1 to the pinned Trixie base.
RUN apt-get update \
 && apt-get install -y --no-install-recommends --only-upgrade 'zlib1g=1:1.3.dfsg+really1.3.1-1+deb13u1' \
 && rm -rf /var/lib/apt/lists/*

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN python -m pip uninstall --yes pip \
 && rm -f /usr/local/lib/python3.*/ensurepip/_bundled/pip-*.whl \
 && useradd --system --uid 10001 --no-create-home --shell /usr/sbin/nologin bootstrap \
 && mkdir -p /var/run/factory/grafana \
 && chown 10001:10001 /var/run/factory/grafana \
 && chmod 0755 /var/run/factory/grafana
WORKDIR /app
COPY --chown=root:root bootstrap.py /app/bootstrap.py
USER 10001
ENTRYPOINT ["python", "/app/bootstrap.py"]
