{{- define "cp.labels" -}}
app.kubernetes.io/part-of: ape-control-plane
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version }}
{{- end -}}

{{- define "cp.selector" -}}
app.kubernetes.io/instance: {{ .root.Release.Name }}
app.kubernetes.io/component: {{ .component }}
{{- end -}}

{{- define "cp.image" -}}
{{ .root.Values.image.registry }}/{{ .name }}:{{ .root.Values.image.tag | default .root.Chart.AppVersion }}
{{- end -}}

{{- define "cp.secretName" -}}
{{- .Values.secrets.existingSecret | default (printf "%s-secrets" .Release.Name) -}}
{{- end -}}

{{- define "cp.pgHost" -}}
{{- if eq .Values.postgres.mode "cnpg" -}}{{ .Release.Name }}-pg-rw{{- else -}}{{ required "postgres.external.host is required" .Values.postgres.external.host }}{{- end -}}
{{- end -}}

{{- define "cp.pgPort" -}}
{{- if eq .Values.postgres.mode "cnpg" -}}5432{{- else -}}{{ .Values.postgres.external.port }}{{- end -}}
{{- end -}}

{{- define "cp.pgSslmode" -}}
{{- if eq .Values.postgres.mode "cnpg" -}}require{{- else -}}{{ .Values.postgres.external.sslmode }}{{- end -}}
{{- end -}}

{{- define "cp.s3Endpoint" -}}
{{- if eq .Values.s3.mode "seaweedfs" -}}http://{{ .Release.Name }}-s3:8333{{- else -}}{{ required "s3.external.endpointUrl is required" .Values.s3.external.endpointUrl }}{{- end -}}
{{- end -}}

{{- define "cp.publicUrl" -}}
{{- if .Values.ingress.tls.enabled -}}https{{- else -}}http{{- end -}}://{{ required "host is required, e.g. --set host=cp.example.com" .Values.host }}
{{- end -}}

{{/* A value from the chart Secret. */}}
{{- define "cp.secretEnv" -}}
- name: {{ .name }}
  valueFrom:
    secretKeyRef:
      name: {{ include "cp.secretName" .root }}
      key: {{ .name }}
{{- end -}}

{{/* Pod and container hardening shared by every workload. */}}
{{- define "cp.podSecurity" -}}
runAsNonRoot: true
seccompProfile:
  type: RuntimeDefault
{{- end -}}

{{- define "cp.containerSecurity" -}}
allowPrivilegeEscalation: false
readOnlyRootFilesystem: true
capabilities:
  drop: [ALL]
{{- end -}}

{{/* Environment shared by api and worker. */}}
{{- define "cp.backendEnv" -}}
{{ include "cp.secretEnv" (dict "root" . "name" "APP_BACKEND_PASSWORD") }}
- name: DATABASE_URL
  value: "postgres://app_backend:$(APP_BACKEND_PASSWORD)@{{ include "cp.pgHost" . }}:{{ include "cp.pgPort" . }}/{{ .Values.postgres.database }}?sslmode={{ include "cp.pgSslmode" . }}"
- name: BETTER_AUTH_URL
  value: {{ include "cp.publicUrl" . | quote }}
- name: AUTH_JWKS_URL
  value: "http://{{ .Release.Name }}-web:3000/api/auth/jwks"
- name: S3_ENDPOINT_URL
  value: {{ include "cp.s3Endpoint" . | quote }}
- name: S3_REGION
  value: {{ .Values.s3.external.region | quote }}
{{- if .Values.s3.publicHost }}
- name: S3_PUBLIC_ENDPOINT_URL
  value: "https://{{ .Values.s3.publicHost }}"
{{- end }}
- name: S3_BUCKET_SNAPSHOTS
  value: {{ .Values.s3.buckets.snapshots | quote }}
- name: S3_BUCKET_REPORTS
  value: {{ .Values.s3.buckets.reports | quote }}
{{ include "cp.secretEnv" (dict "root" . "name" "S3_ACCESS_KEY") }}
{{ include "cp.secretEnv" (dict "root" . "name" "S3_SECRET_KEY") }}
{{ include "cp.secretEnv" (dict "root" . "name" "MASKING_KEY") }}
{{- if .Values.observability.otlpEndpoint }}
- name: OTEL_EXPORTER_OTLP_ENDPOINT
  value: {{ .Values.observability.otlpEndpoint | quote }}
{{- end }}
{{- end -}}
