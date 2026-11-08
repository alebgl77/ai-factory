"""Writable overlay kept in FACTORY_STATE_DIR (JSON files, atomic writes, file lock).

Two documents are stored:

- ``registry_overrides.json``: model-version and assessment changes made through the API,
  applied on top of ``config/usecases/*.yaml`` (the Git definition stays the baseline).
- ``spaces_state.json``: optional persistence of the 3D bookings with an optimistic ``version``.

Every write made through the API passes an :class:`app.audit.AuditContext`: the state change and
its hash-chained audit record (``audit.jsonl``) are written under the same lock, and the state
change is rolled back if the audit record cannot be written (no unaudited write). The four-eyes
rule and the reviewed proposal revision are checked under that lock too.
"""

from __future__ import annotations

import contextlib
import copy
import datetime as dt
import hashlib
import json
import os
import secrets
import tempfile
import threading
from pathlib import Path
from typing import Any, Callable, Iterator

from .audit import AuditContext, AuditLog, canonical, digest

try:  # POSIX
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None  # type: ignore[assignment]

try:  # Windows
    import msvcrt
except ImportError:  # pragma: no cover
    msvcrt = None  # type: ignore[assignment]

REGISTRY_FILE = "registry_overrides.json"
SPACES_FILE = "spaces_state.json"
BOOKING_KEY_FILE = ".booking-view.key"
APPROVING_DECISIONS = frozenset({"approved", "approved_with_conditions"})
EMPTY_SPACES: dict[str, Any] = {"version": 0, "updated_at": None, "state": {}}


class StateError(RuntimeError):
    """The state directory is not usable (not writable, corrupted file...)."""


class AuditError(StateError):
    """The audit record could not be written: the change was rolled back."""


class SeparationOfDuties(RuntimeError):
    """Four-eyes rule: the identity that recorded a model version cannot approve it."""

    def __init__(self, version: str | None) -> None:
        super().__init__("separation of duties")
        self.version = version


class ModelVersionUnchanged(RuntimeError):
    """A model version cannot be proposed while it is already current."""


class StaleReviewRevision(RuntimeError):
    """The proposal changed since the reviewer read it."""


class VersionConflict(RuntimeError):
    def __init__(self, current: dict[str, Any]) -> None:
        super().__init__("version conflict")
        self.current = current


def _registry_item(uc: dict[str, Any], entry: dict[str, Any]) -> dict[str, Any]:
    item = copy.deepcopy(uc)
    item.pop("_review_approval", None)  # only the authenticated state transition may supply this proof
    for section in ("model", "assessment"):
        if isinstance(entry.get(section), dict):
            item[section] = {**(item.get(section) or {}), **copy.deepcopy(entry[section])}
    # Bind to the untranslated proposal, including model history. The history grows under the
    # write lock even when a previous version is reused. Review decisions do not change the proposal.
    proposal = {key: value for key, value in item.items() if key not in ("assessment", "review_revision")}
    item["review_revision"] = hashlib.sha256(canonical(proposal)).hexdigest()
    approval = entry.get("review_approval")
    if isinstance(approval, dict) and approval.get("revision") == item["review_revision"]:
        item["_review_approval"] = copy.deepcopy(approval)
    return item


def apply_registry_overrides(usecases: list[dict[str, Any]], overrides: dict[str, Any]) -> list[dict[str, Any]]:
    """Pure: return use cases with the stored model/assessment overrides applied."""
    out = []
    for uc in usecases:
        entry = overrides.get(str(uc.get("id"))) if isinstance(overrides, dict) else None
        out.append(_registry_item(uc, entry if isinstance(entry, dict) else {}))
    return out


