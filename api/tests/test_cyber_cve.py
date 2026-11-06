from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from app import cyber_cve


_PACKAGE = "pkg:pypi/example-model@1.2.3"
_MATCHED = "CVE-2026-1234"
_UNMAPPED = "CVE-2026-98765"


def _write(path: Path, value: object) -> Path:
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _inventory(path: Path, *, enabled: bool = True, packages: list[str] | None = None) -> Path:
    return _write(path, {
        "v": 1,
        "enabled": enabled,
        "assets": [
            {"id": "model-gateway", "kind": "ai", "packages": packages if packages is not None else [_PACKAGE]},
            {"id": "mcp-tool", "kind": "tool", "depends_on": ["model-gateway"]},
            {"id": "crm-review", "kind": "process", "depends_on": ["mcp-tool"]},
            {"id": "external-crm", "kind": "connector"},
            {"id": "runtime", "kind": "runtime", "packages": ["pkg:pypi/example-runtime@1.0"]},
        ],
    })


def _row(cve: str = _MATCHED, *, purl: str = _PACKAGE, version: str = "1.2.3",
         severity: str = "HIGH") -> dict:
    return {
        "VulnerabilityID": cve,
        "PkgName": "example-model",
        "InstalledVersion": version,
        "PkgIdentifier": {"PURL": purl},
        "Severity": severity,
    }


def _scan(path: Path, *rows: dict, packages: list[dict] | None = None) -> Path:
    return _write(path, {
        "SchemaVersion": 2,
        "Results": [{"Vulnerabilities": rows, "Packages": packages, "Target": "/private/workspace"}],
    })


def test_disabled_ignores_scanner_and_kev_files(tmp_path, capsys):
    inventory = _inventory(tmp_path / "inventory.json", enabled=False)
    args = ["--inventory", str(inventory), "--trivy", str(tmp_path / "missing"),
            "--kev", str(tmp_path / "private-kev"), "--fail-on", "low", "--format", "jsonl"]
    assert cyber_cve.main(args) == 0
    output = capsys.readouterr()
    assert json.loads(output.out) == {"v": 1, "enabled": False, "event": "cyber.cve.report"}
    assert output.err == ""


def test_correlates_only_exact_version_and_qualifiers_with_transitive_impact(tmp_path):
    inventory = _inventory(tmp_path / "inventory.json")
    scan = _scan(tmp_path / "scan.json",
                 _row(),
                 _row(_UNMAPPED, purl="pkg:pypi/example-model@2.0", version="2.0"),
                 _row("CVE-2026-98766", purl=_PACKAGE + "?repository=other"),
                 _row("CVE-2026-98767", version="0.0"),
                 _row("GHSA-test"))

    report = cyber_cve.correlate(inventory, [scan])
    findings = {finding["cve"]: finding for finding in report["findings"]}
    assert set(findings) == {_MATCHED, _UNMAPPED, "CVE-2026-98766", "CVE-2026-98767"}
    assert findings[_MATCHED]["direct_assets"] == ["model-gateway"]
    assert findings[_MATCHED]["affected_assets"] == ["crm-review", "mcp-tool", "model-gateway"]
    assert findings[_MATCHED]["mapping"] == "mapped"
    assert findings[_MATCHED]["known_exploited"] is None
    for cve in (_UNMAPPED, "CVE-2026-98766", "CVE-2026-98767"):
        assert findings[cve]["mapping"] == "unmapped"
        assert findings[cve]["affected_assets"] == []
    assert report["ignored_non_cve"] == 1


