# Kubernetes operations

**English** · [Français](fr/KUBERNETES_OPERATIONS.md) · [Installation](KUBERNETES.md)

## Factory state and recovery

The Factory PVC holds the API state directory, including SQLite databases and the
hash-chained `audit.jsonl`. Keep a versioned copy of the Helm values and the image
references used to install the release. Back up the data volume on a schedule
that matches your recovery point objective. Store encrypted copies in a separate
failure domain. Back up the external secrets or their source separately and
restrict access to the operator group.

For a cold backup, suspend any controller that would restart the workload, scale
the Factory Deployment to zero and wait for its Pod to terminate. Take a CSI
snapshot or use your storage provider's backup mechanism while the claim is
quiescent, then resume the workload. For a file-level backup, archive all
children of the state directory together, including dotfiles. Omit the volume
mount root from the archive. A live copy of individual SQLite files can miss
transactions or disagree with the associated audit records.

Restore to a new claim in an isolated namespace. Set `persistence.existingClaim`
to the restored claim and start with the chart and image versions recorded at
backup time. For a file archive, preserve the destination volume's ownership
and access settings when extracting, for example with GNU tar's
`--no-same-owner --no-same-permissions` as the workload UID. Check the Factory
health endpoint, read a known item from the
restored state and, if present, run
`python -m app.audit verify /data/audit.jsonl` in the API container. Record the
restore duration and data loss window, then complete any application upgrade
before switching traffic. Restore and verify external systems with their own
procedures.

### Verify recovered Portfolio decisions

