# Deploy AI Factory on Kubernetes

The Helm chart runs the dashboard, API and Caddy in one Pod. The private profile adds oauth2-proxy.
The edge is the sole Service endpoint. The other containers listen on loopback and share the
Pod's trust boundary. Use one release per namespace and one private release per organization.

| Profile | Access | Data and services |
| --- | --- | --- |
| `demo` | Local `kubectl port-forward`, no authentication | Fictional snapshot, demo Workbench and local API state |
| `private` | HTTPS Ingress, OIDC and a required business-unit policy | Private pilot with operator-managed telemetry and model services, enabled individually |

Use the [Compose deployment guide](DEPLOYMENT.md) for the full simulated stack and its bundled
dependencies. Kubernetes operators provide the Ingress controller, identity provider, CSI storage,
network policy enforcement and any external integrations. The single API writer and its PVC
imply planned downtime for a Pod replacement or release upgrade.

## Run a local demo

From a Git checkout, use Docker, Python 3, Helm 3, kubectl and kind. The CI tool versions are
Helm 3.22.1, kind 0.33.0 and Kubernetes 1.37.0. The staging script assembles the chart from the
same tracked configuration and runtime assets as the checkout.

```bash
kind create cluster --name factory-demo \
  --image kindest/node:v1.37.0@sha256:a1ed56cfb0e7b93589bdf97c8cd566405a265939e3620fc4f5de89adff580ae5
docker build -t ai-factory/api:local api/
docker build -t ai-factory/dashboard:local dashboard/
kind load docker-image --name factory-demo ai-factory/api:local ai-factory/dashboard:local

chart_dir="$(mktemp -d)"
python3 scripts/package_helm.py "$chart_dir"
helm lint --strict "$chart_dir/ai-factory"
kubectl create namespace factory-demo
kubectl label namespace factory-demo pod-security.kubernetes.io/enforce=restricted
helm upgrade --install factory "$chart_dir/ai-factory" -n factory-demo --wait
kubectl -n factory-demo port-forward --address 127.0.0.1 svc/factory-ai-factory 8080:8080
```

Open `http://127.0.0.1:8080/`. From another terminal, check `/healthz`, `/api/v1/health` and
`/api/v1/snapshot` on that same origin. Access to port-forward and exec can bypass the normal
Ingress boundary. Limit these Kubernetes permissions to operators trusted with release data.

## Install a private pilot

