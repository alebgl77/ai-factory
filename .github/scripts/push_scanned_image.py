#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Push one scanned Docker object and verify its registry identity before promotion."""

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys


DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
TAG = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}")
MANIFEST_TYPES = {
    "application/vnd.docker.distribution.manifest.v2+json",
    "application/vnd.oci.image.manifest.v1+json",
}


def docker(*args):
    return subprocess.run(["docker", *args], check=True, capture_output=True,
                          text=True, timeout=300).stdout.strip()


def verify_remote_image(image_id, repository, digest):
    manifest = json.loads(docker("buildx", "imagetools", "inspect", f"{repository}@{digest}", "--raw"))
    if (not isinstance(manifest, dict) or manifest.get("schemaVersion") != 2
            or manifest.get("mediaType") not in MANIFEST_TYPES
            or not isinstance(manifest.get("config"), dict)
            or manifest["config"].get("digest") != image_id):
        raise ValueError("The registry image differs from the scanned image.")


def push_image(image_id, repository, tags, expected_digest=None):
    if not DIGEST.fullmatch(image_id) or (expected_digest is not None and not DIGEST.fullmatch(expected_digest)):
        raise ValueError("The image ID and expected digest must be SHA-256 references.")
    if not tags or any(not tag.startswith(repository + ":") or not TAG.fullmatch(tag[len(repository) + 1:])
                       for tag in tags):
        raise ValueError("Every tag must belong to the release image repository.")
    if docker("image", "inspect", image_id, "--format", "{{.Id}}") != image_id:
        raise ValueError("The local image differs from the scanned image.")
    if expected_digest is not None:
        verify_remote_image(image_id, repository, expected_digest)

    digest = expected_digest
    for tag in tags:
        if expected_digest is None:
            docker("image", "tag", image_id, tag)
            # The release contract is one platform. Push its manifest, without a new build or index.
            docker("image", "push", "--platform", "linux/amd64", tag)
        else:
            docker("buildx", "imagetools", "create", "--prefer-index=false", "--tag", tag,
                   f"{repository}@{expected_digest}")
        descriptor = json.loads(docker("buildx", "imagetools", "inspect", tag,
                                       "--format", "{{json .Manifest}}"))
        if not isinstance(descriptor, dict):
            raise ValueError("The registry returned an invalid image descriptor.")
        remote_digest = descriptor.get("digest", "")
        if not isinstance(remote_digest, str) or not DIGEST.fullmatch(remote_digest):
            raise ValueError("The registry did not return a valid image digest.")
        verify_remote_image(image_id, repository, remote_digest)
        if digest is not None and remote_digest != digest:
            raise ValueError("The registry digest changed during image promotion.")
        digest = remote_digest
    return digest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image_id")
    parser.add_argument("repository")
    parser.add_argument("--tags", required=True, help="Newline-separated destination tags.")
    parser.add_argument("--expected-digest")
    args = parser.parse_args()
    try:
        digest = push_image(args.image_id, args.repository, args.tags.splitlines(), args.expected_digest)
    except (ValueError, TypeError, AttributeError, subprocess.SubprocessError) as exc:
        print(f"Image publication failed. {exc}", file=sys.stderr)
        return 1
    if output := os.environ.get("GITHUB_OUTPUT"):
        with Path(output).open("a", encoding="utf-8") as stream:
            stream.write(f"digest={digest}\n")
    print(digest)
    return 0


if __name__ == "__main__":
    sys.exit(main())
