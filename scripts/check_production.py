#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Validate a rendered production Compose configuration read from standard input."""

import argparse
import ipaddress
import json
import re
import sys
from urllib.parse import urlsplit


ROLE_NAMES = {"viewer", "editor", "maintainer", "approver", "admin"}
IDENTITY_SERVICES = {"edge", "api", "oauth2-proxy"}
EDGE_PORTS = {(80, "tcp"), (443, "tcp"), (443, "udp"), (3001, "tcp"), (3002, "tcp")}
DEMO_SERVICES = {"mock-llm", "traffic", "gpu-sim"}
COMPOSE_GPU_BASES = {
    "LOCAL_API_BASE": ("vllm-local", "http://vllm-local:8000/v1"),
    "LOCAL_EMBED_API_BASE": ("vllm-embed", "http://vllm-embed:8000/v1"),
}
OPENAI_MODEL = re.compile(r"openai/[A-Za-z0-9][A-Za-z0-9_.:-]*(?:/[A-Za-z0-9][A-Za-z0-9_.:-]*)*")
VPS_HTTP_BASE = "http://ollama-vps:11434/v1"
PRIVATE_NETWORKS = tuple(ipaddress.ip_network(network) for network in (
    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "100.64.0.0/10", "fc00::/7",
))
OIDC_INSECURE_FLAGS = (
    "OAUTH2_PROXY_INSECURE_OIDC_ALLOW_UNVERIFIED_EMAIL",
    "OAUTH2_PROXY_INSECURE_OIDC_SKIP_ISSUER_VERIFICATION",
    "OAUTH2_PROXY_SSL_INSECURE_SKIP_VERIFY",
)
REQUIRED_CREDENTIALS = {
    "oauth2-proxy": ("OAUTH2_PROXY_CLIENT_ID", "OAUTH2_PROXY_CLIENT_SECRET", "OAUTH2_PROXY_COOKIE_SECRET"),
    "litellm": ("LITELLM_MASTER_KEY", "LITELLM_SALT_KEY"),
    "langfuse-web": ("SALT", "NEXTAUTH_SECRET", "LANGFUSE_INIT_PROJECT_PUBLIC_KEY",
                     "LANGFUSE_INIT_PROJECT_SECRET_KEY", "LANGFUSE_INIT_USER_PASSWORD"),
    "langfuse-worker": ("SALT", "NEXTAUTH_SECRET"),
    "grafana": ("GF_SECURITY_ADMIN_PASSWORD",),
    "postgres": ("POSTGRES_PASSWORD", "LITELLM_DB_PASSWORD", "LANGFUSE_DB_PASSWORD"),
    "clickhouse": ("CLICKHOUSE_PASSWORD",),
    "valkey": ("VALKEY_PASSWORD",),
    "seaweedfs": ("S3_ACCESS_KEY", "S3_SECRET_KEY"),
}


def _object(value):
    return value if isinstance(value, dict) else {}


def _value(environment, name):
    value = environment.get(name)
    return value if isinstance(value, str) else ""


def _hostname(value):
    if len(value) > 253 or value != value.lower() or "." not in value:
        return False
    if value.endswith((".test", ".example", ".invalid", ".localhost")):
        return False
    if any(value == domain or value.endswith("." + domain)
           for domain in ("example.com", "example.net", "example.org")):
        return False
    labels = value.split(".")
    return (labels[0] != "localhost" and not re.fullmatch(r"[0-9]+|0x[0-9a-f]+", labels[-1]) and all(
        re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in labels
    ))


def _http_url(value):
    if any(char.isspace() or ord(char) < 32 or char in "\\?#" for char in value):
        return None
    try:
        url = urlsplit(value)
        port = url.port
    except ValueError:
        return None
    if url.scheme not in {"http", "https"} or not url.hostname or url.username is not None or port == 0:
        return None
    return url


def _https_url(value):
    url = _http_url(value)
    return url is not None and url.scheme == "https" and _hostname(url.hostname)


def _provider_url(value):
    url = _http_url(value)
    if url is None or "%" in url.hostname:
        return None
    host = url.hostname
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        if len(host) > 253 or not all(re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
                                      for label in host.split(".")):
            return None
        authority = host
    else:
        authority = f"[{host}]" if address.version == 6 else host
    expected = authority if url.port is None else f"{authority}:{url.port}"
    return url if url.netloc.lower() == expected else None


