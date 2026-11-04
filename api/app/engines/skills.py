"""Skills Lab / R&D Lab engine: AI Act Art. 4 literacy coverage per role and per person.

Art. 4 (as rewritten by Regulation 2026/1744) asks for measures adapted to the role, the risk
and the context. The engine measures completion of the modules required per role
(``skills.yaml`` ``requirements``) and flags overdue Art. 4 modules once the literacy
milestone applies. Indicative, not legal advice.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from ..i18n import DEFAULT_LANG, msg
from ..util import r2, safe_div, to_date

ART4_NOTE_KEY = "skills.art4_note"
COVERAGE_TARGET = 0.8
SKILLS_ROOM = {"id": "S1", "seats": 8}
RND_STAGES = ("explore", "prototype", "evaluate", "transfer", "stopped")


def _literacy_date(regulatory: dict[str, Any] | None) -> dt.date | None:
    for m in ((regulatory or {}).get("ai_act") or {}).get("milestones") or []:
        if isinstance(m, dict) and m.get("id") == "literacy":
            return to_date(m.get("date"))
    return None


def evaluate_skills(skills_cfg: dict[str, Any], profiles: dict[str, Any], regulatory: dict[str, Any] | None,
                    today: dt.date, lang: str = DEFAULT_LANG) -> dict[str, Any]:
    """Art. 4 coverage. Text fields of the inputs must already be localised; notes are in ``lang``."""
    modules = [m for m in skills_cfg.get("modules") or [] if isinstance(m, dict) and m.get("id")]
    module_by_id = {str(m["id"]): m for m in modules}
    requirements = {str(k): [str(x) for x in (v or [])] for k, v in (skills_cfg.get("requirements") or {}).items()}
    people = [p for p in profiles.get("people") or [] if isinstance(p, dict) and p.get("id")]
    roles_cfg = [r for r in profiles.get("roles") or [] if isinstance(r, dict) and r.get("id")]
    literacy = _literacy_date(regulatory)
    literacy_applies = literacy is not None and today >= literacy
    warnings = []
    for role, mods in sorted(requirements.items()):
        for mid in mods:
            if mid not in module_by_id:
                warnings.append(msg(lang, "skills.warning.missing_module", module=mid, role=role))

    done_by_person: dict[str, dict[str, str]] = {}
    for c in skills_cfg.get("completions") or []:
        if not isinstance(c, dict):
            continue
        d = to_date(c.get("date"))
        if d is None or d > today:
            continue  # a future completion date is not a completion yet
        done_by_person.setdefault(str(c.get("person")), {})[str(c.get("module"))] = d.isoformat()

    sessions_out = []
    planned: dict[str, set[str]] = {}
    for s in sorted((s for s in skills_cfg.get("sessions") or [] if isinstance(s, dict)),
                    key=lambda s: (str(s.get("date")), str(s.get("start")))):
        d = to_date(s.get("date"))
        attendees = [str(a) for a in s.get("attendees") or []]
        status = "past" if d and d < today else ("today" if d == today else "upcoming")
        if status != "past":
            for a in attendees:
                planned.setdefault(a, set()).add(str(s.get("module")))
        module = module_by_id.get(str(s.get("module")), {})
        seats = int(s.get("seats") or SKILLS_ROOM["seats"])
        sessions_out.append({
            **s,
            "room": s.get("room") or SKILLS_ROOM["id"],
            "module_title": module.get("title"),
            "art4": bool(module.get("art4")),
            "seats": seats,
            "free_seats": max(0, seats - len(attendees)),
            "status": status,
        })

    people_out = []
    role_acc: dict[str, list[int]] = {}
    art4_req_total = art4_done_total = 0
    total_req = total_done = 0
    for p in people:
        pid = str(p["id"])
        role = str(p.get("role"))
        required = requirements.get(role, [])
        done_map = done_by_person.get(pid, {})
        done = [m for m in required if m in done_map]
        missing = [m for m in required if m not in done_map]
        overdue = [m for m in missing if literacy_applies and module_by_id.get(m, {}).get("art4")
                   and m not in planned.get(pid, set())]
        art4_required = [m for m in required if module_by_id.get(m, {}).get("art4")]
        art4_req_total += len(art4_required)
        art4_done_total += sum(1 for m in art4_required if m in done_map)
        total_req += len(required)
        total_done += len(done)
        acc = role_acc.setdefault(role, [0, 0])
        acc[0] += len(done)
        acc[1] += len(required)
        people_out.append({
            "person": pid,
            "name": p.get("name"),
            "role": role,
            "required": len(required),
            "done": len(done),
            "coverage": r2(safe_div(len(done), len(required), 1.0)),
            "missing": missing,
            "planned": sorted(m for m in missing if m in planned.get(pid, set())),
            "overdue": overdue,
            "hours_remaining": sum(float(module_by_id.get(m, {}).get("hours") or 0) for m in missing),
        })

    role_ids = [str(r["id"]) for r in roles_cfg] + sorted(
        r for r in set(requirements) | set(role_acc) if r not in {str(x["id"]) for x in roles_cfg})
    roles_out = []
    role_label = {str(r["id"]): r.get("label") for r in roles_cfg}
    for role in role_ids:
        done, req = role_acc.get(role, [0, 0])
        headcount = sum(1 for p in people if str(p.get("role")) == role)
        required = requirements.get(role, [])
        roles_out.append({
            "role": role,
            "label": role_label.get(role),
            "required": required,
            "art4_modules": [m for m in required if module_by_id.get(m, {}).get("art4")],
            "headcount": headcount,
            # None when nobody holds the role (coverage would be meaningless).
            "coverage": r2(safe_div(done, req, 1.0)) if headcount else None,
        })

    coverage = r2(safe_div(total_done, total_req, 1.0))
    art4_coverage = r2(safe_div(art4_done_total, art4_req_total, 0.0))
    staffed_roles = [r for r in roles_out if r["headcount"] > 0]
    every_role_has_art4 = all(r["art4_modules"] for r in staffed_roles) if staffed_roles else False
    in_place = every_role_has_art4 and art4_coverage >= COVERAGE_TARGET
    overdue_people = [p["person"] for p in people_out if p["overdue"]]
    return {
        "modules": modules,
        "roles": roles_out,
        "people": people_out,
        "coverage": coverage,
        "art4_coverage": art4_coverage,
        "sessions": sessions_out,
        "art4": {
            "status": "measures_in_place" if in_place else "partial",
            "note": msg(lang, ART4_NOTE_KEY),
            "applicable_from": literacy.isoformat() if literacy else None,
            "target": COVERAGE_TARGET,
            "roles_without_art4_module": [r["role"] for r in staffed_roles if not r["art4_modules"]],
            "overdue_people": overdue_people,
        },
        "warnings": warnings,
    }


def evaluate_rnd(skills_cfg: dict[str, Any], profiles: dict[str, Any]) -> dict[str, Any]:
    people = {str(p.get("id")): p for p in profiles.get("people") or [] if isinstance(p, dict)}
    experiments = []
    for x in (skills_cfg.get("rnd") or {}).get("experiments") or []:
        if not isinstance(x, dict):
            continue
        owner = people.get(str(x.get("owner")), {})
        stage = x.get("stage") if x.get("stage") in RND_STAGES else "explore"
        experiments.append({**x, "stage": stage, "owner_role": owner.get("role"), "owner_name": owner.get("name"),
                            "bench": x.get("bench")})
    # Benches X1..X3 of the R&D Lab: assign active experiments in order when not configured.
    benches = ["X1", "X2", "X3"]
    active = [x for x in experiments if x["stage"] not in ("stopped", "transfer")]
    for n, x in enumerate(active):
        if not x.get("bench"):
            x["bench"] = benches[n] if n < len(benches) else None
    gpu = sum(float(x.get("gpu_hours") or 0) for x in experiments if x["stage"] != "stopped")
    return {
        "experiments": experiments,
        "gpu_hours_month": r2(gpu),
        "by_stage": {s: sum(1 for x in experiments if x["stage"] == s) for s in RND_STAGES},
        "fine_tuning": False,
    }
