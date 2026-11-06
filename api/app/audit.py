"""Tamper-evident audit log of every write (MIT): ``FACTORY_STATE_DIR/audit.jsonl``.

One JSON object per line, chained with SHA-256:

- ``hash`` = SHA-256 (hex) of the canonical JSON of the record without its ``hash`` field
  (keys sorted, no whitespace, UTF-8: :func:`canonical`);
- ``prev_hash`` = ``hash`` of the previous line (``GENESIS``, 64 zeros, for the first one);
- ``seq`` = 1, 2, 3... (line number).

Editing, deleting, inserting or reordering a line breaks the chain at that point, and
:func:`scan` reports the first broken link. The state documents are also reconciled with the
log (:meth:`AuditLog.reconcile`): the last ``after_hash`` recorded for a target must equal the
digest of what is stored, so a change made outside the API shows up as ``drift``.

A hash chain alone cannot stop someone who rewrites the whole file: every appended record is
therefore also emitted as a JSON log line (``factory.audit``, shipped to Loki) and the head hash is
published in ``/api/v1/health``, ``/metrics`` and ``meta.security`` of the snapshot, so copies kept
outside the state volume witness the history.

Records hold ids, versions, decisions, hashes, the actor and the client IP; never a secret,
a request body or a free-text field.

Offline verification for auditors (standard library only)::

    python -m app.audit verify /path/to/audit.jsonl
"""

from __future__ import annotations

import contextlib
import datetime as dt
import hashlib
import json
import logging
import os
import sys
import threading
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import seclog

AUDIT_FILE = "audit.jsonl"
GENESIS = "0" * 64
RECORD_VERSION = 1
REQUIRED_FIELDS = ("v", "seq", "ts", "actor", "role", "action", "target", "outcome", "prev_hash", "hash")
ACTIONS = ("registry.model_version", "registry.assessment", "spaces.put", "placement.decide")
OUTCOMES = ("success", "denied")