def _https_provider_base(value):
    url = _provider_url(value)
    return url is not None and url.scheme == "https"


def _gateway_dns_override(gateway):
    return any(gateway.get(key) for key in ("extra_hosts", "links", "external_links", "dns", "dns_opt", "dns_search"))


def _private_compose_service(services, networks, project, target):
    gateway = services["litellm"]
    service = _object(services.get(target))
    factory = _object(networks.get("factory"))
    attached = _object(gateway.get("networks"))
    service_networks = _object(service.get("networks"))
    if (not isinstance(project, str) or not project or factory.get("internal") is not True
            or "factory" not in attached or "factory" not in service_networks or service.get("ports")
            or gateway.get("network_mode") or service.get("network_mode") or _gateway_dns_override(gateway)):
        return False
    # External or custom networks can host peers and aliases absent from this Compose model.
    managed = attached.keys() | service_networks.keys()
    network_names = {f"{project}_{key}" for key in managed}
    for key in managed:
        network = _object(networks.get(key))
        if (network.get("name") != f"{project}_{key}" or network.get("external") not in (None, False)
                or network.get("driver") not in (None, "bridge") or network.get("driver_opts")
                or network.get("attachable")):
            return False
    if any(_value(_object(networks[key]), "name") in network_names for key in networks.keys() - managed):
        return False
    # A Compose peer cannot also claim the name on a network visible to the gateway.
    for name, peer in services.items():
        if name == target:
            continue
        peer_networks = _object(peer.get("networks"))
        for network in attached.keys() & peer_networks.keys():
            aliases = _object(peer_networks[network]).get("aliases") or []
            if not isinstance(aliases, list) or any(
                isinstance(alias, str) and alias.rstrip(".").lower() == target
                for alias in (*aliases, name, peer.get("hostname"), peer.get("container_name"))
            ):
                return False
    return True


def _gpu_dns_name(value):
    return _hostname(value) and value.split(".", 1)[0] not in DEMO_SERVICES


def _local_gpu_url(key, value, services, networks, project, private_gpu_hosts):
    url = _provider_url(value)
    if url is None:
        return False
    host = url.hostname
    target, compose_base = COMPOSE_GPU_BASES[key]
    if value == compose_base:
        return _private_compose_service(services, networks, project, target)
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return _gpu_dns_name(host) and host in private_gpu_hosts and not _gateway_dns_override(services["litellm"])
    return any(address in network for network in PRIVATE_NETWORKS)


def _roles_valid(value):
    entries = [entry.rpartition(":") for entry in value.split(",") if entry.strip()]
    return bool(entries) and all(separator and group.strip() and role.strip().lower() in ROLE_NAMES
                                 for group, separator, role in entries)


def _configured(value):
    normalized = value.strip().lower()
    return (bool(normalized) and normalized not in {"password", "secret", "admin", "example"}
            and len(set(normalized)) > 1
            and not re.search(r"change[-_]?me|replace[-_]?me|not[-_]?a[-_]?secret", normalized))


def _edge_identity(services, networks):
    identity = _object(networks.get("edge-identity"))
    members = {name for name, service in services.items()
               if "edge-identity" in _object(service.get("networks"))}
    edge_network = _object(_object(services["edge"].get("networks")).get("edge-identity"))
    pools = _object(identity.get("ipam")).get("config")
    if identity.get("internal") is not True or members != IDENTITY_SERVICES:
        return None
    if not isinstance(pools, list) or len(pools) != 1:
        return None
    try:
        edge_ip = ipaddress.IPv4Address(_value(edge_network, "ipv4_address"))
        pool = _object(pools[0])
        subnet = ipaddress.IPv4Network(_value(pool, "subnet"))
        dynamic = ipaddress.IPv4Network(_value(pool, "ip_range"))
    except ValueError:
        return None
    if edge_ip not in subnet or edge_ip in {subnet.network_address, subnet.broadcast_address}:
        return None
    if not dynamic.subnet_of(subnet) or edge_ip in dynamic:
        return None
    return f"{edge_ip}/32"


