CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

CREATE TABLE meetings (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    teams_call_id VARCHAR(255) UNIQUE,
    title VARCHAR(500),
    started_at TIMESTAMPTZ,
    ended_at TIMESTAMPTZ,
    status VARCHAR(50) NOT NULL DEFAULT 'active',
    owner_aad_id VARCHAR(255) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE participants (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    meeting_id UUID NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
    aad_user_id VARCHAR(255),
    display_name VARCHAR(255) NOT NULL,
    email VARCHAR(255),
    msi_id INTEGER,
    is_room_device BOOLEAN NOT NULL DEFAULT false,
    joined_at TIMESTAMPTZ,
    left_at TIMESTAMPTZ
);

CREATE TABLE transcript_segments (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    meeting_id UUID NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
    participant_id UUID REFERENCES participants(id),
    start_ms INTEGER NOT NULL,
    end_ms INTEGER NOT NULL,
    original_text TEXT NOT NULL,
    translated_text TEXT,
    source_language VARCHAR(10),
    confidence REAL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE summaries (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    meeting_id UUID NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
    summary_text TEXT NOT NULL,
    summary_language VARCHAR(10) NOT NULL DEFAULT 'et',
    model_used VARCHAR(100),
    prompt_tokens INTEGER,
    completion_tokens INTEGER,
    generated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_meetings_owner ON meetings(owner_aad_id);
CREATE INDEX idx_meetings_status ON meetings(status);
CREATE INDEX idx_segments_meeting ON transcript_segments(meeting_id);
CREATE INDEX idx_segments_time ON transcript_segments(meeting_id, start_ms);
CREATE INDEX idx_participants_meeting ON participants(meeting_id);
CREATE UNIQUE INDEX uq_participant_meeting_aad ON participants(meeting_id, aad_user_id);
