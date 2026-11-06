"""Offline CVE correlation for operator-declared AI Factory assets."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import sys
from collections import defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import unquote


_MAX_INVENTORY_BYTES = 1024 * 1024
_MAX_TRIVY_BYTES = 32 * 1024 * 1024
_MAX_KEV_BYTES = 4 * 1024 * 1024
_MAX_REPORTS = 16
_MAX_ASSETS = 256
_MAX_ASSET_ENTRIES = 128
_MAX_RESULTS = 2048
_MAX_SCAN_ENTRIES = 100_000
_MAX_FINDINGS = 5000
_MAX_IMPACT_LINKS = 100_000
_MAX_KEV_ENTRIES = 20_000

_ASSET_ID = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}", re.ASCII)
_KINDS = frozenset({"ai", "runtime", "tool", "connector", "process"})
_CVE_ID = re.compile(r"CVE-[0-9]{4}-[0-9]{4,19}", re.ASCII)
_PURL_TYPE = re.compile(r"[a-z][a-z0-9.-]{0,31}", re.ASCII)
_PURL_PATH = re.compile(r"[A-Za-z0-9._~%/-]+", re.ASCII)
_PURL_VERSION = re.compile(r"[A-Za-z0-9._~%:-]+", re.ASCII)
_PURL_SUFFIX = re.compile(r"[A-Za-z0-9._~%:/=&-]+", re.ASCII)
_PURL_BAD_ESCAPE = re.compile(r"%(?![0-9a-f]{2})|%(?:0[0-9a-f]|1[0-9a-f]|7f)", re.IGNORECASE)
_SEVERITIES = ("unknown", "low", "medium", "high", "critical")
_SEVERITY_RANK = {name: rank for rank, name in enumerate(_SEVERITIES)}


class CyberCveError(ValueError):
    """An operator error represented by a fixed code safe for CLI output."""


@dataclass(frozen=True)
class Asset:
    asset_id: str
    kind: str
    packages: frozenset[str]
    dependencies: frozenset[str]


@dataclass
class Finding:
    cve: str
    purl: str | None
    package_ref: str
    severity: str
    evidence: dict[str, tuple[int, int]] = field(default_factory=dict)


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _reject_constant(_: str) -> None:
    raise ValueError("invalid JSON constant")


def _read_json(path: Path, limit: int, label: str) -> tuple[dict[str, Any], str]:
    flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0)
    try:
        with os.fdopen(os.open(path, flags), "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode):
                raise CyberCveError(f"{label}_file_invalid")
            if info.st_size > limit:
                raise CyberCveError(f"{label}_too_large")
            raw = stream.read(limit + 1)
    except OSError:
        raise CyberCveError(f"{label}_file_unavailable") from None
    if len(raw) > limit:
        raise CyberCveError(f"{label}_too_large")
    try:
        data = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object,
                          parse_constant=_reject_constant)
    except (ValueError, RecursionError):
        raise CyberCveError(f"{label}_invalid") from None
    if not isinstance(data, dict):
        raise CyberCveError(f"{label}_invalid")
    return data, hashlib.sha256(raw).hexdigest()


def _purl_version(value: Any) -> str | None:
    if (not isinstance(value, str) or not 1 <= len(value) <= 512
            or not value.startswith("pkg:") or _PURL_BAD_ESCAPE.search(value)):
        return None
    main, fragment_marker, fragment = value[4:].partition("#")
    main, query_marker, query = main.partition("?")
    path, version_marker, version = main.rpartition("@")
    package_type, slash, package_path = path.partition("/")
    if (not slash or not version_marker or not _PURL_TYPE.fullmatch(package_type)
            or not _PURL_PATH.fullmatch(package_path) or not _PURL_VERSION.fullmatch(version)
            or any(part in {"", ".", ".."} for part in package_path.split("/"))):
        return None
    if ((query_marker and not _PURL_SUFFIX.fullmatch(query))
            or (fragment_marker and not _PURL_PATH.fullmatch(fragment))):
        return None
    try:
        return unquote(version, errors="strict")
    except UnicodeDecodeError:
        return None


def _inventory(path: Path) -> tuple[bool, dict[str, Asset]]:
    data, _ = _read_json(path, _MAX_INVENTORY_BYTES, "inventory")
    if (set(data) != {"v", "enabled", "assets"} or type(data["v"]) is not int or data["v"] != 1
            or type(data["enabled"]) is not bool or not isinstance(data["assets"], list)
            or len(data["assets"]) > _MAX_ASSETS):
        raise CyberCveError("inventory_invalid")

    assets: dict[str, Asset] = {}
    for row in data["assets"]:
        if not isinstance(row, dict) or row.keys() - {"id", "kind", "packages", "depends_on"}:
            raise CyberCveError("inventory_asset_invalid")
        asset_id = row.get("id")
        kind = row.get("kind")
        packages = row.get("packages", [])
        dependencies = row.get("depends_on", [])
        if (not isinstance(asset_id, str) or not _ASSET_ID.fullmatch(asset_id)
                or asset_id in assets or not isinstance(kind, str) or kind not in _KINDS):
            raise CyberCveError("inventory_asset_invalid")
        if (not isinstance(packages, list) or len(packages) > _MAX_ASSET_ENTRIES
                or any(_purl_version(package) is None for package in packages)
                or len(set(packages)) != len(packages)):
            raise CyberCveError("inventory_package_invalid")
        if (not isinstance(dependencies, list) or len(dependencies) > _MAX_ASSET_ENTRIES
                or any(not isinstance(item, str) or not _ASSET_ID.fullmatch(item) for item in dependencies)
                or len(set(dependencies)) != len(dependencies)):
            raise CyberCveError("inventory_dependency_invalid")
        assets[asset_id] = Asset(asset_id, kind, frozenset(packages), frozenset(dependencies))
    return data["enabled"], assets


def _asset_impacts(assets: dict[str, Asset]) -> dict[str, set[str]]:
    """Resolve dependent assets in reverse topological order, rejecting cycles."""
    dependents: dict[str, set[str]] = defaultdict(set)
    pending: dict[str, int] = {}
    for asset_id, asset in assets.items():
        pending[asset_id] = len(asset.dependencies)
        for dependency in asset.dependencies:
            if dependency not in assets:
                raise CyberCveError("inventory_dependency_unknown")
            dependents[dependency].add(asset_id)

    queue = deque(asset_id for asset_id, count in pending.items() if count == 0)
    ordered: list[str] = []
    while queue:
        asset_id = queue.popleft()
        ordered.append(asset_id)
        for dependent in dependents[asset_id]:
            pending[dependent] -= 1
            if pending[dependent] == 0:
                queue.append(dependent)
    if len(ordered) != len(assets):
        raise CyberCveError("inventory_dependency_cycle")

    impacts: dict[str, set[str]] = {}
    for asset_id in reversed(ordered):
        impacts[asset_id] = {asset_id}
        for dependent in dependents[asset_id]:
            impacts[asset_id].update(impacts[dependent])
    return impacts


def _kev(path: Path | None) -> tuple[set[str], str | None]:
    if path is None:
        return set(), None
    data, reference = _read_json(path, _MAX_KEV_BYTES, "kev")
    rows = data.get("vulnerabilities")
    if not isinstance(rows, list) or len(rows) > _MAX_KEV_ENTRIES:
        raise CyberCveError("kev_invalid")
    if "count" in data and (type(data["count"]) is not int or data["count"] != len(rows)):
        raise CyberCveError("kev_invalid")
    result = set()
    for row in rows:
        cve = row.get("cveID") if isinstance(row, dict) else None
        if not isinstance(cve, str) or not _CVE_ID.fullmatch(cve):
            raise CyberCveError("kev_invalid")
        result.add(cve)
    return result, reference


def _result_rows(data: dict[str, Any], key: str, maximum: int) -> list[Any]:
    rows = data.get(key)
    if rows is None:
        return []
    if not isinstance(rows, list) or len(rows) > maximum:
        raise CyberCveError("trivy_invalid")
    return rows


def _identifier(row: dict[str, Any], key: str) -> Any:
    value = row.get(key)
    return value.get("PURL") if isinstance(value, dict) else None


def _reference(value: str) -> str:
    return hashlib.sha256(value.encode("ascii")).hexdigest()


def _observe_cve(row: dict[str, Any], report_ref: str, result_index: int, row_index: int,
                 observed: set[str], findings: dict[tuple[str, str], Finding]) -> bool:
    purl = _identifier(row, "PkgIdentifier")
    version = _purl_version(purl)
    observed_package = version is not None and version == row.get("InstalledVersion")
    if observed_package:
        observed.add(purl)
    cve = row.get("VulnerabilityID")
    if not isinstance(cve, str) or not _CVE_ID.fullmatch(cve):
        return False
    if observed_package:
        package_ref = _reference(f"purl:{purl}")
    else:
        # Keep the CVE, without merging rows by an unverified name or version.
        purl = None
        package_ref = _reference(f"unmapped:{report_ref}:{result_index}:{row_index}")
    severity = row.get("Severity")
    severity = severity.lower() if isinstance(severity, str) and len(severity) <= 8 else "unknown"
    if severity not in _SEVERITY_RANK:
        severity = "unknown"
    key = (cve, package_ref)
    finding = findings.get(key)
    if finding is None:
        if len(findings) >= _MAX_FINDINGS:
            raise CyberCveError("too_many_findings")
        finding = findings[key] = Finding(cve, purl, package_ref, severity)
    elif _SEVERITY_RANK[severity] > _SEVERITY_RANK[finding.severity]:
        finding.severity = severity
    finding.evidence.setdefault(report_ref, (result_index, row_index))
    return True


def _scan_reports(paths: list[Path]) -> tuple[set[str], list[Finding], list[str], int]:
    if not 1 <= len(paths) <= _MAX_REPORTS:
        raise CyberCveError("trivy_reports_required_or_limit_exceeded")
    observed: set[str] = set()
    findings: dict[tuple[str, str], Finding] = {}
    references: set[str] = set()
    scanned = ignored = 0
    for path in paths:
        data, reference = _read_json(path, _MAX_TRIVY_BYTES, "trivy")
        if reference in references:
            continue
        references.add(reference)
        version = data.get("SchemaVersion")
        if type(version) is not int or version != 2:
            raise CyberCveError("trivy_schema_unsupported")
        for result_index, result in enumerate(_result_rows(data, "Results", _MAX_RESULTS)):
            if not isinstance(result, dict):
                raise CyberCveError("trivy_invalid")
            packages = _result_rows(result, "Packages", _MAX_SCAN_ENTRIES)
            vulnerabilities = _result_rows(result, "Vulnerabilities", _MAX_SCAN_ENTRIES)
            scanned += len(packages) + len(vulnerabilities)
            if scanned > _MAX_SCAN_ENTRIES:
                raise CyberCveError("too_many_scan_entries")
            for package in packages:
                if not isinstance(package, dict):
                    raise CyberCveError("trivy_invalid")
                purl = _identifier(package, "Identifier")
                if _purl_version(purl) is not None:
                    observed.add(purl)
            for row_index, row in enumerate(vulnerabilities):
                if not isinstance(row, dict):
                    raise CyberCveError("trivy_invalid")
                if not _observe_cve(row, reference, result_index, row_index, observed, findings):
                    ignored += 1
    return observed, list(findings.values()), sorted(references), ignored


def _asset_report(asset: Asset, observed: set[str]) -> dict[str, Any]:
    seen = len(asset.packages.intersection(observed))
    if not asset.packages:
        coverage = "dependency_only" if asset.dependencies else "not_versioned"
    elif seen == len(asset.packages):
        coverage = "observed"
    else:
        coverage = "partial" if seen else "unverified"
    return {"id": asset.asset_id, "kind": asset.kind, "coverage": coverage,
            "packages_observed": seen, "packages_declared": len(asset.packages),
            "depends_on": sorted(asset.dependencies)}


def _finding_report(findings: list[Finding], assets: dict[str, Asset],
                    impacts: dict[str, set[str]], kev: set[str], kev_ref: str | None) -> list[dict[str, Any]]:
    direct: dict[str, set[str]] = defaultdict(set)
    for asset in assets.values():
        for package in asset.packages:
            direct[package].add(asset.asset_id)
    affected: dict[str, set[str]] = {}
    report: list[dict[str, Any]] = []
    links = 0
    for finding in findings:
        direct_assets = direct.get(finding.purl, ()) if finding.purl is not None else ()
        affected_assets: set[str] = set()
        if direct_assets and finding.purl is not None:
            if finding.purl not in affected:
                affected[finding.purl] = set().union(*(impacts[asset_id] for asset_id in direct_assets))
            affected_assets = affected[finding.purl]
        links += len(direct_assets) + len(affected_assets)
        if links > _MAX_IMPACT_LINKS:
            raise CyberCveError("too_many_impact_links")
        known_exploited = finding.cve in kev if kev_ref is not None else None
        report.append({
            "finding_ref": _reference(f"{finding.cve}:{finding.package_ref}"),
            "cve": finding.cve,
            "severity": finding.severity,
            "priority": "critical" if known_exploited else finding.severity,
            "known_exploited": known_exploited,
            "package_ref": finding.package_ref,
            "mapping": "mapped" if direct_assets else "unmapped",
            "direct_assets": sorted(direct_assets),
            "affected_assets": sorted(affected_assets),
            "evidence": [{"report_ref": reference, "result_index": result_index, "row_index": row_index}
                         for reference, (result_index, row_index) in sorted(finding.evidence.items())],
        })
    return sorted(report, key=lambda row: (-_SEVERITY_RANK[row["priority"]], row["cve"], row["finding_ref"]))


def correlate(inventory_path: Path, trivy_paths: list[Path], kev_path: Path | None = None) -> dict[str, Any]:
    enabled, assets = _inventory(inventory_path)
    impacts = _asset_impacts(assets)
    if not enabled:
        return {"v": 1, "enabled": False}
    observed, findings, report_refs, ignored = _scan_reports(trivy_paths)
    kev, kev_ref = _kev(kev_path)
    return {
        "v": 1,
        "enabled": True,
        "report_refs": report_refs,
        "kev_ref": kev_ref,
        "ignored_non_cve": ignored,
        "assets": [_asset_report(assets[asset_id], observed) for asset_id in sorted(assets)],
        "findings": _finding_report(findings, assets, impacts, kev, kev_ref),
    }


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        # argparse normally repeats user input, which may contain private paths.
        raise CyberCveError("invalid_arguments")


def _emit(report: dict[str, Any], output_format: str) -> None:
    def write(record: dict[str, Any]) -> None:
        print(json.dumps(record, sort_keys=True, separators=(",", ":")))

    if output_format == "json" or not report["enabled"]:
        write({"event": "cyber.cve.report", **report})
        return
    summary = {key: value for key, value in report.items() if key not in {"assets", "findings"}}
    write({"event": "cyber.cve.report", **summary, "asset_count": len(report["assets"]),
           "finding_count": len(report["findings"])})
    for asset in report["assets"]:
        write({"v": 1, "event": "cyber.cve.coverage", **asset})
    for finding in report["findings"]:
        write({"v": 1, "event": "cyber.cve.finding", **finding})


def main(argv: list[str] | None = None) -> int:
    parser = _ArgumentParser(prog="python -m app.cyber_cve", description=__doc__)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--trivy", type=Path, action="append", default=[], help="Trivy schema 2 JSON report")
    parser.add_argument("--kev", type=Path, help="Local CISA KEV JSON")
    parser.add_argument("--format", choices=("json", "jsonl"), default="json")
    parser.add_argument("--fail-on", choices=_SEVERITIES[1:], help="Exit 1 on a finding at this priority or above")
    try:
        args = parser.parse_args(argv)
        report = correlate(args.inventory, args.trivy, args.kev)
        _emit(report, args.format)
    except CyberCveError as exc:
        print(json.dumps({"event": "cyber.cve.error", "code": str(exc)}), file=sys.stderr)
        return 2
    if args.fail_on and any(_SEVERITY_RANK[row["priority"]] >= _SEVERITY_RANK[args.fail_on]
                            for row in report.get("findings", [])):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
