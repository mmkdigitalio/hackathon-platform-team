-- Zugangscodes und Nachrichten des Mitarbeiter-Chats.
CREATE TABLE portal_access_codes (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    label         text NOT NULL,
    code_hash     text UNIQUE NOT NULL CHECK (code_hash ~ '^[0-9a-f]{64}$'),
    created_at    timestamptz NOT NULL DEFAULT now(),
    expires_at    timestamptz,
    revoked_at    timestamptz,
    last_used_at  timestamptz
);

CREATE TABLE conversation_messages (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    incident_id        uuid NOT NULL REFERENCES incidents (id),
    job_id             uuid REFERENCES jobs (id),
    role               text NOT NULL CHECK (role IN ('reporter', 'agent', 'system')),
    author             text,
    kind               text CHECK (
                           (role = 'agent' AND kind IS NOT NULL AND kind IN ('reply', 'result'))
                           OR (role <> 'agent' AND kind IS NULL)),
    body               text NOT NULL,
    client_message_id  text,
    created_at         timestamptz NOT NULL DEFAULT now(),
    access_code_id     uuid REFERENCES portal_access_codes (id),
    UNIQUE (incident_id, client_message_id)
);

CREATE INDEX conversation_messages_code_time ON conversation_messages (access_code_id, created_at);
CREATE INDEX conversation_messages_incident_time ON conversation_messages (incident_id, created_at);
CREATE INDEX conversation_messages_job_kind ON conversation_messages (job_id, kind);
