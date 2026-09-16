BEGIN;

CREATE TABLE experiments (
    experiment_id TEXT PRIMARY KEY,

    name TEXT NOT NULL,

    status TEXT NOT NULL
        CHECK (status IN ('DRAFT', 'RUNNING', 'STOPPED')),

    config JSONB NOT NULL DEFAULT '{}'::jsonb,

    started_at TIMESTAMPTZ,
    ended_at TIMESTAMPTZ,

    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    CHECK (
        ended_at IS NULL
        OR started_at IS NULL
        OR ended_at >= started_at
    )
);

CREATE TABLE recommendation_requests (
    request_id UUID PRIMARY KEY,

    user_id BIGINT NOT NULL
        REFERENCES users(user_id),

    session_id UUID
        REFERENCES user_sessions(session_id),

    scene TEXT NOT NULL,
    trigger_type TEXT NOT NULL,

    context_room_id BIGINT
        REFERENCES live_rooms(room_id),

    interest_version BIGINT
        CHECK (interest_version IS NULL OR interest_version >= 0),

    experiment_id TEXT
        REFERENCES experiments(experiment_id),

    experiment_variant TEXT,

    rec_model_name TEXT NOT NULL,
    rec_model_version TEXT NOT NULL,
    rec_model_alias TEXT,

    latency_ms INTEGER
        CHECK (latency_ms IS NULL OR latency_ms >= 0),

    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    UNIQUE(request_id, user_id)
);

CREATE INDEX idx_rec_requests_user_created
ON recommendation_requests(user_id, created_at DESC);

CREATE INDEX idx_rec_requests_experiment
ON recommendation_requests(experiment_id, experiment_variant);

CREATE TABLE recommendation_impressions (
    impression_id UUID PRIMARY KEY,

    request_id UUID NOT NULL
        REFERENCES recommendation_requests(request_id)
        ON DELETE CASCADE,

    item_id BIGINT NOT NULL
        REFERENCES items(item_id),

    position INTEGER NOT NULL
        CHECK (position >= 1),

    recall_sources TEXT[] NOT NULL DEFAULT '{}',

    retrieval_score REAL,
    rank_score REAL,
    final_score REAL,

    reason_codes TEXT[] NOT NULL DEFAULT '{}',

    -- Server-side "served" timestamp. Actual viewport exposure is recorded
    -- separately as a feedback event with action=IMPRESSION.
    served_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    UNIQUE(request_id, position),
    UNIQUE(request_id, item_id),
    UNIQUE(impression_id, request_id, item_id)
);

CREATE INDEX idx_rec_impressions_item_served
ON recommendation_impressions(item_id, served_at DESC);

CREATE TABLE recommendation_feedback (
    feedback_event_id UUID PRIMARY KEY,

    impression_id UUID NOT NULL,

    request_id UUID NOT NULL,

    user_id BIGINT NOT NULL
        REFERENCES users(user_id),

    item_id BIGINT NOT NULL
        REFERENCES items(item_id),

    CONSTRAINT fk_feedback_impression
        FOREIGN KEY (impression_id, request_id, item_id)
        REFERENCES recommendation_impressions(
            impression_id, request_id, item_id
        ),

    CONSTRAINT fk_feedback_request_user
        FOREIGN KEY (request_id, user_id)
        REFERENCES recommendation_requests(request_id, user_id),

    position INTEGER NOT NULL
        CHECK (position >= 1),

    action TEXT NOT NULL,

    dwell_ms BIGINT
        CHECK (dwell_ms IS NULL OR dwell_ms >= 0),

    order_value_cent BIGINT
        CHECK (order_value_cent IS NULL OR order_value_cent >= 0),

    experiment_id TEXT,
    experiment_variant TEXT,

    rec_model_name TEXT NOT NULL,
    rec_model_version TEXT NOT NULL,

    event_time TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_rec_feedback_user_time
ON recommendation_feedback(user_id, event_time DESC);

CREATE INDEX idx_rec_feedback_impression
ON recommendation_feedback(impression_id, event_time);

CREATE INDEX idx_rec_feedback_action_time
ON recommendation_feedback(action, event_time DESC);

COMMIT;
