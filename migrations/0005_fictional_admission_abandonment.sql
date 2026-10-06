-- Explicit fictional reservation abandonment; never deletes originals/jobs.
BEGIN;
DO $$ BEGIN
    IF (SELECT max(version) FROM law_app.schema_versions) IS DISTINCT FROM 4 THEN
        RAISE EXCEPTION 'Requires fictional admission schema v4';
    END IF;
END $$;
ALTER TABLE law_app.fictional_admissions DROP CONSTRAINT fictional_admissions_state_check;
ALTER TABLE law_app.fictional_admissions ADD CHECK (state IN ('reserved','committed','abandoned'));
ALTER TABLE law_app.fictional_admissions DROP CONSTRAINT fictional_admissions_check;
ALTER TABLE law_app.fictional_admissions ADD CHECK (
    (state IN ('reserved','abandoned') AND job_id IS NULL AND attempt_id IS NULL AND outbox_id IS NULL)
    OR (state='committed' AND job_id IS NOT NULL AND attempt_id IS NOT NULL AND outbox_id IS NOT NULL));
ALTER TABLE law_app.fictional_admissions ADD COLUMN abandoned_at timestamptz;
ALTER TABLE law_app.fictional_admissions ADD CHECK (
    (state='abandoned' AND abandoned_at IS NOT NULL)
    OR (state<>'abandoned' AND abandoned_at IS NULL));
INSERT INTO law_app.schema_versions (version) VALUES (5);
COMMIT;
