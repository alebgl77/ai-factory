"""Operator-only verification, export and triage of local audit evidence."""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import hashlib
import hmac
import json
import os
import re
import stat
import sys
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, BinaryIO

from . import audit


MAX_LINE = 64 * 1024
MAX_KEY = 4096
MAX_CHECKPOINT = 2048
MAX_PAGE = 1000
MAX_SEQ = 2**63 - 1
LABEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z")
HASH = re.compile(r"[0-9a-f]{64}\Z")
TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|\+00:00)\Z", re.ASCII)
KINDS = ("platform", "workbench")
SEVERITIES = ("info", "low", "medium", "high", "critical")
CHECKPOINT_FIELDS = frozenset({"v", "kind", "stream", "key_id", "seq", "head_hash", "sealed_at", "previous_mac", "mac"})
WORKBENCH_EVENTS = frozenset({
    "journal.enabled", "journal.disabled", "case.proposed", "case.revised", "case.withdrawn", "case.blocked",
    "decision.recorded", "execution.claimed", "execution.dispatch_authorized", "execution.completed",
    "execution.hold", "execution.unknown",
    "crm.checkpoint",
})
WORKBENCH_OUTCOMES = frozenset({"success", "blocked", "unknown", "approved", "rejected", "changes_requested"})
REASON_CODES = frozenset({
    "crm_conflict", "crm_invalid", "crm_incomplete", "crm_reconciliation", "erp_readback_conflict", "erp_timeout",
})
CHANNELS = frozenset({"direct", "slack", "teams", "service"})


class EvidenceError(Exception):
    """A fixed error code suitable for operator output."""

    def __init__(self, code: str, *, integrity: bool = False) -> None:
        super().__init__(code)
        self.code = code
        self.integrity = integrity


class OperatorParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        # argparse's default error echoes arbitrary argument values.
        raise EvidenceError("invalid_arguments")


def _label(value: object) -> bool:
    return isinstance(value, str) and LABEL.fullmatch(value) is not None


def _hash(value: object) -> bool:
    return isinstance(value, str) and HASH.fullmatch(value) is not None


def _timestamp(value: object) -> bool:
    if not isinstance(value, str) or TIMESTAMP.fullmatch(value) is None:
        return False
    try:
        dt.datetime.fromisoformat(value)
    except ValueError:
        return False
    return True


def _unique_fields(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate field")
        result[key] = value
    return result


def _reject_constant(_value: str) -> None:
    raise ValueError("invalid JSON number")


def _json_object(raw: bytes, error: str) -> dict[str, Any]:
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_fields, parse_constant=_reject_constant)
        if isinstance(value, dict):
            return value
    except (ValueError, RecursionError):
        pass
    raise EvidenceError(error, integrity=True)


@contextlib.contextmanager
def _regular_input(path: Path, *, error: str, private: bool = False) -> Iterator[BinaryIO]:
    try:
        if not stat.S_ISREG(path.lstat().st_mode):
            raise EvidenceError(error)
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0)
        fd = os.open(path, flags)
        with os.fdopen(fd, "rb") as source:
            mode = os.fstat(source.fileno()).st_mode
            if not stat.S_ISREG(mode) or (private and os.name == "posix" and mode & 0o077):
                raise EvidenceError(error)
            yield source
    except OSError:
        raise EvidenceError(error) from None


def _read_key(path: Path, *public_paths: Path | None) -> bytes:
    with _regular_input(path, error="invalid_key_file", private=True) as source:
        key_info = os.fstat(source.fileno())
        for public in public_paths:
            if public is None:
                continue
            try:
                if os.path.samestat(key_info, public.stat()):
                    raise EvidenceError("key_file_not_separate")
            except FileNotFoundError:
                pass
        key = source.read(MAX_KEY + 1)
    if not 32 <= len(key) <= MAX_KEY:
        raise EvidenceError("invalid_key_file")
    return key


def _mac(key: bytes, domain: bytes, data: Any) -> str:
    return hmac.digest(key, domain + b"\0" + audit.canonical(data), hashlib.sha256).hex()


