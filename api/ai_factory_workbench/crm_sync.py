"""One-shot, trusted-host CRM polling for the Workbench SDK."""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
from collections.abc import Sequence
from contextlib import closing
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from . import Actor, Workbench, WorkbenchError
from .crm import CRMError, crm_time, source_id, sync_opportunities
from .hubspot import HubSpotCRM
from .profiles import get_profile
from .salesforce import SalesforceCRM
from .twenty import TwentyCRM

_ENV = re.compile(r"[A-Z_][A-Z0-9_]{0,127}", re.ASCII)
_ERROR = re.compile(r"[a-z][a-z0-9_]{0,99}", re.ASCII)
_REFERENCE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}", re.ASCII)
_CURRENCY = re.compile(r"[A-Z]{3}", re.ASCII)
_MAX_CONFIG_BYTES = 256 * 1024
_COMMON_FIELDS = {"provider", "instance", "token_env", "default_currency"}
_PROVIDER_FIELDS = {
    "salesforce": {"base_url", "api_version"},
    "hubspot": {"won_stages"},
    "twenty": {"base_url", "won_stages"},
}
_REQUIRED_FIELDS = {
    "salesforce": {"base_url"},
    "hubspot": {"won_stages"},
    "twenty": {"base_url", "won_stages"},
}


def _unique_fields(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate configuration field")
        result[key] = value
    return result


def _read_config(path: Path, workspace: str) -> tuple[dict[str, dict[str, Any]], list[dict[str, str]], str]:
    with path.open("rb") as stream:
        body = stream.read(_MAX_CONFIG_BYTES + 1)
    if len(body) > _MAX_CONFIG_BYTES:
        raise ValueError("configuration limit exceeded")
    config = json.loads(body, object_pairs_hook=_unique_fields)
    if not isinstance(config, dict) or set(config) - {"sources", "customers", "profile"}:
        raise ValueError("invalid configuration")
    sources = config.get("sources")
    if not isinstance(sources, list) or not 1 <= len(sources) <= 20:
        raise ValueError("invalid source list")
    by_source: dict[str, dict[str, Any]] = {}
    for source in sources:
        if not isinstance(source, dict) or not isinstance(source.get("provider"), str):
            raise ValueError("invalid source")
        provider = source["provider"]
        if (provider not in _PROVIDER_FIELDS or set(source) - (_COMMON_FIELDS | _PROVIDER_FIELDS[provider])
                or not ({"provider", "instance", "token_env"} | _REQUIRED_FIELDS[provider]) <= set(source)
                or not isinstance(source["token_env"], str) or not _ENV.fullmatch(source["token_env"])):
            raise ValueError("invalid source")
        currency = source.get("default_currency")
        if currency is not None and (not isinstance(currency, str) or not _CURRENCY.fullmatch(currency)):
            raise ValueError("invalid default currency")
        identifier = source_id(provider, source["instance"])
        if identifier in by_source:
            raise ValueError("duplicate source")
        by_source[identifier] = source

    rows = config.get("customers", [])
    if not isinstance(rows, list) or len(rows) > 1000:
        raise ValueError("invalid customer catalog")
    customers = []
    references = set()
    for row in rows:
        if (not isinstance(row, dict) or set(row) != {"id", "name", "reference"}
                or not all(isinstance(value, str) for value in row.values())):
            raise ValueError("invalid customer mapping")
        ref = row["reference"]
        if (not _REFERENCE.fullmatch(row["id"]) or not _REFERENCE.fullmatch(ref)
                or ref.rpartition("/")[0] not in by_source or not ref.rpartition("/")[2]
                or ref in references or not 1 <= len(row["name"].strip()) <= 160
                or not row["name"].isprintable()):
            raise ValueError("invalid customer mapping")
        references.add(ref)
        customers.append({**row, "tenant": workspace})
    profile = config.get("profile", "governance")
    if not isinstance(profile, str):
        raise ValueError("invalid work profile")
    return by_source, customers, profile


def _open_source(config: dict[str, Any]) -> SalesforceCRM | HubSpotCRM | TwentyCRM:
    token = os.environ.get(config["token_env"])
    if not token:
        raise CRMError("missing_crm_credentials")
    instance = config["instance"]
    currency = config.get("default_currency")
    provider = config["provider"]
    if provider == "salesforce":
        return SalesforceCRM(config["base_url"], token, instance=instance,
                             api_version=config.get("api_version", "61.0"), default_currency=currency)
    if provider == "hubspot":
        return HubSpotCRM(token, instance=instance, won_stages=config["won_stages"], default_currency=currency)
    return TwentyCRM(config["base_url"], token, instance=instance,
                     won_stages=config["won_stages"], default_currency=currency)


def _error_code(error: Exception) -> str:
    if isinstance(error, (CRMError, WorkbenchError)):
        code = str(error)
        if _ERROR.fullmatch(code):
            return code
    if isinstance(error, (OSError, sqlite3.Error)):
        return "crm_sync_storage_error"
    return "invalid_crm_sync_configuration"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read CRM opportunities into an approved Workbench workflow.")
    parser.add_argument("--database", type=Path, required=True, help="Workspace SQLite database.")
    parser.add_argument("--workspace", required=True, help="Workspace identifier bound to the database.")
    parser.add_argument("--config", type=Path, required=True, help="Operator-managed CRM source and mapping JSON.")
    parser.add_argument("--since", help="Initial start time with an explicit timezone.")
    parser.add_argument("--until", help="Exclusive end time, defaults to five minutes before this run.")
    parser.add_argument("--provider", help="Provider kind or one exact configured source identifier.")
    parser.add_argument("--profile", choices=("governance", "operations", "platform"), help="Override the work profile.")
    args = parser.parse_args(argv)

    try:
        configs, customers, configured_profile = _read_config(args.config, args.workspace)
        profile = get_profile(args.profile or configured_profile).snapshot()
        since = crm_time(args.since) if args.since is not None else None
        until = crm_time(args.until) if args.until is not None else datetime.now(timezone.utc) - timedelta(minutes=5)
        selected = {identifier: config for identifier, config in configs.items()
                    if args.provider is None or args.provider in (identifier, config["provider"])}
        if not selected:
            raise ValueError("unknown provider")
        engine = Workbench(args.database, mode="protected_demo", workspace=args.workspace,
                           customers=customers, crm_sources=tuple(configs))
        actor = Actor("crm-sync", frozenset({"editor"}))
        results = []
        failed = False
        for identifier, config in selected.items():
            try:
                with closing(_open_source(config)) as provider:
                    if provider.source != identifier:
                        raise ValueError("invalid provider source")
                    result = sync_opportunities(engine, provider, actor, since=since, until=until, profile=profile)
            except (CRMError, WorkbenchError, OSError, ValueError, sqlite3.Error) as error:
                results.append({"source": identifier, "error": _error_code(error)})
                failed = True
            else:
                report = asdict(result)
                if isinstance(report["until"], datetime):
                    report["until"] = report["until"].isoformat()
                results.append(report)
    except (CRMError, WorkbenchError, OSError, ValueError, sqlite3.Error) as error:
        print(json.dumps({"error": _error_code(error)}), file=sys.stderr)
        return 1
    print(json.dumps(results))
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