1. Verify the release bundle and images. If the bundle includes the Helm assets, use
   `deploy/helm/ai-factory-${TAG#v}.tgz` and `deploy/helm/values.release.json`. The values lock
   the API, dashboard, Caddy, oauth2-proxy and optional Alloy to the digests in `release-stack.json`. Use
   Linux amd64 nodes for released application images. For a source build, stage the chart and
   supply compatible digest-pinned image references. [Release verification](DEPLOYMENT.md#vérifier-et-installer-une-release)
2. Create a dedicated namespace with Restricted Pod Security admission and a NetworkPolicy-enforcing
   CNI. Provision a writable, single-writer PVC through the cluster's CSI driver. Test file
   ownership with UID and GID 10001. Confirm the driver supports the selected access mode and
   the filesystem locking and durability required by the API's SQLite and file state.
3. Configure an Ingress controller and DNS entry for the HTTPS origin. Provision the named TLS
   Secret and configure the controller to redirect HTTP to HTTPS before forwarding requests.
   The Ingress TLS section configures termination. The controller must enforce HTTPS access.
   Restrict the edge Service to the approved controller Pods. Retain the canonical Host through
   the controller and forward requests to port 8080 over HTTP. Keep access to the controller and
   backend inside the intended trust boundary.
4. Register an OIDC client with callback `https://<factory-host>/oauth2/callback`. Use an HTTPS
   issuer that both the browser and cluster can reach. Put `client-id`, `client-secret` and
   `cookie-secret` in an existing Secret. Generate a random 32-byte base64url cookie secret.
   If the issuer uses a private CA, put its certificate in an existing Secret under `ca.crt`
   and set `sso.providerCA.existingSecret`.
5. Create a separate Secret with your business-unit policy under `business-units.yaml`. Match
   IdP groups to both `api.roleMap` and the policy. Add the provider's group scope or claim
   mapping when required. Rotate credentials through your secret manager and restart the Pod
   after changing Secrets consumed as environment variables or read at startup.
   [Example policy](../config/business-units.example.yaml) · [Business-unit security](BU_SECURITY.md)

Create an operator values file with real Secret names and narrow selectors for your environment.
The example assumes the issuer is served by an in-cluster endpoint reachable through the same
DNS name from browser and Pod. For an issuer outside the cluster, use the authorized CIDRs or
your network's controlled egress gateway on TCP 443.

```yaml
profile: private
ingress:
  enabled: true
  className: internal
  host: factory.example.org
  tlsSecretName: factory-tls
sso:
  issuerURL: https://login.example.org
  existingSecret: factory-oidc
  scope: openid email profile groups
  allowedEmailDomains: [example.org]
  allowedGroups: [factory-ops, factory-finance]
businessUnits:
  existingSecret: factory-business-units
api:
  roleMap: ["factory-ops:maintainer", "factory-finance:viewer"]
persistence:
  existingClaim: factory-state
networkPolicy:
  ingressPeers:
    - namespaceSelector:
        matchLabels: {kubernetes.io/metadata.name: ingress-system}
      podSelector:
        matchLabels: {app.kubernetes.io/name: ingress-controller}
  dnsPeers:
    - namespaceSelector:
        matchLabels: {kubernetes.io/metadata.name: kube-system}
      podSelector:
        matchLabels: {k8s-app: kube-dns}
  egressRules:
    - to:
        - namespaceSelector:
            matchLabels: {kubernetes.io/metadata.name: identity}
          podSelector:
            matchLabels: {app.kubernetes.io/name: oidc}
      ports: [{protocol: TCP, port: 443}]
```

```bash
helm upgrade --install factory "deploy/helm/ai-factory-${TAG#v}.tgz" -n factory \
  -f deploy/helm/values.release.json -f /secure/factory-private.yaml --wait --timeout 5m
kubectl -n factory rollout status deploy/factory-ai-factory
```

Verify anonymous API denial, a real user login and cross-BU read and write denial with synthetic
accounts on the target Ingress. Qualify the controller's TLS policy, client IP handling, request
limits and network selectors there. Verify the approved controller can reach the Service and that
an unauthorized Pod cannot. Use the `telemetry` values and destination-specific egress rules for
externally managed services. The API omits telemetry data for unavailable sources and the private
profile uses live data mode. Qualify each enabled source and check its credentials, HTTPS trust,
network path and operational status before relying on its data.

If an API telemetry source uses a private CA, set `telemetry.caBundle.existingSecret` and include
the complete PEM trust bundle under `ca-bundle.crt`, including public roots for other sources
that require them. Restart the Pod after rotation. The optional Portfolio runner expects
`https://litellm:<port>` with a certificate valid for the `litellm` Service name and an operator
Secret referenced by `api.portfolioRunner.existingSecret`.

## Preserve state through upgrades and reinstalls

Record the Helm values, source release and name of the claim mounted at `/data`. Stop writes and
back up the complete directory before changing versions. Preserve the hidden files, state,
audit trail and SQLite files together. Test restoration to a fresh claim before switching the
Deployment. Use the same profile for a mounted claim. A profile change requires a separate
deployment and an explicit data migration. [Backup, restore and Alloy logging](KUBERNETES_OPERATIONS.md)

A chart-created claim has the annotation `helm.sh/resource-policy: keep`. Before a planned
uninstall, record its name with `kubectl -n factory get pvc -l app.kubernetes.io/instance=factory`
and take a verified backup. To reinstall using that same claim, confirm that the old Pod is gone,
the PVC is still Bound, and set `persistence.existingClaim` to its exact name. This makes the new
release consume the retained claim without creating or managing it. For an operator-provisioned
claim, always set `persistence.existingClaim`. Keep the namespace and review the backing PV's
reclaim policy. Deleting the namespace deletes its PVC objects regardless of Helm annotations.
Roll back application code against existing state only after verifying format compatibility. Use
the validated restore point on a separate claim when compatibility is uncertain.

## What CI exercises

The `Kubernetes (Helm and kind)` job stages and packages the chart, runs strict Helm lint and
negative render tests, and runs server-side schema validation in a Restricted namespace. The
job builds the source API and dashboard images, checks dashboard assets and API health and
snapshot through the edge, then verifies a state write and audit chain across an API container
restart, a replacement Pod and restore to a second PVC.

In the private profile the job runs OAuth2 authorization code with PKCE against a TLS-enabled
Dex fixture, checks the group gate and business-unit isolation, and attempts forged identity and
forwarding headers before and after sign-in. It checks the API listener from within the Pod
namespace and exercises NetworkPolicy with an allowed test TLS proxy and a denied client Pod.
The test TLS proxy represents an Ingress data path. Use your target controller and storage driver
for the pilot's qualification.

The job also exercises optional Alloy delivery to a disposable TLS and Basic Auth Loki endpoint.
It checks release-scoped collection, log labels, redaction and the collector's Kubernetes permissions.
