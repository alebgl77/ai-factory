#!/usr/bin/env python3
"""Build the offline demo snapshots embedded in dashboard/index.html (MIT).

The snapshots are produced by the aggregation API demo generator (api/app) in mode ``always``
with FACTORY_CONFIG_DIR=config/, so the offline dashboard (file://, API down) shows exactly what
the API would serve in demo mode. Output is deterministic for a seed and an anchor date.

One snapshot per language (CONTRACT section 9): English first, French second. Ids, numbers and
dates are identical in both; only the human-readable strings differ.

Usage:
  python scripts/build_snapshot.py                      # update dashboard/index.html (today, seed 42)
  python scripts/build_snapshot.py --date 2026-11-03 --seed 42
  python scripts/build_snapshot.py --check              # exit 1 if an embedded snapshot is out of date
  python scripts/build_snapshot.py --out snap.json      # write the English JSON only (HTML untouched)
  python scripts/build_snapshot.py --lang fr --out snap-fr.json

The JSON goes between ``<!-- DEMO_SNAPSHOT_START -->`` and ``<!-- DEMO_SNAPSHOT_END -->`` in two
elements, ``<script type="application/json" id="demo-snapshot-en">`` and ``id="demo-snapshot-fr"``
(markers are created just before ``</body>`` when absent; an older single ``id="demo-snapshot"``
block between the markers is replaced). ``</`` is escaped as ``<\\/`` so the JSON cannot close the tag.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LANGS = ("en", "fr")
DEFAULT_LANG = "en"
START = "<!-- DEMO_SNAPSHOT_START -->"
END = "<!-- DEMO_SNAPSHOT_END -->"
SCRIPT_ID = "demo-snapshot-{lang}"
SCRIPT_CLOSE = "</script>"
BLOCK_RE = re.compile(re.escape(START) + r"(.*?)" + re.escape(END), re.DOTALL)


def script_open(lang: str) -> str:
    return f'<script type="application/json" id="{SCRIPT_ID.format(lang=lang)}">'


def _import_api(api_dir: Path):
    sys.path.insert(0, str(api_dir))
    from app.demo import reference_time  # noqa: PLC0415
    from app.service import FactoryService  # noqa: PLC0415
    from app.settings import Settings  # noqa: PLC0415
    return FactoryService, Settings, reference_time


def build_snapshots(config_dir: Path, seed: int, anchor: dt.date, langs: tuple[str, ...] = LANGS,
                    api_dir: Path = ROOT / "api") -> dict[str, dict]:
    """``{lang: snapshot}`` for each requested language (same seed, date and state)."""
    FactoryService, Settings, reference_time = _import_api(api_dir)
    with tempfile.TemporaryDirectory(prefix="factory-state-") as state_dir:
        # Empty state dir: the embedded snapshots reflect the Git definition only.
        settings = Settings(config_dir=str(config_dir), demo_mode="always", demo_seed=seed,
                            demo_date=anchor.isoformat(), state_dir=state_dir, cache_ttl_s=0)
        probe = FactoryService(settings)
        ref = reference_time(probe.config.get(), anchor)
        svc = FactoryService(settings, clock=lambda: ref.astimezone(dt.timezone.utc))
        return {lang: svc.snapshot(use_cache=False, lang=lang) for lang in langs}


def build_snapshot(config_dir: Path, seed: int, anchor: dt.date, api_dir: Path = ROOT / "api",
                   lang: str = DEFAULT_LANG) -> dict:
    return build_snapshots(config_dir, seed, anchor, (lang,), api_dir)[lang]


def to_embedded_json(snap: dict) -> str:
    text = json.dumps(snap, ensure_ascii=False, separators=(",", ":"))
    return text.replace("</", "<\\/").replace("<!--", "<\\u0021--")


def render_block(snaps: dict[str, dict]) -> str:
    """Marker block holding one ``<script>`` per language, in the order of ``LANGS``."""
    lines = [f"{script_open(lang)}{to_embedded_json(snaps[lang])}{SCRIPT_CLOSE}"
             for lang in LANGS if lang in snaps]
    return "\n".join([START, *lines, END])


def inject(html: str, block: str) -> str:
    if BLOCK_RE.search(html):
        return BLOCK_RE.sub(lambda _m: block, html, count=1)
    idx = html.lower().rfind("</body>")
    if idx == -1:
        return html.rstrip("\n") + "\n" + block + "\n"
    return html[:idx] + block + "\n" + html[idx:]


def embedded_snapshot(html: str, lang: str = DEFAULT_LANG) -> dict | None:
    """Embedded snapshot of ``lang`` (None when absent or unreadable)."""
    m = BLOCK_RE.search(html)
    if not m:
        return None
    inner = m.group(1)
    opening = script_open(lang)
    a = inner.find(opening)
    if a == -1:
        return None
    b = inner.find(SCRIPT_CLOSE, a + len(opening))
    if b == -1:
        return None
    try:
        return json.loads(inner[a + len(opening):b].replace("<\\/", "</"))
    except ValueError:
        return None


def embedded_snapshots(html: str) -> dict[str, dict | None]:
    return {lang: embedded_snapshot(html, lang) for lang in LANGS}


def _today_paris() -> dt.date:
    try:
        from zoneinfo import ZoneInfo  # noqa: PLC0415
        return dt.datetime.now(ZoneInfo("Europe/Paris")).date()
    except Exception:
        return dt.date.today()


def _check(html_path: Path, config_dir: Path, seed_arg: int | None, anchor: dt.date | None, seed: int) -> int:
    if not html_path.is_file():
        print(f"{html_path} not found", file=sys.stderr)
        return 1
    html = html_path.read_text(encoding="utf-8")
    current = embedded_snapshots(html)
    missing = [lang for lang, snap in current.items() if snap is None]
    if missing:
        print(f"embedded snapshot missing or unreadable ({', '.join(missing)}): run scripts/build_snapshot.py",
              file=sys.stderr)
        return 1
    meta = current[DEFAULT_LANG].get("meta") or {}
    check_anchor = anchor or dt.date.fromisoformat(str(meta.get("anchor_date")))
    check_seed = seed_arg if seed_arg is not None else int(meta.get("seed", seed))
    expected_snaps = build_snapshots(config_dir, check_seed, check_anchor)
    stale = [lang for lang in LANGS if current[lang] != expected_snaps[lang]]
    expected = inject(html, render_block(expected_snaps))
    if stale or expected != html:
        print(f"embedded snapshot out of date ({', '.join(stale) or 'layout'}): run scripts/build_snapshot.py",
              file=sys.stderr)
        return 1
    print(f"snapshots up to date ({', '.join(LANGS)}; seed {check_seed}, date {check_anchor.isoformat()})")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the offline demo snapshots of the 3D dashboard.")
    parser.add_argument("--config", default=os.environ.get("FACTORY_CONFIG_DIR", str(ROOT / "config")),
                        help="configuration directory (default: config/)")
    parser.add_argument("--html", default=None, help="HTML file to update (default: dashboard/index.html)")
    parser.add_argument("--out", default=None, help="write one snapshot JSON to this file (HTML untouched "
                                                     "unless --html is also given)")
    parser.add_argument("--lang", choices=LANGS, default=None,
                        help="language of the --out snapshot (default: en); the HTML always gets both")
    parser.add_argument("--seed", type=int, default=None, help="demo seed (default: FACTORY_DEMO_SEED or 42)")
    parser.add_argument("--date", default=None, help="anchor date YYYY-MM-DD (default: today, Europe/Paris)")
    parser.add_argument("--check", action="store_true",
                        help="exit 1 if an embedded snapshot differs from a rebuild (same seed and date)")
    args = parser.parse_args(argv)

    config_dir = Path(args.config)
    if not (config_dir / "factory.yaml").is_file():
        print(f"configuration not found: {config_dir}/factory.yaml", file=sys.stderr)
        return 2
    if args.lang and not args.out:
        print("--lang selects the language of --out: give --out as well", file=sys.stderr)
        return 2
    html_path = Path(args.html) if args.html else ROOT / "dashboard" / "index.html"
    seed = args.seed if args.seed is not None else int(os.environ.get("FACTORY_DEMO_SEED", "42"))
    anchor = dt.date.fromisoformat(args.date) if args.date else None

    if args.check:
        return _check(html_path, config_dir, args.seed, anchor, seed)

    anchor = anchor or _today_paris()
    write_html = bool(args.html) or not args.out
    out_lang = args.lang or DEFAULT_LANG
    snaps = build_snapshots(config_dir, seed, anchor, LANGS if write_html else (out_lang,))
    if args.out:
        Path(args.out).write_text(json.dumps(snaps[out_lang], ensure_ascii=False, indent=2) + "\n",
                                  encoding="utf-8")
        print(f"{out_lang} snapshot written to {args.out}")
        if not write_html:
            return 0
    if not html_path.is_file():
        print(f"{html_path} not found", file=sys.stderr)
        return 2
    html = html_path.read_text(encoding="utf-8")
    new_html = inject(html, render_block(snaps))
    if new_html != html:
        tmp = html_path.with_suffix(html_path.suffix + ".tmp")
        tmp.write_text(new_html, encoding="utf-8")
        os.replace(tmp, html_path)
        size = sum(len(to_embedded_json(s)) for s in snaps.values())
        print(f"{html_path} updated ({', '.join(LANGS)}; {size} bytes of JSON)")
    else:
        print(f"{html_path} already up to date")
    return 0


if __name__ == "__main__":
    sys.exit(main())
