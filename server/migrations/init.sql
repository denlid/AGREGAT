-- ============================================================
-- AGREGAT Security V2 — Database Schema
-- Compatible with Supabase, Neon, any PostgreSQL
-- ============================================================
-- Run this in Supabase SQL Editor or via:
--   psql $DATABASE_URL < migrations/init.sql
-- ============================================================

-- Enable UUID extension
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ─── USERS ───
CREATE TABLE IF NOT EXISTS users (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    email           TEXT UNIQUE NOT NULL,
    password_hash   TEXT,                      -- bcrypt (admin only, NULL for regular users)
    role            TEXT NOT NULL DEFAULT 'user', -- owner, admin, support, auditor, user
    status          TEXT NOT NULL DEFAULT 'active', -- active, disabled, banned
    totp_secret     TEXT,                      -- Encrypted TOTP secret (admin only)
    totp_enabled    BOOLEAN DEFAULT FALSE,
    backup_codes    TEXT[],                    -- Encrypted backup codes
    ip_whitelist    TEXT[],                    -- Optional IP whitelist (admin only)
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_login_at   TIMESTAMPTZ,
    metadata        JSONB DEFAULT '{}'
);

-- ─── DEVICES ───
CREATE TABLE IF NOT EXISTS devices (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id             UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    device_fingerprint  TEXT NOT NULL,
    device_name         TEXT,
    os_info             TEXT,
    app_version         TEXT,
    first_seen_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_ip_hash        TEXT,
    revoked_at          TIMESTAMPTZ,
    revoke_reason       TEXT,
    UNIQUE(user_id, device_fingerprint)
);

-- ─── LICENSES ───
CREATE TABLE IF NOT EXISTS licenses (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    tier            TEXT NOT NULL DEFAULT 'beta',
    status          TEXT NOT NULL DEFAULT 'active',
    activation_key  TEXT UNIQUE NOT NULL,
    starts_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at      TIMESTAMPTZ NOT NULL,
    max_devices     INT NOT NULL DEFAULT 1,
    notes           TEXT,
    created_by      UUID REFERENCES users(id),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    revoked_at      TIMESTAMPTZ,
    revoke_reason   TEXT
);

-- ─── SESSIONS ───
CREATE TABLE IF NOT EXISTS sessions (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id             UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    device_id           UUID NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
    license_id          UUID NOT NULL REFERENCES licenses(id) ON DELETE CASCADE,
    refresh_token_hash  TEXT NOT NULL,
    issued_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at          TIMESTAMPTZ NOT NULL,
    last_heartbeat_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    revoked_at          TIMESTAMPTZ,
    revoke_reason       TEXT,
    client_metadata     JSONB DEFAULT '{}'
);

-- ─── ENTITLEMENTS ───
CREATE TABLE IF NOT EXISTS entitlements (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    license_id      UUID NOT NULL REFERENCES licenses(id) ON DELETE CASCADE,
    capability      TEXT NOT NULL,
    limit_value     INT,
    metadata        JSONB DEFAULT '{}',
    UNIQUE(license_id, capability)
);

-- ─── STRATEGY REGISTRY ───
CREATE TABLE IF NOT EXISTS strategy_registry (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    strategy_hash   TEXT NOT NULL,
    strategy_code   TEXT,
    file_name       TEXT,
    first_seen_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    usage_count     INT NOT NULL DEFAULT 1,
    metadata        JSONB DEFAULT '{}'
);

-- ─── AUDIT EVENTS (Immutable) ───
CREATE TABLE IF NOT EXISTS audit_events (
    id                  BIGSERIAL PRIMARY KEY,
    ts                  TIMESTAMPTZ NOT NULL DEFAULT now(),
    actor_user_id       UUID REFERENCES users(id),
    actor_role          TEXT,
    action              TEXT NOT NULL,
    target_type         TEXT,
    target_id           TEXT,
    ip_hash             TEXT,
    user_agent_hash     TEXT,
    payload_hash        TEXT NOT NULL,
    payload_json        JSONB,
    prev_event_hash     TEXT,
    event_hash          TEXT NOT NULL
);

