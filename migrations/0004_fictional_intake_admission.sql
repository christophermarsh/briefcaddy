-- Fictional actor/purpose evidence and admission; no production consent/RLS.
BEGIN;
DO $$ BEGIN
    IF (SELECT max(version) FROM law_app.schema_versions) IS DISTINCT FROM 3 THEN
        RAISE EXCEPTION 'Requires fictional delivery schema v3';
    END IF;
END $$;
CREATE TABLE law_app.fictional_intake_limits (
    firm_id uuid NOT NULL, enrollment_id uuid NOT NULL,
    pending_work_cap integer NOT NULL CHECK (pending_work_cap>0),
    PRIMARY KEY (firm_id,enrollment_id),
    FOREIGN KEY (firm_id,enrollment_id) REFERENCES law_app.enrollments
);
CREATE TABLE law_app.fictional_intake_heads (
    firm_id uuid NOT NULL, enrollment_id uuid NOT NULL, intake_id uuid NOT NULL,
    current_generation_id text,
    PRIMARY KEY (firm_id,enrollment_id,intake_id),
    FOREIGN KEY (firm_id,enrollment_id) REFERENCES law_app.enrollments,
    FOREIGN KEY (firm_id,enrollment_id,intake_id,current_generation_id)
        REFERENCES law_app.portal_generations (firm_id,enrollment_id,intake_id,generation_id)
        DEFERRABLE INITIALLY DEFERRED
);
-- Refuse ambiguous legacy ready heads through the PK; do not choose a winner.
INSERT INTO law_app.fictional_intake_heads
    SELECT i.firm_id,i.enrollment_id,i.intake_id,g.generation_id
    FROM law_app.intakes i LEFT JOIN law_app.portal_generations g
      ON (i.firm_id,i.enrollment_id,i.intake_id)=(g.firm_id,g.enrollment_id,g.intake_id) AND g.state='ready';
CREATE TABLE law_app.fictional_admissions (
    firm_id uuid NOT NULL, enrollment_id uuid NOT NULL, reservation_id uuid NOT NULL,
    evidence_id uuid NOT NULL, intake_id uuid NOT NULL, generation_id text NOT NULL,
    operation_id text NOT NULL CHECK (operation_id ~ '^[0-9a-f]{32,64}$'),
    actor_id uuid NOT NULL, purpose text NOT NULL CHECK (purpose='fictional_intake_upload'),
    request_sha256 text NOT NULL CHECK (request_sha256 ~ '^[0-9a-f]{64}$'),
    request_json jsonb NOT NULL CHECK (jsonb_typeof(request_json)='object'),
    manifest_sha256 text NOT NULL CHECK (manifest_sha256 ~ '^[0-9a-f]{64}$'),
    state text NOT NULL DEFAULT 'reserved' CHECK (state IN ('reserved','committed')),
    job_id uuid, attempt_id uuid, outbox_id uuid,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (firm_id,enrollment_id,reservation_id),
    UNIQUE (firm_id,enrollment_id,intake_id,operation_id),
    UNIQUE (firm_id,enrollment_id,evidence_id),
    FOREIGN KEY (firm_id,enrollment_id,intake_id) REFERENCES law_app.fictional_intake_heads,
    FOREIGN KEY (firm_id,enrollment_id,job_id,intake_id,generation_id)
        REFERENCES law_app.job_intents (firm_id,enrollment_id,job_id,intake_id,generation_id),
    FOREIGN KEY (firm_id,enrollment_id,attempt_id,job_id)
        REFERENCES law_app.processing_attempts (firm_id,enrollment_id,attempt_id,job_id),
    FOREIGN KEY (firm_id,enrollment_id,outbox_id,job_id)
        REFERENCES law_app.outbox_intents (firm_id,enrollment_id,outbox_id,job_id),
    CHECK ((state='reserved' AND job_id IS NULL AND attempt_id IS NULL AND outbox_id IS NULL)
        OR (state='committed' AND job_id IS NOT NULL AND attempt_id IS NOT NULL AND outbox_id IS NOT NULL))
);
INSERT INTO law_app.schema_versions (version) VALUES (4);
COMMIT;