def test_cisa_enrichment_changes_priority_not_scanner_severity_and_gates(tmp_path, capsys):
    inventory = _inventory(tmp_path / "inventory.json")
    scan = _scan(tmp_path / "scan.json", _row(severity="LOW"), _row(_UNMAPPED))
    kev = _write(tmp_path / "kev.json", {
        "count": 1,
        "vulnerabilities": [{"cveID": _MATCHED, "shortDescription": "secret or hostile payload"}],
    })
    args = ["--inventory", str(inventory), "--trivy", str(scan), "--kev", str(kev),
            "--fail-on", "critical", "--format", "jsonl"]
    assert cyber_cve.main(args) == 1
    output = capsys.readouterr()
    records = [json.loads(line) for line in output.out.splitlines()]
    assert records[0]["finding_count"] == 2
    assert records[0]["event"] == "cyber.cve.report"
    findings = [record for record in records if record["event"] == "cyber.cve.finding"]
    assert findings[0]["cve"] == _MATCHED
    assert findings[0]["severity"] == "low"
    assert findings[0]["priority"] == "critical"
    assert findings[0]["known_exploited"] is True
    assert findings[1]["known_exploited"] is False
    assert "hostile payload" not in output.out
    assert output.err == ""


def test_no_package_evidence_is_not_clean_and_package_presence_is_bounded(tmp_path):
    inventory = _inventory(tmp_path / "inventory.json", packages=[_PACKAGE, "pkg:pypi/another@1"])
    scan = _scan(tmp_path / "scan.json", packages=[{"Identifier": {"PURL": _PACKAGE}}])

    report = cyber_cve.correlate(inventory, [scan])
    coverage = {asset["id"]: asset["coverage"] for asset in report["assets"]}
    assert report["findings"] == []
    assert coverage == {
        "model-gateway": "partial",
        "mcp-tool": "dependency_only",
        "crm-review": "dependency_only",
        "external-crm": "not_versioned",
        "runtime": "unverified",
    }
    empty_report = _write(tmp_path / "empty.json", {"SchemaVersion": 2})
    coverage = {asset["id"]: asset["coverage"]
                for asset in cyber_cve.correlate(inventory, [empty_report])["assets"]}
    assert coverage["model-gateway"] == "unverified"


def test_deduplicates_versioned_findings_and_preserves_evidence_in_stable_order(tmp_path):
    inventory = _inventory(tmp_path / "inventory.json")
    first = _scan(tmp_path / "first.json", _row(severity="LOW"), _row(severity="MEDIUM"))
    second = _scan(tmp_path / "second.json", _row(severity="HIGH"))
    report = cyber_cve.correlate(inventory, [first, second, first])
    assert cyber_cve.correlate(inventory, [second, first]) == report
    assert len(report["findings"]) == 1
    finding = report["findings"][0]
    assert finding["severity"] == "high"
    refs = {hashlib.sha256(path.read_bytes()).hexdigest() for path in (first, second)}
    assert {evidence["report_ref"] for evidence in finding["evidence"]} == refs
    assert all(evidence["result_index"] == evidence["row_index"] == 0 for evidence in finding["evidence"])


def test_report_strings_and_paths_do_not_leak_to_siem(tmp_path, capsys):
    secret = "THIS-IS-A-PRIVATE-CREDENTIAL"
    inventory = _inventory(tmp_path / "inventory.json")
    scan = _write(tmp_path / f"{secret}.json", {
        "SchemaVersion": 2,
        "Results": [{
            "Target": f"/private/{secret}",
            "Secrets": [{"Match": secret}],
            "Vulnerabilities": [
                {**_row(purl=f"{_PACKAGE}?repository={secret}", severity=f"HIGH\n{secret}"),
                 "Description": f"\r\n{secret}", "PrimaryURL": f"https://example.com/{secret}",
                 "FixedVersion": f"upgrade {secret}", "PkgName": secret},
                _row(f"CVE-2026-5555\r\n{secret}"),
            ],
        }],
    })
    assert cyber_cve.main(["--inventory", str(inventory), "--trivy", str(scan), "--format", "jsonl"]) == 0
    output = capsys.readouterr()
    assert output.err == ""
    assert secret not in output.out
    assert len(output.out.splitlines()) == 7
    assert json.loads(output.out.splitlines()[-1])["severity"] == "unknown"