def _read_checkpoint(path: Path, key: bytes, kind: str, stream: str) -> dict[str, Any]:
    with _regular_input(path, error="checkpoint_read_failed") as source:
        raw = source.read(MAX_CHECKPOINT + 1)
    if len(raw) > MAX_CHECKPOINT:
        raise EvidenceError("invalid_checkpoint", integrity=True)
    checkpoint = _json_object(raw, "invalid_checkpoint")
    if (checkpoint.keys() != CHECKPOINT_FIELDS or type(checkpoint["v"]) is not int or checkpoint["v"] != 1
            or checkpoint["kind"] not in KINDS or not _label(checkpoint["stream"]) or not _label(checkpoint["key_id"])
            or type(checkpoint["seq"]) is not int or not 0 <= checkpoint["seq"] <= MAX_SEQ
            or not _hash(checkpoint["head_hash"]) or not _hash(checkpoint["mac"])
            or not _timestamp(checkpoint["sealed_at"])
            or (checkpoint["previous_mac"] is not None and not _hash(checkpoint["previous_mac"]))):
        raise EvidenceError("invalid_checkpoint", integrity=True)
    body = {name: value for name, value in checkpoint.items() if name != "mac"}
    if not hmac.compare_digest(checkpoint["mac"], _mac(key, b"ai-factory/cyber/checkpoint/v1", body)):
        raise EvidenceError("checkpoint_authentication_failed", integrity=True)
    if checkpoint["kind"] != kind or checkpoint["stream"] != stream:
        raise EvidenceError("checkpoint_stream_mismatch", integrity=True)
    return checkpoint


def _write_checkpoint(path: Path, checkpoint: dict[str, Any]) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    try:
        fd = os.open(path, flags, 0o600)
        with os.fdopen(fd, "wb") as target:
            target.write(audit.canonical(checkpoint) + b"\n")
            target.flush()
            os.fsync(target.fileno())
    except FileExistsError:
        raise EvidenceError("checkpoint_exists") from None
    except OSError:
        raise EvidenceError("checkpoint_write_failed") from None


def _platform_events(path: Path) -> Iterator[dict[str, Any]]:
    with _regular_input(path, error="audit_read_failed") as source:
        remaining = os.fstat(source.fileno()).st_size
        previous = audit.GENESIS
        seq = 0
        while remaining:
            raw = source.readline(min(MAX_LINE + 1, remaining))
            if not raw or len(raw) > MAX_LINE or not raw.endswith(b"\n"):
                raise EvidenceError("audit_invalid_record_boundary", integrity=True)
            remaining -= len(raw)
            seq += 1
            record = _json_object(raw, "audit_invalid_record")
            if (not all(name in record for name in audit.REQUIRED_FIELDS)
                    or type(record["v"]) is not int or record["v"] != audit.RECORD_VERSION
                    or type(record["seq"]) is not int or record["seq"] != seq
                    or not _timestamp(record["ts"]) or record["prev_hash"] != previous or not _hash(record["hash"])
                    or any(not isinstance(record[name], str) for name in ("actor", "role", "action", "target", "outcome"))):
                raise EvidenceError("audit_invalid_chain", integrity=True)
            try:
                matches = hmac.compare_digest(record["hash"], audit.record_hash(record))
            except (ValueError, RecursionError):
                matches = False
            if not matches:
                raise EvidenceError("audit_invalid_chain", integrity=True)
            previous = record["hash"]
            yield record


def _source_events(kind: str, path: Path) -> Iterator[dict[str, Any]]:
    if kind == "platform":
        yield from _platform_events(path)
    else:
        from ai_factory_workbench.cyberlog import JournalError, iter_verified_events

        try:
            yield from iter_verified_events(path)
        except JournalError:
            raise EvidenceError("workbench_journal_invalid", integrity=True) from None


def _ref(key: bytes, kind: str, stream: str, field_name: str, value: object) -> str | None:
    if not isinstance(value, str) or not 0 < len(value) <= 2048:
        return None
    try:
        return _mac(key, b"ai-factory/cyber/export-ref/v1", [kind, stream, field_name, value])
    except UnicodeError:
        return None


