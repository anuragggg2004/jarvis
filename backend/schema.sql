-- Enable extensions
CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- Master user (single row, hardcoded)
CREATE TABLE IF NOT EXISTS jarvis_user (
    id                    UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    username              VARCHAR(50)  UNIQUE NOT NULL DEFAULT 'anurag',
    email                 VARCHAR(255) UNIQUE NOT NULL DEFAULT 'anurag@jarvis.local',
    password_hash         TEXT NOT NULL,           -- Argon2id
    master_key_salt       BYTEA NOT NULL,
    encrypted_master_key  BYTEA NOT NULL,           -- Wrapped with passphrase-derived key
    totp_secret           VARCHAR(32),              -- Optional 2FA
    created_at            TIMESTAMPTZ DEFAULT NOW(),
    last_login            TIMESTAMPTZ,
    preferences           JSONB DEFAULT '{
        "theme": "jarvis-dark",
        "personality": "jarvis",
        "default_llm": "llama3.2:latest",
        "auto_summarize": true,
        "auto_tag": true,
        "voice_enabled": false
    }'::jsonb
);

-- Bookmarks
CREATE TABLE IF NOT EXISTS bookmarks (
    id                UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id           UUID REFERENCES jarvis_user(id) ON DELETE CASCADE,
    url               TEXT NOT NULL,
    canonical_url     TEXT,
    title             VARCHAR(500),
    description       TEXT,
    content_hash      VARCHAR(64),   -- SHA256 for deduplication

    -- AI-generated fields (AES-256-GCM encrypted)
    summary_encrypted     BYTEA,
    summary_iv            BYTEA,
    summary_tag           BYTEA,
    summary_dek_wrapped   BYTEA,     -- DEK encrypted with master key
    summary_dek_iv        BYTEA,
    summary_dek_tag       BYTEA,

    key_points_encrypted  BYTEA,
    key_points_iv         BYTEA,
    key_points_tag        BYTEA,
    key_points_dek_wrapped BYTEA,
    key_points_dek_iv     BYTEA,
    key_points_dek_tag    BYTEA,

    category_encrypted    BYTEA,
    category_iv           BYTEA,
    category_tag          BYTEA,
    category_dek_wrapped  BYTEA,
    category_dek_iv       BYTEA,
    category_dek_tag      BYTEA,

    -- Metadata (plaintext, non-sensitive)
    domain            VARCHAR(255),
    favicon_url       TEXT,
    screenshot_path   TEXT,          -- Filesystem path under /data/screenshots/
    content_type      VARCHAR(100),
    word_count        INT,
    language          VARCHAR(10) DEFAULT 'en',

    -- pgvector embedding (mxbai-embed-large = 1024 dims)
    embedding         vector(1024),

    -- Organisation
    tags              TEXT[]    DEFAULT '{}',
    collection_ids    UUID[]    DEFAULT '{}',
    is_archived       BOOLEAN   DEFAULT FALSE,
    is_favorite       BOOLEAN   DEFAULT FALSE,
    read_status       VARCHAR(20) DEFAULT 'unread', -- unread | reading | read
    reading_progress  FLOAT     DEFAULT 0.0,

    -- Timestamps
    added_at          TIMESTAMPTZ DEFAULT NOW(),
    updated_at        TIMESTAMPTZ DEFAULT NOW(),
    last_accessed     TIMESTAMPTZ,
    archived_at       TIMESTAMPTZ,

    -- Source
    source            VARCHAR(50) DEFAULT 'manual', -- manual | extension | api | import
    source_metadata   JSONB DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_bookmarks_user      ON bookmarks(user_id);
CREATE INDEX IF NOT EXISTS idx_bookmarks_domain    ON bookmarks(user_id, domain);
CREATE INDEX IF NOT EXISTS idx_bookmarks_tags      ON bookmarks USING GIN(tags);
CREATE INDEX IF NOT EXISTS idx_bookmarks_added     ON bookmarks(user_id, added_at DESC);
CREATE INDEX IF NOT EXISTS idx_bookmarks_fts       ON bookmarks USING GIN(
    to_tsvector('english', COALESCE(title,'') || ' ' || COALESCE(description,''))
);
CREATE INDEX IF NOT EXISTS idx_bookmarks_embedding ON bookmarks
    USING hnsw (embedding vector_cosine_ops);

-- Collections (nested knowledge spaces)
CREATE TABLE IF NOT EXISTS collections (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id     UUID REFERENCES jarvis_user(id) ON DELETE CASCADE,
    name        VARCHAR(200) NOT NULL,
    description TEXT,
    icon        VARCHAR(100),
    color       VARCHAR(7),          -- Hex e.g. #00d4aa
    parent_id   UUID REFERENCES collections(id),
    is_system   BOOLEAN DEFAULT FALSE,
    sort_order  INT     DEFAULT 0,
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    updated_at  TIMESTAMPTZ DEFAULT NOW()
);

-- Conversations
CREATE TABLE IF NOT EXISTS conversations (
    id           UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id      UUID REFERENCES jarvis_user(id) ON DELETE CASCADE,
    title        VARCHAR(500),
    system_prompt TEXT,
    model_used   VARCHAR(100),
    total_tokens INT DEFAULT 0,
    created_at   TIMESTAMPTZ DEFAULT NOW(),
    updated_at   TIMESTAMPTZ DEFAULT NOW()
);

-- Messages (encrypted)
CREATE TABLE IF NOT EXISTS messages (
    id               UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    conversation_id  UUID REFERENCES conversations(id) ON DELETE CASCADE,
    role             VARCHAR(20) NOT NULL,  -- user | assistant | system | tool
    content_encrypted BYTEA NOT NULL,
    content_iv        BYTEA NOT NULL,
    content_tag       BYTEA NOT NULL,
    content_dek_wrapped BYTEA NOT NULL,
    content_dek_iv    BYTEA NOT NULL,
    content_dek_tag   BYTEA NOT NULL,
    token_count      INT,
    tool_calls       JSONB,
    tool_results     JSONB,
    citations        UUID[],             -- bookmark IDs referenced in this reply
    created_at       TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_messages_conv ON messages(conversation_id, created_at);

-- Ingestion jobs
CREATE TABLE IF NOT EXISTS ingestion_jobs (
    id               UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id          UUID REFERENCES jarvis_user(id) ON DELETE CASCADE,
    source_url       TEXT,
    source_type      VARCHAR(50),  -- url | pdf | youtube | twitter
    status           VARCHAR(20) DEFAULT 'pending', -- pending | processing | completed | failed
    progress         FLOAT DEFAULT 0.0,
    result_bookmark_id UUID REFERENCES bookmarks(id),
    error_message    TEXT,
    created_at       TIMESTAMPTZ DEFAULT NOW(),
    completed_at     TIMESTAMPTZ
);

-- Audit log
CREATE TABLE IF NOT EXISTS audit_log (
    id            BIGSERIAL PRIMARY KEY,
    user_id       UUID REFERENCES jarvis_user(id),
    action        VARCHAR(100) NOT NULL,
    resource_type VARCHAR(50),
    resource_id   UUID,
    ip_address    INET,
    user_agent    TEXT,
    metadata      JSONB DEFAULT '{}',
    created_at    TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_audit_user_time ON audit_log(user_id, created_at DESC);
