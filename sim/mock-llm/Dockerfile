# syntax=docker/dockerfile:1
# SPDX-License-Identifier: MIT
# OpenAI-compatible mock LLM (cloud / vps / local profiles) for the key-less demo.
FROM python:3.12.16-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
COPY requirements.txt .
# The optional build secret "build_ca" holds extra CA certificates for TLS-intercepting
# corporate proxies; it is never written to an image layer.
RUN --mount=type=secret,id=build_ca,required=false \
    set -eu; \
    if [ -s /run/secrets/build_ca ]; then \
      cat /etc/ssl/certs/ca-certificates.crt /run/secrets/build_ca > /tmp/ca.pem; \
      export PIP_CERT=/tmp/ca.pem; \
    fi; \
    pip install --no-compile -r requirements.txt; \
    rm -f /tmp/ca.pem; \
    useradd --system --uid 10001 --no-create-home --shell /usr/sbin/nologin mock

COPY --chown=root:root app.py .
USER 10001
EXPOSE 8080
HEALTHCHECK --interval=15s --timeout=3s --start-period=10s --retries=3 \
  CMD ["python", "-c", "import urllib.request,sys; urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=2)"]
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8080", "--no-access-log", "--timeout-keep-alive", "30"]