def _project(record: dict[str, Any], kind: str, stream: str, key: bytes) -> dict[str, Any]:
    seq = record["seq"]
    row = {"v": 1, "kind": kind, "stream": stream, "seq": seq, "event_id": f"{stream}:{seq}",
           "ts": record["ts"], "hash": record["hash"]}
    if kind == "platform":
        row["event"] = record["action"] if record["action"] in audit.ACTIONS else "platform.audit"
        row["outcome"] = record["outcome"] if record["outcome"] in audit.OUTCOMES else "other"
        pairs = (("actor_ref", "actor"), ("target_ref", "target"), ("request_ref", "request_id"))
    else:
        row["event"] = record["event"] if record["event"] in WORKBENCH_EVENTS else "workbench.audit"
        row["outcome"] = record["outcome"] if record["outcome"] in WORKBENCH_OUTCOMES else "other"
        pairs = (("actor_ref", "actor_ref"), ("case_ref", "case_ref"), ("source_ref", "source_ref"),
                 ("request_ref", "request_id"))
        for name, allowed in (("channel", CHANNELS), ("reason_code", REASON_CODES)):
            value = record.get(name)
            if isinstance(value, str) and value in allowed:
                row[name] = value
        if _hash(record.get("proposal_hash")):
            row["proposal_hash"] = record["proposal_hash"]
        if type(record.get("revision")) is int and 1 <= record["revision"] <= MAX_SEQ:
            row["revision"] = record["revision"]
    for exported, original in pairs:
        value = _ref(key, kind, stream, exported, record.get(original))
        if value is not None:
            row[exported] = value
    return row


@dataclass
class Snapshot:
    seq: int = 0
    head_hash: str = audit.GENESIS
    selected: list[dict[str, Any]] = field(default_factory=list)


def _scan_snapshot(args: argparse.Namespace, key: bytes, anchor: dict[str, Any] | None,
                   *, select: bool = False) -> Snapshot:
    snapshot = Snapshot()
    anchor_seq = anchor["seq"] if anchor else 0
    anchored_hash = audit.GENESIS if anchor_seq == 0 else None
    with contextlib.closing(_source_events(args.kind, args.path)) as events:
        for record in events:
            snapshot.seq = record["seq"]
            snapshot.head_hash = record["hash"]
            if snapshot.seq == anchor_seq:
                anchored_hash = snapshot.head_hash
            if select and args.after < snapshot.seq <= anchor_seq and len(snapshot.selected) < args.limit:
                snapshot.selected.append(_project(record, args.kind, args.stream, key))
    if anchor and (anchored_hash is None or not hmac.compare_digest(anchor["head_hash"], anchored_hash)):
        raise EvidenceError("audit_checkpoint_mismatch", integrity=True)
    return snapshot


def _seal(args: argparse.Namespace, key: bytes) -> int:
    if not _label(args.key_id) or (args.previous_key_file and not args.previous):
        raise EvidenceError("invalid_arguments")
    previous = None
    if args.previous:
        previous_key = (_read_key(args.previous_key_file, args.path, args.previous, args.output)
                        if args.previous_key_file else key)
        previous = _read_checkpoint(args.previous, previous_key, args.kind, args.stream)
    snapshot = _scan_snapshot(args, key, previous)
    checkpoint = {"v": 1, "kind": args.kind, "stream": args.stream, "key_id": args.key_id,
                  "seq": snapshot.seq, "head_hash": snapshot.head_hash,
                  "sealed_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
                  "previous_mac": previous["mac"] if previous else None}
    checkpoint["mac"] = _mac(key, b"ai-factory/cyber/checkpoint/v1", checkpoint)
    _write_checkpoint(args.output, checkpoint)
    _print_json({"status": "sealed", "seq": snapshot.seq, "head_hash": snapshot.head_hash, "key_id": args.key_id})
    return 0


def _finding(row: dict[str, Any]) -> dict[str, Any] | None:
    event = row["event"]
    if event in {"execution.unknown", "execution.hold", "journal.disabled"}:
        code, severity = event, "high"
    elif event == "case.blocked" and row.get("reason_code") == "crm_conflict":
        code, severity = "crm_conflict", "medium"
    elif row["kind"] == "platform" and row["outcome"] == "denied":
        code, severity = "platform_decision_denied", "medium"
    elif event == "decision.recorded" and row.get("channel") == "service" and row["outcome"] == "approved":
        code, severity = "service_approval_recorded", "info"
    else:
        return None
    return {"code": code, "severity": severity, "evidence_id": row["event_id"], "seq": row["seq"], "ts": row["ts"]}


