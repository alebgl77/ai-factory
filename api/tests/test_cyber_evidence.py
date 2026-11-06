from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from ai_factory_workbench.cyberlog import append_event
from ai_factory_workbench.store import Store
from app import audit, cyber


def _key(path: Path, value: bytes = b"test-evidence-key-for-fixtures-only") -> Path:
    path.write_bytes(value)
    path.chmod(0o600)
    return path


def _records(count: int) -> list[dict]:
    return [{
        "v": 1, "ts": "2026-11-06T08:00:00.000Z", "actor": "operator@example.invalid",
        "role": "approver", "action": "registry.assessment", "target": "private asset\nspoofed event",
        "outcome": "denied" if index == 1 else "success", "request_id": "private request identifier",
        "client_ip": "192.0.2.17", "details": {"prompt": "hidden prompt", "token": "hidden fixture credential"},
    } for index in range(count)]


def _write_log(path: Path, records: list[dict]) -> None:
    previous = audit.GENESIS
    lines = []
    for seq, record in enumerate(records, start=1):
        record.update(seq=seq, prev_hash=previous)
        previous = record["hash"] = audit.record_hash(record)
        lines.append(audit.canonical(record) + b"\n")
    path.write_bytes(b"".join(lines))


def _call(command, source, key, checkpoint, *extra, kind="platform", stream="fixture"):
    args = [command, "--kind", kind, "--path", str(source), "--stream", stream, "--key-file", str(key)]
    if command == "seal":
        args += ["--key-id", "v1", "--output", str(checkpoint)]
    else:
        args += ["--checkpoint", str(checkpoint)]
    return cyber.main([*args, *map(str, extra)])


@pytest.fixture
def sealed(tmp_path, capsys):
    source = tmp_path / "audit.jsonl"
    key = _key(tmp_path / "evidence.key")
    checkpoint = tmp_path / "anchor.json"
    _write_log(source, _records(2))
    assert _call("seal", source, key, checkpoint) == 0
    assert json.loads(capsys.readouterr().out)["seq"] == 2
    return source, key, checkpoint


def test_export_only_authenticated_prefix_and_pseudonymizes(sealed, capsys):
    source, key, checkpoint = sealed
    _write_log(source, _records(3))
    assert _call("verify", source, key, checkpoint) == 0
    result = json.loads(capsys.readouterr().out)
    assert (result["sealed_through"], result["head_seq"], result["unsigned_tail"]) == (2, 3, 1)

    assert _call("export", source, key, checkpoint) == 0
    output = capsys.readouterr().out
    rows = [json.loads(line) for line in output.splitlines()]
    assert [row["event_id"] for row in rows] == ["fixture:1", "fixture:2"]
    assert rows[0]["actor_ref"] == rows[1]["actor_ref"]
    assert len(rows[0]["target_ref"]) == len(rows[0]["request_ref"]) == 64
    assert not any(secret in output for secret in (
        "operator@example.invalid", "private asset", "spoofed event", "private request identifier",
        "192.0.2.17", "hidden prompt", "hidden fixture credential",
    ))
    assert _call("export", source, key, checkpoint, "--after", 1, "--limit", 1) == 0
    assert json.loads(capsys.readouterr().out) == rows[1]


@pytest.mark.parametrize("attack", ["truncate", "recompute", "edit", "malformed_tail", "duplicate_field"])
def test_verified_export_fails_closed_on_tampering(sealed, capsys, attack):
    source, key, checkpoint = sealed
    if attack == "truncate":
        _write_log(source, _records(1))
    elif attack == "recompute":
        forged = _records(2)
        forged[0]["actor"] = "changed@example.invalid"
        _write_log(source, forged)
        assert audit.scan(source.read_bytes()).ok  # a plain hash chain accepts a full rewrite
    elif attack == "edit":
        source.write_bytes(source.read_bytes().replace(b"operator@example.invalid", b"attacker@example.invalid", 1))
    elif attack == "duplicate_field":
        source.write_bytes(source.read_bytes().replace(b'"actor":', b'"actor":"hidden duplicate", "actor":', 1))
    else:
        with source.open("ab") as tail:
            tail.write(b'{"unsealed":"private tail"}')

    assert _call("export", source, key, checkpoint) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert json.loads(captured.err)["severity"] == "critical"
    assert "private" not in captured.err


def test_checkpoint_rotation_scope_and_rollback(sealed, tmp_path, capsys):
    source, key, checkpoint = sealed
    new_key = _key(tmp_path / "new.key", b"second-key-used-by-rotation-fixture-only")
    rotated = tmp_path / "rotated.json"
    _write_log(source, _records(3))
    assert _call("seal", source, new_key, rotated, "--key-id", "v2", "--previous", checkpoint,
                 "--previous-key-file", key) == 0
    capsys.readouterr()
    assert json.loads(rotated.read_text())["previous_mac"] == json.loads(checkpoint.read_text())["mac"]
    assert _call("verify", source, new_key, rotated) == 0
    assert json.loads(capsys.readouterr().out)["key_id"] == "v2"
    assert _call("verify", source, key, rotated) == 1
    assert json.loads(capsys.readouterr().err)["code"] == "checkpoint_authentication_failed"
    assert _call("verify", source, new_key, rotated, stream="different") == 1
    assert json.loads(capsys.readouterr().err)["code"] == "checkpoint_stream_mismatch"
    assert _call("verify", source, new_key, rotated, kind="workbench") == 1
    assert json.loads(capsys.readouterr().err)["code"] == "checkpoint_stream_mismatch"
    old_contents = rotated.read_bytes()
    assert _call("seal", source, new_key, rotated) == 2
    assert json.loads(capsys.readouterr().err)["code"] == "checkpoint_exists"
    assert rotated.read_bytes() == old_contents

    _write_log(source, _records(1))
    next_checkpoint = tmp_path / "next.json"
    assert _call("seal", source, new_key, next_checkpoint, "--previous", rotated) == 1
    assert json.loads(capsys.readouterr().err)["code"] == "audit_checkpoint_mismatch"
    assert not next_checkpoint.exists()


