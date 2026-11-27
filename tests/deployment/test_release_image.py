# SPDX-License-Identifier: MIT
"""A release must publish the scanned object and promote only its verified digest."""

import importlib.util
import json
from pathlib import Path
import subprocess

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("push_scanned_image", ROOT / ".github/scripts/push_scanned_image.py")
publisher = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(publisher)
INDEX_ID = "sha256:" + "1" * 64
MANIFEST_ID = "sha256:" + "2" * 64
CONFIG_ID = "sha256:" + "3" * 64
IMAGE_ID = CONFIG_ID
DIGEST = MANIFEST_ID
OTHER_DIGEST = "sha256:" + "4" * 64
REPOSITORY = "ghcr.io/example/api"


@pytest.fixture
def registry(monkeypatch):
    state = {"calls": [], "config": IMAGE_ID, "digest": DIGEST, "local": IMAGE_ID,
             "manifest_type": "application/vnd.oci.image.manifest.v1+json", "platform": {"Id": IMAGE_ID}}

    def docker(*args):
        state["calls"].append(args)
        if args[:2] == ("image", "inspect"):
            if "--platform" in args:
                return json.dumps(state["platform"])
            return state["local"]
        if args[:2] == ("image", "push") and state.get("push_failure"):
            raise subprocess.CalledProcessError(1, ["docker", *args])
        if args[:3] == ("buildx", "imagetools", "inspect"):
            if args[-1] == "--raw":
                if state.get("missing"):
                    raise subprocess.CalledProcessError(1, ["docker", *args])
                return json.dumps({"schemaVersion": 2, "mediaType": state["manifest_type"],
                                   "config": {"digest": state["config"]}})
            return json.dumps({"digest": state["digest"]})
        return ""

    monkeypatch.setattr(publisher, "docker", docker)
    return state


def test_stage_and_promote_retain_the_scanned_image_id_and_verified_digest(registry):
    assert publisher.push_image(IMAGE_ID, REPOSITORY, [REPOSITORY + ":candidate-1"]) == DIGEST
    assert publisher.push_image(IMAGE_ID, REPOSITORY,
                                [REPOSITORY + ":1.2.3", REPOSITORY + ":1.2"], DIGEST) == DIGEST
    tags = [call for call in registry["calls"] if call[:2] == ("image", "tag")]
    assert tags == [("image", "tag", IMAGE_ID, REPOSITORY + ":candidate-1")]
    manifests = [call[3] for call in registry["calls"] if call[-1] == "--raw"]
    assert manifests == [REPOSITORY + "@" + DIGEST] * 4
    pushes = [call for call in registry["calls"] if call[:2] == ("image", "push")]
    assert all(call[2:4] == ("--platform", "linux/amd64") for call in pushes)
    copies = [call for call in registry["calls"] if call[:3] == ("buildx", "imagetools", "create")]
    assert copies == [("buildx", "imagetools", "create", "--prefer-index=false", "--tag", tag,
                       REPOSITORY + "@" + DIGEST) for tag in (REPOSITORY + ":1.2.3", REPOSITORY + ":1.2")]


def test_local_identity_mismatch_stops_before_any_push(registry):
    registry["local"] = OTHER_DIGEST
    with pytest.raises(ValueError, match="local image differs"):
        publisher.push_image(IMAGE_ID, REPOSITORY, [REPOSITORY + ":candidate-1"])
    assert registry["calls"] == [("image", "inspect", IMAGE_ID, "--format", "{{.Id}}")]


def test_containerd_index_handle_can_publish_its_distinct_platform_manifest_and_config(registry):
    registry.update(local=INDEX_ID, config=CONFIG_ID, digest=MANIFEST_ID,
                    platform={"Id": MANIFEST_ID, "Descriptor": {
                        "mediaType": "application/vnd.oci.image.manifest.v1+json", "digest": MANIFEST_ID}})
    assert publisher.push_image(INDEX_ID, REPOSITORY, [REPOSITORY + ":candidate-1"]) == MANIFEST_ID
    assert publisher.push_image(INDEX_ID, REPOSITORY, [REPOSITORY + ":1.2.3"], MANIFEST_ID) == MANIFEST_ID
    assert ("image", "tag", INDEX_ID, REPOSITORY + ":candidate-1") in registry["calls"]


