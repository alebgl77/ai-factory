#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Validate the GitOps configuration of the Virtual AI Factory (docs/CONTRACT.md section 2).

Checks that every YAML file parses and that identifiers are consistent across files:
people, roles, desks, lockers, squads, cost centers, use cases, models, agents, tools,
skills modules, sessions, R&D experiments and framework references used by the crosswalk.
It also enforces the factory guards: no fine-tuning, sensitive data classes on the local GPU
only, at least one use case per AI Act tier.

Languages (CONTRACT section 9): wherever text is expected, a value may be a plain string (same
text in every language) or a mapping ``{en: "...", fr: "..."}``. Every such mapping must carry
non-empty strings; a mapping that has only one of the two languages raises a warning.

Usage:
    python scripts/validate_config.py [--config config] [--today 2026-11-03] [--json] [--strict]

Exit code 1 when an error is found (warnings fail only with --strict). Standard library +
PyYAML only, so it runs in CI without the API dependencies.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import re
import sys
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:  # pragma: no cover - PyYAML is a dependency of the API and the CI job
    sys.stderr.write("PyYAML is required: pip install pyyaml\n")
    sys.exit(2)

ROOT = Path(__file__).resolve().parents[1]

# ---------------------------------------------------------------- contract vocabularies
LANGUAGES = ("en", "fr")      # CONTRACT section 9: English first, French second
ROLE_IDS = ["head", "po", "architect", "llm_engineer", "data_engineer", "eval_scientist", "llmops",
            "governance", "agent_ops", "research", "enablement"]
DESKS = ["D1", "D2", "D3", "D4", "D5", "D6", "B1", "B2", "X1", "X2", "X3", "O1", "O2"]
RND_BENCHES = ["X1", "X2", "X3"]
SKILLS_ROOM_SEATS = 8
STAGES = ["cadrage", "poc", "mvp", "indus", "run", "retired"]
TASK_TYPES = ["rag_qa", "agent", "classification", "extraction", "routing", "generation", "summarization"]
SENSITIVITY = ["C1", "C2", "C3", "C4"]
AI_ACT_ROLES = ["provider", "deployer", "both"]
ANNEX_III_AREAS = ["biometrics", "critical_infrastructure", "education", "employment", "essential_services",
                   "law_enforcement", "migration_border", "justice_democracy"]
TRANSPARENCY = ["chatbot", "synthetic_content", "emotion_recognition", "deepfake"]
DECISIONS = ["approved", "approved_with_conditions", "rejected", "pending"]
NIST_FUNCTIONS = ["govern", "map", "measure", "manage"]
NIST_STATUS = ["done", "partial", "todo"]
ALIASES = ["chat-default", "chat-sensitive", "chat-small", "embed-default"]
SENSITIVE_ALIAS = "chat-sensitive"
HOSTING = ["cloud", "vps", "local"]
TOOL_PERMISSIONS = ["read", "write", "execute", "admin"]
TOOL_RISK = ["low", "medium", "high"]
EVAL_STATUS = ["passed", "pending", "failed"]
TIERS = ["prohibited", "high", "limited", "minimal"]
CROSSWALK_TIERS = TIERS + ["gpai"]
RND_STAGES = ["explore", "prototype", "evaluate", "transfer", "stopped"]
REPO_PILLARS = ["rag", "evaluation", "multi_agents", "llmops_observability", "governance_security"]
REPO_STATUS = ["integrated", "to_relicense", "excluded"]
SECRETS_SCAN = ["clean", "findings", "not_scanned"]
# Same list as api/app/engines/catalog.py: licenses compatible with an MIT repository.
PERMISSIVE_LICENSES = {"MIT", "MIT-0", "Apache-2.0", "BSD-2-Clause", "BSD-3-Clause", "0BSD", "ISC", "PSF-2.0",
                       "PostgreSQL", "Unlicense", "CC0-1.0", "Zlib", "BSL-1.0"}
MILESTONES = ["prohibitions", "literacy", "gpai", "transparency", "annex_iii", "annex_i"]
WASTE_RULES = ["oversized_model", "low_cache_hit", "retry_storm", "prompt_bloat", "idle_gpu", "orphan_index",
               "eval_overspend", "budget_breach"]
OKABE_ITO = {"#e69f00", "#56b4e9", "#009e73", "#f0e442", "#0072b2", "#d55e00", "#cc79a7", "#999999", "#000000"}
# Status colors of the dashboard (light and dark themes): avatars must stay distinguishable.
STATUS_COLORS = ["#16a34a", "#dc2626", "#ea8a0c", "#2563eb", "#22c55e", "#f05252", "#f59e0b", "#4f8ff7"]
MIN_DELTA_E_STATUS = 25.0
MIN_DELTA_E_PEOPLE = 15.0
HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
FORBIDDEN_TRAINING = re.compile(r"fine[-_ ]?tun|finetun|retrain|r[ée]entra[iî]n|lora\b|rlhf", re.I)
# "Fictional" marker expected in the names of cost centers and use cases (no real data).
FICTIONAL_MARKERS = {"en": re.compile(r"\bfictional\b", re.I), "fr": re.compile(r"\bficti(f|ve)s?\b", re.I)}


class Report:
    def __init__(self) -> None:
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self.stats: dict[str, Any] = {}

    def err(self, where: str, msg: str) -> None:
        self.errors.append(f"{where}: {msg}")

    def warn(self, where: str, msg: str) -> None:
        self.warnings.append(f"{where}: {msg}")


# ---------------------------------------------------------------- helpers
def load_yaml(path: Path, rep: Report, root: Path) -> Any:
    rel = path.relative_to(root) if root in path.parents else path
    try:
        with path.open(encoding="utf-8") as fh:
            return yaml.safe_load(fh)
    except FileNotFoundError:
        rep.err(str(rel), "file not found")
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        where = f" at line {mark.line + 1}, column {mark.column + 1}" if mark else ""
        problem = getattr(exc, "problem", None) or exc.__class__.__name__
        rep.err(str(rel), f"invalid YAML{where}: {problem}")
    return None


