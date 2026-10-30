-- Initiales Schema der Datenbank operations (Strang C).
-- Vorfälle, Aufträge, Ereignisse, Ausführungen, Aktionen, Operations-Sperre,
-- Verifikationsnachweise und Worker-Heartbeat.

CREATE TABLE incidents (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    source        text NOT NULL,
    -- Azure Monitor: alertId; GitHub: <owner>/<repo>#<nummer>; Wartung: externalRef
    incident_key  text NOT NULL,
    app_id        text,
    environment   text,
    -- Technischer Alarmzustand, getrennt vom Vorfallabschluss
    alert_state   text CHECK (alert_state IN ('fired', 'resolved')),
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now(),
    UNIQUE (source, incident_key)
);

CREATE TABLE jobs (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    incident_id       uuid NOT NULL REFERENCES incidents (id),
    app_id            text,
    environment       text,
    state             text NOT NULL CHECK (state IN (
                          'received', 'queued', 'investigating', 'acting',
                          'verifying', 'resolved', 'failed', 'needs_attention')),
    state_reason      text,
    lease_owner       text,
    lease_expires_at  timestamptz,
    lease_version     bigint NOT NULL DEFAULT 0,
    attempts          integer NOT NULL DEFAULT 0,
    created_at        timestamptz NOT NULL DEFAULT now(),
    updated_at        timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX jobs_claimable ON jobs (created_at)
    WHERE state IN ('queued', 'investigating', 'acting', 'verifying');

-- Höchstens ein aktiver Auftrag je Vorfall; weitere Ereignisse hängen sich an.
CREATE UNIQUE INDEX jobs_one_active_per_incident ON jobs (incident_id)
    WHERE state IN ('received', 'queued', 'investigating', 'acting', 'verifying');

CREATE TABLE events (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    schema_version  text NOT NULL,
    source          text NOT NULL,
    delivery_id     text NOT NULL,
    external_ref    text NOT NULL,
    app_id          text,
    environment     text,
    event_type      text NOT NULL,
    severity        text NOT NULL,
    state           text NOT NULL,
    occurred_at     timestamptz,
    received_at     timestamptz NOT NULL,
    source_ref      jsonb NOT NULL,
    payload         jsonb NOT NULL,
    mapping_error   text,
    incident_id     uuid NOT NULL REFERENCES incidents (id),
    job_id          uuid REFERENCES jobs (id),
    UNIQUE (source, delivery_id)
);

CREATE INDEX events_job ON events (job_id);
CREATE INDEX events_incident ON events (incident_id);

CREATE TABLE job_transitions (
    id            bigserial PRIMARY KEY,
    job_id        uuid NOT NULL REFERENCES jobs (id),
    from_state    text NOT NULL,
    to_state      text NOT NULL,
    accepted      boolean NOT NULL,
    reason        text,
    requested_by  text NOT NULL,
    created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX job_transitions_job ON job_transitions (job_id, id);

CREATE TABLE executions (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id         uuid NOT NULL REFERENCES jobs (id),
    worker_id      text NOT NULL,
    lease_version  bigint NOT NULL,
    resumed        boolean NOT NULL,
    started_at     timestamptz NOT NULL DEFAULT now(),
    finished_at    timestamptz,
    proposed_state text,
    reason         text,
    limit_reached  text,
    required_change text,
    code_version   text,
    config_ref     text,
    model_settings jsonb NOT NULL DEFAULT '{}',
    usage          jsonb NOT NULL DEFAULT '{}',
    "references"   jsonb NOT NULL DEFAULT '{}',
    error          text
);

CREATE INDEX executions_job ON executions (job_id);

CREATE TABLE findings (
    id             bigserial PRIMARY KEY,
    job_id         uuid NOT NULL REFERENCES jobs (id),
    execution_id   uuid REFERENCES executions (id),
    -- confirmed: durch Werkzeugausgabe belegt; sonst Vermutung (hypothesis)
    confirmed      boolean NOT NULL,
    statement      text NOT NULL,
    provenance     text,
    evidence_refs  jsonb NOT NULL DEFAULT '[]',
    observed_at    timestamptz,
    created_at     timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE tool_calls (
    id              bigserial PRIMARY KEY,
    job_id          uuid NOT NULL REFERENCES jobs (id),
    execution_id    uuid REFERENCES executions (id),
    tool            text NOT NULL,
    target          text,
    action_id       text,
    ok              boolean NOT NULL,
    error_kind      text,
    started_at      timestamptz NOT NULL,
    duration_ms     integer NOT NULL
);

CREATE TABLE operations (
    id              text PRIMARY KEY,
    app_id          text NOT NULL,
    environment     text NOT NULL,
    kind            text NOT NULL CHECK (kind IN ('agent', 'deploy', 'break-glass')),
    owner_ref       text NOT NULL,
    job_id          uuid REFERENCES jobs (id),
    status          text NOT NULL CHECK (status IN ('active', 'completed', 'failed')),
    note            text,
    git_sha         text,
    workflow_run    text,
    started_at      timestamptz NOT NULL DEFAULT now(),
    finished_at     timestamptz
);

CREATE INDEX operations_job ON operations (job_id);

CREATE TABLE actions (
    id             text PRIMARY KEY,
    operation_id   text NOT NULL REFERENCES operations (id),
    job_id         uuid REFERENCES jobs (id),
    app_id         text NOT NULL,
    environment    text NOT NULL,
    kind           text NOT NULL,
    target         text NOT NULL,
    mutating       boolean NOT NULL,
    status         text NOT NULL CHECK (status IN ('planned', 'started', 'succeeded', 'failed', 'unclear')),
    status_reason  text,
    correlation    jsonb NOT NULL DEFAULT '{}',
    external_refs  jsonb NOT NULL DEFAULT '{}',
    detail         jsonb NOT NULL DEFAULT '{}',
    created_at     timestamptz NOT NULL DEFAULT now(),
    updated_at     timestamptz NOT NULL DEFAULT now(),
    finished_at    timestamptz
);

CREATE INDEX actions_operation ON actions (operation_id);
CREATE INDEX actions_job ON actions (job_id);
CREATE INDEX actions_open ON actions (app_id, environment)
    WHERE status IN ('planned', 'started', 'unclear');

CREATE SEQUENCE operation_lock_version;

-- Eine Zeile je gehaltener (oder abgelaufener, nicht freigegebener) Sperre.
CREATE TABLE operation_locks (
    app_id        text NOT NULL,
    environment   text NOT NULL,
    operation_id  text NOT NULL REFERENCES operations (id),
    owner_ref     text NOT NULL,
    lock_version  bigint NOT NULL,
    acquired_at   timestamptz NOT NULL DEFAULT now(),
    expires_at    timestamptz NOT NULL,
    PRIMARY KEY (app_id, environment)
);

CREATE TABLE verifications (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id       uuid NOT NULL REFERENCES jobs (id),
    kind         text NOT NULL CHECK (kind IN ('smoke-test', 'k8s-state')),
    app_id       text NOT NULL,
    environment  text NOT NULL,
    success      boolean NOT NULL,
    release      jsonb NOT NULL,
    check_ref    text,
    -- Smoke-Test: Testbeginn; Kubernetes-Zustand: Erfassungszeitpunkt
    started_at   timestamptz NOT NULL,
    finished_at  timestamptz,
    evidence     jsonb NOT NULL DEFAULT '{}',
    recorded_at  timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX verifications_job ON verifications (job_id);

CREATE TABLE worker_heartbeats (
    worker_id       text PRIMARY KEY,
    beat_at         timestamptz NOT NULL,
    started_at      timestamptz NOT NULL DEFAULT now(),
    current_job_id  uuid
);
