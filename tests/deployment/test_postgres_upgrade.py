# SPDX-License-Identifier: MIT
"""Keep the PostgreSQL upgrade target separate from the existing data volume."""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def render(*overlays):
    command = ["docker", "compose", "--env-file", ".env.example", "-f", "docker-compose.yml"]
    for overlay in overlays:
        command.extend(["-f", overlay])
    return json.loads(subprocess.check_output([*command, "config", "--format", "json"], cwd=ROOT, timeout=60))


@pytest.mark.skipif(shutil.which("docker") is None, reason="Docker Compose is required for config rendering")
def test_postgres18_target_does_not_mount_or_replace_the_postgres17_volume():
    baseline = render()["services"]["postgres"]
    target = render("docker-compose.postgres18.yml")["services"]["postgres"]
    assert baseline["image"] == "postgres:17.11-alpine"
    assert target["image"].startswith("postgres:18.4-alpine@sha256:")
    assert target["environment"]["PGDATA"] == "/var/lib/postgresql/18/docker"
    base_volumes = [volume for volume in baseline["volumes"] if volume["type"] == "volume"]
    target_volumes = [volume for volume in target["volumes"] if volume["type"] == "volume"]
    assert [(item["source"], item["target"]) for item in base_volumes] == [
        ("postgres-data", "/var/lib/postgresql/data")
    ]
    assert [(item["source"], item["target"]) for item in target_volumes] == [
        ("postgres18-data", "/var/lib/postgresql")
    ]
    baseline.pop("image")
    target.pop("image")
    baseline.pop("volumes")
    target.pop("volumes")
    target["environment"].pop("PGDATA")
    assert baseline == target


@pytest.mark.skipif(os.name == "nt" or shutil.which("bash") is None, reason="Linux Bash is required")
@pytest.mark.parametrize("arguments", [[], ["production"], ["factory-ci-postgres18-short", "extra"]])
def test_rehearsal_refuses_an_invalid_project_before_accessing_docker(arguments):
    result = subprocess.run(
        ["bash", "scripts/rehearse_postgres_upgrade.sh", *arguments], cwd=ROOT,
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 2
    assert "Use one unique factory-ci-postgres18-* project name." in result.stderr
