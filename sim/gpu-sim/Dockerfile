# syntax=docker/dockerfile:1@sha256:4edf897a3ffa55b89f906fc8cc78afdb3f1834cc9c7083565e611a8a7d5fe99e
# SPDX-License-Identifier: MIT
# DCGM-compatible GPU telemetry simulator (standard library only).
# Base image pinned by digest (multi-arch index); the tag stays for Dependabot, which updates both.
FROM python:3.15.0-slim@sha256:f5b92f2a669a4c347044f29367f31983280b648250ca94dea66f98c53bc87e58

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
