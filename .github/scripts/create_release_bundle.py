#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Generate the immutable deployment overlay and service image evidence for one release."""

import json
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit

DIGEST = re.compile(r"sha256:[a-f0-9]{64}")
IMAGE_NAME = re.compile(r"[a-z0-9][a-z0-9._/-]*(?::[a-zA-Z0-9._-]+)?")
SERVICE_NAME = re.compile(r"[a-z0-9][a-z0-9_-]*")
LOCK_NAME = "docker-compose.release-lock.yml"
HELM_VALUES_NAME = "values.release.json"
MARKDOWN_LINK = re.compile(r"\[[^\]\n]*\]\(\s*(?:<([^>\n]+)>|([^\s)]+))[^)\n]*\)")
HTML_IMAGE = re.compile(r"""<img\s[^>]*?\bsrc=["']([^"']+)["']""", re.IGNORECASE)
FENCED_CODE = re.compile(r"^ {0,3}(`{3,}|~{3,}).*?^ {0,3}\1[ \t]*$", re.MULTILINE | re.DOTALL)


def validate_markdown_links(root):
    """Require bundle-local files linked by the deployment documentation."""
    root = root.resolve()
    missing = []
    for doc in sorted(root.rglob("*.md")):
        content = FENCED_CODE.sub("", doc.read_text(encoding="utf-8"))
        links = [first or second for first, second in MARKDOWN_LINK.findall(content)]
        links.extend(HTML_IMAGE.findall(content))
        for link in links:
            target = urlsplit(link)
            if target.scheme or target.netloc or not target.path:
                continue
            path = (doc.parent / unquote(target.path)).resolve()
            if not path.is_relative_to(root) or not path.exists():
                missing.append(f"{doc.relative_to(root)} -> {link}")
    if missing:
        raise ValueError("Missing deployment documentation targets:\n" + "\n".join(missing))


def render_compose(root, *overlays):
    command = ["docker", "compose", "--env-file", ".env.example", "--profile", "*", "-f", "docker-compose.yml"]
    for overlay in overlays:
        command.extend(["-f", overlay])
    return json.loads(subprocess.check_output(
        [*command, "config", "--format", "json"], cwd=root, timeout=60,
    ))["services"]


def resolve_registry_digest(ref):
    # Use the registry's manifest or index digest, never a local image/config ID.
    manifest = subprocess.check_output([
        "docker", "buildx", "imagetools", "inspect", "--format", "{{json .Manifest}}", ref,
    ], timeout=90)
    return json.loads(manifest)["digest"]


def deployment_inventory(services, refs, registry, resolve=resolve_registry_digest):
    if not services or set(refs) != {name for name, service in services.items() if "build" in service}:
        raise ValueError("Release image artifacts must cover exactly the services built from this repository.")

    inventory = {}
    resolved = {}
    for name, service in sorted(services.items()):
        if not SERVICE_NAME.fullmatch(name):
            raise ValueError("Unexpected Compose service name.")
        declared = service["image"]
        image, separator, digest = declared.partition("@")
        if not IMAGE_NAME.fullmatch(image) or (separator and not DIGEST.fullmatch(digest)):
            raise ValueError(f"Unsupported image reference for {name}.")
        if name in refs:
            ref = refs[name]
            expected = f"{registry}/{name}@"
            if not ref.startswith(expected) or not DIGEST.fullmatch(ref[len(expected):]):
                raise ValueError(f"Release artifact for {name} does not match its published image.")
        else:
            if not separator:
                if image not in resolved:
                    resolved[image] = resolve(image)
                digest = resolved[image]
            if not isinstance(digest, str) or not DIGEST.fullmatch(digest):
                raise ValueError(f"Registry did not resolve a SHA-256 manifest digest for {name}.")
            ref = f"{image}@{digest}"
        inventory[name] = {
            "declared_image": declared,
            "image": ref,
            "origin": "release" if name in refs else "upstream",
            "profiles": service.get("profiles", []),
        }
    return inventory


