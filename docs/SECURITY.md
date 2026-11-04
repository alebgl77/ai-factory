# Security

*[Version française](fr/SECURITY.md)*

## Principles

- **No secret in the repository**: values come from `.env` (not versioned). `.env.example` only
  contains placeholder values. CI runs gitleaks on every push.
- **No token in the browser**: the dashboard only talks to the aggregation API, same origin,
  through the VPS proxy. The Grafana token (*Viewer* service account) is read server side from
  `GRAFANA_TOKEN_FILE` and never appears in any response.
- **Restricted Grafana proxy**: only GET requests to an allowlist of Grafana paths, and *named*
  PromQL queries defined server side. The browser cannot send arbitrary PromQL.
- **Minimal exposed surface**: only `edge` publishes ports. LiteLLM, Prometheus, Qdrant,
  ClickHouse, Postgres, Valkey and SeaweedFS stay on the internal network.
- **Sensitive data**: data classes C3 and C4 are only routed to the `chat-sensitive` alias,
  served by the local GPU. The placement policy queues the workload rather than sending it to
  the cloud. The dashboard flags any booking of a sensitive use case outside the secure Lab.
- **Sensitive routing enforced at the gateway**: the MIT LiteLLM callback reroutes every request
  labeled C3/C4 to `chat-sensitive` and masks its content in Langfuse
  (`FACTORY_ENFORCE_SENSITIVE_ROUTING`).
- **Networks**: `factory` is internal, with no Internet access; only `edge`, `litellm`, Ollama
  and vLLM join the `egress` network.
- **No training on your data**: `fine_tuning: false`. The API rejects any `retrain` or
  `fine_tune` action in the data loop.
- **Language does not widen the surface**: `?lang=en|fr` only selects strings from the
  configuration and the message catalog (`api/app/i18n.py`); unknown values fall back to the
  default language.

## Recommended hardening on a VPS

1. Set `FACTORY_HOST` to a real domain name: Caddy then obtains a TLS certificate automatically.
2. Enable proxy authentication (`FACTORY_AUTH=basic`, `FACTORY_BASIC_AUTH_USER`,
   `FACTORY_BASIC_AUTH_HASH` generated with `caddy hash-password`) or put an OIDC provider
   (oauth2-proxy, Authelia…) in front of `edge`.
3. Change **every** password and key from `.env.example`, for example with `openssl rand -hex 32`.
4. Restrict inbound ports to 80 and 443, and connect the local GPU to the VPS through an
   encrypted tunnel (WireGuard) rather than exposing vLLM.
5. Back up the `postgres-data`, `clickhouse-data`, `seaweedfs-data` and `api-state` volumes.

## Reporting a vulnerability

Open a private report (*Security advisory*) on the repository. Never publish a proof of concept
or a secret in a public issue.
