#!/usr/bin/env python3
"""Run the Workbench's synthetic replay or explicitly probe a read-only Odoo page."""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--odoo-read", action="store_true", help="read a partner page instead of replaying")
    parser.add_argument("--limit", type=int, help="Odoo page size, default 25, maximum 200")
    parser.add_argument("--after-write-date", help="Odoo cursor timestamp in UTC, YYYY-MM-DD HH:MM:SS")
    parser.add_argument("--after-id", type=int, help="Odoo partner cursor id")
    parser.add_argument("--overlap-seconds", type=int, help="rewind the Odoo cursor for overlap polling")
    args = parser.parse_args(argv)
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "api"))

    cursor_args = (args.after_write_date, args.after_id, args.overlap_seconds)
    if args.odoo_read:
        from app.workbench.adapters import AdapterError, Odoo19Adapter, PartnerCursor

        required = ("FACTORY_ODOO_URL", "FACTORY_ODOO_DATABASE", "FACTORY_ODOO_API_KEY")
        values = [os.environ.get(name) for name in required]
        if not all(values):
            parser.error("--odoo-read needs " + ", ".join(required))
        if (args.after_write_date is None) != (args.after_id is None):
            parser.error("--after-write-date and --after-id must be provided together")
        if args.overlap_seconds is not None and args.after_id is None:
            parser.error("--overlap-seconds requires a cursor")

        limit = args.limit if args.limit is not None else 25
        try:
            adapter = Odoo19Adapter(*values)
            cursor = PartnerCursor(args.after_write_date, args.after_id) if args.after_id is not None else None
            if cursor is not None and args.overlap_seconds is not None:
                cursor = cursor.overlap(args.overlap_seconds)
            page = adapter.partners_page(cursor, limit=limit)
        except ValueError:
            parser.error("invalid Odoo origin, credentials, limit, or cursor")
        except AdapterError as exc:
            print(str(exc), file=sys.stderr)
            return 1

        result = {"source": "odoo_read_only", "records_read": len(page.records),
                  "page_full": len(page.records) == limit,
                  "next_cursor": asdict(page.checkpoint) if page.checkpoint else None}
        print(json.dumps(result, indent=2))
        return 0

    if args.limit is not None or any(value is not None for value in cursor_args):
        parser.error("Odoo paging options require --odoo-read")
    from app.workbench.replay import run_benchmark

    result = run_benchmark()
    print(json.dumps(result, indent=2))
    summary = result["summary"]
    return int(summary["governed_passed"] != summary["scenarios"]
               or summary["routing_passed"] != summary["routing_scenarios"])


if __name__ == "__main__":
    raise SystemExit(main())
