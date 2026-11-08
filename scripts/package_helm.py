# SPDX-License-Identifier: MIT
"""Stage the Helm chart and its shared runtime assets from a Git checkout."""

import argparse
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[1]
SOURCE_CHART = ROOT / "deploy" / "helm" / "ai-factory"
SHARED_FILES = {
    "compose/caddy/Caddyfile": "assets/Caddyfile",
    "dashboard/nginx.conf": "assets/nginx.conf",
}
MAX_ASSET_BYTES = 512 * 1024


def tracked_config_paths() -> list[str]:
    result = subprocess.run(
        ["git", "ls-files", "-z", "--", "config/"],
        cwd=ROOT,
        check=True,
        stdout=subprocess.PIPE,
    )
    paths = result.stdout.decode("utf-8").split("\0")
    allowed_top_level = {
        "business-units.example.yaml", "factory.yaml", "profiles.yaml", "skills.yaml",
    }
    allowed_directories = {"catalog", "frameworks", "usecases"}
    selected = []
    keys = set()
    for path in sorted(filter(None, paths)):
        relative = PurePosixPath(path).relative_to("config")
        if not (len(relative.parts) == 1 and relative.name in allowed_top_level) and not (
            len(relative.parts) > 1 and relative.parts[0] in allowed_directories
        ):
            continue
        if not re.fullmatch(r"[a-zA-Z0-9._/-]+\.yaml", relative.as_posix()):
            raise ValueError(f"unsupported config asset name: {path}")
        key = relative.as_posix().replace("/", "__")
        if key in keys:
            raise ValueError(f"duplicate ConfigMap key: {key}")
        keys.add(key)
        selected.append(path)
    if not allowed_top_level.issubset({PurePosixPath(path).name for path in selected}):
        raise ValueError("tracked config is incomplete")
    return selected


def reject_symlinks(path: Path) -> None:
    for entry in (path, *path.parents):
        if entry == ROOT:
            break
        if entry.is_symlink() or getattr(entry, "is_junction", lambda: False)():
            raise ValueError(f"symlink or junction in asset path: {path.relative_to(ROOT)}")


def read_text_asset(path: Path) -> bytes:
    reject_symlinks(path)
    if not path.is_file():
        raise ValueError(f"missing asset: {path.relative_to(ROOT)}")
    if path.stat().st_size > MAX_ASSET_BYTES:
        raise ValueError(f"asset exceeds the {MAX_ASSET_BYTES}-byte limit: {path.relative_to(ROOT)}")
    content = path.read_bytes().replace(b"\r\n", b"\n")
    content.decode("utf-8")
    if not content.endswith(b"\n") or b"\r" in content or b"\0" in content or content.startswith(b"\xef\xbb\xbf"):
        raise ValueError(f"expected UTF-8 without BOM, with LF and a final newline: {path.relative_to(ROOT)}")
    return content


def stage_chart(output: Path) -> Path:
    output = output.resolve()
    target = output / SOURCE_CHART.name
    if target == SOURCE_CHART or SOURCE_CHART in output.parents or output == SOURCE_CHART:
        raise ValueError("stage the chart outside its source directory")
    if target.exists() or target.is_symlink():
        raise ValueError(f"target already exists: {target}")
    paths = {**SHARED_FILES, **{path: f"assets/{path}" for path in tracked_config_paths()}}
    assets = {dest: read_text_asset(ROOT / source) for source, dest in paths.items()}
    if sum(map(len, assets.values())) > MAX_ASSET_BYTES:
        raise ValueError(f"shared assets exceed the {MAX_ASSET_BYTES}-byte limit")
    if assets["assets/nginx.conf"].count(b"        listen 80;\n") != 1:
        raise ValueError("dashboard nginx listener changed; update the Kubernetes loopback listener override")

    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".stage-", dir=output) as scratch:
        chart = Path(scratch) / SOURCE_CHART.name
        chart.mkdir()
        for filename in ("Chart.yaml", "values.yaml", "values.schema.json"):
            source = SOURCE_CHART / filename
            reject_symlinks(source)
            shutil.copyfile(source, chart / filename)
        (chart / "templates").mkdir()
        for source in sorted((SOURCE_CHART / "templates").iterdir()):
            reject_symlinks(source)
            if source.is_file() and source.suffix in {".tpl", ".yaml", ".txt"}:
                shutil.copyfile(source, chart / "templates" / source.name)
        for dest, content in assets.items():
            path = chart / dest
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        chart.rename(target)
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output_dir", type=Path)
    args = parser.parse_args()
    try:
        print(stage_chart(args.output_dir))
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        parser.exit(1, f"chart staging failed: {exc}\n")


if __name__ == "__main__":
    main()