def _print_json(row: dict[str, Any], *, error: bool = False) -> None:
    print(json.dumps(row, ensure_ascii=True, separators=(",", ":"), sort_keys=True),
          file=sys.stderr if error else sys.stdout)


def _report(args: argparse.Namespace, key: bytes) -> int:
    anchor = _read_checkpoint(args.checkpoint, key, args.kind, args.stream)
    if args.command == "verify":
        snapshot = _scan_snapshot(args, key, anchor)
        _print_json({"status": "verified", "stream": args.stream, "kind": args.kind, "key_id": anchor["key_id"],
                     "sealed_through": anchor["seq"], "head_seq": snapshot.seq, "head_hash": snapshot.head_hash,
                     "unsigned_tail": snapshot.seq - anchor["seq"]})
        return 0
    if not 0 <= args.after <= MAX_SEQ or not 1 <= args.limit <= MAX_PAGE:
        raise EvidenceError("invalid_page")
    snapshot = _scan_snapshot(args, key, anchor, select=True)
    if args.command == "export":
        for row in snapshot.selected:
            _print_json(row)
        return 0
    findings = [finding for row in snapshot.selected if (finding := _finding(row)) is not None]
    scanned_through = snapshot.selected[-1]["seq"] if snapshot.selected else min(args.after, anchor["seq"])
    _print_json({"v": 1, "stream": args.stream, "kind": args.kind, "sealed_through": anchor["seq"],
                 "scanned_through": scanned_through, "more": scanned_through < anchor["seq"], "findings": findings})
    if args.fail_on != "none" and any(SEVERITIES.index(finding["severity"]) >= SEVERITIES.index(args.fail_on)
                                     for finding in findings):
        return 3
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = OperatorParser(description=__doc__, allow_abbrev=False)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("seal", "verify", "export", "detect"):
        command = commands.add_parser(name, allow_abbrev=False)
        command.add_argument("--kind", choices=KINDS, required=True)
        command.add_argument("--path", type=Path, required=True)
        command.add_argument("--stream", required=True)
        command.add_argument("--key-file", type=Path, required=True)
        if name == "seal":
            command.add_argument("--key-id", required=True)
            command.add_argument("--output", type=Path, required=True)
            command.add_argument("--previous", type=Path)
            command.add_argument("--previous-key-file", type=Path)
        else:
            command.add_argument("--checkpoint", type=Path, required=True)
        if name in {"export", "detect"}:
            command.add_argument("--after", type=int, default=0)
            command.add_argument("--limit", type=int, default=MAX_PAGE)
        if name == "detect":
            command.add_argument("--fail-on", choices=("none", *SEVERITIES), default="none")
    journal = commands.add_parser("journal", allow_abbrev=False)
    journal.add_argument("--path", type=Path, required=True)
    journal.add_argument("--enabled", choices=("on", "off"), required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        args = _parser().parse_args(argv)
        if args.command == "journal":
            from ai_factory_workbench.cyberlog import JournalError, set_journal_enabled

            enabled = args.enabled == "on"
            try:
                changed = set_journal_enabled(args.path, enabled)
            except JournalError:
                raise EvidenceError("journal_configuration_failed") from None
            _print_json({"status": "configured", "enabled": enabled, "changed": changed})
            return 0
        if not _label(args.stream):
            raise EvidenceError("invalid_stream")
        key = _read_key(args.key_file, args.path, getattr(args, "checkpoint", None),
                        getattr(args, "previous", None), getattr(args, "output", None))
        return _seal(args, key) if args.command == "seal" else _report(args, key)
    except EvidenceError as exc:
        event = {"status": "error", "code": exc.code}
        if exc.integrity:
            event["severity"] = "critical"
        _print_json(event, error=True)
        return 1 if exc.integrity else 2


if __name__ == "__main__":  # pragma: no cover - CLI smoke tests exercise the entry point
    with contextlib.suppress(BrokenPipeError):
        sys.exit(main())
