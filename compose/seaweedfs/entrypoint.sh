#!/bin/sh
# SPDX-License-Identifier: MIT
# Renders the S3 identity file of SeaweedFS from the environment (S3_ACCESS_KEY,
# S3_SECRET_KEY, S3_BUCKET), then hands over to the image entrypoint. docker-compose.yml runs
# the container as the seaweed user (uid 1000) with no capability, so neither this script nor
# the image entrypoint has a root step; started as root, the image entrypoint still drops to
# seaweed (su-exec). The rendered file lives in a private tmpfs, never in an image layer or in
# the repository.
set -eu

: "${S3_ACCESS_KEY:?S3_ACCESS_KEY is required}"
: "${S3_SECRET_KEY:?S3_SECRET_KEY is required}"
S3_BUCKET="${S3_BUCKET:-langfuse}"

case "$S3_ACCESS_KEY$S3_SECRET_KEY$S3_BUCKET" in
  *[!A-Za-z0-9_+=.-]*)
    echo "S3_ACCESS_KEY / S3_SECRET_KEY / S3_BUCKET: use only letters, digits and _ + = . -" >&2
    exit 1;;
esac

out_dir=/run/seaweedfs
umask 077
mkdir -p "$out_dir"
# The mount is private to SeaweedFS. Restore owner write access if an earlier
# version left the directory at 0500, then replace the identity file atomically.
chmod 0700 "$out_dir"
temp_file=$(mktemp "$out_dir/.s3.json.XXXXXX")
trap 'rm -f "$temp_file"' EXIT
trap 'exit 1' HUP INT TERM
sed -e "s|\${S3_ACCESS_KEY}|${S3_ACCESS_KEY}|g" \
    -e "s|\${S3_SECRET_KEY}|${S3_SECRET_KEY}|g" \
    -e "s|\${S3_BUCKET}|${S3_BUCKET}|g" \
    /etc/seaweedfs/s3.json.tmpl > "$temp_file"
if [ "$(id -u)" = "0" ]; then
  chown seaweed:seaweed "$out_dir" "$temp_file"
fi
chmod 0400 "$temp_file"
mv -fT "$temp_file" "$out_dir/s3.json"
trap - EXIT HUP INT TERM

# The only identity is scoped to its bucket (Admin:<bucket>, Read:<bucket>...), which lets
# it create that bucket on first write (-s3.autoCreateBucket) and nothing else.
exec /entrypoint.sh server \
  -ip=seaweedfs -ip.bind=0.0.0.0 \
  -master.volumeSizeLimitMB=256 \
  -master.telemetry=false \
  -filer \
  -s3 -s3.port=8333 -s3.config="$out_dir/s3.json" \
  -s3.autoCreateBucket \
  "$@"