-- ─── ADMIN SESSIONS (Persistent, replaces in-memory store) ───
CREATE TABLE IF NOT EXISTS admin_sessions (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id             UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash          TEXT NOT NULL UNIQUE,           -- SHA-256 hash of session token (never plaintext)
    role                TEXT NOT NULL,                   -- "owner" or "admin" (snapshot at login)
    ip_hash_at_login    TEXT,                            -- IP hash at login time (for anomaly detection)
    user_agent_hash     TEXT,                            -- User agent hash (for anomaly detection)
    expires_at          TIMESTAMPTZ NOT NULL,
    revoked_at          TIMESTAMPTZ,
    revoke_reason       TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_activity_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ─── RATE LIMIT EVENTS ───
CREATE TABLE IF NOT EXISTS rate_limit_events (
    id          BIGSERIAL PRIMARY KEY,
    ts          TIMESTAMPTZ NOT NULL DEFAULT now(),
    subject     TEXT NOT NULL,
    endpoint    TEXT NOT NULL,
    result      TEXT NOT NULL,
    metadata    JSONB DEFAULT '{}'
);

-- ─── INDEXES ───
CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_events(ts DESC);
CREATE INDEX IF NOT EXISTS idx_audit_action ON audit_events(action);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_sessions_device ON sessions(device_id);
CREATE INDEX IF NOT EXISTS idx_sessions_refresh ON sessions(refresh_token_hash);
CREATE INDEX IF NOT EXISTS idx_licenses_key ON licenses(activation_key);
CREATE INDEX IF NOT EXISTS idx_licenses_user ON licenses(user_id);
CREATE INDEX IF NOT EXISTS idx_devices_fingerprint ON devices(device_fingerprint);
CREATE INDEX IF NOT EXISTS idx_strategy_hash ON strategy_registry(strategy_hash);
CREATE INDEX IF NOT EXISTS idx_strategy_user ON strategy_registry(user_id);
CREATE INDEX IF NOT EXISTS idx_admin_sessions_token ON admin_sessions(token_hash);
CREATE INDEX IF NOT EXISTS idx_admin_sessions_user ON admin_sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_admin_sessions_expires ON admin_sessions(expires_at);

-- ─── ROW LEVEL SECURITY (Supabase) ───
-- Only server service key can access all tables
-- Regular users have NO direct database access
ALTER TABLE users ENABLE ROW LEVEL SECURITY;
ALTER TABLE devices ENABLE ROW LEVEL SECURITY;
ALTER TABLE licenses ENABLE ROW LEVEL SECURITY;
ALTER TABLE sessions ENABLE ROW LEVEL SECURITY;
ALTER TABLE entitlements ENABLE ROW LEVEL SECURITY;
ALTER TABLE strategy_registry ENABLE ROW LEVEL SECURITY;
ALTER TABLE audit_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE rate_limit_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE admin_sessions ENABLE ROW LEVEL SECURITY;

-- Deny all by default (server uses service key to bypass RLS)
CREATE POLICY "deny_all_users" ON users FOR ALL USING (false);
CREATE POLICY "deny_all_devices" ON devices FOR ALL USING (false);
CREATE POLICY "deny_all_licenses" ON licenses FOR ALL USING (false);
CREATE POLICY "deny_all_sessions" ON sessions FOR ALL USING (false);
CREATE POLICY "deny_all_entitlements" ON entitlements FOR ALL USING (false);
CREATE POLICY "deny_all_strategies" ON strategy_registry FOR ALL USING (false);
CREATE POLICY "deny_all_audit" ON audit_events FOR ALL USING (false);
CREATE POLICY "deny_all_rate_limit" ON rate_limit_events FOR ALL USING (false);
CREATE POLICY "deny_all_admin_sessions" ON admin_sessions FOR ALL USING (false);

-- ─── AUDIT HASH CHAIN FUNCTION ───
-- Automatically computes event_hash from previous event
CREATE OR REPLACE FUNCTION compute_audit_hash()
RETURNS TRIGGER AS $$
DECLARE
    prev_hash TEXT;
    payload_h TEXT;
BEGIN
    -- Get previous event hash
    SELECT event_hash INTO prev_hash
    FROM audit_events
    ORDER BY id DESC
    LIMIT 1;
    
    -- Compute payload hash
    payload_h := encode(sha256(convert_to(COALESCE(NEW.payload_json::text, '{}'), 'UTF8')), 'hex');
    NEW.payload_hash := payload_h;
    
    -- Compute event hash (chain)
    NEW.event_hash := encode(
        sha256(convert_to(
            COALESCE(prev_hash, 'genesis') || 
            NEW.ts::text || 
            NEW.action || 
            payload_h, 
            'UTF8'
        )), 
        'hex'
    );
    
    NEW.prev_event_hash := prev_hash;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_audit_hash_chain
    BEFORE INSERT ON audit_events
    FOR EACH ROW
    EXECUTE FUNCTION compute_audit_hash();

-- ─── CLEANUP FUNCTIONS ───
-- Auto-delete expired sessions older than 90 days
CREATE OR REPLACE FUNCTION cleanup_expired_sessions()
RETURNS void AS $$
BEGIN
    DELETE FROM sessions
    WHERE expires_at < now() - INTERVAL '90 days'
    AND revoked_at IS NOT NULL;
END;
$$ LANGUAGE plpgsql;

-- ─── COMMENTS ───
COMMENT ON TABLE users IS 'Application users. Regular users have no password_hash (auth via license key). Admins have password + MFA.';
COMMENT ON TABLE devices IS 'Registered devices bound to users. Fingerprint prevents device cloning.';
COMMENT ON TABLE licenses IS 'License keys with expiration. Issued by admin via panel or CLI.';
COMMENT ON TABLE sessions IS 'Active user sessions. Tied to user+device+license. Heartbeat updates last_heartbeat_at.';
COMMENT ON TABLE entitlements IS 'Granular capabilities granted per license. Replaces boolean license check.';
COMMENT ON TABLE strategy_registry IS 'Strategy code registry. Collects strategies from desktop clients for analysis.';
COMMENT ON TABLE audit_events IS 'Immutable audit log with hash chain. Every admin action is recorded.';
COMMENT ON TABLE admin_sessions IS 'Persistent admin sessions. Replaces in-memory store. Token stored as SHA-256 hash. Survives server restarts.';
