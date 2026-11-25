{{- define "ape-agent.name" -}}ape-agent{{- end -}}

{{- define "ape-agent.labels" -}}
app.kubernetes.io/name: ape-agent
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version }}
{{- end -}}

{{- define "ape-agent.selectorLabels" -}}
app.kubernetes.io/name: ape-agent
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{- define "ape-agent.secretName" -}}
{{- if .Values.existingSecret -}}{{ .Values.existingSecret }}{{- else -}}ape-agent-token{{- end -}}
{{- end -}}

{{/* Fail early with a readable message instead of a crash-looping pod. */}}
{{- define "ape-agent.validate" -}}
{{- if and (not .Values.token) (not .Values.existingSecret) -}}
{{- fail "set token (shown once in the panel) or existingSecret" -}}
{{- end -}}
{{- if and (hasPrefix "ws://" .Values.endpoint) (not .Values.allowInsecure) -}}
{{- fail "endpoint must use wss://; ws:// needs allowInsecure=true and is for local development only" -}}
{{- end -}}
{{- end -}}