def _port_allowed(port):
    if not isinstance(port, dict) or type(port.get("target")) is not int:
        return False
    target = port["target"]
    protocol = _value(port, "protocol")
    if (target, protocol) not in EDGE_PORTS:
        return False
    try:
        host_ip = ipaddress.ip_address(_value(port, "host_ip"))
        published = int(_value(port, "published"))
    except ValueError:
        return False
    return 1 <= published <= 65535 and (target not in {3001, 3002} or host_ip.is_loopback)


def _readonly_bind(service, target):
    mounts = service.get("volumes", [])
    if not isinstance(mounts, list):
        return False
    matching = [mount for mount in mounts if isinstance(mount, dict) and mount.get("target") == target]
    return (len(matching) == 1 and matching[0].get("type") == "bind"
            and matching[0].get("read_only") is True and bool(_value(matching[0], "source")))


def production_errors(config, *, private_gpu_hosts=()):
    services = _object(_object(config).get("services"))
    required = IDENTITY_SERVICES | REQUIRED_CREDENTIALS.keys()
    if not required.issubset(services) or not all(isinstance(service, dict) for service in services.values()):
        return ["Required Compose services are missing or invalid. Render the stack with --profile sso."]

    environments = {name: _object(service.get("environment")) for name, service in services.items()}
    edge, api, oauth, gateway = (environments[name] for name in ("edge", "api", "oauth2-proxy", "litellm"))
    errors = []

    host = _value(edge, "FACTORY_HOST")
    if _value(edge, "FACTORY_SCHEME") != "https" or not _hostname(host):
        errors.append("Set FACTORY_SCHEME to https and FACTORY_HOST to a production DNS hostname.")
    if _value(edge, "FACTORY_AUTH") != "sso" or _value(api, "FACTORY_AUTH_MODE") != "proxy":
        errors.append("Set FACTORY_AUTH to sso and FACTORY_AUTH_MODE to proxy.")
    if not _roles_valid(_value(api, "FACTORY_ROLE_MAP")) or api.get("FACTORY_DEFAULT_ROLE") != "":
        errors.append("Set an explicit FACTORY_ROLE_MAP and leave FACTORY_DEFAULT_ROLE empty.")
    if (_value(api, "FACTORY_DEMO_MODE") != "never"
            or _value(api, "FACTORY_WORKBENCH_MODE") not in {"off", "portfolio"}
            or DEMO_SERVICES.intersection(services)):
        errors.append("Disable demo mode and the sim profile; use only off or portfolio for the Workbench.")
    if _value(api, "FACTORY_WORKBENCH_MODE") == "portfolio":
        if _value(api, "FACTORY_WORKBENCH_CHAT_MODE") not in {"", "simulation"}:
            errors.append("Keep generic Workbench chat disabled in portfolio mode.")
        if _value(api, "FACTORY_WORKBENCH_CYBER_AUDIT") != "on":
            errors.append("Enable FACTORY_WORKBENCH_CYBER_AUDIT for portfolio decisions.")
    bu_policy = _value(api, "FACTORY_BU_POLICY")
    if (not re.fullmatch(r"/config/[A-Za-z0-9_-][A-Za-z0-9_.-]*\.ya?ml", bu_policy)
            or re.search(r"\.example\.ya?ml$", bu_policy) or not _readonly_bind(services["api"], "/config")):
        errors.append("Set FACTORY_BU_POLICY to an operator-owned policy in the read-only /config bind mount.")
    runner_path = _value(api, "FACTORY_PORTFOLIO_RUNNER_FILE")
    assist_enabled = _value(api, "FACTORY_ASSIST_ENABLED").strip().lower() == "on"
    if runner_path or assist_enabled:
        expected = "/run/secrets/factory_portfolio_runner"
        if runner_path != expected or not _readonly_bind(services["api"], expected):
            errors.append("Mount the private portfolio runner binding file read-only using its Compose overlay.")
        if (_value(api, "LITELLM_URL") != "http://litellm:4000"
                or _object(services["api"].get("deploy")).get("replicas", 1) != 1):
            errors.append("Keep the portfolio runner on one API replica and use the internal LiteLLM origin.")
    if assist_enabled:
        try:
            rate = int(_value(api, "FACTORY_RATE_LIMIT_PER_MIN") or "60")
        except ValueError:
            rate = 0
        if rate <= 0:
            errors.append("Keep write rate limiting enabled for FACTORY_ASSIST_ENABLED.")
    if _value(edge, "FACTORY_PRIVATE_TELEMETRY") != "disabled":
        errors.append("Disable direct Grafana and Langfuse edge routes for production BU scopes.")

    trusted = _edge_identity(services, _object(config.get("networks")))
    if (trusted is None or _value(api, "FACTORY_TRUSTED_PROXIES") != trusted
            or _value(oauth, "OAUTH2_PROXY_TRUSTED_PROXY_IPS") != trusted):
        errors.append("Limit identity headers to the fixed edge IPv4 /32 on the isolated edge-identity network.")

    if not _https_url(_value(oauth, "OAUTH2_PROXY_OIDC_ISSUER_URL")):
        errors.append("Set SSO_OIDC_ISSUER_URL to the production HTTPS issuer.")
    if any(oauth.get(flag, "false") != "false" for flag in OIDC_INSECURE_FLAGS):
        errors.append("Keep OAuth2 proxy verification of issuer, email and TLS certificates enabled.")
    if (_value(oauth, "OAUTH2_PROXY_REDIRECT_URL") != f"https://{host}/oauth2/callback"
            or _value(oauth, "OAUTH2_PROXY_WHITELIST_DOMAINS") != host
            or _value(oauth, "OAUTH2_PROXY_CODE_CHALLENGE_METHOD") != "S256"):
        errors.append("Use the exact HTTPS callback host and PKCE S256 for SSO.")
    domains = [domain.strip() for domain in _value(oauth, "OAUTH2_PROXY_EMAIL_DOMAINS").split(",")]
    groups = [group.strip() for group in _value(oauth, "OAUTH2_PROXY_ALLOWED_GROUPS").split(",")]
    if (not all(_hostname(domain.lstrip(".")) or domain == "*" for domain in domains)
            or ("*" in domains and not any(groups))):
        errors.append("Restrict SSO sign-in by production email domains or by explicit SSO_ALLOWED_GROUPS.")
    if (_value(oauth, "OAUTH2_PROXY_COOKIE_SECURE") != "true"
            or _value(oauth, "OAUTH2_PROXY_COOKIE_HTTPONLY") != "true"
            or _value(oauth, "OAUTH2_PROXY_COOKIE_SAMESITE") not in {"lax", "strict"}
            or not _value(oauth, "OAUTH2_PROXY_COOKIE_NAME").startswith("__Host-")
            or _value(oauth, "OAUTH2_PROXY_COOKIE_DOMAINS")
            or oauth.get("OAUTH2_PROXY_COOKIE_PATH", "/") != "/"):
        errors.append("Use a Secure, HttpOnly, SameSite SSO cookie with a __Host- name, root path and no Domain.")

    for service, keys in REQUIRED_CREDENTIALS.items():
        for key in keys:
            if not _configured(_value(environments[service], key)):
                errors.append(f"Replace the missing or placeholder credential {service}.{key}.")
    for service in ("langfuse-web", "langfuse-worker"):
        key = _value(environments[service], "ENCRYPTION_KEY")
        if not re.fullmatch(r"[0-9a-fA-F]{64}", key) or not _configured(key):
            errors.append(f"Set {service}.ENCRYPTION_KEY to a non-placeholder 64-character hexadecimal key.")
        if not _https_url(_value(environments[service], "NEXTAUTH_URL")):
            errors.append(f"Use a production HTTPS LANGFUSE_PUBLIC_URL for {service}.")
    grafana = environments["grafana"]
    cookie_mode = _value(grafana, "GRAFANA_COOKIE_SECURE")
    if (not _https_url(_value(grafana, "GF_SERVER_ROOT_URL")) or cookie_mode not in {"auto", "true"}
            or (cookie_mode == "auto" and _value(grafana, "FACTORY_SCHEME") != "https")):
        errors.append("Use a production HTTPS GRAFANA_PUBLIC_URL and enable secure Grafana cookies.")

    if (_value(gateway, "FACTORY_GUARDRAIL_MODE").strip().lower() != "block"
            or _value(gateway, "FACTORY_ENFORCE_SENSITIVE_ROUTING").lower() not in {"true", "1", "yes"}
            or _value(gateway, "FACTORY_PII_REDACTION").strip().lower() not in {"true", "1", "yes", "on"}
            or not {"C3", "C4"}.issubset(part.strip().upper() for part in
                                         _value(gateway, "FACTORY_SENSITIVE_CLASSES").split(","))):
        errors.append("Enable blocking LiteLLM guardrails, PII redaction and sensitive routing for C3 and C4.")
    if "LITELLM_LOG" in gateway and _value(gateway, "LITELLM_LOG").upper() not in {"WARNING", "ERROR", "CRITICAL"}:
        errors.append("Set LITELLM_LOG to WARNING, ERROR or CRITICAL for strict LiteLLM inference.")
    if (_value(gateway, "FACTORY_DEMO_MODE") != "never"
            or _value(gateway, "FACTORY_GATEWAY_POLICY") != "/run/factory-gateway/policy.json"
            or _value(gateway, "FACTORY_GATEWAY_REGISTRY") != "/factory-usecases"
            or not _readonly_bind(services["litellm"], "/run/factory-gateway/policy.json")
            or not _readonly_bind(services["litellm"], "/factory-usecases")):
        errors.append("Mount FACTORY_GATEWAY_POLICY and the use-case registry read-only for strict LiteLLM inference.")
    if (gateway.get("FACTORY_SENSITIVE_ALIAS", "chat-sensitive") != "chat-sensitive"
            or gateway.get("FACTORY_SENSITIVE_OK_ALIASES", "embed-default") != "embed-default"
            or gateway.get("FACTORY_LOCAL_ALIASES", "chat-sensitive,embed-default")
            != "chat-sensitive,embed-default"):
        errors.append("Keep the shipped LiteLLM sensitive and local alias allowlists for production.")
    if not _https_provider_base(_value(gateway, "CLOUD_API_BASE")):
        errors.append("Set CLOUD_API_BASE to an HTTPS provider URL without credentials, a query or a fragment.")
    vps_base = _value(gateway, "VPS_API_BASE")
    networks = _object(config.get("networks"))
    project = config.get("name")
    if not (_https_provider_base(vps_base) or (vps_base == VPS_HTTP_BASE
            and _private_compose_service(services, networks, project, "ollama-vps"))):
        errors.append("Set VPS_API_BASE to HTTPS or the unpublished Compose ollama-vps service on the factory network.")
    for key in COMPOSE_GPU_BASES:
        if not _local_gpu_url(key, _value(gateway, key), services, networks, project, private_gpu_hosts):
            errors.append(f"Set {key} to the expected private Compose GPU service, a private IP or an approved private GPU DNS host.")
    for key in ("LOCAL_CHAT_MODEL", "LOCAL_EMBED_MODEL"):
        model = _value(gateway, key)
        if len(model) > 256 or not OPENAI_MODEL.fullmatch(model):
            errors.append(f"Set {key} to an explicit openai/ model name for the private GPU endpoint.")
    for key in ("CLOUD_CHAT_MODEL", "CLOUD_SMALL_MODEL", "VPS_CHAT_MODEL"):
        model = _value(gateway, key)
        if len(model) > 256 or not OPENAI_MODEL.fullmatch(model):
            errors.append(f"Set {key} to an explicit openai/ model name for the strict provider transport.")

    ports = services["edge"].get("ports", [])
    if not isinstance(ports, list) or not all(_port_allowed(port) for port in ports):
        errors.append("Bind only supported edge ports to explicit IPs and keep alternative UI ports on loopback.")
    if any(service.get("network_mode") not in (None, "none") or (name != "edge" and service.get("ports"))
           or not isinstance(service.get("networks", {}), dict)
           for name, service in services.items()):
        errors.append("Use isolated Compose networks and publish host ports only from the edge service.")
    return errors


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--private-gpu-host", action="append", default=[], metavar="HOST",
                        help="allow a verified private GPU DNS hostname (repeatable, exact hostname only)")
    args = parser.parse_args(argv)
    if not all(_gpu_dns_name(host) for host in args.private_gpu_host):
        print("Production preflight failed. Expected an exact private GPU DNS hostname.", file=sys.stderr)
        return 1

    try:
        config = json.load(sys.stdin)
    except (ValueError, UnicodeError, RecursionError):
        print("Production preflight failed. Expected rendered Compose JSON on standard input.", file=sys.stderr)
        return 1

    errors = production_errors(config, private_gpu_hosts=frozenset(args.private_gpu_host))
    if errors:
        print("Production preflight failed.", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    print("Production preflight passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
