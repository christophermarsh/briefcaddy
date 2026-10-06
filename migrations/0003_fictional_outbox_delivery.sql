-- Fictional local delivery only; no broker, provider or processed-event delivery.
BEGIN;
DO $$ BEGIN
    IF (SELECT max(version) FROM law_app.schema_versions) IS DISTINCT FROM 2 THEN
        RAISE EXCEPTION 'Requires synthetic work schema v2';
    END IF;
END $$;
ALTER TABLE law_app.outbox_intents DROP CONSTRAINT outbox_intents_state_check;
ALTER TABLE law_app.outbox_intents ADD CHECK (state IN ('pending','delivered'));
ALTER TABLE law_app.outbox_intents ADD COLUMN delivery_id uuid;
ALTER TABLE law_app.outbox_intents ADD COLUMN delivery_owner_id uuid;
ALTER TABLE law_app.outbox_intents ADD COLUMN delivery_fence bigint NOT NULL DEFAULT 0 CHECK (delivery_fence >= 0);
ALTER TABLE law_app.outbox_intents ADD COLUMN delivery_expires_at timestamptz;
ALTER TABLE law_app.outbox_intents ADD COLUMN delivered_at timestamptz;
ALTER TABLE law_app.outbox_intents ADD CHECK (
    (delivery_id IS NULL AND delivery_owner_id IS NULL AND delivery_expires_at IS NULL AND delivery_fence=0)
    OR (delivery_id IS NOT NULL AND delivery_owner_id IS NOT NULL AND delivery_expires_at IS NOT NULL AND delivery_fence>0));
ALTER TABLE law_app.outbox_intents ADD CHECK (
    (state='pending' AND delivered_at IS NULL)
    OR (state='delivered' AND event_kind='portal.job_intent.v1' AND delivery_id IS NOT NULL AND delivered_at IS NOT NULL));
INSERT INTO law_app.schema_versions (version) VALUES (3);
COMMIT;