def test_platform_reader_takes_bounded_append_snapshot(sealed, monkeypatch, capsys):
    source, key, checkpoint = sealed
    old_contents = source.read_bytes()
    newer = _records(3)
    _write_log(source, newer)
    appended = source.read_bytes()[len(old_contents):]
    source.write_bytes(old_contents)
    parse = cyber._json_object
    has_appended = False

    def append_during_read(raw, error):
        nonlocal has_appended
        if error == "audit_invalid_record" and not has_appended:
            with source.open("ab") as target:
                target.write(appended)
            has_appended = True
        return parse(raw, error)

    monkeypatch.setattr(cyber, "_json_object", append_during_read)
    assert _call("verify", source, key, checkpoint) == 0
    assert json.loads(capsys.readouterr().out)["head_seq"] == 2
    assert _call("verify", source, key, checkpoint) == 0
    assert json.loads(capsys.readouterr().out)["head_seq"] == 3


def test_detect_is_paginated_and_policy_exit_is_explicit(sealed, capsys):
    source, key, checkpoint = sealed
    assert _call("detect", source, key, checkpoint, "--limit", 1) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["findings"] == [] and result["more"] and result["scanned_through"] == 1
    assert _call("detect", source, key, checkpoint, "--after", 1, "--fail-on", "medium") == 3
    result = json.loads(capsys.readouterr().out)
    assert result["findings"] == [{"code": "platform_decision_denied", "severity": "medium",
                                   "seq": 2, "evidence_id": "fixture:2", "ts": "2026-11-06T08:00:00.000Z"}]
    assert not result["more"]


def test_workbench_cli_exports_fixed_schema_and_detects_execution_alerts(tmp_path, capsys):
    source = tmp_path / "workbench.sqlite3"
    key = _key(tmp_path / "evidence.key")
    checkpoint = tmp_path / "anchor.json"
    store = Store(source)
    with store.transaction():
        pass
    assert cyber.main(["journal", "--path", str(source), "--enabled", "on"]) == 0
    assert json.loads(capsys.readouterr().out)["changed"]
    with store.transaction() as connection:
        case = {"id": "private case", "source": "private source", "revision": 2, "proposal_hash": "a" * 64}
        append_event(connection, "execution.dispatch_authorized", "success", case=case, actor="private actor")
        append_event(connection, "execution.unknown", "unknown", case=case,
                     actor="private actor", reason_code="erp_timeout")
    assert cyber.main(["journal", "--path", str(source), "--enabled", "off"]) == 0
    assert not json.loads(capsys.readouterr().out)["enabled"]
    assert cyber.main(["journal", "--path", str(source), "--enabled", "off"]) == 0
    assert not json.loads(capsys.readouterr().out)["changed"]
    assert _call("seal", source, key, checkpoint, kind="workbench") == 0
    assert json.loads(capsys.readouterr().out)["seq"] == 4
    assert _call("export", source, key, checkpoint, kind="workbench") == 0
    output = capsys.readouterr().out
    assert "private" not in output
    rows = [json.loads(line) for line in output.splitlines()]
    assert [row["event"] for row in rows] == [
        "journal.enabled", "execution.dispatch_authorized", "execution.unknown", "journal.disabled",
    ]
    assert rows[2]["reason_code"] == "erp_timeout" and len(rows[2]["actor_ref"]) == 64
    assert _call("detect", source, key, checkpoint, "--fail-on", "high", kind="workbench") == 3
    assert [item["code"] for item in json.loads(capsys.readouterr().out)["findings"]] == [
        "execution.unknown", "journal.disabled",
    ]


def test_operator_errors_never_echo_supplied_paths_or_values(sealed, tmp_path, capsys):
    source, key, checkpoint = sealed
    assert cyber.main(["export", "--accidental-credential", "very-secret-value"]) == 2
    assert capsys.readouterr().err == '{"code":"invalid_arguments","status":"error"}\n'
    source.chmod(0o600)
    assert _call("verify", source, source, checkpoint) == 2
    assert json.loads(capsys.readouterr().err)["code"] == "key_file_not_separate"
    assert _call("verify", source, tmp_path / "hidden-directory-name", checkpoint) == 2
    captured = capsys.readouterr()
    assert captured.out == "" and "hidden-directory-name" not in captured.err
    assert _call("export", source, key, checkpoint, "--limit", cyber.MAX_PAGE + 1) == 2
    assert json.loads(capsys.readouterr().err)["code"] == "invalid_page"


@pytest.mark.skipif(os.name != "posix", reason="POSIX permission check")
def test_rejects_group_readable_key(sealed, capsys):
    source, key, checkpoint = sealed
    key.chmod(0o640)
    assert _call("verify", source, key, checkpoint) == 2
    assert json.loads(capsys.readouterr().err)["code"] == "invalid_key_file"


def test_module_entrypoint_is_available():
    result = subprocess.run([sys.executable, "-m", "app.cyber", "--help"], capture_output=True, text=True, check=False)
    assert result.returncode == 0
    assert "seal" in result.stdout and "detect" in result.stdout
