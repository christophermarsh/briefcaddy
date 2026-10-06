-- Candidate v2: synthetic claims and metadata completion; no broker/provider wiring.
BEGIN;
DO $$ BEGIN
    IF (SELECT max(version) FROM law_app.schema_versions) IS DISTINCT FROM 1 THEN
        RAISE EXCEPTION 'Requires portal intent schema v1';
    END IF;
END $$;
ALTER TABLE law_app.job_intents DROP CONSTRAINT job_intents_state_check;
ALTER TABLE law_app.job_intents ADD CHECK (state IN ('intent','running','retry','done'));
ALTER TABLE law_app.job_intents ADD COLUMN state_version bigint NOT NULL DEFAULT 0 CHECK (state_version >= 0);
ALTER TABLE law_app.job_intents ADD COLUMN fence bigint NOT NULL DEFAULT 0 CHECK (fence >= 0);
ALTER TABLE law_app.job_intents ADD COLUMN current_execution_id uuid;
ALTER TABLE law_app.job_intents ADD COLUMN not_before timestamptz;
CREATE TABLE law_app.work_attempts (
    firm_id uuid NOT NULL, enrollment_id uuid NOT NULL, execution_id uuid NOT NULL,
    job_id uuid NOT NULL, bound_attempt_id uuid NOT NULL, owner_id uuid NOT NULL,
    fence bigint NOT NULL CHECK (fence > 0), expires_at timestamptz NOT NULL,
    state text NOT NULL CHECK (state IN ('running','expired','retry','completed')),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (firm_id,enrollment_id,execution_id),
    UNIQUE (firm_id,enrollment_id,job_id,fence),
    UNIQUE (firm_id,enrollment_id,execution_id,job_id),
    UNIQUE (firm_id,enrollment_id,execution_id,job_id,owner_id,fence),
    FOREIGN KEY (firm_id,enrollment_id,bound_attempt_id,job_id)
        REFERENCES law_app.processing_attempts (firm_id,enrollment_id,attempt_id,job_id)
);
ALTER TABLE law_app.job_intents ADD FOREIGN KEY (firm_id,enrollment_id,current_execution_id,job_id)
    REFERENCES law_app.work_attempts (firm_id,enrollment_id,execution_id,job_id)
    DEFERRABLE INITIALLY DEFERRED;
CREATE TABLE law_app.work_progress (
    firm_id uuid NOT NULL, enrollment_id uuid NOT NULL, job_id uuid NOT NULL,
    execution_id uuid NOT NULL, state_version bigint NOT NULL, value jsonb NOT NULL,
    PRIMARY KEY (firm_id,enrollment_id,job_id),
    FOREIGN KEY (firm_id,enrollment_id,execution_id,job_id)
        REFERENCES law_app.work_attempts (firm_id,enrollment_id,execution_id,job_id),
    CHECK (jsonb_typeof(value)='object')
);
CREATE TABLE law_app.processing_results (
    firm_id uuid NOT NULL, enrollment_id uuid NOT NULL, result_id uuid NOT NULL,
    job_id uuid NOT NULL, execution_id uuid NOT NULL, owner_id uuid NOT NULL, fence bigint NOT NULL,
    result_sha256 text NOT NULL CHECK (result_sha256 ~ '^[0-9a-f]{64}$'),
    result_json jsonb NOT NULL CHECK (jsonb_typeof(result_json)='object'),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (firm_id,enrollment_id,result_id), UNIQUE (firm_id,enrollment_id,job_id),
    UNIQUE (firm_id,enrollment_id,result_id,job_id,execution_id,owner_id,fence),
    FOREIGN KEY (firm_id,enrollment_id,execution_id,job_id,owner_id,fence)
        REFERENCES law_app.work_attempts (firm_id,enrollment_id,execution_id,job_id,owner_id,fence)
);
CREATE TABLE law_app.processed_receipts (
    firm_id uuid NOT NULL, enrollment_id uuid NOT NULL, receipt_id uuid NOT NULL,
    job_id uuid NOT NULL, intake_id uuid NOT NULL, generation_id text NOT NULL,
    bound_attempt_id uuid NOT NULL, execution_id uuid NOT NULL, owner_id uuid NOT NULL,
    fence bigint NOT NULL, claim_state_version bigint NOT NULL,
    result_id uuid NOT NULL, completion_outbox_id uuid NOT NULL,
    request_sha256 text NOT NULL CHECK (request_sha256 ~ '^[0-9a-f]{64}$'),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (firm_id,enrollment_id,receipt_id), UNIQUE (firm_id,enrollment_id,job_id),
    UNIQUE (firm_id,enrollment_id,receipt_id,job_id),
    FOREIGN KEY (firm_id,enrollment_id,job_id,intake_id,generation_id)
        REFERENCES law_app.job_intents (firm_id,enrollment_id,job_id,intake_id,generation_id),
    FOREIGN KEY (firm_id,enrollment_id,bound_attempt_id,job_id)
        REFERENCES law_app.processing_attempts (firm_id,enrollment_id,attempt_id,job_id),
    FOREIGN KEY (firm_id,enrollment_id,result_id,job_id,execution_id,owner_id,fence)
        REFERENCES law_app.processing_results (firm_id,enrollment_id,result_id,job_id,execution_id,owner_id,fence),
    FOREIGN KEY (firm_id,enrollment_id,completion_outbox_id,job_id)
        REFERENCES law_app.outbox_intents (firm_id,enrollment_id,outbox_id,job_id)
        DEFERRABLE INITIALLY DEFERRED
);
ALTER TABLE law_app.outbox_intents DROP CONSTRAINT outbox_intents_event_kind_check;
ALTER TABLE law_app.outbox_intents ADD COLUMN processed_receipt_id uuid;
ALTER TABLE law_app.outbox_intents ADD CHECK (
    (event_kind='portal.job_intent.v1' AND processed_receipt_id IS NULL) OR
    (event_kind='portal.processed.v1' AND processed_receipt_id IS NOT NULL));
ALTER TABLE law_app.outbox_intents ADD FOREIGN KEY (firm_id,enrollment_id,processed_receipt_id,job_id)
    REFERENCES law_app.processed_receipts (firm_id,enrollment_id,receipt_id,job_id);
INSERT INTO law_app.schema_versions (version) VALUES (2);
COMMIT;
