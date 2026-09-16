BEGIN;

CREATE TABLE user_sessions (
    session_id UUID PRIMARY KEY,

    user_id BIGINT NOT NULL
        REFERENCES users(user_id),

    room_id BIGINT NOT NULL
        REFERENCES live_rooms(room_id),

    enter_at TIMESTAMPTZ NOT NULL,
    exit_at TIMESTAMPTZ,

    effective_watch_ms BIGINT NOT NULL DEFAULT 0
        CHECK (effective_watch_ms >= 0),

    like_count INTEGER NOT NULL DEFAULT 0
        CHECK (like_count >= 0),

    follow_count INTEGER NOT NULL DEFAULT 0
        CHECK (follow_count >= 0),

    item_click_count INTEGER NOT NULL DEFAULT 0
        CHECK (item_click_count >= 0),

    status TEXT NOT NULL DEFAULT 'ACTIVE'
        CHECK (status IN ('ACTIVE', 'CLOSED', 'ABANDONED')),

    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    CHECK (exit_at IS NULL OR exit_at >= enter_at)
);

CREATE INDEX idx_user_sessions_user_enter
ON user_sessions(user_id, enter_at DESC);

CREATE INDEX idx_user_sessions_room_enter
ON user_sessions(room_id, enter_at DESC);

CREATE TABLE attributed_interactions (
    attribution_id UUID PRIMARY KEY,

    source_event_id UUID NOT NULL UNIQUE,

    user_id BIGINT NOT NULL
        REFERENCES users(user_id),

    session_id UUID NOT NULL
        REFERENCES user_sessions(session_id),

    room_id BIGINT NOT NULL
        REFERENCES live_rooms(room_id),

    stream_session_id UUID NOT NULL
        REFERENCES live_stream_sessions(stream_session_id),

    action TEXT NOT NULL,

    attribution_method TEXT NOT NULL,
    contributions JSONB NOT NULL,

    attribution_confidence REAL NOT NULL
        CHECK (attribution_confidence >= 0 AND attribution_confidence <= 1),

    playback_discontinuity BOOLEAN NOT NULL DEFAULT FALSE,

    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_attr_interactions_user_created
ON attributed_interactions(user_id, created_at DESC);

CREATE INDEX idx_attr_interactions_session
ON attributed_interactions(session_id, created_at);

CREATE INDEX idx_attr_interactions_contrib_gin
ON attributed_interactions USING GIN(contributions);

COMMIT;
