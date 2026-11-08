{{- define "ai-factory.fullname" -}}
{{- default (printf "%s-%s" .Release.Name .Chart.Name) .Values.fullnameOverride | trunc 56 | trimSuffix "-" -}}
{{- end }}

{{- define "ai-factory.selectorLabels" -}}
app.kubernetes.io/name: {{ .Chart.Name }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/component: factory
{{- end }}

{{- define "ai-factory.labels" -}}
{{ include "ai-factory.selectorLabels" . }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{- define "ai-factory.image" -}}
{{- if .digest -}}
{{- printf "%s@%s" .repository .digest -}}
{{- else -}}
{{- printf "%s:%s" .repository (required "image tag or digest is required" .tag) -}}
{{- end -}}
{{- end }}

{{- define "ai-factory.containerSecurity" -}}
runAsNonRoot: true
runAsUser: {{ . }}
runAsGroup: {{ . }}
allowPrivilegeEscalation: false
readOnlyRootFilesystem: true
capabilities:
  drop: ["ALL"]
{{- end }}

{{- define "ai-factory.secretEnv" -}}
- name: {{ .env }}
  valueFrom:
    secretKeyRef:
      name: {{ required (printf "existing Secret required for %s" .env) .secret | quote }}
      key: {{ required (printf "existing Secret key required for %s" .env) .key | quote }}
{{- end }}

{{- define "ai-factory.fileSecret" -}}
- name: {{ .volume }}
  secret:
    secretName: {{ required (printf "existing Secret required for %s" .volume) .secret | quote }}
    defaultMode: 0440
    items:
      - key: {{ required (printf "existing Secret key required for %s" .volume) .key | quote }}
        path: {{ .path }}
{{- end }}

{{- define "ai-factory.peers" -}}
{{- range . -}}
  {{- if or (empty .) (and (empty .ipBlock) (empty .namespaceSelector) (empty .podSelector)) -}}
    {{- fail "networkPolicy peers must specify a nonempty namespace selector, pod selector or IP block" -}}
  {{- end -}}
  {{- if and .ipBlock (or (empty .ipBlock.cidr) (hasSuffix "/0" .ipBlock.cidr)) -}}
    {{- fail "networkPolicy IP blocks must specify a CIDR narrower than /0" -}}
  {{- end -}}
{{- end -}}
{{- end }}

{{- define "ai-factory.workbenchMode" -}}
{{- default (ternary "off" "demo" (eq .Values.profile "private")) .Values.api.workbenchMode -}}
{{- end }}

{{- define "ai-factory.validate" -}}
{{- $v := .Values -}}
{{- if not (has $v.profile (list "demo" "private")) -}}
  {{- fail "profile must be demo or private" -}}
{{- end -}}
{{- if not (has $v.persistence.accessMode (list "ReadWriteOnce" "ReadWriteOncePod")) -}}
  {{- fail "persistence.accessMode must be ReadWriteOnce or ReadWriteOncePod" -}}
{{- end -}}
{{- range $name, $image := $v.images -}}
  {{- if and $image.digest (not (regexMatch "^sha256:[a-f0-9]{64}$" $image.digest)) -}}
    {{- fail (printf "images.%s.digest must be a SHA-256 OCI digest" $name) -}}
  {{- end -}}
{{- end -}}
{{- $workbench := include "ai-factory.workbenchMode" . -}}
{{- with $v.api.defaultRole -}}
  {{- if ne . "viewer" -}}{{- fail "api.defaultRole must be empty or viewer; map privileged roles to explicit groups" -}}{{- end -}}
{{- end -}}
{{- range $v.api.roleMap -}}
  {{- if not (regexMatch "^[^,]+:(viewer|editor|maintainer|approver|admin)$" .) -}}
    {{- fail "api.roleMap entries must be group:role mappings" -}}
  {{- end -}}
{{- end -}}
{{- if eq $v.profile "demo" -}}
  {{- if or $v.ingress.enabled $v.networkPolicy.ingressPeers $v.networkPolicy.dnsPeers $v.networkPolicy.egressRules -}}
    {{- fail "demo is isolated and reachable only with kubectl port-forward; ingress and network policy rules must be empty" -}}
  {{- end -}}
  {{- if or $v.businessUnits.existingSecret $v.sso.existingSecret $v.api.assistEnabled $v.api.portfolioRunner.existingSecret -}}
    {{- fail "demo cannot use private runtime settings" -}}
  {{- end -}}
  {{- if or $v.telemetry.prometheusURL $v.telemetry.grafanaURL $v.telemetry.grafanaPublicURL $v.telemetry.langfuseURL $v.telemetry.litellmURL $v.telemetry.qdrantURL -}}
    {{- fail "demo cannot connect to external telemetry or model services" -}}
  {{- end -}}
  {{- if not (has $workbench (list "off" "demo")) -}}
    {{- fail "demo supports api.workbenchMode off or demo" -}}
  {{- end -}}
{{- else -}}
  {{- if not $v.allowMutableImagesForLocalTesting -}}
    {{- range $name, $image := $v.images -}}
      {{- if not $image.digest -}}
        {{- fail (printf "private requires images.%s.digest; allowMutableImagesForLocalTesting is only for local test clusters" $name) -}}
      {{- end -}}
    {{- end -}}
  {{- end -}}
  {{- if not (and $v.ingress.enabled $v.ingress.className $v.ingress.tlsSecretName) -}}
    {{- fail "private requires ingress.enabled, ingress.className and ingress.tlsSecretName" -}}
  {{- end -}}
  {{- if not (regexMatch "^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$" $v.ingress.host) -}}
    {{- fail "private requires ingress.host as a lowercase fully qualified DNS name without a port" -}}
  {{- end -}}
  {{- if not (regexMatch "^https://[A-Za-z0-9][A-Za-z0-9.-]*(:[0-9]+)?(/[^[:space:]?#@]*)?$" $v.sso.issuerURL) -}}
    {{- fail "private requires an HTTPS sso.issuerURL" -}}
  {{- end -}}
  {{- if or (empty $v.sso.existingSecret) (empty $v.businessUnits.existingSecret) -}}
    {{- fail "private requires sso.existingSecret and businessUnits.existingSecret" -}}
  {{- end -}}
  {{- if or (empty $v.sso.allowedEmailDomains) (and (has "*" $v.sso.allowedEmailDomains) (empty $v.sso.allowedGroups)) -}}
    {{- fail "private requires sso.allowedEmailDomains; a wildcard requires sso.allowedGroups" -}}
  {{- end -}}
  {{- if not (or $v.api.roleMap $v.api.defaultRole) -}}
    {{- fail "private requires api.roleMap or api.defaultRole: viewer" -}}
  {{- end -}}
  {{- if not (has $workbench (list "off" "portfolio")) -}}
    {{- fail "private supports api.workbenchMode off or portfolio" -}}
  {{- end -}}
  {{- if and (eq $workbench "portfolio") (not $v.api.workbenchCyberAudit) -}}
    {{- fail "portfolio Workbench requires api.workbenchCyberAudit: true" -}}
  {{- end -}}
  {{- if and $v.api.assistEnabled (empty $v.api.portfolioRunner.existingSecret) -}}
    {{- fail "api.assistEnabled requires api.portfolioRunner.existingSecret" -}}
  {{- end -}}
  {{- if and $v.api.portfolioRunner.existingSecret (not (regexMatch "^https?://litellm:[1-9][0-9]*/?$" $v.telemetry.litellmURL)) -}}
    {{- fail "api.portfolioRunner requires a private LiteLLM Service named litellm and an explicit port in telemetry.litellmURL" -}}
  {{- end -}}
  {{- if not (and $v.networkPolicy.ingressPeers $v.networkPolicy.dnsPeers $v.networkPolicy.egressRules) -}}
    {{- fail "private requires explicit networkPolicy.ingressPeers, dnsPeers and egressRules" -}}
  {{- end -}}
  {{- include "ai-factory.peers" $v.networkPolicy.ingressPeers -}}
  {{- include "ai-factory.peers" $v.networkPolicy.dnsPeers -}}
  {{- range $v.networkPolicy.egressRules -}}
    {{- if not (and .to .ports) -}}
      {{- fail "networkPolicy.egressRules entries must specify both peers and ports" -}}
    {{- end -}}
    {{- include "ai-factory.peers" .to -}}
    {{- range .ports -}}
      {{- if not .port -}}{{- fail "networkPolicy.egressRules entries must name specific ports" -}}{{- end -}}
    {{- end -}}
  {{- end -}}
  {{- range $url := list $v.telemetry.prometheusURL $v.telemetry.grafanaURL $v.telemetry.grafanaPublicURL $v.telemetry.langfuseURL $v.telemetry.litellmURL $v.telemetry.qdrantURL -}}
    {{- if and $url (not (regexMatch "^https?://[A-Za-z0-9][A-Za-z0-9.-]*(:[0-9]+)?(/[^[:space:]?#@]*)?$" $url)) -}}
      {{- fail "telemetry URLs must be HTTP(S) without embedded credentials, queries or fragments" -}}
    {{- end -}}
  {{- end -}}
  {{- if and $v.telemetry.grafanaPublicURL (not (hasPrefix "https://" $v.telemetry.grafanaPublicURL)) -}}
    {{- fail "telemetry.grafanaPublicURL must use HTTPS" -}}
  {{- end -}}
  {{- range $url := list $v.telemetry.prometheusURL $v.telemetry.grafanaURL $v.telemetry.langfuseURL $v.telemetry.litellmURL $v.telemetry.qdrantURL -}}
    {{- if and (hasPrefix "http://" $url) (not (and $v.telemetry.allowInsecureClusterHTTP (regexMatch "^http://[a-z0-9][a-z0-9-]*\\.[a-z0-9][a-z0-9-]*\\.svc(\\.cluster\\.local)?(:[0-9]+)?(/|$)" $url))) -}}
      {{- fail "telemetry endpoints must use HTTPS; HTTP requires telemetry.allowInsecureClusterHTTP and an in-cluster Service FQDN" -}}
    {{- end -}}
  {{- end -}}
{{- end -}}
{{- end }}
