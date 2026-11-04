"""Self-service catalogue engine: models, agents, tools and external GitHub repositories.

Enriches the catalogue with cross references (who uses what), the self-service approval path
and licence checks for the repositories integrated as git submodules.
"""

from __future__ import annotations

from typing import Any

from ..i18n import DEFAULT_LANG, msg

PERMISSIVE_LICENSES = {
    "MIT", "MIT-0", "Apache-2.0", "BSD-2-Clause", "BSD-3-Clause", "0BSD", "ISC", "PSF-2.0",
    "PostgreSQL", "Unlicense", "CC0-1.0", "Zlib", "BSL-1.0",
}
COPYLEFT_PREFIXES = ("GPL", "AGPL", "LGPL", "SSPL", "EUPL", "OSL", "CC-BY-SA")
_APPROVAL_BY_RISK = {"low": "none", "medium": "owner", "high": "governance"}
_APPROVAL_BY_TIER = {"minimal": "none", "limited": "owner", "high": "governance", "prohibited": "forbidden"}


def license_class(license_id: str | None) -> str:
    lic = (license_id or "").strip()
    if not lic or lic.upper() in ("NOASSERTION", "NONE", "UNKNOWN"):
        return "unknown"
    if lic in PERMISSIVE_LICENSES:
        return "permissive"
    if lic.upper().startswith(COPYLEFT_PREFIXES):
        return "copyleft"
    if lic.lower() == "proprietary":
        return "proprietary"
    return "other"


def evaluate_catalog(catalog: dict[str, Any], registry: list[dict[str, Any]], people: list[dict[str, Any]],
                     sensitive_classes: list[str], lang: str = DEFAULT_LANG) -> dict[str, Any]:
    names = {str(p.get("id")): p.get("name") for p in people or []}
    uc_by_id = {str(u.get("id")): u for u in registry}
    agents_cfg = [a for a in catalog.get("agents") or [] if isinstance(a, dict)]
    tools_cfg = [t for t in catalog.get("tools") or [] if isinstance(t, dict)]
    tool_ids = {str(t.get("id")) for t in tools_cfg}

    models = []
    for m in catalog.get("models") or []:
        if not isinstance(m, dict):
            continue
        allowed = list(m.get("allowed_classes") or [])
        users = [u["id"] for u in registry if (u.get("model") or {}).get("alias") == m.get("alias")]
        sensitive_ok = bool(set(allowed) & set(sensitive_classes)) and m.get("hosting") == "local"
        models.append({
            **m,
            "usecases": users,
            "sensitive_ok": sensitive_ok,
            "license_class": license_class(m.get("license")),
            "self_service": {"allowed": True, "approval": "none" if not sensitive_ok else "owner",
                             "note": msg(lang, "catalog.allowed_classes",
                                         classes=", ".join(allowed) if allowed else msg(lang, "catalog.unspecified"))},
        })

    agents = []
    for a in agents_cfg:
        uc = uc_by_id.get(str(a.get("usecase")), {})
        registry_tier = (uc.get("risk") or {}).get("tier")
        tier = registry_tier or a.get("risk_tier")
        missing_tools = [t for t in a.get("tools") or [] if str(t) not in tool_ids]
        agents.append({
            **a,
            "owner_name": names.get(str(a.get("owner"))),
            "usecase_name": uc.get("name"),
            "registry_tier": registry_tier,
            "risk_tier_mismatch": bool(registry_tier and a.get("risk_tier") and registry_tier != a.get("risk_tier")),
            "missing_tools": missing_tools,
            "self_service": {"allowed": tier != "prohibited" and a.get("eval_status") == "passed",
                             "approval": _APPROVAL_BY_TIER.get(tier or "minimal", "governance")},
        })

    tools = []
    for t in tools_cfg:
        used_by = [str(a.get("id")) for a in agents_cfg if str(t.get("id")) in [str(x) for x in a.get("tools") or []]]
        perms = [str(p) for p in t.get("permissions") or []]
        approval = _APPROVAL_BY_RISK.get(str(t.get("risk")), "governance")
        if any(p in ("write", "execute", "admin") for p in perms) and approval == "none":
            approval = "owner"
        tools.append({**t, "owner_name": names.get(str(t.get("owner"))), "used_by": used_by,
                      "self_service": {"allowed": True, "approval": approval}})

    repos = []
    for r in catalog.get("repos") or []:
        if not isinstance(r, dict):
            continue
        lic = license_class(r.get("license"))
        copyleft = list(r.get("copyleft_deps") or [])
        integrable = (lic == "permissive" and r.get("secrets_scan") == "clean" and not copyleft
                      and r.get("status") == "integrated")
        repos.append({**r, "license_class": lic, "integrable": integrable,
                      "needs_relicense": r.get("status") == "to_relicense" or lic in ("unknown", "other")})

    return {
        "models": models,
        "agents": agents,
        "tools": tools,
        "repos": repos,
        "counts": {"models": len(models), "agents": len(agents), "tools": len(tools), "repos": len(repos),
                   "repos_integrated": sum(1 for r in repos if r.get("status") == "integrated"),
                   "repos_to_relicense": sum(1 for r in repos if r.get("status") == "to_relicense")},
    }
