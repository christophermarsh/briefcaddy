-- Candidate v1 shared logical schema: no automatic startup migration or grants.
-- Apply only to an explicitly approved empty disposable fictional PostgreSQL DB.
BEGIN;
CREATE SCHEMA law_app;
CREATE TABLE law_app.schema_versions (
    version integer PRIMARY KEY,
    applied_at timestamptz NOT NULL DEFAULT transaction_timestamp()
);
CREATE TABLE law_app.enrollments (
    firm_id uuid NOT NULL,
    enrollment_id uuid NOT NULL,
    active boolean NOT NULL DEFAULT false,
    PRIMARY KEY (firm_id, enrollment_id)
);
CREATE TABLE law_app.intakes (
    firm_id uuid NOT NULL,
    enrollment_id uuid NOT NULL,
    intake_id uuid NOT NULL,
    open boolean NOT NULL DEFAULT false,
    PRIMARY KEY (firm_id, enrollment_id, intake_id),
    FOREIGN KEY (firm_id, enrollment_id) REFERENCES law_app.enrollments
);
CREATE TABLE law_app.portal_generations (
    firm_id uuid NOT NULL,
    enrollment_id uuid NOT NULL,
    intake_id uuid NOT NULL,
    generation_id text NOT NULL CHECK (generation_id ~ '^[0-9a-f]{32,64}$'),
    state text NOT NULL CHECK (state IN ('pending', 'ready', 'revoked', 'superseded')),
    authority_evidence_id uuid NOT NULL,
    revision_manifest_sha256 text NOT NULL CHECK (revision_manifest_sha256 ~ '^[0-9a-f]{64}$'),
    PRIMARY KEY (firm_id, enrollment_id, intake_id, generation_id),
    FOREIGN KEY (firm_id, enrollment_id, intake_id) REFERENCES law_app.intakes
);
CREATE TABLE law_app.revisions (
    firm_id uuid NOT NULL,
    enrollment_id uuid NOT NULL,
    intake_id uuid NOT NULL,
    revision_id uuid NOT NULL,
    sha256 text NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
    byte_length bigint NOT NULL CHECK (byte_length >= 0),
    locator text NOT NULL CHECK (length(locator) BETWEEN 1 AND 2048),
    state text NOT NULL CHECK (state IN ('readable', 'quarantined', 'revoked')),
    PRIMARY KEY (firm_id, enrollment_id, revision_id),
    UNIQUE (firm_id, enrollment_id, intake_id, revision_id, sha256, byte_length),
    FOREIGN KEY (firm_id, enrollment_id, intake_id) REFERENCES law_app.intakes
);
CREATE TABLE law_app.generation_revisions (
    firm_id uuid NOT NULL,
    enrollment_id uuid NOT NULL,
    intake_id uuid NOT NULL,
    generation_id text NOT NULL,
    revision_id uuid NOT NULL,
    sha256 text NOT NULL,
    byte_length bigint NOT NULL,
    PRIMARY KEY (firm_id, enrollment_id, intake_id, generation_id, revision_id),
    UNIQUE (firm_id, enrollment_id, intake_id, generation_id, revision_id, sha256, byte_length),
    FOREIGN KEY (firm_id, enrollment_id, intake_id, generation_id) REFERENCES law_app.portal_generations,
    FOREIGN KEY (firm_id, enrollment_id, intake_id, revision_id, sha256, byte_length)
        REFERENCES law_app.revisions (firm_id, enrollment_id, intake_id, revision_id, sha256, byte_length)
);
CREATE TABLE law_app.job_intents (
    firm_id uuid NOT NULL,
    enrollment_id uuid NOT NULL,
    job_id uuid NOT NULL,
    intake_id uuid NOT NULL,
    generation_id text NOT NULL,
    operation_id text NOT NULL CHECK (operation_id ~ '^[0-9a-f]{32,64}$'),
    attempt_id uuid NOT NULL,
    outbox_id uuid NOT NULL,
    digest_version smallint NOT NULL CHECK (digest_version = 1),
    request_sha256 text NOT NULL CHECK (request_sha256 ~ '^[0-9a-f]{64}$'),
    request_json jsonb NOT NULL CHECK (jsonb_typeof(request_json) = 'object'),
    state text NOT NULL DEFAULT 'intent' CHECK (state = 'intent'),
    created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
    PRIMARY KEY (firm_id, enrollment_id, job_id),
    UNIQUE (firm_id, enrollment_id, intake_id, generation_id, operation_id),
    UNIQUE (firm_id, enrollment_id, job_id, intake_id, generation_id),
    FOREIGN KEY (firm_id, enrollment_id, intake_id, generation_id) REFERENCES law_app.portal_generations
);
CREATE TABLE law_app.processing_attempts (
    firm_id uuid NOT NULL,
    enrollment_id uuid NOT NULL,
    attempt_id uuid NOT NULL,
    job_id uuid NOT NULL,
    state text NOT NULL DEFAULT 'bound' CHECK (state = 'bound'),
    created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
    PRIMARY KEY (firm_id, enrollment_id, attempt_id),
    UNIQUE (firm_id, enrollment_id, attempt_id, job_id),
    FOREIGN KEY (firm_id, enrollment_id, job_id) REFERENCES law_app.job_intents
);
CREATE TABLE law_app.outbox_intents (
    firm_id uuid NOT NULL,
    enrollment_id uuid NOT NULL,
    outbox_id uuid NOT NULL,
    job_id uuid NOT NULL,
    event_kind text NOT NULL DEFAULT 'portal.job_intent.v1' CHECK (event_kind = 'portal.job_intent.v1'),
    state text NOT NULL DEFAULT 'pending' CHECK (state = 'pending'),
    created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
    PRIMARY KEY (firm_id, enrollment_id, outbox_id),
    UNIQUE (firm_id, enrollment_id, outbox_id, job_id),
    FOREIGN KEY (firm_id, enrollment_id, job_id) REFERENCES law_app.job_intents
);
ALTER TABLE law_app.job_intents ADD FOREIGN KEY (firm_id, enrollment_id, attempt_id, job_id)
    REFERENCES law_app.processing_attempts (firm_id, enrollment_id, attempt_id, job_id)
    DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE law_app.job_intents ADD FOREIGN KEY (firm_id, enrollment_id, outbox_id, job_id)
    REFERENCES law_app.outbox_intents (firm_id, enrollment_id, outbox_id, job_id)
    DEFERRABLE INITIALLY DEFERRED;
CREATE TABLE law_app.job_revisions (
    firm_id uuid NOT NULL,
    enrollment_id uuid NOT NULL,
    job_id uuid NOT NULL,
    intake_id uuid NOT NULL,
    generation_id text NOT NULL,
    revision_id uuid NOT NULL,
    sha256 text NOT NULL,
    byte_length bigint NOT NULL,
    PRIMARY KEY (firm_id, enrollment_id, job_id, revision_id),
    FOREIGN KEY (firm_id, enrollment_id, job_id, intake_id, generation_id)
        REFERENCES law_app.job_intents (firm_id, enrollment_id, job_id, intake_id, generation_id),
    FOREIGN KEY (firm_id, enrollment_id, intake_id, generation_id, revision_id, sha256, byte_length)
        REFERENCES law_app.generation_revisions (firm_id, enrollment_id, intake_id, generation_id, revision_id, sha256, byte_length)
);
INSERT INTO law_app.schema_versions (version) VALUES (1);
COMMIT;