@pytest.mark.parametrize(("assets", "code"), [
    ([{"id": "a", "kind": "tool"}, {"id": "a", "kind": "tool"}], "inventory_asset_invalid"),
    ([{"id": "a\nSECRET", "kind": "tool"}], "inventory_asset_invalid"),
    ([{"id": "a", "kind": "bad"}], "inventory_asset_invalid"),
    ([{"id": "a", "kind": "tool", "packages": ["pkg:pypi/a"]}], "inventory_package_invalid"),
    ([{"id": "a", "kind": "tool", "packages": ["pkg:pypi/a@1%0a"]}], "inventory_package_invalid"),
    ([{"id": "a", "kind": "tool", "depends_on": ["unknown"]}], "inventory_dependency_unknown"),
    ([{"id": "a", "kind": "tool", "depends_on": ["b"]},
      {"id": "b", "kind": "process", "depends_on": ["a"]}], "inventory_dependency_cycle"),
])
def test_inventory_rejects_unsafe_or_ambiguous_graph(tmp_path, assets, code):
    inventory = _write(tmp_path / "inventory.json", {"v": 1, "enabled": True, "assets": assets})
    with pytest.raises(cyber_cve.CyberCveError, match=f"^{code}$"):
        cyber_cve.correlate(inventory, [])


@pytest.mark.parametrize(("content", "code"), [
    ('{"SchemaVersion": 2, "SchemaVersion": 2}', "trivy_invalid"),
    ('{"SchemaVersion": 2, "Results": NaN}', "trivy_invalid"),
    ('{"SchemaVersion": true}', "trivy_schema_unsupported"),
    ('{"SchemaVersion": 3}', "trivy_schema_unsupported"),
    ('{"SchemaVersion": 2, "Results": [7]}', "trivy_invalid"),
    ('{"SchemaVersion": 2, "Results": [{"Packages": "secret"}]}', "trivy_invalid"),
    ('{"SchemaVersion": 2, "Results": [{"Vulnerabilities": [7]}]}', "trivy_invalid"),
    ("[" * 1600 + "]" * 1600, "trivy_invalid"),
])
def test_invalid_report_fails_closed_with_redacted_error(tmp_path, capsys, content, code):
    inventory = _inventory(tmp_path / "inventory.json")
    scan = tmp_path / "private-path.json"
    scan.write_text(content, encoding="utf-8")
    assert cyber_cve.main(["--inventory", str(inventory), "--trivy", str(scan)]) == 2
    output = capsys.readouterr()
    assert output.out == ""
    assert json.loads(output.err) == {"event": "cyber.cve.error", "code": code}


def test_rejects_oversized_reports_and_scan_entry_counts(tmp_path, monkeypatch):
    inventory = _inventory(tmp_path / "inventory.json")
    scan = _scan(tmp_path / "scan.json", _row())
    with monkeypatch.context() as context:
        context.setattr(cyber_cve, "_MAX_TRIVY_BYTES", 12)
        with pytest.raises(cyber_cve.CyberCveError, match="^trivy_too_large$"):
            cyber_cve.correlate(inventory, [scan])
    with monkeypatch.context() as context:
        context.setattr(cyber_cve, "_MAX_SCAN_ENTRIES", 1)
        report = _scan(tmp_path / "many.json", _row(), packages=[{}])
        with pytest.raises(cyber_cve.CyberCveError, match="^too_many_scan_entries$"):
            cyber_cve.correlate(inventory, [report])


def test_malformed_kev_fails_closed(tmp_path):
    inventory = _inventory(tmp_path / "inventory.json")
    scan = _scan(tmp_path / "scan.json", _row())
    kev = _write(tmp_path / "kev.json", {"count": 1, "vulnerabilities": [{"cveID": "not a cve"}]})
    with pytest.raises(cyber_cve.CyberCveError, match="^kev_invalid$"):
        cyber_cve.correlate(inventory, [scan], kev)


def test_bad_cli_arguments_never_echo_user_input(capsys):
    assert cyber_cve.main(["--secret", "hidden"]) == 2
    assert json.loads(capsys.readouterr().err)["code"] == "invalid_arguments"
