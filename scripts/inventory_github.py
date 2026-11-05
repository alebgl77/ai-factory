#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Reproducible inventory of a GitHub account's public repositories for the Virtual AI Factory.

For every public, non-fork repository the script clones the full history, detects the licence
(LICENSE text and manifest declarations), looks up the licence of each declared dependency
(lockfile, PyPI, npm registry, pkg.go.dev, crates.io), scans the whole history for secrets with
gitleaks (pinned container image, default rules, inline allow comments ignored) and computes a
decision from the human pillar classification of ``integrations/inventory.yaml``:

- ``integrated``: pillar match, permissive licence, no copyleft or unknown runtime dependency,
  clean scan after the manual triage of ``integrations/secrets-triage.yaml``;
- ``to_relicense``: pillar match but no licence, or a licence the owner must change;
- ``excluded``: fork, private, no pillar match, secrets found or copyleft dependency.

The redacted scanner report is temporary. Finding locations are used in memory for triage,
and inventory outputs include only scanner status and counts.

Usage:
  python scripts/inventory_github.py                       # repositories of integrations/inventory.yaml
  python scripts/inventory_github.py --from-gh alebgl77    # list from `gh repo list` (gh authenticated)
  python scripts/inventory_github.py --report inv.json --write-cards --add-submodules --markdown table.md
  python scripts/inventory_github.py --only grafana-llmops-forge --no-scan --offline
  python scripts/inventory_github.py --self-test           # decision rules, without network

``--add-submodules`` runs ``git submodule add`` for integrated repositories (it never commits).
Exit code: 0 done, 1 an integrated card or submodule disagrees with the computed decision, 2 error.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import check_licenses as lic

yaml = lic.yaml
ROOT = lic.ROOT
INVENTORY = ROOT / "integrations" / "inventory.yaml"
TRIAGE = ROOT / "integrations" / "secrets-triage.yaml"
CARDS_DIR = ROOT / "config" / "catalog" / "github"
GITLEAKS_IMAGE = os.environ.get("GITLEAKS_IMAGE", "zricethezav/gitleaks:v8.30.1")
PILLARS = ("rag", "evaluation", "multi_agents", "llmops_observability", "governance_security")
PILLAR_LABELS = {"rag": "RAG", "evaluation": "Évaluation", "multi_agents": "Multi-agents",
                 "llmops_observability": "LLMOps et observabilité", "governance_security": "Gouvernance et sécurité",
                 "none": "aucun"}
SKIP_DIRS = {".git", "node_modules", "vendor", ".venv", "venv", "dist", "build", "__pycache__", ".next"}
DEV_HINTS = {"dev", "develop", "development", "test", "tests", "testing", "tool", "tools", "build", "lint",
             "doc", "docs", "ci", "bench", "benchmark", "benchmarks", "optional", "e2e", "fixtures"}
HTTP_TIMEOUT_S = 20


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def run(cmd: list[str], cwd: Path | None = None, check: bool = True, timeout: int = 1800) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=cwd, check=check, capture_output=True, text=True, timeout=timeout)


# --------------------------------------------------------------------------- repository list

def load_inventory(path: Path) -> dict[str, Any]:
    data = lic.load_yaml(path) or {}
    data.setdefault("repos", [])
    return data


def gh_repo_list(owner: str) -> list[dict[str, Any]]:
    """Repository list from the GitHub CLI (requires `gh auth login`)."""
    fields = "name,isFork,isPrivate,url,description,licenseInfo,parent,isArchived"
    out = run(["gh", "repo", "list", owner, "--limit", "1000", "--json", fields]).stdout
    repos = []
    for r in json.loads(out):
        repos.append({"name": r["name"], "fork": bool(r.get("isFork")), "private": bool(r.get("isPrivate")),
                      "archived": bool(r.get("isArchived")), "description": r.get("description") or "",
                      "parent": ((r.get("parent") or {}).get("owner") or {}).get("login", "") + "/" +
                      (r.get("parent") or {}).get("name", "") if r.get("parent") else None,
                      "gh_license": ((r.get("licenseInfo") or {}).get("spdxId"))})
    return repos


