# syntax=docker/dockerfile:1
# SPDX-License-Identifier: MIT
# DCGM-compatible GPU telemetry simulator (standard library only).
FROM python:3.12.16-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
WORKDIR /app
RUN useradd --system --uid 10001 --no-create-home --shell /usr/sbin/nologin gpusim
COPY --chown=root:root gpu_sim.py .
USER 10001
EXPOSE 9400
HEALTHCHECK --interval=15s --timeout=3s --start-period=10s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:9400/healthz', timeout=2)"]
CMD ["python", "gpu_sim.py"]
