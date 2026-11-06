from hashlib import sha256
from importlib.resources import files

import pytest
import yaml


@pytest.mark.parametrize(
    "name,repository,revision,path,skill_sha256,notice,notice_sha256",
    [
        ("ai-factory-skill-vetting", "alebgl77/claude-inc", "3199e383aefa5f8364f3f7389d45e8c5b90b93ab",
         "skills/skill-vetting/SKILL.md", "d24f329d5f22185b8be336e7d6f74d58457cfa74164e8967e18c4f80a547c432",
         "Copyright (c) 2026 Alexandre Beguel",
         "a5cdcf59b39585adabc47540d182582d10baaddbd9c26e311b31cc48b46c2a67"),
        ("ai-factory-agent-security-review", "affaan-m/ECC", "b38be6a7f6350964d59328b52f0624e1d1d552d1",
         "skills/agent-security-hardening/SKILL.md", "0e0895ada90aa8fa085e4257961ebe15f2dff43b843785b1533d4c731186ad81",
         "Copyright (c) 2026 Affaan Mustafa",
         "326146379f01bb137c0a5d3c54770c1aa31076705c8b88a7f6b26a460f6221b2"),
        ("ai-factory-observability-review", "alebgl77/grafana-llmops-forge", "491fcac4f43318e6eda16d8496912ea441bf888b",
         "SKILL.md", "5e84847d5709581147063ca403f6510b6b3694700b125dac0aca8c39810297f8",
         "Copyright (c) 2026 grafana-llmops-forge contributors",
         "1251c20d7acb13d4fa6b525ce0d62c88a256278bf897ea2f5e0a1c82233d1079"),
    ],
    ids=["skill-vetting", "agent-security-review", "observability-review"],
)
def test_reviewed_analyst_skill_resources(name, repository, revision, path, skill_sha256, notice, notice_sha256):
    directory = files("ai_factory_workbench").joinpath("agent_skills", name)
    assert {entry.name for entry in directory.iterdir()} == {"SKILL.md", "LICENSE.upstream"}

    skill = directory.joinpath("SKILL.md").read_text(encoding="utf-8")
    start, frontmatter, instructions = skill.split("---\n", 2)
    assert not start.strip() and instructions.strip()
    metadata = yaml.safe_load(frontmatter)
    assert metadata["name"] == name
    assert metadata["description"].strip()
    assert metadata["license"] == "MIT"
    assert "allowed-tools" not in metadata
    assert metadata["metadata"] == {
        "upstream-repository": repository,
        "upstream-revision": revision,
        "upstream-path": path,
        "upstream-sha256": skill_sha256,
        "reviewed-scope": "read-only",
    }

    license_bytes = directory.joinpath("LICENSE.upstream").read_bytes()
    assert sha256(license_bytes).hexdigest() == notice_sha256
    license_text = license_bytes.decode("utf-8")
    assert notice in license_text
    assert "Permission is hereby granted" in license_text
