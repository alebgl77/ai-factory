# SPDX-License-Identifier: MIT
"""Exercise the SeaweedFS wrapper on the pinned Linux image with a retained tmpfs."""

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]


def fixture_file(directory, name, content):
    path = directory / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="\n")
    path.chmod(0o755)
    return path


@pytest.mark.parametrize("uid", [1000, 0], ids=["compose-nonroot", "image-root-fallback"])
def test_restarts_rotate_identity_without_leaving_partial_credentials(tmp_path, uid):
    docker = shutil.which("docker")
    if docker is None or subprocess.run([docker, "info"], capture_output=True, timeout=20).returncode:
        pytest.skip("Docker daemon unavailable")
    image = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))["services"]["seaweedfs"]["image"]
    compose_dir = ROOT / "compose/seaweedfs"
    script = fixture_file(tmp_path, "factory-entrypoint.sh", (compose_dir / "entrypoint.sh").read_text(encoding="utf-8"))
    stub = fixture_file(
        tmp_path,
        "image-entrypoint.sh",
        '#!/bin/sh\nset -eu\ntest "$1" = server\n'
        'case " $* " in *" -s3.config=/run/seaweedfs/s3.json "*) ;; *) exit 1 ;; esac\n'
        'test -r /run/seaweedfs/s3.json\n',
    )
    fixture_file(tmp_path, "fail-sed/sed", '#!/bin/sh\nprintf "partial fixture output"\nexit 1\n')
    fixture_file(tmp_path, "fail-mv/mv", "#!/bin/sh\nexit 1\n")
    fixture_file(
        tmp_path,
        "exercise.sh",
        r"""#!/bin/sh
set -eu
dir=/run/seaweedfs
file="$dir/s3.json"

start() {
  /bin/sh /etc/seaweedfs/factory-entrypoint.sh
}

assert_permissions() {
  test "$(stat -c '%a:%u:%g' "$dir")" = 700:1000:1000
  test "$(stat -c '%a:%u:%g' "$file")" = 400:1000:1000
  test "$(ls -A "$dir")" = s3.json
}

export S3_ACCESS_KEY=first_access S3_SECRET_KEY=first_secret
unset S3_BUCKET
start
assert_permissions
grep -Fq '"secretKey": "first_secret"' "$file"
grep -Fq '"Write:langfuse"' "$file"
first_inode=$(stat -c '%i' "$file")

# The older entrypoint left an owner-only but non-writable directory behind.
chmod 0500 "$dir"
export S3_ACCESS_KEY=second_access S3_SECRET_KEY=second+secret= S3_BUCKET=second.bucket
start
assert_permissions
test "$(stat -c '%i' "$file")" != "$first_inode"
grep -Fq '"accessKey": "second_access"' "$file"
grep -Fq '"secretKey": "second+secret="' "$file"
grep -Fq '"Write:second.bucket"' "$file"
if grep -Fq 'first_secret' "$file"; then
  exit 1
fi
expected=$(sha256sum "$file")

if S3_SECRET_KEY='invalid&value' start 2>/dev/null; then
  exit 1
fi
for fail_step in sed mv; do
  if PATH="/fixtures/fail-$fail_step:$PATH" S3_SECRET_KEY=third_secret start 2>/dev/null; then
    exit 1
  fi
  assert_permissions
  test "$(sha256sum "$file")" = "$expected"
done
""",
    )
    capabilities = ["--cap-add", "CHOWN", "--cap-add", "DAC_OVERRIDE", "--cap-add", "FOWNER"] if uid == 0 else []
    result = subprocess.run(
        [
            docker, "run", "--rm", "--network", "none", "--read-only",
            "--user", f"{uid}:{uid}", "--cap-drop", "ALL", *capabilities,
            "--security-opt", "no-new-privileges",
            "--tmpfs", "/run/seaweedfs:size=1m,uid=1000,gid=1000,mode=0700",
            "-v", f"{tmp_path}:/fixtures:ro",
            "-v", f"{script}:/etc/seaweedfs/factory-entrypoint.sh:ro",
            "-v", f"{compose_dir / 's3.json'}:/etc/seaweedfs/s3.json.tmpl:ro",
            "-v", f"{stub}:/entrypoint.sh:ro",
            "--entrypoint", "/bin/sh", image, "/fixtures/exercise.sh",
        ],
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert result.returncode == 0, result.stderr
