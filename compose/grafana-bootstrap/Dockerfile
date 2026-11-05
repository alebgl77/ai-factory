# syntax=docker/dockerfile:1@sha256:4edf897a3ffa55b89f906fc8cc78afdb3f1834cc9c7083565e611a8a7d5fe99e
# SPDX-License-Identifier: MIT
# One-shot job: Viewer service account + token for the aggregation API.
# Python standard library only (no package manager access needed at build time); the base
# image is shared with the simulators.
# Base image pinned by digest (multi-arch index); the tag stays for Dependabot, which updates both.
FROM python:3.12.16-slim@sha256:e695ab189fa2d082b1c61c69a6b888a5adeca61ceeccad107b1ed2a125721a9e
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN useradd --system --uid 10001 --no-create-home --shell /usr/sbin/nologin bootstrap \
 && mkdir -p /var/run/factory/grafana \
 && chown 10001:10001 /var/run/factory/grafana \
 && chmod 0755 /var/run/factory/grafana
WORKDIR /app
COPY --chown=root:root bootstrap.py /app/bootstrap.py
USER 10001
ENTRYPOINT ["python", "/app/bootstrap.py"]
