# syntax=docker/dockerfile:1
# SPDX-License-Identifier: MIT
# One-shot job: Viewer service account + token for the aggregation API.
# Python standard library only (no package manager access needed at build time); the base
# image is shared with the simulators.
FROM python:3.12.16-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN useradd --system --uid 10001 --no-create-home --shell /usr/sbin/nologin bootstrap \
 && mkdir -p /var/run/factory/grafana \
 && chown 10001:10001 /var/run/factory/grafana \
 && chmod 0755 /var/run/factory/grafana
WORKDIR /app
COPY --chown=root:root bootstrap.py /app/bootstrap.py
USER 10001
ENTRYPOINT ["python", "/app/bootstrap.py"]