class StateStore:
    def __init__(self, state_dir: str) -> None:
        self.dir = Path(state_dir)
        self._mutex = threading.RLock()
        self.audit = AuditLog(self.dir)

    def ready(self) -> bool:
        """Check the local state path without taking a lock, opening the log or writing."""
        directory = self.dir.absolute()
        try:
            while not directory.exists() and not directory.is_symlink() and directory != directory.parent:
                directory = directory.parent
            if not directory.is_dir() or not os.access(directory, os.R_OK | os.W_OK | os.X_OK):
                return False
            for path in (self.dir / ".state.lock", self.audit.path):
                if path.is_symlink() or (path.exists() and (not path.is_file()
                                                           or not os.access(path, os.R_OK | os.W_OK))):
                    return False
        except OSError:
            return False
        return True

    # -- low level ---------------------------------------------------------------------------
    def _ensure_dir(self) -> None:
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise StateError(f"state directory not accessible: {exc.__class__.__name__}") from exc

    @contextlib.contextmanager
    def _locked(self) -> Iterator[None]:
        with self._mutex:
            self._ensure_dir()
            lock_path = self.dir / ".state.lock"
            try:
                fh = lock_path.open("a+b", buffering=0)
            except OSError as exc:
                raise StateError("state lock unavailable") from exc
            with fh:
                try:
                    if fcntl is not None:
                        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
                    elif msvcrt is not None:
                        # Every process locks byte zero, including on an empty file.
                        fh.seek(0)
                        msvcrt.locking(fh.fileno(), msvcrt.LK_LOCK, 1)
                    else:
                        raise StateError("state lock unavailable")
                except OSError as exc:
                    raise StateError("state lock unavailable") from exc
                try:
                    yield
                finally:
                    with contextlib.suppress(OSError):
                        if fcntl is not None:
                            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
                        elif msvcrt is not None:
                            fh.seek(0)
                            msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)

    def _read(self, name: str, default: Any) -> Any:
        path = self.dir / name
        try:
            with path.open("r", encoding="utf-8") as fh:
                return json.load(fh)
        except FileNotFoundError:
            return copy.deepcopy(default)
        except (OSError, json.JSONDecodeError):
            # A corrupted overlay must not break the API: fall back to the Git baseline.
            return copy.deepcopy(default)

    def _write_atomic(self, name: str, data: Any) -> None:
        self._write_bytes_atomic(name, json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8"))

    def _write_bytes_atomic(self, name: str, payload: bytes) -> None:
        path = self.dir / name
        tmp: str | None = None
        try:
            fd, tmp = tempfile.mkstemp(prefix=f".{name}.", suffix=".tmp", dir=self.dir)
            with os.fdopen(fd, "wb") as fh:
                fh.write(payload)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, path)
        except OSError as exc:
            if tmp is not None:
                with contextlib.suppress(OSError):
                    os.unlink(tmp)
            raise StateError("state write failed") from exc

    def _raw(self, name: str) -> bytes | None:
        try:
            return (self.dir / name).read_bytes()
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise StateError("state read failed") from exc

    def _commit(self, name: str, data: Any, previous: bytes | None, record: Callable[[], Any] | None) -> None:
        """Write ``data`` then its audit record; restore ``previous`` when the record fails."""
        self._write_atomic(name, data)
        if record is None:
            return
        try:
            record()
        except Exception as exc:
            with contextlib.suppress(OSError, StateError):
                if previous is None:
                    (self.dir / name).unlink()
                else:
                    self._write_bytes_atomic(name, previous)
            raise AuditError("audit write failed") from exc

    def _append(self, ctx: AuditContext, **kwargs: Any) -> dict[str, Any]:
        try:
            return self.audit.append(ctx, **kwargs)
        except OSError as exc:
            raise AuditError("audit write failed") from exc

    # -- audit -----------------------------------------------------------------------------------
    def append_audit(self, ctx: AuditContext, *, action: str, target: str, before: Any = None, after: Any = None,
                     details: dict[str, Any] | None = None, require_intact: bool = False) -> dict[str, Any]:
        """Audit a write that keeps no state (e.g. a placement decision)."""
        with self._locked():
            return self._append(ctx, action=action, target=target, before=before, after=after, details=details,
                                require_intact=require_intact)

    def audit_status(self) -> dict[str, Any]:
        """Cheap chain status for the posture (cached on the file's inode, size and mtime)."""
        try:
            with self._mutex:
                return {"available": True, **self.audit.status().public()}
        except OSError:
            return {"available": False, "chain_ok": None, "records": None, "head_hash": None, "first_broken": None}

    def audit_counts(self) -> dict[str, int]:
        """Audit records per action (for ``factory_audit_events_total``)."""
        try:
            with self._mutex:
                return dict(self.audit.status().counts)
        except OSError:
            return {}

    def audit_page(self, *, offset: int, limit: int, action: str | None) -> tuple[int, list[dict[str, Any]]]:
        if not self.audit.path.exists():
            return 0, []
        try:
            with self._locked():
                return self.audit.page(offset=offset, limit=limit, action=action)
        except OSError as exc:
            raise StateError("audit read failed") from exc

    def verify_audit(self) -> dict[str, Any]:
        """Recompute the whole chain and reconcile it with the stored state documents."""
        try:
            if self.audit.path.exists():
                with self._locked():
                    status = self.audit.verify()
                    registry = self._read(REGISTRY_FILE, {})
                    spaces = self._read(SPACES_FILE, None) if (self.dir / SPACES_FILE).exists() else None
            else:
                status = self.audit.verify()
                registry = self._read(REGISTRY_FILE, {}) if self.dir.exists() else {}
                spaces = self._read(SPACES_FILE, None) if (self.dir / SPACES_FILE).exists() else None
        except OSError as exc:
            raise StateError("audit read failed") from exc
        current: dict[str, str | None] = {}
        if isinstance(registry, dict):
            current.update({f"usecase:{k}": digest(v) for k, v in registry.items()})
        if spaces is not None:
            current["spaces:state"] = digest(spaces)
        drift = sorted(t for t, h in status.last_after.items()
                       if t.startswith(("usecase:", "spaces:")) and current.get(t) != h)
        unaudited = sorted(t for t in current if t not in status.last_after)
        return {"ok": status.ok and not drift, **status.public(), "counts": status.counts,
                "state": {"consistent": not drift, "drift": drift, "unaudited": unaudited}}

    # -- registry overlay -------------------------------------------------------------------
    def registry_overrides(self) -> dict[str, Any]:
        with self._mutex:
            data = self._read(REGISTRY_FILE, {})
        return data if isinstance(data, dict) else {}

    def _registry_for_write(self, raw: bytes | None, uc_id: str) -> dict[str, Any]:
        """Parse the locked snapshot; corrupted data cannot be treated as a new proposal."""
        try:
            data = json.loads(raw) if raw is not None else {}
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise StateError("registry state is invalid") from exc
        if not isinstance(data, dict) or not isinstance(data.get(uc_id, {}), dict):
            raise StateError("registry state is invalid")
        return data

    def set_model_version(self, uc: dict[str, Any], version: str, name: str | None, today: dt.date,
                          audit: AuditContext | None = None) -> dict[str, Any]:
        """Append a model version on top of the configured use case and locked overlay."""
        uc_id = str(uc["id"])
        with self._locked():
            previous_raw = self._raw(REGISTRY_FILE)
            data = self._registry_for_write(previous_raw, uc_id)
            before = copy.deepcopy(data.get(uc_id))
            entry = data.get(uc_id, {})
            current = _registry_item(uc, entry)
            model = current.get("model") or {}
            previous_version = model.get("version")
            if str(previous_version) == version:
                raise ModelVersionUnchanged
            history = [h for h in (model.get("history") or []) if isinstance(h, dict)]
            history.append({"version": version, "from": today.isoformat()})
            model["history"] = history
            model["version"] = version
            if name:
                model["name"] = name
            entry["model"] = {"version": model["version"], "name": model.get("name"), "history": history}
            # A new model version always requires a fresh risk assessment (CONTRACT 2.4): the
            # decision becomes 'pending' so the flag flips even when re-using an old version.
            assessment = current.get("assessment") or {}
            previous = assessment.get("decision")
            assessment["decision"] = "pending"
            if previous and previous != "pending":
                assessment["previous_decision"] = previous
            entry["assessment"] = assessment
            entry["updated_at"] = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()
            if audit is not None:
                # Who recorded this version (state only, never in the snapshot): the four-eyes rule
                # compares it with the identity of the approver.
                entry["model_recorded_by"] = {"actor": audit.actor, "ids": sorted(audit.ids), "role": audit.role,
                                              "authenticated": audit.authenticated, "version": version,
                                              "at": entry["updated_at"]}
            else:
                entry.pop("model_recorded_by", None)
            data[uc_id] = entry
            record = None if audit is None else (lambda: self._append(
                audit, action="registry.model_version", target=f"usecase:{uc_id}", before=before, after=entry,
                details={"version": version, "previous_version": previous_version}))
            self._commit(REGISTRY_FILE, data, previous_raw, record)
            return _registry_item(uc, entry)

    def record_assessment(self, uc: dict[str, Any], decision: str, reviewer: str, today: dt.date,
                          *, expected_review_revision: str, audit: AuditContext | None = None,
                          enforce_four_eyes: bool = False, review_subject_ref: str | None = None,
                          review_clock: Callable[[], dt.datetime] | None = None) -> dict[str, Any]:
        """Record a review if the configured proposal and locked model overlay still match.

        With ``enforce_four_eyes`` an approving decision is refused (:class:`SeparationOfDuties`, and
        a ``denied`` audit record) when the approver shares an identity key with the authenticated
        identity that recorded the current model version through the API.
        """
        uc_id = str(uc["id"])
        with self._locked():
            previous_raw = self._raw(REGISTRY_FILE)
            data = self._registry_for_write(previous_raw, uc_id)
            before = copy.deepcopy(data.get(uc_id))
            entry = data.get(uc_id, {})
            current = _registry_item(uc, entry)
            revision = current["review_revision"]
            version = (current.get("model") or {}).get("version")
            if expected_review_revision != revision:
                if audit is not None:
                    self._append(audit, action="registry.assessment", target=f"usecase:{uc_id}", outcome="denied",
                                 before=before, after=before,
                                 details={"decision": decision, "reason": "stale_review_revision"})
                raise StaleReviewRevision
            assessment = {
                "reviewed_model_version": version,
                "reviewed_revision": revision,
                "last_reviewed": today.isoformat(),
                "reviewer": reviewer,
                "decision": decision,
            }
            if audit is not None:
                sod = self._separation_of_duties(entry, version, decision, audit, enforce_four_eyes)
                if sod == "violated":
                    self._append(audit, action="registry.assessment", target=f"usecase:{uc_id}", outcome="denied",
                                 before=before, after=before,
                                 details={"decision": decision, "reviewed_model_version": version,
                                          "reason": "separation_of_duties"})
                    raise SeparationOfDuties(version)
                assessment.update({"reviewed_by": audit.actor, "reviewed_by_role": audit.role,
                                   "separation_of_duties": sod})
            entry["assessment"] = assessment
            now = (review_clock or (lambda: dt.datetime.now(dt.timezone.utc)))().astimezone(dt.timezone.utc)
            if review_subject_ref and decision in APPROVING_DECISIONS:
                entry["review_approval"] = {"subject_ref": review_subject_ref, "reviewed_at": now.isoformat(),
                                            "revision": revision}
            else:
                entry.pop("review_approval", None)
            entry["updated_at"] = now.replace(microsecond=0).isoformat()
            data[uc_id] = entry
            record = None if audit is None else (lambda: self._append(
                audit, action="registry.assessment", target=f"usecase:{uc_id}", before=before, after=entry,
                details={"decision": decision, "reviewer": reviewer, "reviewed_model_version": version,
                         "reviewed_revision": revision,
                         "separation_of_duties": assessment.get("separation_of_duties")}))
            self._commit(REGISTRY_FILE, data, previous_raw, record)
            return _registry_item(uc, entry)

    @staticmethod
    def _separation_of_duties(entry: dict[str, Any], version: Any, decision: str, audit: AuditContext,
                              enforce: bool) -> str:
        """``verified`` | ``violated`` | ``unknown_recorder`` | ``not_required`` | ``not_enforced``."""
        if decision not in APPROVING_DECISIONS:
            return "not_required"  # pending / rejected keep the deployment gate closed
        if not enforce:
            return "not_enforced"
        recorder = entry.get("model_recorded_by")
        if not isinstance(recorder, dict) or not recorder.get("authenticated") \
                or str(recorder.get("version")) != str(version):
            return "unknown_recorder"  # version from Git (reviewed there) or recorded without SSO
        recorded_ids = {str(i) for i in recorder.get("ids") or []}
        return "violated" if recorded_ids & set(audit.ids) else "verified"

    # -- spaces (3D bookings) -------------------------------------------------------------------
    def booking_key(self) -> bytes:
        with self._locked():
            key = self._raw(BOOKING_KEY_FILE)
            if key is None:
                key = secrets.token_bytes(32)
                self._write_bytes_atomic(BOOKING_KEY_FILE, key)
            if len(key) != 32:
                raise StateError("booking view key unavailable")
            return key

    def spaces_state(self) -> dict[str, Any]:
        with self._mutex:
            data = self._read(SPACES_FILE, {"version": 0, "updated_at": None, "state": {}})
        if not isinstance(data, dict) or not isinstance(data.get("version"), int):
            return {"version": 0, "updated_at": None, "state": {}}
        return data

    def put_spaces_state(self, expected_version: int, state: dict[str, Any],
                         audit: AuditContext | None = None, *,
                         merge: Callable[[dict[str, Any], dict[str, Any]],
                                         tuple[dict[str, Any], dict[str, str]]] | None = None) -> dict[str, Any]:
        with self._locked():
            previous_raw = self._raw(SPACES_FILE)
            current = self._read(SPACES_FILE, EMPTY_SPACES)
            if not isinstance(current, dict) or not isinstance(current.get("version"), int):
                current = copy.deepcopy(EMPTY_SPACES)
            if merge is None and "owners" in current:
                raise StateError("business-scoped planning requires its policy")
            if expected_version != current["version"]:
                raise VersionConflict(current)
            if merge is not None:
                state, owners = merge(current, state)
            new = {
                "version": current["version"] + 1,
                "updated_at": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
                "state": state,
            }
            if merge is not None:
                new["owners"] = owners
            before = current if previous_raw is not None else None
            record = None if audit is None else (lambda: self._append(
                audit, action="spaces.put", target="spaces:state", before=before, after=new,
                details={"version": new["version"]}))
            self._commit(SPACES_FILE, new, previous_raw, record)
            return new
