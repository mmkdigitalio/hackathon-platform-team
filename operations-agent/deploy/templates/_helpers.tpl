{{- define "ops.name" -}}operations-agent{{- end -}}

{{- define "ops.labels" -}}
app.kubernetes.io/name: {{ include "ops.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version }}
{{- end -}}

{{- define "ops.image" -}}
{{ required "image.repository fehlt" .Values.image.repository }}@{{ required "image.digest fehlt (nur per Digest)" .Values.image.digest }}
{{- end -}}

{{- define "ops.podSecurity" -}}
securityContext:
  runAsNonRoot: true
  runAsUser: 10001
  seccompProfile: { type: RuntimeDefault }
imagePullSecrets:
  - name: {{ required "imagePullSecretName fehlt" .Values.imagePullSecretName }}
{{- end -}}

{{- define "ops.containerSecurity" -}}
securityContext:
  allowPrivilegeEscalation: false
  readOnlyRootFilesystem: true
  capabilities: { drop: [ALL] }
{{- end -}}

{{- define "ops.env" -}}
envFrom:
  - secretRef:
      name: {{ required "secretName fehlt" .Values.secretName }}
  - configMapRef:
      name: {{ include "ops.name" . }}-config
{{- end -}}