def as_date(value: Any) -> dt.date | None:
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    if isinstance(value, str):
        try:
            return dt.date.fromisoformat(value)
        except ValueError:
            return None
    return None


def is_lang_mapping(value: Any) -> bool:
    """True for a dict whose keys are a non-empty subset of {en, fr} (same rule as api/app/i18n.py)."""
    return isinstance(value, dict) and bool(value) and all(isinstance(k, str) for k in value) \
        and set(value) <= set(LANGUAGES)


def is_text(value: Any) -> bool:
    """A non-empty plain string, or an {en, fr} mapping whose values are all non-empty strings."""
    if isinstance(value, str):
        return bool(value.strip())
    if is_lang_mapping(value):
        return all(isinstance(v, str) and v.strip() for v in value.values())
    return False


def text_variants(value: Any) -> list[str]:
    """Every language variant of a text value (a plain string counts for every language)."""
    if isinstance(value, str):
        return [value]
    if is_lang_mapping(value):
        return [v for v in value.values() if isinstance(v, str)]
    return []


def check_text(rep: Report, where: str, key: str, value: Any, required: bool = True) -> None:
    """Error when ``value`` is neither a plain string nor an {en, fr} mapping (or is missing)."""
    if value is None:
        if required:
            rep.err(where, f"{key} missing")
        return
    if not is_text(value):
        rep.err(where, f"{key} must be a non-empty string or an {{en, fr}} mapping")


def is_marked_fictional(value: Any) -> bool:
    """The text says it is fictional: 'fictional' in English, 'fictif'/'fictive' in French."""
    if isinstance(value, str):
        return any(rx.search(value) for rx in FICTIONAL_MARKERS.values())
    if is_lang_mapping(value):
        return all(isinstance(v, str) and FICTIONAL_MARKERS[lang].search(v) for lang, v in value.items())
    return False


def mentions_training(value: Any) -> bool:
    return any(FORBIDDEN_TRAINING.search(v) for v in text_variants(value))


def _lab(hex_color: str) -> tuple[float, float, float]:
    h = hex_color.lstrip("#")
    rgb = [int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4)]
    lin = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in rgb]
    r, g, b = lin
    x = (r * 0.4124 + g * 0.3576 + b * 0.1805) / 0.95047
    y = r * 0.2126 + g * 0.7152 + b * 0.0722
    z = (r * 0.0193 + g * 0.1192 + b * 0.9505) / 1.08883

    def f(t: float) -> float:
        return t ** (1 / 3) if t > 0.008856 else 7.787 * t + 16 / 116

    return 116 * f(y) - 16, 500 * (f(x) - f(y)), 200 * (f(y) - f(z))


def delta_e(a: str, b: str) -> float:
    """CIE76 color difference."""
    return math.dist(_lab(a), _lab(b))


def compute_tier(ai_act: dict[str, Any]) -> str:
    """Same rule as api/app/engines/risk.py (CONTRACT section 2.4)."""
    if ai_act.get("prohibited_practices"):
        return "prohibited"
    if (ai_act.get("annex_iii_area") and not ai_act.get("art6_3_exemption")) or ai_act.get("annex_i_safety_component"):
        return "high"
    if ai_act.get("transparency"):
        return "limited"
    return "minimal"


def base_article(ref: str) -> str:
    """'Art. 26(7)' -> 'Art. 26'."""
    m = re.match(r"^(Art\. \d+[a-z]?)", str(ref))
    return m.group(1) if m else str(ref)


def walk_strings(node: Any, path: str = ""):
    """Yield (key path, string). A string inside an {en, fr} mapping keeps the path of the mapping."""
    if is_lang_mapping(node):
        for v in node.values():
            if isinstance(v, str):
                yield path, v
    elif isinstance(node, dict):
        for k, v in node.items():
            yield from walk_strings(v, f"{path}.{k}" if path else str(k))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from walk_strings(v, f"{path}[{i}]")
    elif isinstance(node, str):
        yield path, node


def walk_lang_mappings(node: Any, path: str = ""):
    """Yield (key path, mapping) for every {en, fr} mapping of a YAML tree."""
    if is_lang_mapping(node):
        yield path, node
    elif isinstance(node, dict):
        for k, v in node.items():
            yield from walk_lang_mappings(v, f"{path}.{k}" if path else str(k))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from walk_lang_mappings(v, f"{path}[{i}]")


def check_labels(rep: Report, where: str, items: Any, keys: tuple[str, ...] = ("label",)) -> None:
    """Every item of a framework list carries the given text keys."""
    for item in items or []:
        if isinstance(item, dict):
            for key in keys:
                check_text(rep, f"{where} {item.get('id')}", key, item.get(key))


