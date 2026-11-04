"""Load the GitOps definition of the factory from FACTORY_CONFIG_DIR (CONTRACT section 2).

Every file is optional: missing files produce warnings and contract defaults, never a crash.
Values are normalised (YAML dates -> ISO strings) so the result is JSON serialisable.
"""

from __future__ import annotations

import copy
import os
import threading
from pathlib import Path
from typing import Any

import yaml

from .i18n import both, is_lang_mapping
from .util import normalize

# Defaults mirror CONTRACT section 2.1 so the engines always find their parameters.
DEFAULT_FACTORY: dict[str, Any] = {
    "factory": {
        "name": "Virtual AI Factory",
        "tagline": both("default.tagline"),
        "currency": "EUR",
        "demo": True,
        "fine_tuning": False,
        "timezone": "Europe/Paris",
    },
    "regulatory": {
        "ai_act": {
            "regulation": "Regulation (EU) 2024/1689",
            "amended_by": "Regulation (EU) 2026/1744 (Digital Omnibus on AI)",
            "milestones": [
                {"id": "prohibitions", "date": "2025-02-02", "label": both("default.milestone.prohibitions")},
                {"id": "literacy", "date": "2025-02-02", "label": both("default.milestone.literacy")},
                {"id": "gpai", "date": "2025-08-02", "label": both("default.milestone.gpai")},
                {"id": "transparency", "date": "2026-08-02", "label": both("default.milestone.transparency")},
                {"id": "annex_iii", "date": "2027-12-02", "label": both("default.milestone.annex_iii")},
                {"id": "annex_i", "date": "2028-08-02", "label": both("default.milestone.annex_i")},
            ],
        }
    },
    "cost_centers": [],
    "rates": {
        "local_gpu": {"capex_eur": 4500, "amortization_months": 36, "power_w": 240, "kwh_eur": 0.21},
        "cloud_gpu_eur_per_hour": {"h100": 2.9, "l4": 0.7},
        "vps_eur_per_month": 40,
        "platform_overhead_pct": 12,
    },
    "chargeback": {"mode": "showback", "shared_allocation": "tokens"},
    "waste_rules": {
        "oversized_model": {"simple_task_types": ["classification", "extraction", "routing"], "premium_tier": "premium"},
        "low_cache_hit": {"min_repeat_ratio": 0.4, "max_cache_hit": 0.2},
        "retry_storm": {"max_retry_ratio": 0.05},
        "prompt_bloat": {"max_growth_pct": 30},
        "idle_gpu": {"max_util_pct": 10, "min_hours": 4},
        "orphan_index": {"max_idle_days": 14},
        "eval_overspend": {"max_share_pct": 25},
        "budget_breach": {"warn_pct": 90},
    },
    "placement": {"order": ["cloud", "vps", "local"], "sensitive_classes": ["C3", "C4"], "vps_max_params_b": 8},
    "agentops_slo": {"ttft_ms_p95": 1500, "vector_latency_ms_p95": 120, "tokens_per_task_p95": 12000},
    "eval_gates": {
        "minimal": {"faithfulness": 0.75, "answer_relevancy": 0.75, "context_precision": 0.70, "judge_score": 3.5, "redteam_pass": 0.90},
        "limited": {"faithfulness": 0.80, "answer_relevancy": 0.80, "context_precision": 0.75, "judge_score": 3.8, "redteam_pass": 0.95},
        "high": {"faithfulness": 0.90, "answer_relevancy": 0.85, "context_precision": 0.85, "judge_score": 4.2, "redteam_pass": 0.99},
    },
    "rollback": {"thumbs_down_ratio_max": 2.0, "online_faithfulness_drop_max": 0.08, "error_rate_max": 0.05},
}

# Keys whose dict value is replaced wholesale by the configuration (no deep merge): a config that
# lists only some waste rules disables the others on purpose.
_REPLACE_KEYS = {"waste_rules", "eval_gates"}


def _deep_merge(base: dict[str, Any], override: dict[str, Any], depth: int = 0) -> dict[str, Any]:
    out = copy.deepcopy(base)
    for key, value in override.items():
        if depth == 0 and key in _REPLACE_KEYS and isinstance(value, dict):
            out[key] = copy.deepcopy(value)
        elif (isinstance(value, dict) and isinstance(out.get(key), dict)
              and not is_lang_mapping(value) and not is_lang_mapping(out[key])):
            # {en, fr} text mappings are atomic: a configured {en: "..."} must not inherit the
            # French default text of the contract.
            out[key] = _deep_merge(out[key], value, depth + 1)
        else:
            out[key] = copy.deepcopy(value)
    return out


def _read_yaml(path: Path, warnings: list[Any]) -> Any:
    try:
        with path.open("r", encoding="utf-8") as fh:
            return yaml.safe_load(fh)
    except FileNotFoundError:
        return None
    except (OSError, yaml.YAMLError) as exc:  # malformed file: report, keep going
        warnings.append(both("config.read_error", file=path.name, error=type(exc).__name__))
        return None


