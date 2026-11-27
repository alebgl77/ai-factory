# SPDX-License-Identifier: MIT
"""Exercise image publication against a disposable loopback registry."""

import importlib.util
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import time
import urllib.request

import pytest


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("registry_publisher", ROOT / ".github/scripts/push_scanned_image.py")
publisher = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(publisher)
REGISTRY_IMAGE = "registry:3.0.0@sha256:6c5666b861f3505b116bb9aa9b25175e71210414bd010d92035ff64018f9457e"
pytestmark = pytest.mark.skipif(os.environ.get("RELEASE_REGISTRY_TEST") != "1",
                                reason="Set RELEASE_REGISTRY_TEST=1 to run the disposable Docker registry test")


def docker(*args):
    return subprocess.run(["docker", *args], check=True, capture_output=True,
                          text=True, timeout=180).stdout.strip()


@pytest.fixture
def registry():
    name = "release-registry-" + secrets.token_hex(6)
    docker("run", "--detach", "--name", name, "--publish", "127.0.0.1::5000", REGISTRY_IMAGE)
    try:
        address = docker("port", name, "5000/tcp")
        for attempt in range(30):
            try:
                with urllib.request.urlopen(f"http://{address}/v2/", timeout=1) as response:
                    assert response.status == 200
                break
            except OSError:
                if attempt == 29:
                    raise
                time.sleep(0.2)
        yield address + "/release-test"
    finally:
        docker("rm", "--force", name)


@pytest.fixture
def image(tmp_path):
    tag = "release-object-" + secrets.token_hex(6) + ":test"
    (tmp_path / "payload").write_text("Release identity fixture.\n", encoding="utf-8")
    (tmp_path / "Dockerfile").write_text("FROM scratch\nCOPY payload /payload\n", encoding="utf-8")
    docker("build", "--platform", "linux/amd64", "--tag", tag, str(tmp_path))
    image_id = docker("image", "inspect", tag, "--format", "{{.Id}}")
    try:
        yield image_id, tmp_path
    finally:
        docker("image", "rm", "--force", image_id)


def test_registry_staging_and_version_tags_reference_the_same_scanned_object(registry, image):
    image_id, _ = image
    digest = publisher.push_image(image_id, registry, [registry + ":candidate"])
    assert publisher.push_image(image_id, registry, [registry + ":1.2.3", registry + ":1.2"], digest) == digest
    docker("pull", "--platform", "linux/amd64", registry + "@" + digest)
    assert docker("image", "inspect", registry + "@" + digest, "--format", "{{.Id}}") == image_id


def test_missing_registry_digest_never_creates_a_version_tag(registry, image):
    image_id, _ = image
    with pytest.raises(subprocess.CalledProcessError):
        publisher.push_image(image_id, registry, [registry + ":missing-version"], "sha256:" + "0" * 64)
    missing = subprocess.run(["docker", "buildx", "imagetools", "inspect", registry + ":missing-version"],
                             capture_output=True, timeout=60)
    assert missing.returncode != 0


def test_other_registry_object_never_creates_a_version_tag(registry, image):
    image_id, directory = image
    (directory / "payload").write_text("A different image object.\n", encoding="utf-8")
    tag = "release-other-" + secrets.token_hex(6) + ":test"
    docker("build", "--platform", "linux/amd64", "--tag", tag, str(directory))
    other_id = docker("image", "inspect", tag, "--format", "{{.Id}}")
    try:
        other_digest = publisher.push_image(other_id, registry, [registry + ":other-candidate"])
        with pytest.raises(ValueError, match="registry image differs"):
            publisher.push_image(image_id, registry, [registry + ":wrong-version"], other_digest)
        missing = subprocess.run(["docker", "buildx", "imagetools", "inspect", registry + ":wrong-version"],
                                 capture_output=True, timeout=60)
        assert missing.returncode != 0
    finally:
        docker("image", "rm", "--force", other_id)


def test_failed_secret_scan_never_publishes_a_candidate(registry, image):
    if shutil.which("trivy") is None:
        pytest.skip("Trivy is required for the secret scanner integration")
    image_id, directory = image
    # Generate a synthetic matching credential at runtime, so no fixture credential is committed.
    (directory / "payload").write_text("AWS_ACCESS_KEY_ID=AKIA" + secrets.token_hex(8).upper() + "\n"
                                      "AWS_SECRET_ACCESS_KEY=" + secrets.token_urlsafe(30) + "\n", encoding="utf-8")
    tag = "release-secret-" + secrets.token_hex(6) + ":test"
    docker("build", "--platform", "linux/amd64", "--tag", tag, str(directory))
    secret_id = docker("image", "inspect", tag, "--format", "{{.Id}}")
    report = directory / "scan.json"
    try:
        scan = subprocess.run(["trivy", "image", "--scanners", "secret", "--exit-code", "1",
                               "--format", "json", "--output", str(report), secret_id],
                              capture_output=True, text=True, timeout=180)
        if scan.returncode == 0:
            publisher.push_image(secret_id, registry, [registry + ":blocked"])
        assert scan.returncode == 1, "The synthetic secret must be rejected by the scanner."
        findings = json.loads(report.read_text(encoding="utf-8"))
        assert any(result.get("Secrets") for result in findings.get("Results", []))
        missing = subprocess.run(["docker", "buildx", "imagetools", "inspect", registry + ":blocked"],
                                 capture_output=True, timeout=60)
        assert missing.returncode != 0
    finally:
        docker("image", "rm", "--force", secret_id)