# ---------------------------------------------------------------- validation
def validate(config_dir: Path, today: dt.date) -> Report:
    rep = Report()
    root = config_dir.resolve()
    if not root.is_dir():
        rep.err(str(config_dir), "configuration directory not found")
        return rep

    # 1. Every YAML file parses.
    parsed: dict[Path, Any] = {}
    for path in sorted(root.rglob("*.y*ml")):
        parsed[path] = load_yaml(path, rep, root)
    rep.stats["yaml_files"] = len(parsed)

    def get(rel: str) -> Any:
        p = root / rel
        if p not in parsed:
            rep.err(rel, "file not found")
            return None
        return parsed[p]

    factory = get("factory.yaml") or {}
    profiles = get("profiles.yaml") or {}
    skills = get("skills.yaml") or {}
    models_doc = get("catalog/models.yaml") or {}
    agents_doc = get("catalog/agents.yaml") or {}
    tools_doc = get("catalog/tools.yaml") or {}
    nist_doc = get("frameworks/nist_ai_rmf.yaml") or {}
    act_doc = get("frameworks/ai_act.yaml") or {}
    iso_doc = get("frameworks/iso42001.yaml") or {}
    cw_doc = get("frameworks/crosswalk.yaml") or {}

    # 2. Bilingual values (CONTRACT section 9): {en, fr} mappings hold non-empty strings, both languages.
    mappings = incomplete = 0
    for path, data in parsed.items():
        rel = str(path.relative_to(root))
        for key_path, mapping in walk_lang_mappings(data):
            mappings += 1
            bad = [lang for lang, v in mapping.items() if not isinstance(v, str) or not v.strip()]
            if bad:
                rep.err(rel, f"{key_path}: language value(s) {', '.join(bad)} must be non-empty strings")
            missing = [lang for lang in LANGUAGES if lang not in mapping]
            if missing:
                incomplete += 1
                rep.warn(rel, f"{key_path}: bilingual mapping without {', '.join(missing)} "
                              f"(the other language is shown instead)")
    rep.stats["i18n"] = {"mappings": mappings, "incomplete": incomplete}

    # 3. Factory guards.
    f = factory.get("factory") or {}
    if f.get("fine_tuning") is not False:
        rep.err("factory.yaml", "factory.fine_tuning must be false (no fine-tuning)")
    if f.get("currency") != "EUR":
        rep.warn("factory.yaml", "the contract currency is EUR")
    check_text(rep, "factory.yaml", "factory.name", f.get("name"))
    check_text(rep, "factory.yaml", "factory.tagline", f.get("tagline"), required=False)
    ms = (((factory.get("regulatory") or {}).get("ai_act") or {}).get("milestones")) or []
    ms_ids = [m.get("id") for m in ms if isinstance(m, dict)]
    for mid in MILESTONES:
        if mid not in ms_ids:
            rep.err("factory.yaml", f"AI Act milestone '{mid}' missing")
    if len(ms_ids) != len(set(ms_ids)):
        rep.err("factory.yaml", "duplicate milestone ids")
    for m in ms:
        if not as_date(m.get("date")):
            rep.err("factory.yaml", f"milestone {m.get('id')}: invalid date")
        check_text(rep, f"factory.yaml milestone {m.get('id')}", "label", m.get("label"))
    cost_centers = [c for c in factory.get("cost_centers") or [] if isinstance(c, dict)]
    cc_ids = [str(c.get("id")) for c in cost_centers]
    if len(cc_ids) != len(set(cc_ids)):
        rep.err("factory.yaml", "duplicate cost centers")
    for c in cost_centers:
        if not isinstance(c.get("budget_month_eur"), (int, float)) or c["budget_month_eur"] <= 0:
            rep.err("factory.yaml", f"{c.get('id')}: budget_month_eur must be positive")
        check_text(rep, f"factory.yaml {c.get('id')}", "name", c.get("name"))
        if is_text(c.get("name")) and not is_marked_fictional(c.get("name")):
            rep.warn("factory.yaml", f"{c.get('id')}: the name should say it is fictional "
                                     f"(\"fictional\" in English, \"fictif\" in French)")
    for dc in factory.get("data_classes") or []:
        if isinstance(dc, dict):
            check_text(rep, f"factory.yaml data class {dc.get('id')}", "label", dc.get("label"))
    placement = factory.get("placement") or {}
    sensitive = set(placement.get("sensitive_classes") or ["C3", "C4"])
    if not {"C3", "C4"} <= sensitive:
        rep.err("factory.yaml", "placement.sensitive_classes must contain C3 and C4")
    if placement.get("order") and placement["order"][0] != "cloud":
        rep.warn("factory.yaml", "cloud-first policy: placement.order should start with cloud")
    rules = factory.get("waste_rules") or {}
    for rule in WASTE_RULES:
        if rule not in rules:
            rep.warn("factory.yaml", f"waste rule '{rule}' missing (disabled)")
    for tier in ("minimal", "limited", "high"):
        if tier not in (factory.get("eval_gates") or {}):
            rep.err("factory.yaml", f"eval_gates.{tier} missing")
    gates = factory.get("eval_gates") or {}
    if all(t in gates for t in ("minimal", "limited", "high")):
        for k in gates["minimal"]:
            if not (gates["minimal"].get(k, 0) <= gates["limited"].get(k, 0) <= gates["high"].get(k, 0)):
                rep.err("factory.yaml", f"eval_gates.{k} must get stricter as the risk increases")

    # 4. Profiles.
    roles = [r for r in profiles.get("roles") or [] if isinstance(r, dict)]
    role_ids = [str(r.get("id")) for r in roles]
    for rid in ROLE_IDS:
        if rid not in role_ids:
            rep.err("profiles.yaml", f"reference role '{rid}' missing")
    for rid in role_ids:
        if rid not in ROLE_IDS:
            rep.warn("profiles.yaml", f"role '{rid}' is not part of the reference profile")
    for r in roles:
        check_text(rep, f"profiles.yaml role {r.get('id')}", "label", r.get("label"))
        check_text(rep, f"profiles.yaml role {r.get('id')}", "description", r.get("description"), required=False)
    people = [p for p in profiles.get("people") or [] if isinstance(p, dict)]
    person_ids = [str(p.get("id")) for p in people]
    people_set = set(person_ids)
    if len(person_ids) != len(people_set):
        rep.err("profiles.yaml", "duplicate person ids")
    desks_used: dict[str, str] = {}
    lockers_used: dict[int, str] = {}
    team_day = (profiles.get("presence") or {}).get("team_day", 3)
    for p in people:
        pid = str(p.get("id"))
        where = f"profiles.yaml {pid}"
        if not is_text(p.get("name")):
            rep.err(where, "name missing")
        if p.get("role") not in role_ids:
            rep.err(where, f"unknown role '{p.get('role')}'")
        for key in ("color", "skin"):
            if not HEX_RE.match(str(p.get(key, ""))):
                rep.err(where, f"{key} must be a #rrggbb color")
        if HEX_RE.match(str(p.get("color", ""))):
            closest = min(delta_e(p["color"], s) for s in STATUS_COLORS)
            if closest < MIN_DELTA_E_STATUS:
                rep.err(where, f"color {p['color']} too close to a status color (ΔE {closest:.1f})")
        desk = p.get("desk")
        if desk not in DESKS:
            rep.err(where, f"desk '{desk}' is not one of the 3D scene desks {DESKS}")
        elif desk in desks_used:
            rep.err(where, f"desk {desk} already assigned to {desks_used[desk]}")
        else:
            desks_used[desk] = pid
        locker = p.get("locker")
        if not isinstance(locker, int) or not 1 <= locker <= 12:
            rep.err(where, "locker expected between 1 and 12")
        elif locker in lockers_used:
            rep.err(where, f"locker {locker} already assigned to {lockers_used[locker]}")
        else:
            lockers_used[locker] = pid
        if p.get("cost_center") not in cc_ids:
            rep.err(where, f"unknown cost center '{p.get('cost_center')}'")
        pattern = p.get("pattern")
        if not (isinstance(pattern, list) and len(pattern) == 5 and all(x in (0, 1) for x in pattern)):
            rep.err(where, "pattern expected: 5 values 0/1 (Monday to Friday)")
        elif isinstance(team_day, int) and 0 <= team_day < 5 and pattern[team_day] != 1:
            rep.warn(where, "absent on the team day")
        if not isinstance(p.get("skills"), list):
            rep.err(where, "skills must be a list")
        else:
            for i, skill in enumerate(p["skills"]):
                check_text(rep, where, f"skills[{i}]", skill)
    colored = [p for p in people if HEX_RE.match(str(p.get("color", "")))]
    for i, a in enumerate(colored):
        for b in colored[i + 1:]:
            d = delta_e(a["color"], b["color"])
            if d < MIN_DELTA_E_PEOPLE:
                rep.warn("profiles.yaml", f"{a['id']} and {b['id']} have similar colors (ΔE {d:.1f})")
    roles_held = {p.get("role") for p in people}
    for rid in ROLE_IDS:
        if rid in role_ids and rid not in roles_held:
            rep.warn("profiles.yaml", f"nobody holds the role '{rid}'")
    rep.stats["people"] = len(people)

    # 5. Catalog (models first: use cases reference aliases).
    models = [m for m in models_doc.get("models") or [] if isinstance(m, dict)]
    model_ids = [str(m.get("id")) for m in models]
    if len(model_ids) != len(set(model_ids)):
        rep.err("catalog/models.yaml", "duplicate model ids")
    primary_by_alias: dict[str, dict[str, Any]] = {}
    for m in models:
        where = f"catalog/models.yaml {m.get('id')}"
        check_text(rep, where, "name", m.get("name"))
        alias = m.get("alias")
        if alias not in ALIASES:
            rep.err(where, f"unknown LiteLLM alias '{alias}'")
        primary_by_alias.setdefault(str(alias), m)
        if m.get("hosting") not in HOSTING:
            rep.err(where, f"hosting expected in {HOSTING}")
        allowed = set(m.get("allowed_classes") or [])
        if not allowed <= set(SENSITIVITY):
            rep.err(where, "allowed_classes outside C1..C4")
        if allowed & sensitive and m.get("hosting") != "local":
            rep.err(where, "a model allowed for C3/C4 must be hosted on the local GPU")
        for k in ("price_in_per_1k", "price_out_per_1k"):
            if not isinstance(m.get(k), (int, float)) or m[k] < 0:
                rep.err(where, f"{k} must be a number greater than or equal to zero")
        if m.get("hosting") == "vps" and isinstance(m.get("params_b"), (int, float)) \
                and m["params_b"] > float(placement.get("vps_max_params_b", 8)):
            rep.err(where, "model too large for the VPS (placement.vps_max_params_b)")
        for fb in m.get("fallbacks") or []:
            if fb not in model_ids:
                rep.err(where, f"unknown fallback '{fb}'")
    for alias in ALIASES:
        if alias not in primary_by_alias:
            rep.err("catalog/models.yaml", f"no model for the alias {alias}")
    sens_model = primary_by_alias.get(SENSITIVE_ALIAS)
    if sens_model and sens_model.get("hosting") != "local":
        rep.err("catalog/models.yaml", "chat-sensitive must be served by the local GPU")

    tools = [t for t in tools_doc.get("tools") or [] if isinstance(t, dict)]
    tool_ids = [str(t.get("id")) for t in tools]
    if len(tool_ids) != len(set(tool_ids)):
        rep.err("catalog/tools.yaml", "duplicate tool ids")
    for t in tools:
        where = f"catalog/tools.yaml {t.get('id')}"
        check_text(rep, where, "name", t.get("name"), required=False)
        check_text(rep, where, "description", t.get("description"), required=False)
        if t.get("owner") not in people_set:
            rep.err(where, f"unknown owner '{t.get('owner')}'")
        if not set(t.get("permissions") or []) <= set(TOOL_PERMISSIONS) or not t.get("permissions"):
            rep.err(where, f"permissions expected in {TOOL_PERMISSIONS}")
        if t.get("risk") not in TOOL_RISK:
            rep.err(where, f"risk expected in {TOOL_RISK}")
        if t.get("kind") not in ("mcp", "function"):
            rep.warn(where, "kind expected: mcp")

    # 6. Use cases.
    uc_dir = root / "usecases"
    usecases: dict[str, dict[str, Any]] = {}
    for path in sorted(uc_dir.glob("*.y*ml")) if uc_dir.is_dir() else []:
        uc = parsed.get(path)
        where = f"usecases/{path.name}"
        if not isinstance(uc, dict) or not uc.get("id"):
            rep.err(where, "id missing")
            continue
        uid = str(uc["id"])
        if uid in usecases:
            rep.err(where, f"duplicate id {uid}")
            continue
        if path.stem != uid:
            rep.err(where, f"the file name must be {uid}.yaml")
        usecases[uid] = uc
    if not usecases:
        rep.err("usecases/", "no use case")
    iso_controls = {str(c.get("id")) for obj in iso_doc.get("annex_a") or [] for c in obj.get("controls") or []}
    tiers_seen: dict[str, list[str]] = {}
    colors_seen: dict[str, str] = {}
    mismatched = []
    for uid, uc in usecases.items():
        where = f"usecases/{uid}.yaml"
        for key in ("code", "name", "description", "owner", "cost_center", "stage", "task_type", "sensitivity",
                    "color", "ai_act", "model", "assessment", "nist", "iso42001", "kpis"):
            if key not in uc:
                rep.err(where, f"key '{key}' missing")
        for key in ("name", "description"):
            if key in uc:
                check_text(rep, where, key, uc.get(key))
        if is_text(uc.get("name")) and not is_marked_fictional(uc.get("name")):
            rep.err(where, "the name must say \"(fictional)\" in English and \"(fictif)\" in French (no real data)")
        if uc.get("owner") not in people_set:
            rep.err(where, f"unknown owner '{uc.get('owner')}'")
        if uc.get("cost_center") not in cc_ids:
            rep.err(where, f"unknown cost center '{uc.get('cost_center')}'")
        if uc.get("stage") not in STAGES:
            rep.err(where, f"stage expected in {STAGES}")
        if uc.get("task_type") not in TASK_TYPES:
            rep.err(where, f"task_type expected in {TASK_TYPES}")
        if uc.get("sensitivity") not in SENSITIVITY:
            rep.err(where, "sensitivity expected in C1..C4")
        color = str(uc.get("color", "")).lower()
        if not HEX_RE.match(color):
            rep.err(where, "color must be #rrggbb")
        else:
            if color not in OKABE_ITO:
                rep.warn(where, f"color {color} outside the Okabe-Ito palette")
            if color in colors_seen:
                rep.err(where, f"color already used by {colors_seen[color]}")
            colors_seen[color] = uid
        a = uc.get("ai_act") or {}
        if a.get("role") not in AI_ACT_ROLES:
            rep.err(where, f"ai_act.role expected in {AI_ACT_ROLES}")
        if a.get("annex_iii_area") is not None and a.get("annex_iii_area") not in ANNEX_III_AREAS:
            rep.err(where, f"unknown annex_iii_area '{a.get('annex_iii_area')}'")
        if a.get("annex_iii_area") and not a.get("annex_iii_use"):
            rep.err(where, "annex_iii_use must name the Annex III point")
        if a.get("art6_3_exemption"):
            if not a.get("annex_iii_area"):
                rep.err(where, "Art. 6(3) derogation without an Annex III area")
            basis = a.get("art6_3_basis") or {}
            if not basis.get("record"):
                rep.err(where, "undocumented Art. 6(3) derogation (art6_3_basis.record, Art. 6(4))")
            if basis.get("profiling"):
                rep.err(where, "Art. 6(3): a system that performs profiling is always high-risk")
        if not set(a.get("transparency") or []) <= set(TRANSPARENCY):
            rep.err(where, f"transparency expected in {TRANSPARENCY}")
        tier = compute_tier(a)
        tiers_seen.setdefault(tier, []).append(uid)
        model = uc.get("model") or {}
        alias = model.get("alias")
        if alias not in ALIASES:
            rep.err(where, f"unknown model alias '{alias}'")
        if uc.get("sensitivity") in sensitive:
            if alias != SENSITIVE_ALIAS:
                rep.err(where, f"{uc.get('sensitivity')} => alias {SENSITIVE_ALIAS} required (local GPU)")
        else:
            prim = primary_by_alias.get(str(alias))
            if prim and uc.get("sensitivity") not in (prim.get("allowed_classes") or SENSITIVITY):
                rep.err(where, f"model {prim.get('id')} does not accept the class {uc.get('sensitivity')}")
        history = [h for h in model.get("history") or [] if isinstance(h, dict)]
        versions = [str(h.get("version")) for h in history]
        if model.get("version") is None:
            rep.err(where, "model.version missing")
        elif uc.get("stage") not in ("cadrage", "retired") and str(model["version"]) not in versions:
            rep.err(where, "model.version not found in model.history")
        dates = [as_date(h.get("from")) for h in history]
        if any(d is None for d in dates):
            rep.err(where, "model.history.from: invalid date")
        elif dates != sorted(dates):
            rep.err(where, "model.history must be in chronological order")
        ass = uc.get("assessment") or {}
        if ass.get("decision") not in DECISIONS:
            rep.err(where, f"assessment.decision expected in {DECISIONS}")
        if ass.get("reviewer") not in people_set:
            rep.err(where, f"unknown reviewer '{ass.get('reviewer')}'")
        if ass.get("last_reviewed") is not None and not as_date(ass.get("last_reviewed")):
            rep.err(where, "assessment.last_reviewed: invalid date")
        if str(model.get("version")) != str(ass.get("reviewed_model_version")) or ass.get("decision") == "pending":
            mismatched.append(uid)
        nist = uc.get("nist") or {}
        for fn in NIST_FUNCTIONS:
            entry = nist.get(fn)
            status = entry.get("status") if isinstance(entry, dict) else entry
            if status not in NIST_STATUS:
                rep.err(where, f"nist.{fn}.status expected in {NIST_STATUS}")
        for ctrl in uc.get("iso42001") or []:
            if iso_controls and str(ctrl) not in iso_controls:
                rep.err(where, f"unknown ISO/IEC 42001 control '{ctrl}' (Annex A)")
        rag = uc.get("rag")
        if rag:
            if rag.get("embedding") not in ALIASES:
                rep.err(where, f"unknown rag.embedding '{rag.get('embedding')}'")
            if not rag.get("index"):
                rep.err(where, "rag.index missing")
            cs, co = rag.get("chunk_size"), rag.get("chunk_overlap")
            if isinstance(cs, int) and isinstance(co, int) and not 0 <= co < cs:
                rep.err(where, "rag.chunk_overlap must be lower than chunk_size")
        for k, v in (uc.get("eval") or {}).items():
            if k.endswith("_alias") and v not in ALIASES:
                rep.err(where, f"unknown eval.{k} '{v}'")
            if k.endswith("_alias") and uc.get("sensitivity") in sensitive and v != SENSITIVE_ALIAS:
                rep.err(where, f"eval.{k}: a judge for {uc.get('sensitivity')} data must stay on the local GPU")
    for tier in ("high", "limited", "minimal"):
        if tier not in tiers_seen:
            rep.err("usecases/", f"no use case of tier '{tier}'")
    if len(mismatched) < 1:
        rep.warn("usecases/", "no use case to reassess: the risk_reassessment gate is not demonstrated")
    rep.stats["usecases"] = len(usecases)
    rep.stats["tiers"] = {t: sorted(v) for t, v in sorted(tiers_seen.items())}
    rep.stats["reevaluation_required"] = sorted(mismatched)

    # Squads reference existing use cases.
    for p in people:
        for s in p.get("squads") or []:
            if s not in usecases:
                rep.err(f"profiles.yaml {p.get('id')}", f"squad points to an unknown use case '{s}'")

    # 7. Agents.
    agents = [a for a in agents_doc.get("agents") or [] if isinstance(a, dict)]
    agent_ids = [str(a.get("id")) for a in agents]
    if len(agent_ids) != len(set(agent_ids)):
        rep.err("catalog/agents.yaml", "duplicate agent ids")
    for a in agents:
        where = f"catalog/agents.yaml {a.get('id')}"
        for key in ("name", "pattern", "human_in_the_loop", "note"):
            check_text(rep, where, key, a.get(key), required=False)
        if a.get("owner") not in people_set:
            rep.err(where, f"unknown owner '{a.get('owner')}'")
        uc = usecases.get(str(a.get("usecase")))
        if uc is None:
            rep.err(where, f"unknown use case '{a.get('usecase')}'")
        else:
            expected = compute_tier(uc.get("ai_act") or {})
            if a.get("risk_tier") != expected:
                rep.err(where, f"risk_tier '{a.get('risk_tier')}' differs from the registry tier '{expected}'")
            alias = a.get("model_alias")
            if alias and uc.get("sensitivity") in sensitive and alias != SENSITIVE_ALIAS:
                rep.err(where, "agent of a sensitive use case: model_alias chat-sensitive required")
        for t in a.get("tools") or []:
            if t not in tool_ids:
                rep.err(where, f"unknown tool '{t}'")
        if a.get("model_alias") is not None and a.get("model_alias") not in ALIASES:
            rep.err(where, f"unknown model_alias '{a.get('model_alias')}'")
        if a.get("framework") != "LangGraph":
            rep.warn(where, "reference framework: LangGraph")
        if a.get("eval_status") not in EVAL_STATUS:
            rep.err(where, f"eval_status expected in {EVAL_STATUS}")
        if uc is not None:
            for t in tools:
                declared = t.get("data_classes") or SENSITIVITY
                if t.get("id") in (a.get("tools") or []) and uc.get("sensitivity") not in declared:
                    rep.warn(where, f"tool {t.get('id')} not declared for the class {uc.get('sensitivity')}")

    # 8. Skills Lab and R&D Lab.
    modules = [m for m in skills.get("modules") or [] if isinstance(m, dict)]
    module_ids = [str(m.get("id")) for m in modules]
    if len(module_ids) != len(set(module_ids)):
        rep.err("skills.yaml", "duplicate modules")
    for m in modules:
        check_text(rep, f"skills.yaml module {m.get('id')}", "title", m.get("title"))
        for key in ("audience", "outcome"):
            check_text(rep, f"skills.yaml module {m.get('id')}", key, m.get(key), required=False)
    art4_modules = {str(m["id"]) for m in modules if m.get("art4")}
    reqs = skills.get("requirements") or {}
    for role, mods in reqs.items():
        if role not in role_ids:
            rep.err("skills.yaml", f"requirements for an unknown role '{role}'")
        for mid in mods or []:
            if mid not in module_ids:
                rep.err("skills.yaml", f"{role}: unknown module '{mid}'")
        if not set(mods or []) & art4_modules:
            rep.err("skills.yaml", f"{role}: no Art. 4 measure (module with art4: true)")
    for rid in ROLE_IDS:
        if rid in roles_held and rid not in reqs:
            rep.err("skills.yaml", f"no requirement for the role '{rid}' (Art. 4: measures per role)")
    done = 0
    seen_completion = set()
    for c in skills.get("completions") or []:
        where = f"skills.yaml completion {c.get('person')}/{c.get('module')}"
        if c.get("person") not in people_set:
            rep.err(where, "unknown person")
        if c.get("module") not in module_ids:
            rep.err(where, "unknown module")
        d = as_date(c.get("date"))
        if d is None:
            rep.err(where, "invalid date")
        elif d > today:
            rep.warn(where, f"future date ({d}), not counted")
        key = (c.get("person"), c.get("module"))
        if key in seen_completion:
            rep.warn(where, "duplicate completion")
        seen_completion.add(key)
    role_of = {str(p.get("id")): p.get("role") for p in people}
    required_total = 0
    for pid, role in role_of.items():
        mods = reqs.get(role) or []
        required_total += len(mods)
        done += sum(1 for m in mods if any(c.get("person") == pid and c.get("module") == m
                                           and (as_date(c.get("date")) or today) <= today
                                           for c in skills.get("completions") or []))
    coverage = round(done / required_total, 2) if required_total else 1.0
    rep.stats["skills_coverage"] = coverage
    session_ids = []
    for s in skills.get("sessions") or []:
        where = f"skills.yaml session {s.get('id')}"
        session_ids.append(s.get("id"))
        check_text(rep, where, "title", s.get("title"), required=False)
        if s.get("module") not in module_ids:
            rep.err(where, f"unknown module '{s.get('module')}'")
        if s.get("trainer") not in people_set:
            rep.err(where, f"unknown trainer '{s.get('trainer')}'")
        attendees = s.get("attendees") or []
        for a in attendees:
            if a not in people_set:
                rep.err(where, f"unknown attendee '{a}'")
        if s.get("trainer") in attendees:
            rep.err(where, "the trainer cannot be an attendee")
        if len(attendees) > SKILLS_ROOM_SEATS:
            rep.err(where, f"more than {SKILLS_ROOM_SEATS} attendees (room S1)")
        if s.get("room", "S1") != "S1":
            rep.err(where, "room expected: S1 (Skills Lab)")
        start, end = str(s.get("start", "")), str(s.get("end", ""))
        if not (TIME_RE.match(start) and TIME_RE.match(end) and start < end):
            rep.err(where, "invalid HH:MM times")
        d = as_date(s.get("date"))
        if d is None:
            rep.err(where, "invalid date")
        elif d.weekday() >= 5:
            rep.warn(where, "session on a weekend")
    if len(session_ids) != len(set(session_ids)):
        rep.err("skills.yaml", "duplicate session ids")
    # Room clashes on S1.
    by_day: dict[Any, list[dict[str, Any]]] = {}
    for s in skills.get("sessions") or []:
        by_day.setdefault(s.get("date"), []).append(s)
    for day, items in by_day.items():
        items = sorted(items, key=lambda x: str(x.get("start")))
        for x, y in zip(items, items[1:], strict=False):
            if str(y.get("start")) < str(x.get("end")):
                rep.err("skills.yaml", f"room S1 double-booked on {day}: {x.get('id')} / {y.get('id')}")
    upcoming = [s for s in skills.get("sessions") or [] if (as_date(s.get("date")) or today) >= today]
    if not upcoming:
        rep.warn("skills.yaml", "no upcoming Skills Lab session")
    experiments = [x for x in ((skills.get("rnd") or {}).get("experiments") or []) if isinstance(x, dict)]
    x_ids = [str(x.get("id")) for x in experiments]
    if len(x_ids) != len(set(x_ids)):
        rep.err("skills.yaml", "duplicate R&D experiments")
    for x in experiments:
        where = f"skills.yaml rnd {x.get('id')}"
        if x.get("stage") not in RND_STAGES:
            rep.err(where, f"stage expected in {RND_STAGES}")
        if x.get("owner") not in people_set:
            rep.err(where, f"unknown owner '{x.get('owner')}'")
        if x.get("linked_usecase") is not None and x.get("linked_usecase") not in usecases:
            rep.err(where, f"unknown linked use case '{x.get('linked_usecase')}'")
        if x.get("bench") is not None and x.get("bench") not in RND_BENCHES:
            rep.err(where, f"bench expected in {RND_BENCHES}")
        check_text(rep, where, "title", x.get("title"))
        check_text(rep, where, "hypothesis", x.get("hypothesis"), required=False)
        check_text(rep, where, "transfer_criteria", x.get("transfer_criteria"))
        check_text(rep, where, "result", x.get("result"), required=False)
        if not isinstance(x.get("gpu_hours"), (int, float)) or x["gpu_hours"] < 0:
            rep.err(where, "gpu_hours must be greater than or equal to zero")
        for key in ("title", "hypothesis"):
            if mentions_training(x.get(key)):
                rep.err(where, f"{key} refers to fine-tuning or retraining (forbidden)")

    # 9. Frameworks and crosswalk.
    nist_sub = set()
    nist_cat = set()
    for fn in nist_doc.get("functions") or []:
        check_text(rep, f"frameworks/nist_ai_rmf.yaml {fn.get('id')}", "label", fn.get("label"))
        for cat in fn.get("categories") or []:
            nist_cat.add(str(cat.get("id")))
            check_text(rep, f"frameworks/nist_ai_rmf.yaml {cat.get('id')}", "label", cat.get("label"))
            for sub in cat.get("subcategories") or []:
                nist_sub.add(str(sub.get("id")))
                check_text(rep, f"frameworks/nist_ai_rmf.yaml {sub.get('id')}", "label", sub.get("label"))
    counts = (nist_doc.get("framework") or {}).get("counts") or {}
    if counts and (len(nist_cat) != counts.get("categories") or len(nist_sub) != counts.get("subcategories")):
        rep.err("frameworks/nist_ai_rmf.yaml",
                f"{len(nist_cat)} categories / {len(nist_sub)} subcategories, expected {counts}")
    if [fn.get("id") for fn in nist_doc.get("functions") or []] != NIST_FUNCTIONS:
        rep.err("frameworks/nist_ai_rmf.yaml", "functions expected: govern, map, measure, manage")
    for prof in nist_doc.get("profiles") or []:
        if isinstance(prof, dict):
            check_labels(rep, f"frameworks/nist_ai_rmf.yaml profile {prof.get('id')}", prof.get("risks"))
    articles = {str(a.get("id")) for a in act_doc.get("articles") or []}
    check_labels(rep, "frameworks/ai_act.yaml", act_doc.get("articles"), ("title", "summary"))
    check_labels(rep, "frameworks/ai_act.yaml tier", act_doc.get("tiers"))
    check_labels(rep, "frameworks/ai_act.yaml role", act_doc.get("roles"))
    check_labels(rep, "frameworks/ai_act.yaml transparency", act_doc.get("transparency_duties"))
    check_labels(rep, "frameworks/ai_act.yaml annex_iii", act_doc.get("annex_iii"), ("label", "scope"))
    check_labels(rep, "frameworks/ai_act.yaml art6_3", act_doc.get("art6_3_conditions"))
    areas = [str(a.get("id")) for a in act_doc.get("annex_iii") or []]
    if areas != ANNEX_III_AREAS:
        rep.err("frameworks/ai_act.yaml", f"Annex III areas expected (contract order): {ANNEX_III_AREAS}")
    for art in ("Art. 4", "Art. 5", "Art. 6", "Art. 9", "Art. 10", "Art. 11", "Art. 12", "Art. 13", "Art. 14",
                "Art. 15", "Art. 17", "Art. 26", "Art. 27", "Art. 50", "Art. 72", "Art. 73"):
        if art not in articles:
            rep.err("frameworks/ai_act.yaml", f"key article {art} missing")
    iso_clauses = set()
    for cl in iso_doc.get("clauses") or []:
        iso_clauses.add(str(cl.get("id")))
        check_text(rep, f"frameworks/iso42001.yaml clause {cl.get('id')}", "label", cl.get("label"))
        for sub in cl.get("subclauses") or []:
            iso_clauses.add(str(sub.get("id")))
            check_text(rep, f"frameworks/iso42001.yaml clause {sub.get('id')}", "label", sub.get("label"))
    for obj in iso_doc.get("annex_a") or []:
        check_text(rep, f"frameworks/iso42001.yaml {obj.get('id')}", "label", obj.get("label"))
        check_text(rep, f"frameworks/iso42001.yaml {obj.get('id')}", "objective", obj.get("objective"), required=False)
        check_labels(rep, "frameworks/iso42001.yaml", obj.get("controls"))
    iso_counts = (iso_doc.get("standard") or {}).get("counts") or {}
    if iso_counts and len(iso_controls) != iso_counts.get("annex_a_controls"):
        rep.err("frameworks/iso42001.yaml",
                f"{len(iso_controls)} Annex A controls, expected {iso_counts.get('annex_a_controls')}")
    if [str(c.get("id")) for c in iso_doc.get("clauses") or []] != [str(n) for n in range(4, 11)]:
        rep.err("frameworks/iso42001.yaml", "clauses 4 to 10 expected")
    controls = [c for c in cw_doc.get("controls") or [] if isinstance(c, dict)]
    cw_ids = [str(c.get("id")) for c in controls]
    if len(cw_ids) != len(set(cw_ids)):
        rep.err("frameworks/crosswalk.yaml", "duplicate control families")
    if not 15 <= len(controls) <= 25:
        rep.warn("frameworks/crosswalk.yaml", f"{len(controls)} families (15 to 25 expected)")
    if not (cw_doc.get("meta") or {}).get("indicative"):
        rep.err("frameworks/crosswalk.yaml", "meta.indicative: true expected (indicative mapping)")
    for c in controls:
        where = f"frameworks/crosswalk.yaml {c.get('id')}"
        for ref in c.get("nist") or []:
            if ref not in nist_sub:
                rep.err(where, f"unknown NIST subcategory '{ref}'")
        for ref in c.get("ai_act") or []:
            if base_article(ref) not in articles:
                rep.err(where, f"AI Act article not found in ai_act.yaml '{ref}'")
        for ref in c.get("iso42001") or []:
            if ref not in iso_clauses and ref not in iso_controls:
                rep.err(where, f"unknown ISO/IEC 42001 reference '{ref}'")
        if not any(str(r).startswith("A.") for r in c.get("iso42001") or []):
            rep.warn(where, "no Annex A control: the family can never be evidenced")
        if not c.get("tiers") or not set(c.get("tiers")) <= set(CROSSWALK_TIERS):
            rep.err(where, f"tiers expected in {CROSSWALK_TIERS}")
        for key in ("title", "evidence_hint", "basis"):
            check_text(rep, where, key, c.get(key))
    for tier in TIERS:
        if tier != "prohibited" and not any(tier in (c.get("tiers") or []) for c in controls):
            rep.warn("frameworks/crosswalk.yaml", f"no family required for the tier {tier}")
    rep.stats["crosswalk_controls"] = len(controls)

    # 10. External repositories (catalog/github, owned by the GitHub integration workstream).
    gh_dir = root / "catalog" / "github"
    repos = 0
    for path in sorted(gh_dir.glob("*.y*ml")) if gh_dir.is_dir() else []:
        r = parsed.get(path)
        where = f"catalog/github/{path.name}"
        if not isinstance(r, dict) or not r.get("id"):
            rep.err(where, "id missing")
            continue
        repos += 1
        for key in ("reason", "description", "notes", "secrets_detail", "relicense_to_mit"):
            check_text(rep, where, key, r.get(key), required=key == "reason")
        if r.get("pillar") not in REPO_PILLARS:
            rep.err(where, f"pillar expected in {REPO_PILLARS}")
        if r.get("status") not in REPO_STATUS:
            rep.err(where, f"status expected in {REPO_STATUS}")
        if r.get("secrets_scan") not in SECRETS_SCAN:
            rep.err(where, "secrets_scan must be clean, findings or not_scanned")
        if r.get("status") == "integrated" and (r.get("secrets_scan") != "clean" or r.get("copyleft_deps")):
            rep.err(where, "integrated although the secret scan is not clean "
                           "or there are copyleft dependencies")
        if r.get("status") == "integrated" and r.get("license") not in PERMISSIVE_LICENSES:
            rep.err(where, f"integrated under a non-permissive license '{r.get('license')}' (to relicense)")
        if r.get("status") == "integrated" and not str(r.get("submodule", "")).startswith("integrations/"):
            rep.err(where, "an integrated repository must be a submodule under integrations/")
    rep.stats["repos"] = repos

    # 11. Global guards: no training switch anywhere.
    for path, data in parsed.items():
        if data is None:
            continue
        for key_path, value in walk_strings(data):
            if key_path.endswith("action") and FORBIDDEN_TRAINING.search(value):
                rep.err(str(path.relative_to(root)), f"{key_path}: training action forbidden")
        text = path.read_text(encoding="utf-8")
        if re.search(r"^\s*fine_tuning:\s*true", text, re.M):
            rep.err(str(path.relative_to(root)), "fine_tuning: true is forbidden")
    return rep


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate the GitOps configuration of the Virtual AI Factory")
    parser.add_argument("--config", default=str(ROOT / "config"), help="config/ directory (default: the repository's)")
    parser.add_argument("--today", default=None, help="reference ISO date (default: today)")
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--strict", action="store_true", help="warnings make the validation fail")
    args = parser.parse_args(argv)
    today = dt.date.fromisoformat(args.today) if args.today else dt.date.today()
    rep = validate(Path(args.config), today)
    ok = not rep.errors and not (args.strict and rep.warnings)
    if args.json:
        print(json.dumps({"ok": ok, "errors": rep.errors, "warnings": rep.warnings, "stats": rep.stats},
                         ensure_ascii=False, indent=2))
    else:
        for e in rep.errors:
            print(f"ERROR    {e}")
        for w in rep.warnings:
            print(f"WARNING  {w}")
        print(f"{'OK' if ok else 'FAILED'}: {len(rep.errors)} error(s), {len(rep.warnings)} warning(s)")
        print("Statistics: " + json.dumps(rep.stats, ensure_ascii=False))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
