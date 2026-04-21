-- Migration 001: Add unique constraint for participant upsert by external ID
-- Required by the assembly service rewrite that uses aad_user_id for lookups
-- Safe to run multiple times (IF NOT EXISTS)

CREATE UNIQUE INDEX IF NOT EXISTS uq_participant_meeting_aad
ON participants(meeting_id, aad_user_id);
