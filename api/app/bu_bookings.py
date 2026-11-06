"""Business-scoped booking projection and atomic merge policy."""

from __future__ import annotations

import datetime as dt
import hmac
import re
from collections import Counter
from dataclasses import dataclass
from typing import Any

from .audit import canonical
from .bu_access import BUScope, BusinessPolicy
from .security import SecurityDenied

_IDENTIFIER = re.compile(r"[A-Za-z0-9_-]{1,64}\Z")
_OWNER_REF = re.compile(r"[0-9a-f]{64}\Z")
_BOOKING_FIELDS = {"id", "resourceId", "date", "employeeId", "start", "end", "ucId", "activity", "project", "note",
                   "attendees", "visio"}


class InvalidBooking(ValueError):
    pass


class BookingConflict(ValueError):
    pass


class BookingViewConflict(BookingConflict):
    pass


def _id(value: Any) -> bool:
    return isinstance(value, str) and bool(_IDENTIFIER.fullmatch(value))


def _valid_date(value: Any) -> bool:
    try:
        return isinstance(value, str) and dt.date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


def _canonical(row: Any) -> dict[str, Any]:
    if not isinstance(row, dict) or set(row) - _BOOKING_FIELDS:
        raise InvalidBooking
    id_fields = ("id", "resourceId", "employeeId", "ucId", "activity")
    if any(not _id(row.get(key)) for key in id_fields) or not _valid_date(row.get("date")):
        raise InvalidBooking
    start, end = row.get("start"), row.get("end")
    if type(start) is not int or type(end) is not int or not 0 <= start <= end - 5 <= 1435:
        raise InvalidBooking
    note, attendees, visio = row.get("note", ""), row.get("attendees", []), row.get("visio", False)
    if (not isinstance(note, str) or len(note) > 80 or not isinstance(attendees, list) or len(attendees) > 50
            or any(not _id(person) for person in attendees) or len(set(attendees)) != len(attendees)
            or row["employeeId"] in attendees or type(visio) is not bool):
        raise InvalidBooking
    return {**{key: row[key] for key in id_fields}, "date": row["date"], "start": start, "end": end,
            "note": note, "attendees": attendees, "visio": visio}


def _rows(doc: dict[str, Any]) -> list[Any]:
    state = doc.get("state")
    rows = state.get("bookings") if isinstance(state, dict) else None
    return rows if isinstance(rows, list) else []


def _row_id(row: Any) -> str | None:
    value = row.get("id") if isinstance(row, dict) else None
    return value if _id(value) else None


def _owners(doc: dict[str, Any]) -> dict[str, str]:
    entries = doc.get("owners")
    if not isinstance(entries, dict):
        return {}
    return {key: value for key, value in entries.items() if _id(key) and isinstance(value, str)
            and _OWNER_REF.fullmatch(value)}


def _overlap(new: dict[str, Any], existing: Any) -> bool:
    if not isinstance(existing, dict) or existing.get("resourceId", existing.get("resource")) != new["resourceId"]:
        return False
    day = existing.get("date")
    if day != new["date"]:
        return not _valid_date(day)  # ambiguous legacy reservation: operator must reconcile it
    start, end = existing.get("start"), existing.get("end")
    return (type(start) is not int or type(end) is not int or not 0 <= start < end <= 1440
            or start < new["end"] and new["start"] < end)


