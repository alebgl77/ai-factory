#!/bin/sh
# SPDX-License-Identifier: MIT
# Renders the S3 identity file of SeaweedFS from the environment (S3_ACCESS_KEY,
# S3_SECRET_KEY, S3_BUCKET), then hands over to the image entrypoint, which drops root privileges
# (su-exec seaweed). The rendered file lives in a private tmpfs, never in an image layer
# or in the repository.
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
mkdir -p "$out_dir"
sed -e "s|\${S3_ACCESS_KEY}|${S3_ACCESS_KEY}|g" \
    -e "s|\${S3_SECRET_KEY}|${S3_SECRET_KEY}|g" \
    -e "s|\${S3_BUCKET}|${S3_BUCKET}|g" \
    /etc/seaweedfs/s3.json.tmpl > "$out_dir/s3.json"
chown -R seaweed:seaweed "$out_dir"
chmod 0500 "$out_dir"
chmod 0400 "$out_dir/s3.json"

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
