{{- define "web-app.name" -}}
{{- default .Release.Name .Values.platform.appId -}}
{{- end -}}

{{- define "web-app.labels" -}}
app.kubernetes.io/instance: {{ include "web-app.name" . | quote }}
app.kubernetes.io/name: {{ .Chart.Name | quote }}
{{- end -}}
