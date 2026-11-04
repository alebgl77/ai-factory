#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Licence compatibility check of the Virtual AI Factory (an MIT repository).

Checks, against the policy held in ``licenses/third_party.yaml``:

1. Python dependencies of the services we write (``api/``, ``sim/*``, ``compose/*``): licence read
   from the installed metadata (``api/.venv`` or the running interpreter), else from PyPI, else
   from the licence declared in the requirements comment. Only permissive licences pass; MPL-2.0
   passes with its condition (used unmodified).
2. Docker images: every ``image:`` of ``docker-compose.yml`` and every ``FROM`` of our Dockerfiles
   must be listed in ``licenses/third_party.yaml``; AGPL is allowed **only** for an entry whose usage
   is ``separate-service`` and which is not modified.
3. Code vendored at build time (Three.js) and test-only tools.
4. Git submodules (``integrations/``): LICENSE file of each checked-out submodule, consistency
   with ``.gitmodules`` and the ``config/catalog/github`` cards.

Usage:
  python scripts/check_licenses.py                # table, exit 1 on violation
  python scripts/check_licenses.py --offline      # never query PyPI
  python scripts/check_licenses.py --json         # machine-readable result
  python scripts/check_licenses.py --strict       # warnings count as violations
  python scripts/check_licenses.py --self-test    # built-in tests of the policy engine (no network)

Exit code: 0 compliant, 1 violation(s), 2 usage or input error.
The module also exposes the licence helpers used by ``scripts/inventory_github.py``.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from importlib import metadata as importlib_metadata
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:  # pragma: no cover - PyYAML is a dependency of api/
    print("PyYAML is required: pip install -r api/requirements.txt", file=sys.stderr)
    sys.exit(2)

ROOT = Path(__file__).resolve().parent.parent
THIRD_PARTY = ROOT / "licenses" / "third_party.yaml"
OWN_IMAGE_PREFIX = "ai-factory/"
HTTP_TIMEOUT_S = 15
USER_AGENT = "ai-factory-reference-license-check/1.0"

# --------------------------------------------------------------------------- licence helpers

# Ordered: the first matching rule wins. Each rule: SPDX id, list of regexes that must all match.
_TEXT_RULES: list[tuple[str, list[str]]] = [
    ("AGPL-3.0", [r"GNU AFFERO GENERAL PUBLIC LICENSE"]),
    ("LGPL-3.0", [r"GNU LESSER GENERAL PUBLIC LICENSE", r"Version 3"]),
    ("LGPL-2.1", [r"GNU LESSER GENERAL PUBLIC LICENSE", r"Version 2\.1"]),
    ("GPL-3.0", [r"GNU GENERAL PUBLIC LICENSE", r"Version 3"]),
    ("GPL-2.0", [r"GNU GENERAL PUBLIC LICENSE", r"Version 2"]),
    ("SSPL-1.0", [r"Server Side Public License"]),
    ("BUSL-1.1", [r"Business Source License"]),
    ("Elastic-2.0", [r"Elastic License 2\.0"]),
    ("MPL-2.0", [r"Mozilla Public License,? [Vv]ersion 2\.0"]),
    ("EUPL-1.2", [r"European Union Public Licen[cs]e"]),
    ("Apache-2.0", [r"Apache License", r"Version 2\.0"]),
    ("Unlicense", [r"This is free and unencumbered software released into the public domain"]),
    ("CC0-1.0", [r"CC0 1\.0 Universal"]),
    ("ISC", [r"Permission to use, copy, modify, and(/or)? distribute this software for any"]),
    ("MIT-0", [r"Permission is hereby granted, free of charge", r"without restriction"],),
    ("BSD-3-Clause", [r"Redistribution and use in source and binary forms", r"[Nn]either the name"]),
    ("BSD-2-Clause", [r"Redistribution and use in source and binary forms"]),
]
_MIT_NOTICE = re.compile(r"above copyright notice and this permission notice shall be\s+included", re.IGNORECASE)

_ALIASES = {
    "mit": "MIT", "mit license": "MIT", "the mit license": "MIT", "expat": "MIT", "mit-0": "MIT-0",
    "apache": "Apache-2.0", "apache 2": "Apache-2.0", "apache 2.0": "Apache-2.0", "apache-2": "Apache-2.0",
    "apache-2.0": "Apache-2.0", "apache license 2.0": "Apache-2.0", "apache license, version 2.0": "Apache-2.0",
    "apache license version 2.0": "Apache-2.0", "apache software license": "Apache-2.0", "asl 2.0": "Apache-2.0",
    "apache software license 2.0": "Apache-2.0", "apache2": "Apache-2.0",
    "bsd": "BSD-3-Clause", "bsd license": "BSD-3-Clause", "new bsd": "BSD-3-Clause", "new bsd license": "BSD-3-Clause",
    "modified bsd": "BSD-3-Clause", "bsd-3": "BSD-3-Clause", "bsd 3-clause": "BSD-3-Clause",
    "3-clause bsd license": "BSD-3-Clause", "bsd-3-clause": "BSD-3-Clause", "bsd 3-clause license": "BSD-3-Clause",
    "bsd-2": "BSD-2-Clause", "bsd 2-clause": "BSD-2-Clause", "simplified bsd": "BSD-2-Clause",
    "2-clause bsd license": "BSD-2-Clause", "bsd-2-clause": "BSD-2-Clause", "bsd 2-clause license": "BSD-2-Clause",
    "isc": "ISC", "isc license": "ISC", "isc license (iscl)": "ISC",
    "psf": "PSF-2.0", "psf-2.0": "PSF-2.0", "psfl": "PSF-2.0", "python software foundation license": "PSF-2.0",
    "mpl-2.0": "MPL-2.0", "mpl 2.0": "MPL-2.0", "mozilla public license 2.0 (mpl 2.0)": "MPL-2.0",
    "unlicense": "Unlicense", "the unlicense": "Unlicense", "the unlicense (unlicense)": "Unlicense",
    "cc0": "CC0-1.0", "cc0-1.0": "CC0-1.0", "postgresql": "PostgreSQL", "zlib": "Zlib", "0bsd": "0BSD",
    "gpl": "GPL-3.0", "gplv2": "GPL-2.0", "gplv3": "GPL-3.0", "lgpl": "LGPL-3.0", "agpl": "AGPL-3.0",
    "agplv3": "AGPL-3.0", "lgplv3": "LGPL-3.0", "lgplv2": "LGPL-2.1",
    "gnu general public license v2 (gplv2)": "GPL-2.0", "gnu general public license v3 (gplv3)": "GPL-3.0",
    "gnu lesser general public license v3 (lgplv3)": "LGPL-3.0",
    "gnu lesser general public license v2 or later (lgplv2+)": "LGPL-2.1-or-later",
    "gnu affero general public license v3": "AGPL-3.0", "proprietary": "proprietary",
    "sil open font license": "OFL-1.1", "sil open font license 1.1": "OFL-1.1", "ofl": "OFL-1.1",
    "ofl-1.1": "OFL-1.1", "mit-cmu": "MIT-CMU", "hpnd": "HPND",
}
_CLASSIFIER_PREFIX = "License :: "


def normalize_license(value: str | None) -> str | None:
    """Map a free-form licence name to an SPDX id (or expression); None when empty or unknown."""
    if not value:
        return None
    text = str(value).strip()
    if not text or text.upper() in ("UNKNOWN", "NOASSERTION", "NONE", "SEE LICENSE IN LICENSE"):
        return None
    if "\n" in text or len(text) > 120:  # a full licence text pasted in metadata
        return detect_license_text(text)
    if text.startswith(_CLASSIFIER_PREFIX):
        text = text.split("::")[-1].strip()
    key = text.lower().strip(" .")
    if key in _ALIASES:
        return _ALIASES[key]
    if re.fullmatch(r"[A-Za-z0-9.+\-() ]+", text) and re.search(r"\b(AND|OR|WITH)\b", text):
        return text  # already an SPDX expression
    if re.fullmatch(r"[A-Za-z0-9.+\-]+", text):
        return text  # looks like an SPDX id already
    return None


def detect_license_text(text: str) -> str | None:
    """Identify the licence of a LICENSE/COPYING file from its text (SPDX id) or None."""
    if not text:
        return None
    head = text[:20000]
    for spdx, patterns in _TEXT_RULES:
        if all(re.search(p, head) for p in patterns):
            if spdx == "MIT-0":
                return "MIT" if _MIT_NOTICE.search(head) else "MIT-0"
            return spdx
    return None


def find_license_file(directory: Path) -> Path | None:
    """Return the main licence file of a directory (LICENSE, LICENSE.md, COPYING...)."""
    if not directory.is_dir():
        return None
    candidates = sorted(p for p in directory.iterdir()
                        if p.is_file() and re.match(r"(?i)^(licen[cs]e|copying)(\.(md|txt|rst))?$", p.name))
    return candidates[0] if candidates else None


def _tokenize(expr: str) -> list[str]:
    return re.findall(r"\(|\)|[^\s()]+", expr)


def evaluate_spdx(expr: str | None, policy: Policy) -> str:
    """Rank an SPDX expression: permissive < conditional < separate < unknown < denied.

    ``A OR B`` keeps the most favourable branch, ``A AND B`` the least favourable one, and
    ``X WITH exception`` is ranked as ``X``.
    """
    if not expr:
        return "unknown"
    tokens = _tokenize(expr)
    pos = 0

    def parse_or() -> int:
        nonlocal pos
        rank = parse_and()
        while pos < len(tokens) and tokens[pos].upper() == "OR":
            pos += 1
            rank = min(rank, parse_and())
        return rank

    def parse_and() -> int:
        nonlocal pos
        rank = parse_atom()
        while pos < len(tokens) and tokens[pos].upper() == "AND":
            pos += 1
            rank = max(rank, parse_atom())
        return rank

    def parse_atom() -> int:
        nonlocal pos
        if pos >= len(tokens):
            return RANKS.index("unknown")
        tok = tokens[pos]
        pos += 1
        if tok == "(":
            rank = parse_or()
            if pos < len(tokens) and tokens[pos] == ")":
                pos += 1
            return rank
        rank = RANKS.index(policy.rank_id(tok))
        if pos < len(tokens) and tokens[pos].upper() == "WITH":
            pos += 2  # skip the exception identifier
        return rank

    try:
        return RANKS[parse_or()]
    except (ValueError, IndexError):
        return "unknown"


RANKS = ["permissive", "conditional", "separate", "unknown", "denied"]


@dataclass
class Policy:
    permissive: set[str]
    conditional: dict[str, str]
    separate_only: set[str]
    denied_prefixes: tuple[str, ...]

    @classmethod
    def from_yaml(cls, data: dict[str, Any]) -> Policy:
        p = data.get("policy") or {}
        return cls(
            permissive={str(x) for x in p.get("permissive") or []},
            conditional={str(k): str(v) for k, v in (p.get("conditional") or {}).items()},
            separate_only={str(x) for x in p.get("separate_service_only") or []},
            denied_prefixes=tuple(str(x) for x in p.get("denied_prefixes") or []),
        )

    @classmethod
    def default(cls) -> Policy:
        return cls.from_yaml(load_yaml(THIRD_PARTY) if THIRD_PARTY.is_file() else {})

    def rank_id(self, spdx_id: str) -> str:
        sid = spdx_id.strip()
        base = sid.removesuffix("+")
        if base in self.permissive:
            return "permissive"
        if base in self.conditional:
            return "conditional"
        if base in self.separate_only:
            return "separate"
        if base.lower() == "proprietary" or base.startswith(self.denied_prefixes):
            return "denied"
        if base.upper().startswith(("GPL", "LGPL", "AGPL", "SSPL")):
            return "denied"
        return "unknown"

    def condition(self, expr: str | None) -> str | None:
        for key, note in self.conditional.items():
            if expr and re.search(rf"(?<![\w.-]){re.escape(key)}(?![\w.-])", expr):
                return note
        return None


# --------------------------------------------------------------------------- report model

@dataclass
class Row:
    category: str
    item: str
    license: str
    usage: str
    verdict: str  # ok | condition | warn | violation
    detail: str = ""


@dataclass
class Report:
    rows: list[Row] = field(default_factory=list)

    def add(self, *args: Any, **kwargs: Any) -> None:
        self.rows.append(Row(*args, **kwargs))

    @property
    def violations(self) -> list[Row]:
        return [r for r in self.rows if r.verdict == "violation"]

    @property
    def warnings(self) -> list[Row]:
        return [r for r in self.rows if r.verdict == "warn"]


def load_yaml(path: Path) -> Any:
    with path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


# --------------------------------------------------------------------------- 1. Python deps

_REQ_LINE = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)\s*(?:\[[^\]]*\])?\s*(?:(==|>=|~=|<=|>|<|!=)\s*([^\s;#,]+))?")


def canonical(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def parse_requirements(path: Path) -> list[dict[str, str | None]]:
    """Parse a requirements file: name, pinned version and the licence declared in a comment."""
    out = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", "-")):
            continue
        m = _REQ_LINE.match(line)
        if not m:
            continue
        comment = raw.split("#", 1)[1].strip() if "#" in raw else ""
        declared = normalize_license(re.split(r"[(,;]", comment)[0].strip()) if comment else None
        out.append({"name": m.group(1), "version": m.group(3) if m.group(2) == "==" else None,
                    "declared": declared})
    return out


def parse_pyproject_deps(path: Path) -> list[dict[str, str | None]]:
    try:
        import tomllib
    except ImportError:  # pragma: no cover - Python < 3.11
        return []
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    deps = list((data.get("project") or {}).get("dependencies") or [])
    out = []
    for spec in deps:
        m = _REQ_LINE.match(spec)
        if m:
            out.append({"name": m.group(1), "version": m.group(3) if m.group(2) == "==" else None,
                        "declared": None})
    return out


def metadata_license(meta: Any) -> str | None:
    """SPDX licence of an installed distribution: License-Expression, License, then classifiers."""
    expr = meta.get("License-Expression")
    if expr:
        return normalize_license(expr) or expr.strip()
    lic = normalize_license(meta.get("License"))
    if lic:
        return lic
    found = []
    for c in meta.get_all("Classifier") or []:
        if c.startswith("License ::") and c.count("::") >= 2:
            spdx = normalize_license(c.split("::")[-1].strip())
            if spdx and spdx not in found:
                found.append(spdx)
    if found:
        return " OR ".join(found)  # several licence classifiers mean a choice of licences
    return None


def installed_distributions(extra_paths: Iterable[Path]) -> dict[str, Any]:
    dists: dict[str, Any] = {}
    paths = [str(p) for p in extra_paths if p.is_dir()]
    for source in ([paths] if paths else []) + [None]:
        try:
            iterator = importlib_metadata.distributions(path=source) if source else importlib_metadata.distributions()
            for dist in iterator:
                name = dist.metadata.get("Name")
                if name and canonical(name) not in dists:
                    dists[canonical(name)] = dist
        except Exception as exc:  # noqa: BLE001 - a broken site-packages must not stop the check
            print(f"warning: cannot read distributions in {source}: {exc}", file=sys.stderr)
            continue
    return dists


def pypi_license(name: str, version: str | None) -> str | None:
    url = f"https://pypi.org/pypi/{name}/{version}/json" if version else f"https://pypi.org/pypi/{name}/json"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_S) as resp:
            info = json.load(resp).get("info") or {}
    except (urllib.error.URLError, TimeoutError, ValueError, OSError):
        return None
    expr = info.get("license_expression")
    if expr:
        return normalize_license(expr) or expr
    lic = normalize_license(info.get("license"))
    if lic:
        return lic
    found = [normalize_license(c) for c in info.get("classifiers") or [] if c.startswith("License ::") and c.count("::") >= 2]
    found = [f for f in dict.fromkeys(found) if f]
    return " OR ".join(found) if found else None


def check_python(report: Report, policy: Policy, offline: bool, overrides: dict[str, str]) -> None:
    manifests = sorted({*ROOT.glob("api/requirements*.txt"), *ROOT.glob("api/pyproject.toml"),
                        *ROOT.glob("sim/*/requirements*.txt"), *ROOT.glob("compose/*/requirements*.txt")})
    if not manifests:
        report.add("python", "(aucun manifeste)", "-", "-", "warn", "api/requirements*.txt introuvable")
        return
    venvs = sorted(ROOT.glob("api/.venv/lib/python*/site-packages"))
    dists = installed_distributions(venvs)
    seen: dict[str, Row] = {}
    for manifest in manifests:
        rel = manifest.relative_to(ROOT).as_posix()
        usage = "test" if "dev" in manifest.name or "test" in manifest.name else "runtime"
        deps = parse_pyproject_deps(manifest) if manifest.suffix == ".toml" else parse_requirements(manifest)
        for dep in deps:
            key = canonical(str(dep["name"]))
            if key in seen:
                if rel not in seen[key].detail:
                    seen[key].detail += f", {rel}"
                continue
            spdx, source = None, ""
            if key in overrides:
                spdx, source = overrides[key], "checked by hand"
            elif key in dists:
                spdx, source = metadata_license(dists[key].metadata), "installed metadata"
            if not spdx and not offline:
                spdx = pypi_license(str(dep["name"]), dep.get("version"))  # type: ignore[arg-type]
                source = "PyPI" if spdx else source
            if not spdx and dep.get("declared"):
                spdx, source = str(dep["declared"]), "declared (comment)"
            rank = evaluate_spdx(spdx, policy)
            if rank == "permissive":
                verdict, why = "ok", source
            elif rank == "conditional":
                verdict, why = "condition", f"{source} ; {policy.condition(spdx)}"
            elif rank == "unknown":
                verdict, why = "violation", f"unknown license ({source or 'no source'}): check it and fill python_overrides"
            else:
                verdict, why = "violation", f"{source}; non-permissive license for a linked dependency"
            name = f"{dep['name']}=={dep['version']}" if dep.get("version") else str(dep["name"])
            row = Row("python", name, spdx or "?", usage, verdict, f"{why} [{rel}]")
            seen[key] = row
            report.rows.append(row)


# --------------------------------------------------------------------------- 2. images

def split_image(ref: str) -> tuple[str, str]:
    """Split an image reference into (repository, tag); digests are dropped."""
    ref = ref.split("@", 1)[0].strip()
    for prefix in ("docker.io/library/", "docker.io/", "index.docker.io/library/"):
        ref = ref.removeprefix(prefix)
    last = ref.rsplit("/", 1)[-1]
    if ":" in last:
        repo, tag = ref.rsplit(":", 1)
        return repo, tag
    return ref, "latest"


def _resolve_env_default(value: str) -> str:
    return re.sub(r"\$\{[A-Za-z_][A-Za-z0-9_]*:?-([^}]*)\}", r"\1", value)


def compose_images(path: Path) -> list[tuple[str, str]]:
    data = load_yaml(path) or {}
    out = []
    for name, svc in (data.get("services") or {}).items():
        if isinstance(svc, dict) and svc.get("image"):
            out.append((str(name), _resolve_env_default(str(svc["image"]))))
    return out


def dockerfile_images(root: Path) -> list[tuple[str, str]]:
    out = []
    skip = ("integrations", "node_modules", ".venv", ".git")
    for path in sorted(root.rglob("Dockerfile*")):
        if any(part in skip for part in path.relative_to(root).parts) or not path.is_file():
            continue
        args: dict[str, str] = {}
        stages: set[str] = set()
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            m = re.match(r"(?i)^ARG\s+([A-Za-z_][A-Za-z0-9_]*)=(\S+)", line)
            if m:
                args[m.group(1)] = m.group(2).strip("\"'")
                continue
            m = re.match(r"(?i)^FROM\s+(?:--platform=\S+\s+)?(\S+)(?:\s+AS\s+(\S+))?", line)
            if not m:
                continue
            ref = re.sub(r"\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?",
                         lambda x, a=args: a.get(x.group(1), x.group(0)), m.group(1))
            if m.group(2):
                stages.add(m.group(2).lower())
            if ref.lower() in stages or ref == "scratch":
                continue
            out.append((path.relative_to(root).as_posix(), ref))
    return out


def check_images(report: Report, policy: Policy, inventory: dict[str, Any]) -> None:
    entries = [e for e in inventory.get("images") or [] if isinstance(e, dict) and e.get("image")]
    by_repo: dict[str, list[dict[str, Any]]] = {}
    for e in entries:
        by_repo.setdefault(str(e["image"]), []).append(e)
    used: set[str] = set()
    references: list[tuple[str, str]] = []
    compose = ROOT / "docker-compose.yml"
    if compose.is_file():
        references += [(f"compose:{svc}", ref) for svc, ref in compose_images(compose)]
    references += [(f"{df}", ref) for df, ref in dockerfile_images(ROOT)]
    for where, ref in references:
        repo, tag = split_image(ref)
        if repo.startswith(OWN_IMAGE_PREFIX):
            continue  # built from this repository (MIT)
        if repo not in by_repo:
            report.add("image", f"{repo}:{tag}", "?", "?", "violation",
                       f"image missing from licenses/third_party.yaml ({where})")
            continue
        used.add(repo)
        tags = {str(e.get("tag")) for e in by_repo[repo]}
        if tag not in tags:
            report.add("image", f"{repo}:{tag}", "-", "-", "warn",
                       f"tag missing from the inventory ({', '.join(sorted(tags))}): update it ({where})")
    for e in entries:
        spdx = str(e.get("spdx") or "")
        usage = str(e.get("usage") or "")
        item = f"{e['image']}:{e.get('tag')}"
        label = str(e.get("license") or spdx)
        if usage not in ("separate-service", "build-time"):
            report.add("image", item, label, usage or "?", "violation", "usage attendu : separate-service ou build-time")
            continue
        if not e.get("source"):
            report.add("image", item, label, usage, "violation", "URL du code source manquante")
            continue
        rank = evaluate_spdx(spdx, policy)
        note = "" if str(e["image"]) in used else "not referenced by docker-compose.yml or a Dockerfile; "
        if rank == "permissive":
            report.add("image", item, label, usage, "ok", note + str(e.get("source")))
        elif rank == "conditional":
            report.add("image", item, label, usage, "condition", note + str(policy.condition(spdx)))
        elif rank == "separate":
            if usage == "separate-service" and e.get("modified") is False:
                report.add("image", item, label, usage, "ok",
                           note + "AGPL allowed: separate service, unmodified official image, no linking or copying")
            else:
                report.add("image", item, label, usage, "violation",
                           "AGPL allowed only for an unmodified separate service (modified: false)")
        else:
            report.add("image", item, label, usage, "violation", note + f"{rank} license rejected by the policy")


# --------------------------------------------------------------------------- 3. vendored / tools

def check_vendored(report: Report, policy: Policy, inventory: dict[str, Any]) -> None:
    for section in ("vendored", "tools"):
        for e in inventory.get(section) or []:
            if not isinstance(e, dict):
                continue
            version = e.get("version") or e.get("tag")
            item = f"{e.get('name')}@{version}"
            spdx = str(e.get("spdx") or "")
            rank = evaluate_spdx(spdx, policy)
            verdict = "ok" if rank == "permissive" else "condition" if rank == "conditional" else "violation"
            detail = str(e.get("where") or "")
            chk = e.get("check") or {}
            if chk.get("file") and chk.get("pattern"):
                target = ROOT / str(chk["file"])
                found = re.search(str(chk["pattern"]), target.read_text(encoding="utf-8")) if target.is_file() else None
                if not found:
                    verdict, detail = "warn", f"version introuvable dans {chk['file']}"
                elif found.group(1) != str(version):
                    verdict, detail = "violation", f"{chk['file']} utilise {found.group(1)}, inventaire {version}"
            category = "vendored at build time" if section == "vendored" else "tool"
            report.add(category, item, str(e.get("license") or spdx), str(e.get("usage") or "-"), verdict, detail)


# --------------------------------------------------------------------------- 4. submodules

def parse_gitmodules(path: Path) -> dict[str, dict[str, str]]:
    modules: dict[str, dict[str, str]] = {}
    current: dict[str, str] | None = None
    if not path.is_file():
        return modules
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        m = re.match(r'^\[submodule\s+"([^"]+)"\]$', line)
        if m:
            current = {"name": m.group(1)}
            modules[m.group(1)] = current
            continue
        m = re.match(r"^([A-Za-z]+)\s*=\s*(.+)$", line)
        if m and current is not None:
            current[m.group(1)] = m.group(2).strip()
    return {v.get("path", k): v for k, v in modules.items()}


def load_cards(directory: Path) -> list[dict[str, Any]]:
    cards = []
    for path in sorted(directory.glob("*.y*ml")) if directory.is_dir() else []:
        data = load_yaml(path)
        if isinstance(data, dict) and data.get("id"):
            data["_file"] = path.relative_to(ROOT).as_posix()
            cards.append(data)
    return cards


def check_submodules(report: Report, policy: Policy, strict_checkout: bool) -> None:
    modules = parse_gitmodules(ROOT / ".gitmodules")
    cards = load_cards(ROOT / "config" / "catalog" / "github")
    by_path = {str(c.get("submodule")): c for c in cards if c.get("submodule")}
    for path, mod in sorted(modules.items()):
        card = by_path.get(path)
        if not path.startswith("integrations/"):
            report.add("submodule", path, "?", "-", "violation", "submodules must live under integrations/")
        if card is None:
            report.add("submodule", path, "?", "-", "violation", "submodule without a config/catalog/github/*.yaml card")
            continue
        if card.get("status") != "integrated":
            report.add("submodule", path, str(card.get("license")), "-", "violation",
                       f"fiche {card['_file']} au statut {card.get('status')} : retirer le sous-module")
        if mod.get("url") and card.get("url") and mod["url"].removesuffix(".git") != str(card["url"]).removesuffix(".git"):
            report.add("submodule", path, str(card.get("license")), "-", "violation",
                       f".gitmodules URL ({mod['url']}) differs from the card ({card['url']})")
        directory = ROOT / path
        lic_file = find_license_file(directory)
        if lic_file is None:
            detected, origin = None, "non extrait (git submodule update --init) : licence de la fiche"
        else:
            detected = detect_license_text(lic_file.read_text(encoding="utf-8", errors="replace"))
            origin = f"{lic_file.relative_to(ROOT).as_posix()}"
        declared = str(card.get("license") or "")
        effective = detected or declared
        rank = evaluate_spdx(effective, policy)
        if lic_file is not None and detected and declared and evaluate_spdx(declared, policy) != "unknown" \
                and detected != declared:
            report.add("submodule", path, f"{detected} (card: {declared})", "submodule", "violation",
                       f"license file differs from the card {card['_file']}")
            continue
        if lic_file is not None and not detected:
            report.add("submodule", path, "?", "submodule", "violation", f"{origin}: unrecognized license")
            continue
        if card.get("copyleft_deps"):
            report.add("submodule", path, effective, "submodule", "violation",
                       f"copyleft dependencies listed in the card: {card['copyleft_deps']}")
            continue
        if card.get("secrets_scan") != "clean":
            report.add("submodule", path, effective, "submodule", "violation",
                       f"secrets scan \"{card.get('secrets_scan')}\": integration forbidden")
            continue
        if rank == "permissive":
            verdict = "warn" if lic_file is None and strict_checkout else "ok"
            report.add("submodule", path, effective, "submodule", verdict, origin)
        elif rank == "conditional":
            report.add("submodule", path, effective, "submodule", "condition", f"{origin} ; {policy.condition(effective)}")
        else:
            report.add("submodule", path, effective, "submodule", "violation",
                       f"{origin}; {rank} license: relicense before integrating")
    for card in cards:
        if card.get("status") == "integrated" and str(card.get("submodule")) not in modules:
            report.add("submodule", str(card.get("submodule")), str(card.get("license")), "-", "violation",
                       f"card {card['_file']} is \"integrated\" but has no .gitmodules entry")


def check_own_license(report: Report) -> None:
    lic = ROOT / "LICENSE"
    detected = detect_license_text(lic.read_text(encoding="utf-8")) if lic.is_file() else None
    verdict = "ok" if detected == "MIT" else "violation"
    report.add("repository", "LICENSE", detected or "?", "-", verdict, "the repository is published under MIT")


# --------------------------------------------------------------------------- output

_VERDICT_LABEL = {"ok": "OK", "condition": "OK (cond.)", "warn": "WARNING", "violation": "VIOLATION"}


def print_table(report: Report) -> None:
    headers = ("Category", "Item", "License", "Usage", "Verdict", "Detail")
    rows = [(r.category, r.item, r.license, r.usage, _VERDICT_LABEL.get(r.verdict, r.verdict), r.detail)
            for r in report.rows]
    widths = [min(max(len(h), *(len(str(row[i])) for row in rows)) if rows else len(h), cap)
              for i, (h, cap) in enumerate(zip(headers, (16, 44, 40, 16, 10, 200)))]

    def fmt(cells: Iterable[str]) -> str:
        out = []
        for i, cell in enumerate(cells):
            cell = str(cell)
            if i < len(widths) - 1:
                cell = (cell[: widths[i] - 1] + "…") if len(cell) > widths[i] else cell.ljust(widths[i])
            out.append(cell)
        return " | ".join(out).rstrip()

    print(fmt(headers))
    print("-+-".join("-" * w for w in widths[:-1]) + "-+-" + "-" * 6)
    for row in rows:
        print(fmt(row))


# --------------------------------------------------------------------------- self-test

def self_test() -> int:
    """Built-in checks of the policy engine on a synthetic repository (no network)."""
    import tempfile

    global ROOT
    failures: list[str] = []

    def expect(label: str, got: Any, want: Any) -> None:
        if got != want:
            failures.append(f"{label}: got {got!r}, want {want!r}")

    policy = Policy.from_yaml({"policy": {
        "permissive": ["MIT", "Apache-2.0", "BSD-3-Clause"], "conditional": {"MPL-2.0": "unmodified"},
        "separate_service_only": ["AGPL-3.0-only"], "denied_prefixes": ["GPL-", "LGPL-", "AGPL-", "SSPL-"]}})
    for expr, want in [("MIT", "permissive"), ("MIT OR GPL-3.0-only", "permissive"),
                       ("MIT AND GPL-3.0-only", "denied"), ("(MIT OR Apache-2.0) AND MPL-2.0", "conditional"),
                       ("AGPL-3.0-only", "separate"), ("LGPL-2.1-or-later", "denied"), ("SSPL-1.0", "denied"),
                       ("GPL-2.0-only WITH Classpath-exception-2.0", "denied"), ("Foo-1.0", "unknown"), (None, "unknown")]:
        expect(f"evaluate_spdx({expr})", evaluate_spdx(expr, policy), want)
    mit = ("MIT License\n\nPermission is hereby granted, free of charge, to any person obtaining a copy ... "
           "to deal in the Software without restriction ...\nThe above copyright notice and this permission notice "
           "shall be included in all copies")
    for text, want in [(mit, "MIT"), ("Apache License\nVersion 2.0, January 2004", "Apache-2.0"),
                       ("GNU AFFERO GENERAL PUBLIC LICENSE\nVersion 3", "AGPL-3.0"),
                       ("GNU GENERAL PUBLIC LICENSE\nVersion 3, 29 June 2007", "GPL-3.0"),
                       ("Redistribution and use in source and binary forms ... Neither the name", "BSD-3-Clause"),
                       ("All rights reserved.", None)]:
        expect(f"detect_license_text({text[:24]!r})", detect_license_text(text), want)
    for raw, want in [("MIT License", "MIT"), ("Apache Software License", "Apache-2.0"), ("BSD-3-Clause", "BSD-3-Clause"),
                      ("Mozilla Public License 2.0 (MPL 2.0)", "MPL-2.0"), ("UNKNOWN", None), ("Dual License", None)]:
        expect(f"normalize_license({raw!r})", normalize_license(raw), want)
    expect("split_image", split_image("docker.io/library/postgres:17.11-alpine@sha256:abc"), ("postgres", "17.11-alpine"))

    saved_root = ROOT
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "LICENSE").write_text(mit, encoding="utf-8")
        (root / "docker-compose.yml").write_text(
            "services:\n  a: {image: grafana/grafana-oss:1}\n  b: {image: 'x/unlisted:2'}\n"
            "  c: {image: '${IMG:-y/agpl-build:3}'}\n  d: {image: ai-factory/api:local}\n", encoding="utf-8")
        (root / "integrations" / "sub-ok").mkdir(parents=True)
        (root / "integrations" / "sub-ok" / "LICENSE").write_text(mit, encoding="utf-8")
        (root / "integrations" / "sub-gpl").mkdir(parents=True)
        (root / "integrations" / "sub-gpl" / "LICENSE").write_text("GNU GENERAL PUBLIC LICENSE\nVersion 3", encoding="utf-8")
        (root / ".gitmodules").write_text(
            '[submodule "integrations/sub-ok"]\n\tpath = integrations/sub-ok\n\turl = https://example.org/ok.git\n'
            '[submodule "integrations/sub-gpl"]\n\tpath = integrations/sub-gpl\n\turl = https://example.org/gpl.git\n'
            '[submodule "integrations/orphan"]\n\tpath = integrations/orphan\n\turl = https://example.org/o.git\n',
            encoding="utf-8")
        cards = root / "config" / "catalog" / "github"
        cards.mkdir(parents=True)
        for name, licence in (("sub-ok", "MIT"), ("sub-gpl", "MIT")):
            (cards / f"{name}.yaml").write_text(yaml.safe_dump({
                "id": f"gh-{name}", "name": name, "url": f"https://example.org/{name.split('-')[1]}",
                "pillar": "rag", "license": licence, "status": "integrated", "submodule": f"integrations/{name}",
                "secrets_scan": "clean", "copyleft_deps": []}), encoding="utf-8")
        inventory = {"images": [
            {"image": "grafana/grafana-oss", "tag": "1", "spdx": "AGPL-3.0-only", "usage": "separate-service",
             "modified": False, "source": "https://example.org"},
            {"image": "y/agpl-build", "tag": "3", "spdx": "AGPL-3.0-only", "usage": "build-time",
             "modified": False, "source": "https://example.org"},
            {"image": "z/agpl-modified", "tag": "4", "spdx": "AGPL-3.0-only", "usage": "separate-service",
             "modified": True, "source": "https://example.org"}]}
        try:
            ROOT = root
            report = Report()
            check_own_license(report)
            check_images(report, policy, inventory)
            check_submodules(report, policy, strict_checkout=False)
        finally:
            ROOT = saved_root
        verdicts = {(r.category, r.item): r.verdict for r in report.rows}
        expect("own licence", verdicts.get(("repository", "LICENSE")), "ok")
        expect("AGPL separate-service", verdicts.get(("image", "grafana/grafana-oss:1")), "ok")
        expect("unlisted image", verdicts.get(("image", "x/unlisted:2")), "violation")
        expect("AGPL build-time", verdicts.get(("image", "y/agpl-build:3")), "violation")
        expect("AGPL modified", verdicts.get(("image", "z/agpl-modified:4")), "violation")
        expect("own image skipped", any("ai-factory/" in r.item for r in report.rows), False)
        expect("submodule ok", verdicts.get(("submodule", "integrations/sub-ok")), "ok")
        expect("submodule GPL vs card", verdicts.get(("submodule", "integrations/sub-gpl")), "violation")
        expect("submodule without card", verdicts.get(("submodule", "integrations/orphan")), "violation")

    for f in failures:
        print("FAIL", f)
    print(f"self-test: {'OK' if not failures else f'{len(failures)} failure(s)'}")
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Checks license compatibility with an MIT repository")
    parser.add_argument("--offline", action="store_true", help="ne jamais interroger PyPI")
    parser.add_argument("--json", action="store_true", help="sortie JSON")
    parser.add_argument("--strict", action="store_true", help="les alertes comptent comme des violations")
    parser.add_argument("--self-test", action="store_true", help="self-test of the policy engine (offline)")
    args = parser.parse_args(argv)
    if args.self_test:
        return self_test()

    if not THIRD_PARTY.is_file():
        print(f"missing {THIRD_PARTY.relative_to(ROOT)}", file=sys.stderr)
        return 2
    try:
        inventory = load_yaml(THIRD_PARTY) or {}
    except yaml.YAMLError as exc:
        print(f"invalid {THIRD_PARTY.relative_to(ROOT)}: {exc}", file=sys.stderr)
        return 2
    policy = Policy.from_yaml(inventory)
    overrides = {canonical(k): str(v) for k, v in (inventory.get("python_overrides") or {}).items()}

    report = Report()
    check_own_license(report)
    check_python(report, policy, args.offline, overrides)
    check_images(report, policy, inventory)
    check_vendored(report, policy, inventory)
    check_submodules(report, policy, args.strict)

    failed = report.violations + (report.warnings if args.strict else [])
    if args.json:
        print(json.dumps({"ok": not failed, "rows": [asdict(r) for r in report.rows]}, ensure_ascii=False, indent=2))
    else:
        print_table(report)
        counts = {v: sum(1 for r in report.rows if r.verdict == v) for v in _VERDICT_LABEL}
        print()
        print(f"{len(report.rows)} items: {counts['ok']} OK, {counts['condition']} OK with a condition, "
              f"{counts['warn']} warning(s), {counts['violation']} violation(s).")
        print("Result:", "COMPLIANT" if not failed else "NOT COMPLIANT")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
