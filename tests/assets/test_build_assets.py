# SPDX-License-Identifier: MIT
"""Tests of scripts/build_assets.py on small synthetic images (needs Pillow)."""

from __future__ import annotations

import base64
import io
import json
import subprocess
import sys
from pathlib import Path

import pytest

PIL = pytest.importorskip("PIL")
from PIL import Image, ImageDraw  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "build_assets.py"


def _bundle(out: Path) -> dict:
    js = (out / "assets.js").read_text(encoding="utf-8")
    return json.loads(js.split("window.__AF_ASSETS = ", 1)[1].rstrip().rstrip(";"))


def _decode(item: dict) -> Image.Image:
    return Image.open(io.BytesIO(base64.b64decode(item["src"].split(",", 1)[1])))


def _run(src: Path, out: Path, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(SCRIPT), "--src", str(src), "--out", str(out), *extra],
                          capture_output=True, text=True, check=True)


def test_empty_source_writes_an_empty_bundle(tmp_path):
    _run(tmp_path / "src", tmp_path / "out")
    assert _bundle(tmp_path / "out")["items"] == {}


def test_check_mode_writes_nothing(tmp_path):
    (tmp_path / "src").mkdir()
    Image.new("RGB", (1024, 700), "grey").save(tmp_path / "src" / "floor-oak.png")
    res = _run(tmp_path / "src", tmp_path / "out", "--check")
    assert "center-cropped" in res.stdout
    assert not (tmp_path / "out" / "assets.js").exists()


def test_texture_becomes_tileable(tmp_path):
    src = tmp_path / "src"; src.mkdir()
    im = Image.new("RGB", (1024, 1024))
    d = ImageDraw.Draw(im)
    for x in range(1024):  # strong left-to-right gradient: raw edges do not match at all
        d.line([x, 0, x, 1023], fill=(x // 4, 60, 255 - x // 4))
    im.save(src / "floor-concrete.png")
    _run(src, tmp_path / "out")
    t = _decode(_bundle(tmp_path / "out")["items"]["floor-concrete"]).convert("RGB")
    left, right = t.getpixel((0, 512)), t.getpixel((t.width - 1, 512))
    assert sum(abs(a - b) for a, b in zip(left, right, strict=True)) < 40


def test_magenta_background_is_keyed_out(tmp_path):
    src = tmp_path / "src"; src.mkdir()
    im = Image.new("RGB", (1024, 1024), (255, 0, 255))
    ImageDraw.Draw(im).ellipse([300, 300, 724, 724], fill=(109, 40, 217))
    im.save(src / "logo-factory.png")
    _run(src, tmp_path / "out")
    logo = _decode(_bundle(tmp_path / "out")["items"]["logo-factory"]).convert("RGBA")
    assert logo.getpixel((2, 2))[3] == 0                      # corner transparent
    assert logo.getpixel((logo.width // 2, logo.height // 2))[3] == 255


def test_portrait_sheet_is_sliced_into_twelve_avatars(tmp_path):
    src = tmp_path / "src"; src.mkdir()
    sheet = Image.new("RGB", (1536, 1024), (248, 250, 252))
    d = ImageDraw.Draw(sheet)
    for i in range(12):
        c, r = i % 4, i // 4
        cx, cy = 192 + c * 384, 170 + r * 341
        d.ellipse([cx - 90, cy - 90, cx + 90, cy + 90], fill=(20 * i, 80, 200 - 10 * i))
    sheet.save(src / "avatars-sheet.png")
    _run(src, tmp_path / "out")
    items = _bundle(tmp_path / "out")["items"]
    assert [k for k in items if k.startswith("avatar-")] == [f"avatar-p{i:02d}" for i in range(1, 13)]
    a = _decode(items["avatar-p05"]).convert("RGB")
    assert a.size == (256, 256)
    assert a.getpixel((128, 128)) != (248, 250, 252)          # the bust is centred
    prov = (tmp_path / "out" / "PROVENANCE.md").read_text(encoding="utf-8")
    assert "avatars-sheet" in prov and "MIT" in prov