@dataclass(frozen=True)
class BookingAccess:
    policy: BusinessPolicy
    read: BUScope
    write: BUScope | None
    cases: dict[str, dict[str, Any]]
    subject: str | None
    secret: bytes

    @property
    def owner(self) -> str | None:
        if not self.subject:
            return None
        return hmac.digest(self.secret, b"factory-booking-owner-v1\0" + self.subject.encode(), "sha256").hex()

    @property
    def employee(self) -> str | None:
        return self.policy.identities.get(self.subject or "")

    def _view_token(self, doc: dict[str, Any]) -> str:
        def grant(scope: BUScope | None) -> list[Any] | None:
            return [scope.all_units, sorted(scope.centers)] if scope else None

        authority = {key: [case.get("cost_center"), case.get("sensitivity")] for key, case in self.cases.items()}
        proof = ["factory-booking-view-v1", self.subject, self.policy.fingerprint, doc.get("version", 0),
                 grant(self.read), grant(self.write), authority]
        return hmac.digest(self.secret, canonical(proof), "sha256").hex()

    def _allows(self, booking: dict[str, Any], scope: BUScope) -> bool:
        case = self.cases.get(booking["ucId"]) or {}
        center = case.get("cost_center")
        roster = self.policy.people.get(center, ())
        return (scope.allows(center) and self.policy.resource_allowed(booking["resourceId"], center)
                and {booking["employeeId"], *booking["attendees"]} <= set(roster)
                and (case.get("sensitivity") in {"C1", "C2"} or booking["resourceId"] in self.policy.secure_resources
                     and case.get("sensitivity") in {"C3", "C4"}))

    def _writable(self, row: dict[str, Any], owner: str) -> bool:
        return (self.write is not None and self.owner == owner and self.employee == row["employeeId"]
                and self._allows(row, self.write))

    def _visible(self, doc: dict[str, Any]) -> dict[str, dict[str, Any]]:
        owners = _owners(doc)
        rows = _rows(doc)
        ids = Counter(_row_id(row) for row in rows)
        visible = {}
        for row in rows:
            key = _row_id(row)
            if not key or key not in owners or ids[key] != 1:
                continue
            try:
                booking = _canonical(row)
            except InvalidBooking:
                continue
            if self._allows(booking, self.read):
                visible[key] = booking
        return visible

    def project(self, doc: dict[str, Any]) -> dict[str, Any]:
        visible = self._visible(doc)
        owners = _owners(doc)
        employee = self.employee
        centers = self.policy.people if self.write and self.write.all_units else self.write.centers if self.write else ()
        writable_centers = {center for center in centers if employee in self.policy.people.get(center, ())}
        writable_cases = {case_id for case_id, case in self.cases.items() if case.get("cost_center") in writable_centers}
        combinations = {resource: sorted(case_id for case_id in writable_cases if self._allows(
            {"resourceId": resource, "ucId": case_id, "employeeId": employee, "attendees": []}, self.write))
                        for resource in self.policy.resources} if self.write else {}
        combinations = {resource: ids for resource, ids in combinations.items() if ids}
        return {"version": doc.get("version", 0), "view_token": self._view_token(doc),
                "state": {"v": 2, "bookings": list(visible.values()), "dayStatus": {}, "meta": {"autoDemo": False}},
                "scope": {"write": bool(combinations), "employee": employee if combinations else None,
                          "resources": sorted(combinations), "resource_usecases": combinations,
                          "owned": sorted(key for key, row in visible.items() if self._writable(row, owners[key]))}}

    def merge(self, doc: dict[str, Any], proposed: dict[str, Any], *,
              view_token: str | None) -> tuple[dict[str, Any], dict[str, str]]:
        if not self.write or not self.employee or not self.owner:
            raise SecurityDenied(403, "business_scope_forbidden", "auth.forbidden")
        if not view_token or not hmac.compare_digest(view_token, self._view_token(doc)):
            raise BookingViewConflict
        if (set(proposed) - {"v", "bookings", "dayStatus", "meta"} or type(proposed.get("v")) is not int
                or proposed["v"] != 2 or not isinstance(proposed.get("bookings"), list)
                or proposed.get("dayStatus", {}) != {} or proposed.get("meta", {"autoDemo": False}) != {"autoDemo": False}):
            raise InvalidBooking
        previous = doc.get("state") or {}
        if not isinstance(previous, dict) or not isinstance(previous.get("bookings", []), list):
            raise BookingConflict
        rows = _rows(doc)
        existing_ids = {_row_id(row) for row in rows}
        owners = _owners(doc)
        visible = self._visible(doc)
        editable = {key for key, row in visible.items() if self._writable(row, owners[key])}
        retained = [row for row in rows if _row_id(row) not in editable]
        changes, seen = [], set()
        for raw in proposed["bookings"]:
            booking = _canonical(raw)
            key = booking["id"]
            if key in seen:
                raise InvalidBooking
            seen.add(key)
            if key in existing_ids and key not in editable:
                if visible.get(key) != booking:
                    raise SecurityDenied(403, "business_scope_forbidden", "auth.forbidden")
                continue  # read-only echo, keep the original stored record
            if booking["employeeId"] != self.employee or not self._allows(booking, self.write) \
                    or not self._allows(booking, self.read):
                raise SecurityDenied(403, "business_scope_forbidden", "auth.forbidden")
            if visible.get(key) != booking:
                changes.append(booking)
            retained.append(booking)
            owners[key] = self.owner
        for booking in changes:
            if any(row is not booking and _overlap(booking, row) for row in retained):
                raise BookingConflict
        for key in editable - seen:
            owners.pop(key, None)
        return {**previous, "v": 2, "bookings": retained}, owners


def recover_spaces(doc: dict[str, Any], restored: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
    """Keep ownership only for unchanged, uniquely identified records during an explicit repair."""
    def indexed(rows: list[Any]) -> dict[str, Any]:
        counts = Counter(_row_id(row) for row in rows)
        return {key: row for row in rows if (key := _row_id(row)) and counts[key] == 1}

    before, after = indexed(_rows(doc)), indexed(_rows({"state": restored}))
    return restored, {key: owner for key, owner in _owners(doc).items() if key in after and before.get(key) == after[key]}
