# SPDX-License-Identifier: MIT
"""Protect the paired Langfuse versions and the default Compose deployment."""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
OVERLAY = "docker-compose.langfuse-v4.yml"
LANGFUSE_IMAGES = {"langfuse-web": "langfuse/langfuse", "langfuse-worker": "langfuse/langfuse-worker"}
TRANSITION = {
    "LANGFUSE_MIGRATION_V4_WRITE_MODE": "legacy",
    "LANGFUSE_MIGRATION_V4_NATIVE_OTEL_BEHAVIOUR": "dual_write",
    "LANGFUSE_MIGRATION_V4_ALLOW_PREVIEW_OPT_IN": "false",
    "LANGFUSE_BACKGROUND_MIGRATION_V4_ENABLE_HISTORIC_BACKFILL": "false",
}


def render(*files):
    command = ["docker", "compose", "--env-file", ".env.example", "--profile", "*", "-f", "docker-compose.yml"]
    for file in files:
        command.extend(["-f", file])
    return json.loads(subprocess.check_output([*command, "config", "--format", "json"], cwd=ROOT, timeout=60))


@pytest.mark.skipif(shutil.which("docker") is None, reason="Docker Compose is required for config rendering")
def test_langfuse_v4_changes_only_the_pair_and_legacy_transition():
    default, pilot = render(), render(OVERLAY)
    assert set(default["services"]) == set(pilot["services"])
    default_tags, pilot_tags = set(), set()
    for service, repository in LANGFUSE_IMAGES.items():
        original = default["services"].pop(service)
        changed = pilot["services"].pop(service)
        base_ref = re.fullmatch(rf"{repository}:(3\.\d+\.\d+)(?:@sha256:[a-f0-9]{{64}})?", original.pop("image"))
        opt_in_ref = re.fullmatch(rf"{repository}:(4\.\d+\.\d+)@sha256:[a-f0-9]{{64}}", changed.pop("image"))
        assert base_ref and opt_in_ref
        default_tags.add(base_ref[1])
        pilot_tags.add(opt_in_ref[1])
        assert not TRANSITION.keys() & original["environment"].keys()
        assert changed.pop("environment") == original.pop("environment") | TRANSITION
        assert changed == original

    assert len(default_tags) == len(pilot_tags) == 1
    assert pilot == default