For a private release using `api.workbenchMode: portfolio`, seal the Workbench
journal from a read-only copy of the quiescent source volume at the backup point.
Run the following in a separate verification environment with the same API version
installed. Set `BACKUP_STATE` to the source copy, `RESTORED_STATE` to a consistent
read-only copy of the restored volume, and `WORKBENCH_STREAM` to the existing
stream name. Set `CYBER_KEY_FILE`, `CYBER_KEY_ID` and `CHECKPOINT` to the
restricted HMAC key file, its ID and a new checkpoint path. Keep the checkpoint
and key in independently administered storage, separate from the backups and
the API Pod. For later seals, set `PREVIOUS_CHECKPOINT` to the last trusted
checkpoint for the same stream and key. Follow the key rotation procedure if
the key changed. [Cyber journal and key rotation](CYBER_SECURITY.md#seal-and-verify-a-journal)

```bash
previous=()
if [[ -n ${PREVIOUS_CHECKPOINT:-} ]]; then
  previous=(--previous "$PREVIOUS_CHECKPOINT")
fi
python -m app.cyber seal --kind workbench \
  --path "$BACKUP_STATE/workbench-portfolio.sqlite3" --stream "$WORKBENCH_STREAM" \
  --key-file "$CYBER_KEY_FILE" --key-id "$CYBER_KEY_ID" --output "$CHECKPOINT" "${previous[@]}"
```

Before making any writes to the isolated restore, verify it against that
independently retained checkpoint. Never generate a fresh checkpoint from the
restored database for this check.

```bash
python -m app.cyber verify --kind workbench \
  --path "$RESTORED_STATE/workbench-portfolio.sqlite3" --stream "$WORKBENCH_STREAM" \
  --key-file "$CYBER_KEY_FILE" --checkpoint "$CHECKPOINT"
```

Require `status: verified` and `unsigned_tail: 0` for this matched cold backup.
Record the verified sequence and head hash. Investigate any failure or
unexpected tail before switching traffic.

In a recovery rehearsal, leave a synthetic sensitive task awaiting review at
the backup point. On the isolated restore, use an independent authorized reviewer
to approve the same case in Decision Companion. Confirm that its Portfolio task
becomes completed, that its history records the Workbench approval and that an
account from an unrelated BU cannot read the task details. Use synthetic data and
leave external execution connectors disabled for the rehearsal.
[Portfolio roles and review flow](PORTFOLIO.md#workbench-decisions)

## External telemetry and object storage

The chart consumes operator endpoints for Prometheus, Grafana, Langfuse,
LiteLLM and Qdrant when configured. Provision, update and back up these services
independently. For a Langfuse recovery, verify that its database, ClickHouse data,
object storage and credentials belong to the same deployment and recovery point.
Use the retention and data residency policies of the destination environments
for metrics, traces and logs.

An operator running SeaweedFS for Langfuse should keep its data on persistent
storage and its generated S3 identity under a private ephemeral
`/run/seaweedfs` volume writable by UID 1000. The Compose wrapper rebuilds the
identity on startup and replaces the prior file atomically, including when an
older start left the directory at mode `0500`. Provide both S3 credentials from
an existing Secret if deploying an equivalent wrapper in Kubernetes. Rotate
the credentials in coordination with the Langfuse clients, restart the affected
workloads and verify an authenticated write and read in the intended bucket.

## Send pod logs to Loki

Set `logging.enabled: true` in a dedicated namespace to deploy Alloy. Start from
[the logging values example](../deploy/helm/ai-factory/values-logging.example.yaml).
Create the referenced Secret in the release namespace using your secret
management workflow. Its defaults are `username` and `password`. Set
`logging.loki.caKey` to a PEM CA bundle key for a private CA or leave it empty
for the image trust store. Configure the HTTPS push endpoint and, if the
operator endpoint requires it, `logging.loki.tenantId`. Roll the Alloy Deployment
after changing the Secret so its environment and mounts reflect the new values.

Provide the specific TCP CIDR and port pairs used for the Kubernetes API and the
Loki endpoint. The Service virtual IP and port can differ from the endpoints
that the CNI sees after destination NAT. Validate both the authorized paths and
denied paths on the target CNI. For DNS, set the resolver namespace and Pod
labels, or set `logging.egress.dnsCIDRs` to explicit resolver addresses such as
the NodeLocal DNS IPs. Alloy has no ingress allowance and its status endpoint
listens on loopback. The collector's policy is independent of the Factory Pod
policy. In demo, logging is a separate opt-in egress exception for Alloy.
[Kubernetes NetworkPolicy](https://kubernetes.io/docs/concepts/services-networking/network-policies/)

Alloy watches the Factory Pod labels for this Helm release and tails via
`pods/log`. The Role grants Pod metadata and log reads within the namespace.
Kubernetes RBAC does not enforce a Pod label selector on these reads, so a
compromised Alloy token can read other Pod logs in that namespace. Keep the
namespace dedicated to this release and the same trust boundary. Verify
`kubectl auth can-i get pods --subresource=log -n "$NS" --as="system:serviceaccount:$NS:$ALLOY"`
returns `yes` and that the same checks for Secrets or Pod logs in another
namespace return `no`.
[Alloy Kubernetes log source](https://grafana.com/docs/alloy/latest/reference/components/loki/loki.source.kubernetes/)

The `service` label is the container name and `project` is the Helm release name.
Alloy's Kubernetes source reads the combined Pod log response, which carries no
stdout/stderr provenance for a `stream` label. The pipeline masks the same
recognizable token patterns as the Compose pipeline and drops lines larger than
128 KiB. Treat the pipeline as a second layer behind application-side scrubbing.
The position store is ephemeral and delivery can duplicate or lose lines across
restarts, evictions or a prolonged sink outage. Configure retention, access,
alerting and durable forwarding to a SIEM at the operator destination. Ship
audit files separately through the approved audit export or backup path.

### Verify delivery and redaction

Use a disposable namespace and a scratch tenant or Loki endpoint. Stage the
chart and install it with your logging overlay and the referenced Secret. Set
`NS` and `RELEASE` to match the installation, and set `ALLOY` to the collector
Deployment name. The default name ends in `-ai-factory-alloy`. Wait for
`kubectl -n "$NS" rollout status "deployment/$ALLOY"` to succeed, then run the
following in Bash. It emits only synthetic data.

```bash
marker="factory-log-probe-$(date +%s)"
for project in "$RELEASE" unrelated-release; do
  kubectl -n "$NS" apply -f - <<EOF
apiVersion: v1
kind: Pod
metadata:
  name: probe-$project
  labels:
    app.kubernetes.io/name: ai-factory
    app.kubernetes.io/instance: $project
    app.kubernetes.io/component: factory
spec:
  restartPolicy: Never
  automountServiceAccountToken: false
  securityContext:
    runAsNonRoot: true
    runAsUser: 65532
    seccompProfile: {type: RuntimeDefault}
  containers:
    - name: probe
      image: busybox:1.37.0
      securityContext:
        allowPrivilegeEscalation: false
        readOnlyRootFilesystem: true
        capabilities: {drop: [ALL]}
      command: [sh, -ec]
      args:
        - echo 'level=INFO $marker release=$project sk-syntheticprobe123'; sleep 120
EOF
done
kubectl -n "$NS" wait --for=condition=Ready --timeout=90s "pod/probe-$RELEASE" pod/probe-unrelated-release
printf '%s\n' "$marker"
```

In Grafana Explore, query
`{factory="ai-factory-reference",service="probe"} |= "<printed marker>"` against
the scratch Loki destination. After the first batch arrives, expect a line with
`project` matching `$RELEASE`, `level="info"` and the synthetic token replaced by
`***`. Verify the unrelated release's Pod produced its line with
`kubectl -n "$NS" logs pod/probe-unrelated-release` and has no result in Loki.
Delete the probe Pods after the check.