def _as_list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _as_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def load_config(config_dir: str | os.PathLike[str]) -> dict[str, Any]:
    """Read the whole configuration tree. Pure apart from file reads.

    Human-readable values are kept as written: a plain string or an ``{en, fr}`` mapping
    (CONTRACT section 9). Warnings are ``{en, fr}`` mappings too; ``i18n.localize_tree``
    resolves everything for one language.
    """
    root = Path(config_dir)
    warnings: list[Any] = []
    if not root.is_dir():
        warnings.append(both("config.dir_missing", path=str(root)))

    factory_raw = _read_yaml(root / "factory.yaml", warnings)
    if factory_raw is None:
        warnings.append(both("config.factory_missing"))
    factory = _deep_merge(DEFAULT_FACTORY, _as_dict(normalize(factory_raw)))

    profiles_raw = _as_dict(normalize(_read_yaml(root / "profiles.yaml", warnings)))
    profiles = {"roles": _as_list(profiles_raw.get("roles")), "people": _as_list(profiles_raw.get("people"))}

    skills_raw = _as_dict(normalize(_read_yaml(root / "skills.yaml", warnings)))
    rnd_raw = _as_dict(skills_raw.get("rnd"))
    skills = {
        "modules": _as_list(skills_raw.get("modules")),
        "requirements": _as_dict(skills_raw.get("requirements")),
        "completions": _as_list(skills_raw.get("completions")),
        "sessions": _as_list(skills_raw.get("sessions")),
        "rnd": {"experiments": _as_list(rnd_raw.get("experiments"))},
    }

    usecases: list[dict[str, Any]] = []
    seen: set[str] = set()
    uc_dir = root / "usecases"
    if uc_dir.is_dir():
        for path in sorted(uc_dir.glob("*.yaml")) + sorted(uc_dir.glob("*.yml")):
            data = normalize(_read_yaml(path, warnings))
            if not isinstance(data, dict) or not data.get("id"):
                warnings.append(both("config.usecase_no_id", file=path.name))
                continue
            uc_id = str(data["id"])
            if uc_id in seen:
                warnings.append(both("config.usecase_duplicate", file=path.name, id=uc_id))
                continue
            seen.add(uc_id)
            usecases.append(data)
    usecases.sort(key=lambda u: str(u.get("id")))

    catalog: dict[str, list[Any]] = {"models": [], "agents": [], "tools": [], "repos": []}
    cat_dir = root / "catalog"
    if cat_dir.is_dir():
        for path in sorted(cat_dir.glob("*.yaml")) + sorted(cat_dir.glob("*.yml")):
            data = _as_dict(normalize(_read_yaml(path, warnings)))
            for key in ("models", "agents", "tools"):
                catalog[key].extend(x for x in _as_list(data.get(key)) if isinstance(x, dict))
        gh_dir = cat_dir / "github"
        if gh_dir.is_dir():
            for path in sorted(gh_dir.glob("*.yaml")) + sorted(gh_dir.glob("*.yml")):
                data = normalize(_read_yaml(path, warnings))
                if isinstance(data, dict) and data.get("id"):
                    catalog["repos"].append(data)
    catalog["repos"].sort(key=lambda r: str(r.get("id")))

    frameworks: dict[str, Any] = {}
    fw_dir = root / "frameworks"
    if fw_dir.is_dir():
        for path in sorted(fw_dir.glob("*.yaml")) + sorted(fw_dir.glob("*.yml")):
            frameworks[path.stem] = normalize(_read_yaml(path, warnings))
    crosswalk = _as_dict(frameworks.get("crosswalk"))
    controls = [c for c in _as_list(crosswalk.get("controls")) if isinstance(c, dict) and c.get("id")]

    return {
        "factory": factory,
        "profiles": profiles,
        "skills": skills,
        "usecases": usecases,
        "catalog": catalog,
        "frameworks": frameworks,
        "controls": controls,
        "warnings": warnings,
    }


def _tree_signature(root: Path) -> tuple[Any, ...]:
    entries: list[tuple[str, int, int]] = []
    if not root.is_dir():
        return ()
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            if name.endswith((".yaml", ".yml")):
                full = os.path.join(dirpath, name)
                try:
                    st = os.stat(full)
                except OSError:
                    continue
                entries.append((full, st.st_mtime_ns, st.st_size))
    return tuple(sorted(entries))


class ConfigStore:
    """Caches the configuration and reloads it when a YAML file changes (GitOps)."""

    def __init__(self, config_dir: str) -> None:
        self.config_dir = Path(config_dir)
        self._lock = threading.Lock()
        self._signature: tuple[Any, ...] | None = None
        self._config: dict[str, Any] | None = None
        self.generation = 0

    def get(self) -> dict[str, Any]:
        signature = _tree_signature(self.config_dir)
        with self._lock:
            if self._config is None or signature != self._signature:
                self._config = load_config(self.config_dir)
                self._signature = signature
                self.generation += 1
            return self._config
