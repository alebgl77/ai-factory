"""Writable overlay kept in FACTORY_STATE_DIR (JSON files, atomic writes, file lock).

Two documents are stored:

- ``registry_overrides.json``: model-version and assessment changes made through the API,
  applied on top of ``config/usecases/*.yaml`` (the Git definition stays the baseline).
- ``spaces_state.json``: optional persistence of the 3D bookings with an optimistic ``version``.
"""

from __future__ import annotations

import contextlib
import copy
import datetime as dt
import json
import os
import tempfile
import threading
from pathlib import Path
from typing import Any, Iterator

try:  # POSIX only; on other platforms the in-process lock is the only guard.
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None  # type: ignore[assignment]

REGISTRY_FILE = "registry_overrides.json"
SPACES_FILE = "spaces_state.json"


class StateError(RuntimeError):
    """The state directory is not usable (not writable, corrupted file...)."""


class VersionConflict(RuntimeError):
    def __init__(self, current: dict[str, Any]) -> None:
        super().__init__("version conflict")
        self.current = current


def apply_registry_overrides(usecases: list[dict[str, Any]], overrides: dict[str, Any]) -> list[dict[str, Any]]:
    """Pure: return use cases with the stored model/assessment overrides applied."""
    out: list[dict[str, Any]] = []
    for uc in usecases:
        item = copy.deepcopy(uc)
        ov = overrides.get(str(uc.get("id"))) if isinstance(overrides, dict) else None
        if isinstance(ov, dict):
            if isinstance(ov.get("model"), dict):
                model = dict(item.get("model") or {})
                model.update(copy.deepcopy(ov["model"]))
                item["model"] = model
            if isinstance(ov.get("assessment"), dict):
                assessment = dict(item.get("assessment") or {})
                assessment.update(copy.deepcopy(ov["assessment"]))
                item["assessment"] = assessment
        out.append(item)
    return out


class StateStore:
    def __init__(self, state_dir: str) -> None:
        self.dir = Path(state_dir)
        self._mutex = threading.RLock()

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
                fh = open(lock_path, "a+")
            except OSError as exc:
                raise StateError("state lock unavailable") from exc
            try:
                if fcntl is not None:
                    fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
                yield
            finally:
                if fcntl is not None:
                    with contextlib.suppress(OSError):
                        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
                fh.close()

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
        path = self.dir / name
        tmp: str | None = None
        try:
            fd, tmp = tempfile.mkstemp(prefix=f".{name}.", suffix=".tmp", dir=self.dir)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=False, indent=2, sort_keys=True)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, path)
        except OSError as exc:
            if tmp is not None:
                with contextlib.suppress(OSError):
                    os.unlink(tmp)
            raise StateError("state write failed") from exc

    # -- registry overlay -------------------------------------------------------------------
    def registry_overrides(self) -> dict[str, Any]:
        with self._mutex:
            data = self._read(REGISTRY_FILE, {})
        return data if isinstance(data, dict) else {}

    def set_model_version(self, uc: dict[str, Any], version: str, name: str | None, today: dt.date) -> dict[str, Any]:
        """Append a model version for a use case (uc = current effective use case)."""
        uc_id = str(uc["id"])
        with self._locked():
            data = self._read(REGISTRY_FILE, {})
            if not isinstance(data, dict):
                data = {}
            entry = data.get(uc_id) if isinstance(data.get(uc_id), dict) else {}
            model = copy.deepcopy(uc.get("model") or {})
            history = [h for h in (model.get("history") or []) if isinstance(h, dict)]
            history.append({"version": version, "from": today.isoformat()})
            model["history"] = history
            model["version"] = version
            if name:
                model["name"] = name
            entry["model"] = {"version": model["version"], "name": model.get("name"), "history": history}
            # A new model version always requires a fresh risk assessment (CONTRACT 2.4): the
            # decision becomes 'pending' so the flag flips even when re-using an old version.
            assessment = copy.deepcopy(uc.get("assessment") or {})
            previous = assessment.get("decision")
            assessment["decision"] = "pending"
            if previous and previous != "pending":
                assessment["previous_decision"] = previous
            entry["assessment"] = assessment
            entry["updated_at"] = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()
            data[uc_id] = entry
            self._write_atomic(REGISTRY_FILE, data)
            return copy.deepcopy(entry)

    def record_assessment(self, uc: dict[str, Any], decision: str, reviewer: str, today: dt.date) -> dict[str, Any]:
        uc_id = str(uc["id"])
        with self._locked():
            data = self._read(REGISTRY_FILE, {})
            if not isinstance(data, dict):
                data = {}
            entry = data.get(uc_id) if isinstance(data.get(uc_id), dict) else {}
            version = (uc.get("model") or {}).get("version")
            assessment = {
                "reviewed_model_version": version,
                "last_reviewed": today.isoformat(),
                "reviewer": reviewer,
                "decision": decision,
            }
            entry["assessment"] = assessment
            entry["updated_at"] = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()
            data[uc_id] = entry
            self._write_atomic(REGISTRY_FILE, data)
            return copy.deepcopy(entry)

    # -- spaces (3D bookings) -------------------------------------------------------------------
    def spaces_state(self) -> dict[str, Any]:
        with self._mutex:
            data = self._read(SPACES_FILE, {"version": 0, "updated_at": None, "state": {}})
        if not isinstance(data, dict) or not isinstance(data.get("version"), int):
            return {"version": 0, "updated_at": None, "state": {}}
        return data

    def put_spaces_state(self, expected_version: int, state: dict[str, Any]) -> dict[str, Any]:
        with self._locked():
            current = self._read(SPACES_FILE, {"version": 0, "updated_at": None, "state": {}})
            if not isinstance(current, dict) or not isinstance(current.get("version"), int):
                current = {"version": 0, "updated_at": None, "state": {}}
            if expected_version != current["version"]:
                raise VersionConflict(current)
            new = {
                "version": current["version"] + 1,
                "updated_at": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
                "state": state,
            }
            self._write_atomic(SPACES_FILE, new)
            return new
