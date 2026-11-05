# syntax=docker/dockerfile:1@sha256:4edf897a3ffa55b89f906fc8cc78afdb3f1834cc9c7083565e611a8a7d5fe99e
# SPDX-License-Identifier: MIT
# One-shot job: Viewer service account + token for the aggregation API.
# Python standard library only (no package manager access needed at build time); the base
# image is shared with the simulators.
# Base image pinned by digest (multi-arch index); the tag stays for Dependabot, which updates both.
FROM python:3.15.0-slim@sha256:f5b92f2a669a4c347044f29367f31983280b648250ca94dea66f98c53bc87e58
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