@pytest.mark.parametrize("platform,config", [
    ({"Id": OTHER_DIGEST, "Descriptor": {"mediaType": "application/vnd.oci.image.manifest.v1+json",
                                        "digest": OTHER_DIGEST}}, CONFIG_ID),
    ({"Id": MANIFEST_ID, "Descriptor": None}, CONFIG_ID),
    ({"Id": MANIFEST_ID, "Descriptor": {"mediaType": "application/vnd.oci.image.index.v1+json",
                                        "digest": MANIFEST_ID}}, CONFIG_ID),
    ({"Id": MANIFEST_ID, "Descriptor": {"mediaType": "application/vnd.oci.image.manifest.v1+json",
                                        "digest": "sha256:invalid"}}, CONFIG_ID),
    ({"Id": OTHER_DIGEST, "Descriptor": {"mediaType": "application/vnd.oci.image.manifest.v1+json",
                                        "digest": MANIFEST_ID}}, CONFIG_ID),
    ({"Id": MANIFEST_ID, "Descriptor": {"mediaType": "application/vnd.oci.image.manifest.v1+json",
                                        "digest": MANIFEST_ID}}, "sha256:invalid"),
    ([], CONFIG_ID),
])
def test_containerd_wrong_or_malformed_platform_never_mutates_version_tags(registry, platform, config):
    registry.update(local=INDEX_ID, config=config, platform=platform)
    with pytest.raises(ValueError):
        publisher.push_image(INDEX_ID, REPOSITORY, [REPOSITORY + ":1.2.3"], MANIFEST_ID)
    assert not any(call[:2] in (("image", "tag"), ("image", "push")) or
                   call[:3] == ("buildx", "imagetools", "create") for call in registry["calls"])


@pytest.mark.parametrize("changed", [
    {"config": OTHER_DIGEST},
    {"manifest_type": "application/vnd.oci.image.index.v1+json"},
    {"digest": "sha256:invalid"},
])
def test_remote_identity_or_descriptor_mismatch_stops_promotion(registry, changed):
    registry.update(changed)
    with pytest.raises(ValueError):
        publisher.push_image(IMAGE_ID, REPOSITORY,
                             [REPOSITORY + ":candidate-1", REPOSITORY + ":1.2.3"])
    assert len([call for call in registry["calls"] if call[:2] == ("image", "push")]) == 1


def test_promoting_a_different_manifest_digest_fails_even_with_same_config(registry):
    with pytest.raises(ValueError, match="digest changed"):
        publisher.push_image(IMAGE_ID, REPOSITORY, [REPOSITORY + ":1.2.3"], OTHER_DIGEST)


@pytest.mark.parametrize("changed,error", [
    ({"missing": True}, subprocess.CalledProcessError),
    ({"config": OTHER_DIGEST}, ValueError),
    ({"manifest_type": "application/vnd.oci.image.index.v1+json"}, ValueError),
])
def test_invalid_immutable_source_never_mutates_a_version_tag(registry, changed, error):
    registry.update(changed)
    with pytest.raises(error):
        publisher.push_image(IMAGE_ID, REPOSITORY, [REPOSITORY + ":1.2.3", REPOSITORY + ":1.2"], DIGEST)
    assert not any(call[:2] in (("image", "tag"), ("image", "push")) or
                   call[:3] == ("buildx", "imagetools", "create") for call in registry["calls"])


def test_push_failure_stops_before_remote_lookup_or_later_tags(registry):
    registry["push_failure"] = True
    with pytest.raises(subprocess.CalledProcessError):
        publisher.push_image(IMAGE_ID, REPOSITORY, [REPOSITORY + ":candidate-1", REPOSITORY + ":1.2.3"])
    assert not any(call[:2] == ("buildx", "imagetools") for call in registry["calls"])


@pytest.mark.parametrize("image_id,tags,expected", [
    ("latest", [REPOSITORY + ":1.2.3"], None),
    (IMAGE_ID, [], None),
    (IMAGE_ID, ["ghcr.io/unrelated/api:1.2.3"], None),
    (IMAGE_ID, [REPOSITORY + ":bad tag"], None),
    (IMAGE_ID, [REPOSITORY + ":1.2.3"], "latest"),
])
def test_invalid_or_unrelated_references_never_reach_docker(registry, image_id, tags, expected):
    with pytest.raises(ValueError):
        publisher.push_image(image_id, REPOSITORY, tags, expected)
    assert not registry["calls"]


def test_release_has_one_build_and_failure_gates_before_push_and_promotion():
    workflow = yaml.safe_load((ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8"))
    steps = workflow["jobs"]["images"]["steps"]
    builds = [i for i, step in enumerate(steps) if step.get("uses", "").startswith("docker/build-push-action@")]
    gates = [i for i, step in enumerate(steps) if step.get("uses", "").startswith("aquasecurity/trivy-action@")]
    push = next(i for i, step in enumerate(steps) if step.get("id") == "push")
    promote = next(i for i, step in enumerate(steps) if step.get("name") == "Promote the verified digest to version tags")
    verify = next(i for i, step in enumerate(steps) if step.get("name") == "Verify signature, provenance and SBOM attestations")
    assert len(builds) == 1 and len(gates) == 2
    assert builds[0] < gates[0] < push < gates[1] < verify < promote
    assert all(not step.get("continue-on-error") and "if" not in step for step in
               [steps[push], steps[promote], *(steps[i] for i in gates)])
    assert steps[gates[0]]["with"]["image-ref"] == "${{ steps.candidate.outputs.image_id }}"
    assert steps[gates[1]]["with"]["image-ref"] == "${{ env.IMAGE }}@${{ steps.push.outputs.digest }}"
    assert steps[gates[1]]["env"]["TRIVY_IMAGE_SRC"] == "remote"
    assert steps[builds[0]]["with"]["labels"] == "${{ steps.meta.outputs.labels }}"