def canonical(obj: Any) -> bytes:
    """Canonical JSON bytes: sorted keys, no whitespace, UTF-8 (the hashed representation)."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def digest(obj: Any) -> str | None:
    """SHA-256 of the canonical JSON of ``obj`` (``None`` for ``None``: nothing stored)."""
    if obj is None:
        return None
    return hashlib.sha256(canonical(obj)).hexdigest()


def record_hash(record: dict[str, Any]) -> str:
    body = {k: v for k, v in record.items() if k != "hash"}
    return hashlib.sha256(canonical(body)).hexdigest()


@dataclass(frozen=True)
class AuditContext:
    """Who did it, from where, under which request (built from the authenticated principal)."""

    actor: str
    role: str
    auth_mode: str
    request_id: str
    client_ip: str
    ids: frozenset[str] = frozenset()   # identity keys (user id, lower-cased e-mail) for four-eyes
    authenticated: bool = False


@dataclass
class ChainStatus:
    ok: bool = True
    records: int = 0
    last_seq: int = 0
    head_hash: str = GENESIS
    first_broken: dict[str, Any] | None = None
    counts: dict[str, int] = field(default_factory=dict)
    last_after: dict[str, str | None] = field(default_factory=dict)  # target -> last after_hash
    ends_with_newline: bool = True

    def public(self) -> dict[str, Any]:
        return {"chain_ok": self.ok, "records": self.records, "head_hash": self.head_hash,
                "first_broken": self.first_broken}


def scan(data: bytes) -> ChainStatus:
    """Verify a whole audit file (bytes). Pure function; reports the first broken link."""
    st = ChainStatus(ends_with_newline=(not data) or data.endswith(b"\n"))
    if not data:
        return st
    lines = data.split(b"\n")
    if st.ends_with_newline:
        lines = lines[:-1]
    counts: Counter[str] = Counter()
    prev = GENESIS
    expected_seq = 1

    def broken(line: int, seq: Any, reason: str) -> None:
        if st.first_broken is None:
            st.ok = False
            st.first_broken = {"line": line, "seq": seq if isinstance(seq, int) else None, "reason": reason}

    for number, raw in enumerate(lines, start=1):
        st.records += 1
        truncated = number == len(lines) and not st.ends_with_newline
        try:
            rec = json.loads(raw.decode("utf-8"))
            if not isinstance(rec, dict):
                raise ValueError("not an object")
        except (UnicodeDecodeError, ValueError):
            broken(number, None, "truncated" if truncated else "unparseable")
            prev = hashlib.sha256(raw).hexdigest()  # keep a deterministic link for later records
            expected_seq += 1
            continue
        seq = rec.get("seq")
        if truncated:
            broken(number, seq, "truncated")
        elif any(k not in rec for k in REQUIRED_FIELDS) or not isinstance(seq, int) \
                or not isinstance(rec.get("hash"), str) or not isinstance(rec.get("prev_hash"), str):
            broken(number, seq, "malformed")
        elif seq != expected_seq:
            broken(number, seq, "sequence_mismatch")
        elif rec["prev_hash"] != prev:
            broken(number, seq, "prev_hash_mismatch")
        elif record_hash(rec) != rec["hash"]:
            broken(number, seq, "hash_mismatch")
        stored = rec.get("hash")
        prev = stored if isinstance(stored, str) else record_hash(rec)
        expected_seq = (seq if isinstance(seq, int) else expected_seq) + 1
        st.last_seq = max(st.last_seq, seq if isinstance(seq, int) else st.last_seq + 1)
        if isinstance(rec.get("action"), str):
            counts[rec["action"]] += 1
        if isinstance(rec.get("target"), str) and rec.get("outcome", "success") == "success":
            st.last_after[rec["target"]] = rec.get("after_hash")
    st.last_seq = max(st.last_seq, st.records)
    st.head_hash = prev
    st.counts = dict(sorted(counts.items()))
    return st


class AuditLog:
    """Append-only, hash-chained JSON lines file. ``append`` must run under the state lock."""

    def __init__(self, directory: Path) -> None:
        self.path = Path(directory) / AUDIT_FILE
        self._mutex = threading.Lock()
        self._cache: tuple[tuple[int, int, int] | None, ChainStatus] | None = None

    # ------------------------------------------------------------------ reading
    def _stat_key(self) -> tuple[int, int, int] | None:
        try:
            st = os.stat(self.path)
        except FileNotFoundError:
            return None
        return (st.st_ino, st.st_size, st.st_mtime_ns)

    def _read_bytes(self) -> bytes:
        try:
            return self.path.read_bytes()
        except FileNotFoundError:
            return b""

    def verify(self) -> ChainStatus:
        """Full recomputation of the chain (never cached); refreshes the status cache."""
        key = self._stat_key()
        status = scan(self._read_bytes()) if key is not None else ChainStatus()
        with self._mutex:
            self._cache = (key, status)
        return status

    def status(self) -> ChainStatus:
        """Chain status, recomputed only when the file changed (inode, size or mtime)."""
        key = self._stat_key()
        with self._mutex:
            cached = self._cache
        if cached is not None and cached[0] == key:
            return cached[1]
        return self.verify()

    def records(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for number, raw in enumerate(self._read_bytes().split(b"\n"), start=1):
            if not raw:
                continue
            try:
                rec = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, ValueError):
                continue
            if isinstance(rec, dict):
                rec.setdefault("line", number)
                out.append(rec)
        return out

    # ------------------------------------------------------------------ writing
    def append(self, ctx: AuditContext, *, action: str, target: str, before: Any = None, after: Any = None,
               details: dict[str, Any] | None = None, outcome: str = "success",
               now: dt.datetime | None = None) -> dict[str, Any]:
        """Append one record (caller holds the state lock). Raises ``OSError`` on failure."""
        status = self.status()
        moment = (now or dt.datetime.now(dt.timezone.utc)).astimezone(dt.timezone.utc)
        record: dict[str, Any] = {
            "v": RECORD_VERSION,
            "seq": status.last_seq + 1,
            "ts": moment.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "actor": ctx.actor,
            "role": ctx.role,
            "auth_mode": ctx.auth_mode,
            "action": action,
            "target": target,
            "outcome": outcome,
            "before_hash": digest(before),
            "after_hash": digest(after),
            "request_id": ctx.request_id,
            "client_ip": ctx.client_ip,
            "details": details or {},
            "prev_hash": status.head_hash,
        }
        record["hash"] = record_hash(record)
        line = canonical(record) + b"\n"
        if not status.ends_with_newline:
            line = b"\n" + line  # isolate a truncated last line instead of gluing onto it
        flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(self.path, flags, 0o600)
        try:
            if hasattr(os, "fchmod"):
                # Existing volumes may still have the previous group-readable mode.
                os.fchmod(fd, 0o600)
            view = memoryview(line)
            while view:
                written = os.write(fd, view)
                view = view[written:]
            os.fsync(fd)
        finally:
            os.close(fd)
        new = ChainStatus(ok=status.ok, records=status.records + 1,
                          last_seq=record["seq"], head_hash=record["hash"], first_broken=status.first_broken,
                          counts=dict(sorted({**status.counts, action: status.counts.get(action, 0) + 1}.items())),
                          last_after={**status.last_after, **({target: record["after_hash"]}
                                                              if outcome == "success" else {})},
                          ends_with_newline=True)
        with self._mutex:
            self._cache = (self._stat_key(), new)
        seclog.emit(seclog.AUDIT_LOGGER, "audit.appended", logging.INFO, seq=record["seq"], action=action,
                    target_ref=seclog.reference("target", target), outcome=outcome, hash=record["hash"],
                    prev_hash=record["prev_hash"], request_ref=seclog.reference("request", ctx.request_id))
        return record


# ---------------------------------------------------------------------- offline verifier
def _main(argv: list[str]) -> int:
    if len(argv) != 2 or argv[0] != "verify":
        print("usage: python -m app.audit verify AUDIT_JSONL", file=sys.stderr)
        return 2
    path = Path(argv[1])
    try:
        data = path.read_bytes()
    except OSError as exc:
        print(f"cannot read {path}: {exc.__class__.__name__}", file=sys.stderr)
        return 2
    status = scan(data)
    print(json.dumps({**status.public(), "counts": status.counts}, indent=2, sort_keys=True))
    return 0 if status.ok else 1


if __name__ == "__main__":  # pragma: no cover - exercised through a subprocess in the tests
    with contextlib.suppress(BrokenPipeError):
        sys.exit(_main(sys.argv[1:]))