def merge_repo_lists(inventory: dict[str, Any], gh_list: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    known = {str(r["name"]): dict(r) for r in inventory["repos"] if isinstance(r, dict) and r.get("name")}
    if gh_list is None:
        return list(known.values())
    merged = []
    for r in gh_list:
        entry = {**known.get(r["name"], {}), **{k: v for k, v in r.items() if v is not None}}
        if r["name"] not in known and not r["fork"] and not r["private"]:
            entry.setdefault("pillar", "none")
            entry.setdefault("rationale", "nouveau dépôt non classé : revue humaine requise")
        merged.append(entry)
    return merged


# --------------------------------------------------------------------------- git

def clone_full(url: str, dest: Path) -> None:
    """Full-history clone (the secret scan needs every commit); refresh when present."""
    if (dest / ".git").is_dir():
        if run(["git", "-C", str(dest), "rev-parse", "--is-shallow-repository"]).stdout.strip() == "true":
            run(["git", "-C", str(dest), "fetch", "--unshallow", "--tags", "origin"])
        run(["git", "-C", str(dest), "fetch", "--prune", "--tags", "origin"])
        head = run(["git", "-C", str(dest), "symbolic-ref", "--short", "refs/remotes/origin/HEAD"], check=False).stdout.strip()
        if head:
            run(["git", "-C", str(dest), "checkout", "-q", "--detach", head])
        return
    if dest.exists():
        shutil.rmtree(dest)
    run(["git", "clone", "-q", url, str(dest)])


def clone_license_only(url: str, dest: Path) -> str | None:
    """Blob-less shallow clone, enough to read the licence file of a fork."""
    if not (dest / ".git").is_dir():
        if dest.exists():
            shutil.rmtree(dest)
        res = run(["git", "clone", "-q", "--depth", "1", "--filter=blob:none", "--no-checkout", url, str(dest)], check=False)
        if res.returncode != 0:
            return None
    names = run(["git", "-C", str(dest), "ls-tree", "--name-only", "HEAD"], check=False).stdout.split()
    for name in names:
        if re.match(r"(?i)^(licen[cs]e|copying)(\.(md|txt|rst))?$", name):
            text = run(["git", "-C", str(dest), "show", f"HEAD:{name}"], check=False).stdout
            return lic.detect_license_text(text) or "?"
    return None


def git_info(repo: Path) -> dict[str, Any]:
    head = run(["git", "-C", str(repo), "rev-parse", "HEAD"]).stdout.strip()
    count = run(["git", "-C", str(repo), "rev-list", "--count", "--all"]).stdout.strip()
    last = run(["git", "-C", str(repo), "log", "-1", "--format=%cs"]).stdout.strip()
    branch = run(["git", "-C", str(repo), "symbolic-ref", "--short", "refs/remotes/origin/HEAD"], check=False).stdout.strip()
    return {"head": head, "head_short": head[:7], "commits": int(count or 0), "last_commit": last,
            "default_branch": branch.removeprefix("origin/") or None}


# --------------------------------------------------------------------------- licence of the repository

def declared_licenses(repo: Path) -> dict[str, str]:
    """Licences declared by the root manifests (package.json, pyproject.toml, CITATION.cff, Cargo.toml)."""
    found: dict[str, str] = {}
    pkg = repo / "package.json"
    if pkg.is_file():
        try:
            value = json.loads(pkg.read_text(encoding="utf-8")).get("license")
            if value:
                found["package.json"] = str(value)
        except ValueError:
            pass
    for name in ("pyproject.toml", "Cargo.toml"):
        path = repo / name
        if path.is_file():
            try:
                import tomllib
                data = tomllib.loads(path.read_text(encoding="utf-8"))
            except Exception as exc:  # noqa: BLE001 - malformed manifest
                log(f"warning: cannot parse {path}: {exc}")
                continue
            section = data.get("project") or (data.get("package") if name == "Cargo.toml" else None) or {}
            value = section.get("license-expression") or section.get("license")
            if isinstance(value, dict):
                value = value.get("text") or value.get("file")
            if value:
                found[name] = str(value)
    cff = repo / "CITATION.cff"
    if cff.is_file():
        m = re.search(r"(?m)^license:\s*['\"]?([^'\"\n]+)", cff.read_text(encoding="utf-8", errors="replace"))
        if m:
            found["CITATION.cff"] = m.group(1).strip()
    return found


def repo_license(repo: Path) -> dict[str, Any]:
    lic_file = lic.find_license_file(repo)
    detected = lic.detect_license_text(lic_file.read_text(encoding="utf-8", errors="replace")) if lic_file else None
    declared = declared_licenses(repo)
    normalized = {k: lic.normalize_license(v) or v for k, v in declared.items()}
    spdx = detected or next(iter(normalized.values()), None)
    mismatches = [f"{k} déclare {v}" for k, v in normalized.items() if spdx and v != spdx]
    return {"spdx": spdx, "file": lic_file.name if lic_file else None, "declared": normalized,
            "mismatches": mismatches}


# --------------------------------------------------------------------------- dependencies

def _excluded(rel: str, patterns: list[str]) -> bool:
    return any(rel.startswith(p) for p in patterns)


def find_manifests(repo: Path, exclude: list[str]) -> list[Path]:
    names = re.compile(r"^(package\.json|pyproject\.toml|go\.mod|Cargo\.toml|requirements[^/]*\.(txt|in))$")
    out = []
    for dirpath, dirnames, filenames in os.walk(repo):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in filenames:
            path = Path(dirpath) / fn
            rel = path.relative_to(repo).as_posix()
            if names.match(fn) and not _excluded(rel, exclude):
                out.append(path)
    return sorted(out)


def _is_dev_name(text: str) -> bool:
    return any(tok in DEV_HINTS for tok in re.split(r"[^a-z0-9]+", text.lower()))


def _scope_from_name(rel: str, default: str = "runtime") -> str:
    return "dev" if _is_dev_name(rel) else default


def parse_manifest(repo: Path, path: Path) -> list[dict[str, Any]]:
    rel = path.relative_to(repo).as_posix()
    deps: list[dict[str, Any]] = []
    if path.name == "package.json":
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            return deps
        lock_path = path.with_name("package-lock.json")
        lock = {}
        if lock_path.is_file():
            try:
                lock = json.loads(lock_path.read_text(encoding="utf-8")).get("packages") or {}
            except ValueError:
                lock = {}
        for key, scope in (("dependencies", "runtime"), ("peerDependencies", "runtime"),
                           ("optionalDependencies", "runtime"), ("devDependencies", "dev")):
            for name, spec in (data.get(key) or {}).items():
                locked = lock.get(f"node_modules/{name}") or {}
                deps.append({"ecosystem": "npm", "name": name, "spec": str(spec), "scope": scope,
                             "version": locked.get("version"), "lock_license": locked.get("license"),
                             "manifest": rel})
    elif path.name == "pyproject.toml":
        try:
            import tomllib
            data = tomllib.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return deps
        project = data.get("project") or {}
        groups = [("runtime", project.get("dependencies") or [])]
        for group, items in (project.get("optional-dependencies") or {}).items():
            groups.append(("dev" if _is_dev_name(group) else "optional", items))
        for scope, items in groups:
            for spec in items:
                m = lic._REQ_LINE.match(spec)
                if m:
                    deps.append({"ecosystem": "pypi", "name": m.group(1), "spec": spec, "scope": scope,
                                 "version": m.group(3) if m.group(2) == "==" else None, "manifest": rel})
    elif path.name.startswith("requirements") or path.suffix in (".txt", ".in"):
        scope = _scope_from_name(rel)
        for dep in lic.parse_requirements(path):
            deps.append({"ecosystem": "pypi", "name": dep["name"], "spec": dep.get("version") or "", "scope": scope,
                         "version": dep.get("version"), "manifest": rel})
    elif path.name == "go.mod":
        text = path.read_text(encoding="utf-8")
        for m in re.finditer(r"(?m)^\s*(?:require\s+)?([a-zA-Z0-9.\-_/]+\.[a-z]{2,}[^\s]*)\s+(v[^\s]+)(\s*//\s*indirect)?\s*$", text):
            if m.group(3):
                continue
            deps.append({"ecosystem": "go", "name": m.group(1), "spec": m.group(2), "scope": _scope_from_name(rel),
                         "version": m.group(2), "manifest": rel})
    elif path.name == "Cargo.toml":
        try:
            import tomllib
            data = tomllib.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return deps
        for key, scope in (("dependencies", "runtime"), ("dev-dependencies", "dev"), ("build-dependencies", "dev")):
            for name, spec in (data.get(key) or {}).items():
                version = spec if isinstance(spec, str) else (spec or {}).get("version")
                deps.append({"ecosystem": "cargo", "name": name, "spec": str(version), "scope": scope,
                             "version": None, "manifest": rel})
    return deps


class LicenseLookup:
    """Dependency licence lookups with an on-disk cache."""

    def __init__(self, cache_file: Path, offline: bool, overrides: dict[str, dict[str, str]]):
        self.cache_file = cache_file
        self.offline = offline
        self.overrides = overrides
        try:
            self.cache = json.loads(cache_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.cache = {}

    def save(self) -> None:
        self.cache_file.write_text(json.dumps(self.cache, indent=1, sort_keys=True), encoding="utf-8")

    def _get_json(self, url: str) -> Any:
        req = urllib.request.Request(url, headers={"User-Agent": lic.USER_AGENT, "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_S) as resp:
            return json.load(resp)

    def _get_text(self, url: str) -> str:
        req = urllib.request.Request(url, headers={"User-Agent": lic.USER_AGENT})
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_S) as resp:
            return resp.read().decode("utf-8", errors="replace")

    @staticmethod
    def _npm_field(value: Any) -> str | None:
        if isinstance(value, dict):
            value = value.get("type")
        if isinstance(value, list):
            items = [v.get("type") if isinstance(v, dict) else v for v in value]
            value = " OR ".join(str(i) for i in items if i)
        return lic.normalize_license(str(value)) if value else None

    def lookup(self, dep: dict[str, Any]) -> tuple[str | None, str]:
        eco, name = dep["ecosystem"], dep["name"]
        override = (self.overrides.get(eco) or {}).get(name)
        if override:
            return str(override), "vérifié à la main"
        if eco == "npm" and dep.get("lock_license") and self._npm_field(dep["lock_license"]):
            return self._npm_field(dep["lock_license"]), "package-lock.json"
        key = f"{eco}:{name}@{dep.get('version') or 'latest'}"
        if key in self.cache:
            return self.cache[key][0], self.cache[key][1]
        if self.offline:
            return None, "hors ligne"
        spdx, source = None, ""
        try:
            if eco == "pypi":
                spdx, source = lic.pypi_license(name, dep.get("version")), "PyPI"
            elif eco == "npm":
                quoted = urllib.parse.quote(name, safe="@")
                data = self._get_json(f"https://registry.npmjs.org/{quoted}/{dep.get('version') or 'latest'}")
                spdx, source = self._npm_field(data.get("license") or data.get("licenses")), "registre npm"
            elif eco == "go":
                html = self._get_text(f"https://pkg.go.dev/{name}@{dep.get('version')}")
                m = re.search(r'data-test-id="UnitHeader-license"[^>]*>\s*([^<]+?)\s*</a>', html, re.DOTALL)
                if m:
                    ids = [x.strip() for x in m.group(1).split(",") if x.strip()]
                    spdx = " AND ".join(ids) if ids else None
                source = "pkg.go.dev"
            elif eco == "cargo":
                data = self._get_json(f"https://crates.io/api/v1/crates/{urllib.parse.quote(name)}")
                versions = data.get("versions") or []
                spdx = lic.normalize_license((versions[0] or {}).get("license")) if versions else None
                spdx = spdx.replace("/", " OR ") if spdx else spdx
                source = "crates.io"
        except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
            return None, f"échec de la recherche ({type(exc).__name__})"
        if spdx:  # failures are retried on the next run
            self.cache[key] = [spdx, source]
        return spdx, source


def analyse_dependencies(repo: Path, exclude: list[str], lookup: LicenseLookup, policy: lic.Policy,
                         extra: list[str] | None = None) -> dict[str, Any]:
    deps: list[dict[str, Any]] = []
    manifests = find_manifests(repo, exclude)
    for rel in extra or []:  # lock files with a non-standard name (pinned transitive dependencies)
        path = repo / rel
        if path.is_file() and path not in manifests:
            manifests.append(path)
    seen: set[tuple[str, str, str]] = set()
    for manifest in manifests:
        for dep in parse_manifest(repo, manifest):
            ident = (dep["ecosystem"], dep["name"], dep["scope"])
            if ident in seen:
                continue
            seen.add(ident)
            spdx, source = lookup.lookup(dep)
            dep["license"], dep["license_source"] = spdx, source
            dep["rank"] = lic.evaluate_spdx(spdx, policy)
            dep.pop("lock_license", None)
            deps.append(dep)
    runtime = [d for d in deps if d["scope"] in ("runtime", "optional")]
    return {
        "manifests": [m.relative_to(repo).as_posix() for m in manifests],
        "excluded_paths": exclude,
        "count": len(deps),
        "runtime_count": len(runtime),
        "copyleft_runtime": [f"{d['name']} ({d['license']})" for d in runtime if d["rank"] in ("denied", "separate")],
        "unknown_runtime": [d["name"] for d in runtime if d["rank"] == "unknown"],
        "weak_copyleft": [f"{d['name']} ({d['license']}, {d['scope']})" for d in deps if d["rank"] == "conditional"],
        "dev_flags": [f"{d['name']} ({d['license'] or '?'})" for d in deps
                      if d["scope"] == "dev" and d["rank"] in ("denied", "separate", "unknown")],
        "items": deps,
    }


# --------------------------------------------------------------------------- secrets

GITLEAKS_CONFIG = "[extend]\nuseDefault = true\n"


def gitleaks_summary(repo: Path, work: Path, triage: list[dict[str, Any]], name: str) -> dict[str, Any]:
    """Run a full-history scan and return only aggregate inventory data."""
    cfg = work / "gitleaks-cfg"
    cfg.mkdir(parents=True, exist_ok=True)
    (cfg / "gitleaks.toml").write_text(GITLEAKS_CONFIG, encoding="utf-8")
    (cfg / "empty.gitleaksignore").write_text("", encoding="utf-8")

    def gitleaks_args(scan: str, cfg_dir: str, out_dir: str) -> list[str]:
        return ["git", scan, "--redact", "--no-banner", "--exit-code", "0", "--ignore-gitleaks-allow",
                "--config", f"{cfg_dir}/gitleaks.toml", "--gitleaks-ignore-path", f"{cfg_dir}/empty.gitleaksignore",
                "--report-format", "json", "--report-path", f"{out_dir}/report.json"]

    with tempfile.TemporaryDirectory(prefix="gitleaks-out-", dir=work) as out:
        report = Path(out) / "report.json"
        if shutil.which("docker"):
            cmd = ["docker", "run", "--rm", "--network", "none", "-v", f"{repo}:/scan:ro", "-v", f"{cfg}:/cfg:ro",
                   "-v", f"{out}:/out", GITLEAKS_IMAGE, *gitleaks_args("/scan", "/cfg", "/out")]
        elif shutil.which("gitleaks"):
            cmd = ["gitleaks", *gitleaks_args(str(repo), str(cfg), out)]
        else:
            return {"status": "not_scanned", "reason": "ni docker ni gitleaks disponibles"}
        try:
            res = run(cmd, check=False, timeout=3600)
            if res.returncode != 0 or not report.is_file():
                return {"status": "not_scanned", "reason": f"gitleaks a échoué (code {res.returncode})"}
            raw = json.loads(report.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError, subprocess.TimeoutExpired):
            return {"status": "not_scanned", "reason": "rapport gitleaks indisponible ou invalide"}
    if not isinstance(raw, list) or any(not isinstance(f, dict) or type(f.get("StartLine")) is not int for f in raw):
        return {"status": "not_scanned", "reason": "rapport gitleaks indisponible ou invalide"}
    m = re.search(r"\b([0-9]{1,10}) commits scanned\b", res.stderr + "\n" + res.stdout)
    findings = [{"rule": f.get("RuleID"), "file": f.get("File"), "commit": str(f.get("Commit", ""))[:7],
                 "line": f["StartLine"]} for f in raw]
    remaining, false_pos = apply_triage(findings, triage, name)
    return {"status": "clean" if not remaining else "findings", "tool": GITLEAKS_IMAGE,
            "commits_scanned": int(m.group(1)) if m else None, "raw_findings": len(findings),
            "false_positive_count": len(false_pos), "remaining_count": len(remaining)}


def apply_triage(findings: list[dict[str, Any]], triage: list[dict[str, Any]], name: str
                 ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split findings into (remaining, false positives) with the manual triage of the repository."""
    rules = [t for t in triage if t.get("repo") == name and t.get("verdict") == "false_positive"]

    def match(f: dict[str, Any]) -> dict[str, Any] | None:
        for t in rules:
            if (str(f["commit"])[:7] == str(t.get("commit"))[:7] and f["file"] == t.get("file")
                    and f["rule"] == t.get("rule") and int(f["line"] or 0) == int(t.get("line") or -1)):
                return t
        return None

    remaining, false_pos = [], []
    for f in findings:
        t = match(f)
        (false_pos if t else remaining).append({**f, **({"reason": t.get("reason")} if t else {})})
    return remaining, false_pos


# --------------------------------------------------------------------------- decision

def decide(entry: dict[str, Any], licence: dict[str, Any] | None, deps: dict[str, Any] | None,
           scan: dict[str, Any] | None, policy: lic.Policy) -> tuple[str, str]:
    if entry.get("private"):
        return "excluded", "dépôt privé : hors périmètre d'une publication MIT"
    if entry.get("fork"):
        return "excluded", "fork d'un projet amont : pas un projet propre, relicence impossible (droits des auteurs amont)"
    pillar = entry.get("pillar") or "none"
    if pillar not in PILLARS:
        why = str(entry.get("rationale") or "non classé").rstrip(".")
        extra = ""
        if scan and scan.get("status") == "findings":
            extra = f" ; en outre {scan['remaining_count']} secret(s) potentiel(s) non trié(s)"
        return "excluded", f"aucun pilier de l'usine : {why[:1].lower() + why[1:]}{extra}"
    if scan and scan.get("status") == "findings":
        return "excluded", f"{scan['remaining_count']} secret(s) potentiel(s) non trié(s) dans l'historique"
    if scan is None or scan.get("status") != "clean":
        return "excluded", "scan de secrets non effectué : intégration bloquée"
    if deps and deps["copyleft_runtime"]:
        return "excluded", "dépendance(s) d'exécution copyleft : " + ", ".join(deps["copyleft_runtime"])
    spdx = (licence or {}).get("spdx")
    rank = lic.evaluate_spdx(spdx, policy)
    if not spdx:
        return "to_relicense", "pilier couvert mais aucune licence : ajouter un fichier LICENSE MIT"
    if rank not in ("permissive",):
        return "to_relicense", f"pilier couvert mais licence {spdx} incompatible avec l'usine : relicencier en MIT"
    if deps and deps["unknown_runtime"]:
        return "excluded", "licence inconnue pour : " + ", ".join(deps["unknown_runtime"]) + " (vérifier puis renseigner license_overrides)"
    return "integrated", f"pilier {PILLAR_LABELS[pillar]}, licence {spdx}, aucune dépendance d'exécution copyleft, scan propre"


# --------------------------------------------------------------------------- outputs

def card_for(result: dict[str, Any], owner: str) -> dict[str, Any]:
    name = result["name"]
    scan = result.get("scan_summary") or {}
    deps = result.get("dependencies") or {}
    git = result.get("git") or {}
    status = result["status"]
    reason = f"{result.get('rationale', '')} Décision : {result['reason']}."
    card = {
        "id": f"gh-{re.sub(r'[^a-z0-9-]+', '-', name.lower()).strip('-')}",
        "name": name,
        "url": f"https://github.com/{owner}/{name}",
        "pillar": result["pillar"],
        "license": (result.get("license") or {}).get("spdx") or "NOASSERTION",
        "status": status,
        "submodule": f"integrations/{name}" if status == "integrated" else None,
        "reason": reason.strip(),
        "secrets_scan": scan.get("status", "not_scanned"),
        "copyleft_deps": list(deps.get("copyleft_runtime") or []),
        # Informative fields (ignored by the contract readers).
        "description": result.get("summary") or "",
        "commit": git.get("head_short"),
        "inventoried": result.get("inventoried"),
        "secrets_detail": (f"gitleaks {scan.get('tool', '').rsplit(':', 1)[-1]}, {scan.get('commits_scanned')} commits, "
                            f"{scan['raw_findings']} résultat(s) brut(s), {scan['false_positive_count']} faux positif(s) trié(s), "
                            f"{scan['remaining_count']} restant(s)") if scan.get("status") in ("clean", "findings") else None,
        "dependencies_checked": deps.get("count", 0),
        "relicense_to_mit": result.get("relicense"),
        "notes": result.get("notes"),
    }
    return card


def write_card(card: dict[str, Any]) -> Path:
    CARDS_DIR.mkdir(parents=True, exist_ok=True)
    path = CARDS_DIR / f"{card['name']}.yaml"
    header = ("# SPDX-License-Identifier: MIT\n"
              "# Card generated by scripts/inventory_github.py (CONTRACT 2.5). Facts (license, scan, commit...) are\n"
              "# regenerated; bilingual {en, fr} texts already present in the card are kept (CONTRACT section 9).\n")
    if path.exists():  # keep curated translations: a regenerated text never overwrites an {en, fr} mapping
        try:
            old = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError:
            old = {}
        for k, v in old.items():
            if isinstance(v, dict) and set(v) <= {"en", "fr"} and v and isinstance(card.get(k), str):
                card[k] = v
    body = yaml.safe_dump({k: v for k, v in card.items() if v is not None}, allow_unicode=True, sort_keys=False, width=110)
    path.write_text(header + body, encoding="utf-8")
    return path


def add_submodule(name: str, url: str, head: str | None) -> str:
    path = f"integrations/{name}"
    if (ROOT / path / ".git").exists() or path in lic.parse_gitmodules(ROOT / ".gitmodules"):
        return "déjà présent"
    res = run(["git", "-C", str(ROOT), "submodule", "add", url, path], check=False, timeout=1800)
    if res.returncode != 0:
        return "échec : " + (res.stderr.strip().splitlines() or ["?"])[-1]
    if head:  # pin the gitlink to the exact scanned commit
        run(["git", "-C", str(ROOT / path), "checkout", "-q", head], check=False)
        run(["git", "-C", str(ROOT), "add", path], check=False)
    return "ajouté"


def relicense_note(result: dict[str, Any]) -> str | None:
    spdx = (result.get("license") or {}).get("spdx")
    if result.get("fork") or result.get("private"):
        return None
    if not spdx:
        if result.get("pillar") not in PILLARS:
            return "optionnel : hors piliers, ajouter une licence seulement si une réutilisation est souhaitée"
        return "à faire : aucune licence"
    if spdx == "MIT":
        return None
    if spdx in ("Apache-2.0", "BSD-2-Clause", "BSD-3-Clause", "ISC"):
        return f"optionnel : {spdx} déjà compatible, passage en MIT seulement pour harmoniser"
    return f"à faire : {spdx}"


def markdown_table(results: list[dict[str, Any]]) -> str:
    lines = ["| # | Dépôt | Type | Pilier | Licence | Dépendances | Secrets (gitleaks, historique complet) | Décision | Raison |",
             "|---|---|---|---|---|---|---|---|---|"]
    for i, r in enumerate(results, 1):
        kind = "privé" if r.get("private") else "fork" if r.get("fork") else "propre"
        deps = r.get("dependencies")
        if deps and not deps["count"]:
            dep_txt = "aucune dépendance déclarée"
        elif deps:
            dep_txt = f"{deps['count']} vérifiées"
            extra = []
            if deps["copyleft_runtime"]:
                extra.append("copyleft : " + ", ".join(deps["copyleft_runtime"]))
            if deps["unknown_runtime"]:
                extra.append("inconnues : " + ", ".join(deps["unknown_runtime"]))
            if deps["weak_copyleft"]:
                extra.append("copyleft faible : " + ", ".join(deps["weak_copyleft"]))
            if deps["dev_flags"]:
                extra.append("outillage dev : " + ", ".join(deps["dev_flags"]))
            dep_txt += (" ; " + " ; ".join(extra)) if extra else ", aucune copyleft"
        else:
            dep_txt = "—"
        scan = r.get("scan_summary")
        if scan and scan.get("status") in ("clean", "findings"):
            sec = (f"{scan['commits_scanned']} commits, {scan['raw_findings']} brut(s), "
                   f"{scan['false_positive_count']} faux positif(s), {scan['remaining_count']} restant(s)")
        else:
            sec = "non scanné (" + ("fork" if r.get("fork") else "privé" if r.get("private") else "hors piliers") + ")" \
                if not scan else scan.get("reason", "non scanné")
        licence = ((r.get("license") or {}).get("spdx")
                   or (r.get("upstream_license") and f"{r['upstream_license']} (amont)")
                   or ("non lue" if r.get("private") else "aucun fichier LICENSE"))
        status = {"integrated": "**intégré**", "to_relicense": "**à relicencier**", "excluded": "exclu"}[r["status"]]
        link = r["name"] if r.get("private") else f"[{r['name']}]({r.get('url')})"
        lines.append(f"| {i} | {link} | {kind} | {PILLAR_LABELS.get(r.get('pillar') or 'none', r.get('pillar'))} "
                     f"| {licence} | {dep_txt} | {sec} | {status} | {r['reason']} |")
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- self-test

def self_test() -> int:
    """Built-in checks of the decision rules, scope detection and triage (no network, no clone)."""
    policy = lic.Policy.default()
    failures: list[str] = []

    def expect(label: str, got: Any, want: Any) -> None:
        if got != want:
            failures.append(f"{label}: got {got!r}, want {want!r}")

    clean = {"status": "clean"}
    no_deps = {"copyleft_runtime": [], "unknown_runtime": []}
    mit = {"spdx": "MIT"}
    cases = [
        ("fork", {"fork": True, "pillar": "rag"}, mit, no_deps, clean, "excluded"),
        ("private", {"private": True, "pillar": "rag"}, mit, no_deps, clean, "excluded"),
        ("no pillar", {"pillar": "none"}, mit, no_deps, clean, "excluded"),
        ("integrated", {"pillar": "rag"}, mit, no_deps, clean, "integrated"),
        ("apache", {"pillar": "evaluation"}, {"spdx": "Apache-2.0"}, no_deps, clean, "integrated"),
        ("no licence", {"pillar": "rag"}, {"spdx": None}, no_deps, clean, "to_relicense"),
        ("own GPL", {"pillar": "rag"}, {"spdx": "GPL-3.0"}, no_deps, clean, "to_relicense"),
        ("secrets", {"pillar": "rag"}, mit, no_deps, {"status": "findings", "remaining_count": 1}, "excluded"),
        ("not scanned", {"pillar": "rag"}, mit, no_deps, None, "excluded"),
        ("copyleft dep", {"pillar": "rag"}, mit, {"copyleft_runtime": ["x (GPL-3.0)"], "unknown_runtime": []}, clean, "excluded"),
        ("unknown dep", {"pillar": "rag"}, mit, {"copyleft_runtime": [], "unknown_runtime": ["y"]}, clean, "excluded"),
    ]
    for label, entry, licence, deps, scan, want in cases:
        expect(f"decide({label})", decide(entry, licence, deps, scan, policy)[0], want)
    for rel, want in [("requirements-dev.txt", "dev"), ("tools/motion/requirements.txt", "dev"),
                      ("agent/pyproject.toml", "runtime"), ("docker/requirements.txt", "runtime"),
                      ("requirements/runtime.txt", "runtime")]:
        expect(f"scope({rel})", _scope_from_name(rel), want)
    findings = [{"rule": "generic-api-key", "file": "a.py", "commit": "abc1234", "line": 3},
                {"rule": "private-key", "file": "k.pem", "commit": "def5678", "line": 1}]
    triage = [{"repo": "r", "commit": "abc1234", "file": "a.py", "rule": "generic-api-key", "line": 3,
               "verdict": "false_positive", "reason": "test"},
              {"repo": "other", "commit": "def5678", "file": "k.pem", "rule": "private-key", "line": 1,
               "verdict": "false_positive"}]
    remaining, false_pos = apply_triage(findings, triage, "r")
    expect("triage remaining", [f["file"] for f in remaining], ["k.pem"])
    expect("triage false positives", [f["file"] for f in false_pos], ["a.py"])
    for f in failures:
        print("ÉCHEC", f)
    print(f"auto-test : {'OK' if not failures else f'{len(failures)} échec(s)'}")
    return 1 if failures else 0


# --------------------------------------------------------------------------- main

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Inventaire reproductible des dépôts GitHub publics d'un compte")
    parser.add_argument("--repos-file", default=str(INVENTORY), help="liste et classement (défaut integrations/inventory.yaml)")
    parser.add_argument("--from-gh", metavar="OWNER", help="liste des dépôts via `gh repo list OWNER` (gh authentifié)")
    parser.add_argument("--workdir", default=str(Path(tempfile.gettempdir()) / "ai-factory-inventory"))
    parser.add_argument("--only", nargs="*", help="limiter à ces dépôts")
    parser.add_argument("--no-scan", action="store_true", help="ne pas lancer gitleaks")
    parser.add_argument("--no-deps", action="store_true", help="ne pas analyser les dépendances")
    parser.add_argument("--no-forks", action="store_true", help="ne pas lire la licence des forks")
    parser.add_argument("--offline", action="store_true", help="aucune requête vers les registres de paquets")
    parser.add_argument("--report", help="écrire le rapport JSON (aucune valeur de secret)")
    parser.add_argument("--markdown", help="écrire le tableau d'inventaire (Markdown)")
    parser.add_argument("--write-cards", action="store_true", help="écrire config/catalog/github/<repo>.yaml")
    parser.add_argument("--add-submodules", action="store_true", help="git submodule add des dépôts intégrés (sans commit)")
    parser.add_argument("--self-test", action="store_true", help="auto-test des règles de décision (sans réseau)")
    args = parser.parse_args(argv)
    if args.self_test:
        return self_test()

    inventory = load_inventory(Path(args.repos_file))
    owner = args.from_gh or str(inventory.get("owner") or "")
    if not owner:
        log("owner missing (inventory.yaml 'owner' or --from-gh)")
        return 2
    gh_list = None
    if args.from_gh:
        try:
            gh_list = gh_repo_list(args.from_gh)
        except (subprocess.CalledProcessError, FileNotFoundError, ValueError) as exc:
            log(f"gh repo list failed ({exc}); run `gh auth login` or use --repos-file")
            return 2
    repos = merge_repo_lists(inventory, gh_list)
    if args.only:
        repos = [r for r in repos if r["name"] in args.only]
    work = Path(args.workdir)
    work.mkdir(parents=True, exist_ok=True)
    policy = lic.Policy.default()
    triage = (lic.load_yaml(TRIAGE) or {}).get("triage") or [] if TRIAGE.is_file() else []
    lookup = LicenseLookup(work / "license-cache.json", args.offline, inventory.get("license_overrides") or {})
    excludes = inventory.get("dependency_exclude_paths") or {}
    today = dt.datetime.now(dt.UTC).date().isoformat()

    results = []
    for entry in repos:
        name = entry["name"]
        url = f"https://github.com/{owner}/{name}.git"
        result: dict[str, Any] = {k: v for k, v in entry.items() if k not in ("secrets", "scan_summary")}
        result.update(url=f"https://github.com/{owner}/{name}", inventoried=today, pillar=entry.get("pillar") or "none")
        licence = deps = scan = None
        if entry.get("private"):
            pass
        elif entry.get("fork"):
            if not args.no_forks:
                log(f"[fork] {name}: licence")
                result["upstream_license"] = clone_license_only(url, work / "forks" / name) or entry.get("gh_license")
        else:
            log(f"[repo] {name}: clone")
            dest = work / "repos" / name
            try:
                clone_full(url, dest)
            except subprocess.CalledProcessError as exc:
                result.update(status="excluded", reason=f"clonage impossible ({exc.returncode})")
                results.append(result)
                continue
            result["git"] = git_info(dest)
            licence = repo_license(dest)
            result["license"] = licence
            if not args.no_deps:
                log(f"[repo] {name}: dependencies")
                deps = analyse_dependencies(dest, list(excludes.get(name) or []), lookup, policy,
                                            list((inventory.get("extra_manifests") or {}).get(name) or []))
                result["dependencies"] = deps
            if not args.no_scan:
                log(f"[repo] {name}: gitleaks")
                scan = gitleaks_summary(dest, work, triage, name)
                result["scan_summary"] = scan
        status, reason = decide(entry, licence, deps, scan, policy)
        if args.no_scan and status == "excluded" and result["pillar"] in PILLARS and scan is None:
            reason += " (relancer sans --no-scan)"
        result.update(status=status, reason=reason, relicense=relicense_note(result))
        results.append(result)
    lookup.save()

    for r in results:
        if r["status"] == "integrated" and args.add_submodules:
            r["submodule_action"] = add_submodule(r["name"], f"https://github.com/{owner}/{r['name']}.git",
                                                  (r.get("git") or {}).get("head"))
        if args.write_cards and r.get("pillar") in PILLARS and r["status"] in ("integrated", "to_relicense"):
            r["card"] = write_card(card_for(r, owner)).relative_to(ROOT).as_posix()

    # Consistency: an existing integrated card or submodule must match the computed decision.
    problems = []
    gitmodules = lic.parse_gitmodules(ROOT / ".gitmodules")
    for r in results:
        path = f"integrations/{r['name']}"
        if path in gitmodules and r["status"] != "integrated" and not (args.no_scan or args.no_deps):
            problems.append(f"{path}: sous-module présent alors que la décision est « {r['status']} »")

    if args.report:
        slim = [{("secrets" if k == "scan_summary" else k): v
                 for k, v in r.items() if k != "gh_license"} for r in results]
        Path(args.report).write_text(json.dumps({"owner": owner, "date": today, "tool": GITLEAKS_IMAGE,
                                                 "repos": slim}, ensure_ascii=False, indent=1), encoding="utf-8")
    if args.markdown:
        target = Path(args.markdown)
        table = markdown_table(results)
        text = target.read_text(encoding="utf-8") if target.is_file() else ""
        start, end = "<!-- INVENTORY_TABLE_START -->", "<!-- INVENTORY_TABLE_END -->"
        if start in text and end in text:  # refresh the generated block of a hand-written document
            head, rest = text.split(start, 1)
            text = f"{head}{start}\n{table}{end}{rest.split(end, 1)[1]}"
        else:
            text = table
        target.write_text(text, encoding="utf-8")

    width = max((len(r["name"]) for r in results), default=10)
    print(f"{'dépôt'.ljust(width)}  {'type':6}  {'pilier':20}  {'licence':10}  {'secrets':9}  décision")
    for r in results:
        kind = "privé" if r.get("private") else "fork" if r.get("fork") else "propre"
        licence = (r.get("license") or {}).get("spdx") or r.get("upstream_license") or "-"
        print(f"{r['name'].ljust(width)}  {kind:6}  {r['pillar']:20}  {licence!s:10}  "
              f"{(r.get('scan_summary') or {}).get('status', '-'):9}  {r['status']}")
    counts = {s: sum(1 for r in results if r["status"] == s) for s in ("integrated", "to_relicense", "excluded")}
    print(f"\n{len(results)} dépôts : {counts['integrated']} intégrés, {counts['to_relicense']} à relicencier, "
          f"{counts['excluded']} exclus.")
    for p in problems:
        print("INCOHÉRENCE :", p)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
