#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Build the optional visual pack of the 3D dashboard (see docs/ASSETS.md).

Reads the PNG/JPEG/WebP images generated with ChatGPT (or any tool) from dashboard/assets/src/,
checks their format, makes textures tileable, keys out flat magenta backgrounds, slices the
portrait sheet into 12 avatars, converts everything to WebP and writes:

- dashboard/assets/assets.js   a data-URI bundle loaded by the dashboard (works offline, file://)
- dashboard/assets/out/*.webp  the optimised images (for the README, inspection, CDN use)
- dashboard/assets/PROVENANCE.md  source SHA-256, generator, date and licence of every image

Every image is optional: the dashboard keeps its procedural look for whatever is missing.
An empty bundle is always written, so the page never requests a file that does not exist.

Usage:
  python scripts/build_assets.py              # build
  python scripts/build_assets.py --check      # report missing / invalid sources, write nothing
  python scripts/build_assets.py --src DIR --generator "ChatGPT (OpenAI)"
"""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import hashlib
import io
import json
import sys
from pathlib import Path

try:
    from PIL import Image, ImageChops, ImageOps
except ImportError:  # pragma: no cover - explained to the user
    sys.exit("Pillow is required: pip install -r scripts/requirements-assets.txt")

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "dashboard" / "assets"

# id, kind, expected aspect (w/h), output size, extra options
SPEC: list[dict] = [
    {"id": "floor-oak", "kind": "texture", "aspect": 1.0, "size": (1024, 1024)},
    {"id": "floor-carpet", "kind": "texture", "aspect": 1.0, "size": (1024, 1024)},
    {"id": "floor-concrete", "kind": "texture", "aspect": 1.0, "size": (1024, 1024)},
    {"id": "floor-terrazzo", "kind": "texture", "aspect": 1.0, "size": (1024, 1024)},
    {"id": "wood-desk", "kind": "texture", "aspect": 1.0, "size": (1024, 1024)},
    {"id": "felt-acoustic", "kind": "texture", "aspect": 1.0, "size": (1024, 1024)},
    {"id": "ground-plaza", "kind": "ground", "aspect": 1.0, "size": (1024, 1024)},
    {"id": "skyline-panorama", "kind": "backdrop", "aspect": 1.5, "size": (1536, 1024)},
    {"id": "art-1", "kind": "image", "aspect": 2 / 3, "size": (640, 960)},
    {"id": "art-2", "kind": "image", "aspect": 2 / 3, "size": (640, 960)},
    {"id": "art-3", "kind": "image", "aspect": 1.5, "size": (1200, 800)},
    {"id": "logo-factory", "kind": "sprite", "aspect": 1.0, "size": (512, 512)},
    {"id": "avatars-sheet", "kind": "sheet", "aspect": 1.5, "size": (256, 256), "grid": (4, 3)},
    {"id": "mascot-visitor", "kind": "sprite", "aspect": 2 / 3, "size": (384, 576)},
    {"id": "hero-isometric", "kind": "image", "aspect": 1.5, "size": (1280, 853)},
]
EXTS = (".png", ".jpg", ".jpeg", ".webp")
MAGENTA = (255, 0, 255)


def find_source(src: Path, asset_id: str) -> Path | None:
    for ext in EXTS:
        p = src / f"{asset_id}{ext}"
        if p.exists():
            return p
    return None


def center_crop(img: Image.Image, aspect: float) -> Image.Image:
    w, h = img.size
    if abs(w / h - aspect) < 1e-3:
        return img
    if w / h > aspect:  # too wide
        nw = round(h * aspect)
        x = (w - nw) // 2
        return img.crop((x, 0, x + nw, h))
    nh = round(w / aspect)
    y = (h - nh) // 2
    return img.crop((0, y, w, y + nh))


def _edge_ramp(n: int, band: float) -> list[int]:
    """255 on both edges, 0 beyond `band` (fraction of n) from them, smoothstep in between."""
    b = max(1.0, n * band)
    out = []
    for i in range(n):
        t = max(0.0, 1 - min(i, n - 1 - i) / b)
        out.append(int(255 * t * t * (3 - 2 * t)))
    return out


def make_tileable(img: Image.Image, horizontal_only: bool = False, band: float = 0.22) -> Image.Image:
    """Cross-fade each edge band with the half-period offset copy of the image.

    T(x) = (1 - a(x)) * I(x) + a(x) * I(x + w/2), with a = 1 on the edges and 0 in the middle, is
    periodic by construction (T(0) = I(w/2), T(w - 1) = I(w/2 - 1)) and keeps the original image in
    the centre. Applied horizontally, then vertically on the result (which stays horizontally
    periodic because both terms are).
    """
    img = img.convert("RGB")
    w, h = img.size
    mask_x = Image.new("L", (w, 1)); mask_x.putdata(_edge_ramp(w, band))
    out = Image.composite(ImageChops.offset(img, w // 2, 0), img, mask_x.resize((w, h)))
    if not horizontal_only:
        mask_y = Image.new("L", (1, h)); mask_y.putdata(_edge_ramp(h, band))
        out = Image.composite(ImageChops.offset(out, 0, h // 2), out, mask_y.resize((w, h)))
    return out


def key_magenta(img: Image.Image, t0: float = 60, t1: float = 140) -> Image.Image:
    """Flat #FF00FF background -> alpha (soft edge + despill), unless the image already has alpha."""
    if img.mode in ("RGBA", "LA") and img.getextrema()[-1][0] < 250:
        return img.convert("RGBA")
    rgb = img.convert("RGB")
    out = Image.new("RGBA", rgb.size)
    src, dst = rgb.load(), out.load()
    w, h = rgb.size
    for y in range(h):
        for x in range(w):
            r, g, b = src[x, y]
            d = ((r - 255) ** 2 + g ** 2 + (b - 255) ** 2) ** 0.5
            a = 0 if d <= t0 else 255 if d >= t1 else int(255 * (d - t0) / (t1 - t0))
            if 0 < a < 255:  # despill the magenta fringe
                m = min(r, b)
                r, b = max(g, r - (m - g) // 2), max(g, b - (m - g) // 2)
            dst[x, y] = (r, g, b, a)
    return out


def trim_pad(img: Image.Image, aspect: float, pad: float = 0.04) -> Image.Image:
    bbox = img.getchannel("A").point(lambda a: 255 if a > 8 else 0).getbbox()
    if not bbox:
        return img
    img = img.crop(bbox)
    w, h = img.size
    cw, ch = (max(w, round(h * aspect)), max(h, round(w / aspect)))
    cw, ch = round(cw * (1 + pad)), round(ch * (1 + pad))
    canvas = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
    canvas.paste(img, ((cw - w) // 2, (ch - h) // 2))
    return canvas


def fade_edges(img: Image.Image, band: float = 0.12) -> Image.Image:
    img = img.convert("RGBA")
    w, h = img.size
    mask = Image.new("L", (w, h), 255)
    px = mask.load()
    bw, bh = int(w * band), int(h * band)
    for y in range(h):
        fy = min(1.0, y / bh, (h - 1 - y) / bh)
        for x in range(w):
            fx = min(1.0, x / bw, (w - 1 - x) / bw)
            px[x, y] = int(255 * max(0.0, min(fx, fy)) ** 1.5)
    img.putalpha(mask)
    return img


def slice_sheet(img: Image.Image, cols: int, rows: int, size: tuple[int, int]) -> list[Image.Image]:
    """Cut an exact grid, then re-centre each bust on its own non-background pixels."""
    rgb = img.convert("RGB")
    w, h = rgb.size
    corners = [rgb.getpixel((2, 2)), rgb.getpixel((w - 3, 2)), rgb.getpixel((2, h - 3)), rgb.getpixel((w - 3, h - 3))]
    bg = tuple(sorted(c[i] for c in corners)[1] for i in range(3))
    out = []
    for r in range(rows):
        for c in range(cols):
            cell = rgb.crop((c * w // cols, r * h // rows, (c + 1) * w // cols, (r + 1) * h // rows))
            px, cw, ch = cell.load(), *cell.size
            ix, iy = int(cw * 0.08), int(ch * 0.08)  # ignore the cell borders (neighbours may bleed in)
            mask = Image.new("L", (cw, ch), 0)
            mp = mask.load()
            for y in range(iy, ch - iy):
                for x in range(ix, cw - ix):
                    p = px[x, y]
                    mp[x, y] = 255 if abs(p[0] - bg[0]) + abs(p[1] - bg[1]) + abs(p[2] - bg[2]) > 36 else 0
            bx0, by0, bx1, by1 = mask.getbbox() or (ix, iy, cw - ix, ch - iy)
            side = int(max(bx1 - bx0, by1 - by0) * 1.08)
            cx, cy = (bx0 + bx1) // 2, (by0 + by1) // 2
            inner = Image.new("RGB", (cw, ch), bg)  # cell without its borders, padded with the background
            inner.paste(cell.crop((ix, iy, cw - ix, ch - iy)), (ix, iy))
            padded = Image.new("RGB", (cw + 2 * side, ch + 2 * side), bg)
            padded.paste(inner, (side, side))
            x0, y0 = side + cx - side // 2, side + cy - side // 2
            out.append(padded.crop((x0, y0, x0 + side, y0 + side)).resize(size, Image.LANCZOS))
    return out


def webp(img: Image.Image, quality: int = 80) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "WEBP", quality=quality, method=6, exact=img.mode == "RGBA")  # exact: keep RGB under alpha 0
    return buf.getvalue()


def process(spec: dict, path: Path) -> list[tuple[str, Image.Image, int]]:
    img = Image.open(path)
    img = ImageOps.exif_transpose(img)
    kind, size = spec["kind"], spec["size"]
    if kind == "texture":
        im = center_crop(img.convert("RGB"), 1.0).resize(size, Image.LANCZOS)
        return [(spec["id"], make_tileable(im), 80)]
    if kind == "backdrop":
        im = center_crop(img.convert("RGB"), spec["aspect"]).resize(size, Image.LANCZOS)
        return [(spec["id"], make_tileable(im, horizontal_only=True), 78)]
    if kind == "ground":
        im = center_crop(img.convert("RGB"), 1.0).resize(size, Image.LANCZOS)
        return [(spec["id"], fade_edges(im), 78)]
    if kind == "image":
        return [(spec["id"], center_crop(img.convert("RGB"), spec["aspect"]).resize(size, Image.LANCZOS), 82)]
    if kind == "sprite":
        im = trim_pad(key_magenta(img), spec["aspect"])
        im.thumbnail(size, Image.LANCZOS)
        return [(spec["id"], im, 88)]
    if kind == "sheet":
        cols, rows = spec["grid"]
        im = center_crop(img.convert("RGB"), spec["aspect"])
        return [(f"avatar-p{i + 1:02d}", a, 84) for i, a in enumerate(slice_sheet(im, cols, rows, size))]
    raise ValueError(kind)


def validate(spec: dict, path: Path) -> list[str]:
    issues = []
    with Image.open(path) as im:
        w, h = im.size
    if min(w, h) < 512:
        issues.append(f"{path.name}: resolution too low ({w}×{h}, minimum 512 px)")
    ratio = w / h
    if abs(ratio - spec["aspect"]) / spec["aspect"] > 0.06:
        issues.append(f"{path.name}: {w}×{h} (ratio {ratio:.2f}) instead of {spec['aspect']:.2f}: the image will be center-cropped")
    return issues


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--src", default=str(ASSETS / "src"))
    ap.add_argument("--out", default=str(ASSETS))
    ap.add_argument("--generator", default="ChatGPT (OpenAI) image generation, by the author")
    ap.add_argument("--check", action="store_true", help="report only, write nothing")
    args = ap.parse_args()
    src, out = Path(args.src), Path(args.out)

    found, missing, warnings = [], [], []
    for spec in SPEC:
        p = find_source(src, spec["id"])
        if p is None:
            missing.append(spec["id"])
            continue
        warnings += validate(spec, p)
        found.append((spec, p))
    print(f"{len(found)}/{len(SPEC)} images found in {src}")
    for m in missing:
        print(f"  missing: {m}.png (the procedural look is kept)")
    for w in warnings:
        print(f"  warning: {w}")
    if args.check:
        return 0

    items, rows = {}, []
    (out / "out").mkdir(parents=True, exist_ok=True)
    for spec, p in found:
        digest = hashlib.sha256(p.read_bytes()).hexdigest()
        stamp = dt.datetime.fromtimestamp(p.stat().st_mtime, dt.timezone.utc).date().isoformat()
        for asset_id, im, q in process(spec, p):
            data = webp(im, q)
            (out / "out" / f"{asset_id}.webp").write_bytes(data)
            items[asset_id] = {"src": "data:image/webp;base64," + base64.b64encode(data).decode(), "w": im.size[0], "h": im.size[1]}
            print(f"  {asset_id:<18} {im.size[0]}×{im.size[1]}  {len(data) / 1024:6.0f} KB")
        rows.append(f"| `{spec['id']}` | `{p.name}` | `{digest[:16]}…` | {args.generator} | {stamp} | MIT (repository) |")

    version = hashlib.sha256(json.dumps(sorted((k, v["src"][-64:]) for k, v in items.items())).encode()).hexdigest()[:12]
    bundle = {"version": version, "items": items}
    js = ("/* Generated by scripts/build_assets.py from dashboard/assets/src (docs/ASSETS.md). Do not edit. */\n"
          f"window.__AF_ASSETS = {json.dumps(bundle, separators=(',', ':'))};\n")
    (out / "assets.js").write_text(js, encoding="utf-8")
    prov = ["# Visual pack provenance", "",
            "Images generated for this repository (no real person or brand), published under the repository MIT",
            "license by the author. Rebuild with `python scripts/build_assets.py` after adding sources to `dashboard/assets/src/`.", "",
            "| Id | Source | SHA-256 (source) | Generator | Date | License |", "|---|---|---|---|---|---|", *rows, ""]
    (out / "PROVENANCE.md").write_text("\n".join(prov), encoding="utf-8")
    print(f"bundle: {out / 'assets.js'} ({len(js) / 1024:.0f} KB, {len(items)} items, version {version})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