def create_evidence(root, assets, tag, sha, registry, resolve=resolve_registry_digest):
    if not re.fullmatch(r"v[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?", tag):
        raise ValueError("Expected a semantic version tag.")
    if not re.fullmatch(r"[a-f0-9]{40}", sha) or not re.fullmatch(r"ghcr\.io/[a-z0-9][a-z0-9-]*", registry):
        raise ValueError("Expected an exact source commit and release registry.")
    refs = {path.stem: path.read_text(encoding="utf-8").strip() for path in assets.glob("*.ref")}
    inventory = deployment_inventory(render_compose(root), refs, registry, resolve)
    lines = ["# SPDX-License-Identifier: MIT", "# Generated immutable image references for this release.", "services:"]
    for name, entry in inventory.items():
        lines.append(f"  {name}:")
        if entry["origin"] == "release":
            lines.append("    build: !reset null")
        lines.append(f'    image: "{entry["image"]}"')
        lines.append("    pull_policy: missing")
    (root / LOCK_NAME).write_text("\n".join(lines) + "\n", encoding="utf-8")

    rendered = render_compose(root, LOCK_NAME)
    if set(rendered) != set(inventory) or any(
        "build" in service or service["image"] != inventory[name]["image"] for name, service in rendered.items()
    ):
        raise ValueError("Rendered release overlay does not match the digest inventory.")

    evidence = {"schema": 1, "tag": tag, "source_commit": sha, "platform": "linux/amd64", "services": inventory}
    (root / "release-stack.json").write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")


def create_helm_values(root):
    """Project the signed release inventory into the chart's image values."""
    evidence = json.loads((root / "release-stack.json").read_text(encoding="utf-8"))
    services = evidence["services"]
    images = {}
    for name in ("api", "dashboard"):
        entry = services[name]
        repository, _, digest = entry["image"].partition("@")
        if (entry["origin"] != "release" or not re.fullmatch(rf"ghcr\.io/[a-z0-9][a-z0-9-]*/{name}", repository)
                or not DIGEST.fullmatch(digest)):
            raise ValueError(f"Invalid first-party release image for {name}.")
        images[name] = {"repository": repository, "digest": digest}

    def upstream(service, expected_repository):
        entry = services[service]
        reference, _, digest = entry["image"].partition("@")
        if (entry["origin"] != "upstream" or reference.split(":", 1)[0] != expected_repository
                or not DIGEST.fullmatch(digest)):
            raise ValueError(f"Invalid upstream release image for {service}.")
        return {"repository": expected_repository, "digest": digest}

    images["caddy"] = upstream("edge", "caddy")
    images["oauth2Proxy"] = upstream("oauth2-proxy", "quay.io/oauth2-proxy/oauth2-proxy")
    values = {"images": images, "logging": {"alloy": {"image": upstream("alloy", "grafana/alloy")}}}
    (root / "deploy" / "helm" / HELM_VALUES_NAME).write_text(
        json.dumps(values, indent=2) + "\n", encoding="utf-8",
    )


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--check-docs":
        validate_markdown_links(Path(sys.argv[2]))
        sys.exit(0)
    if len(sys.argv) == 3 and sys.argv[1] == "--helm-values":
        create_helm_values(Path(sys.argv[2]))
        sys.exit(0)
    if len(sys.argv) != 6:
        sys.exit("usage: create_release_bundle.py BUNDLE_ROOT IMAGE_ARTIFACT_DIR TAG SOURCE_SHA REGISTRY\n"
                 "       create_release_bundle.py --helm-values BUNDLE_ROOT\n"
                 "       create_release_bundle.py --check-docs BUNDLE_ROOT")
    root, assets, tag, sha, registry = sys.argv[1:]
    create_evidence(Path(root), Path(assets), tag, sha, registry)
