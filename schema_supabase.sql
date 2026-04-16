-- =====================================================
-- Supabase PostgreSQL Schema for Thai mHealth Anti-Aging App
-- Run this ENTIRE file in Supabase SQL Editor
-- =====================================================

-- Drop and recreate users table with full schema
CREATE TABLE IF NOT EXISTS users (
    id BIGSERIAL PRIMARY KEY,
    email TEXT UNIQUE,
    password TEXT,
    name TEXT,
    role TEXT,
    last_seen TIMESTAMP
);

CREATE TABLE IF NOT EXISTS learning_materials (
    id BIGSERIAL PRIMARY KEY,
    title TEXT,
    type TEXT,
    url TEXT,
    category TEXT
);

CREATE TABLE IF NOT EXISTS user_points (
    user_id BIGINT PRIMARY KEY,
    points INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS user_health_stats (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT UNIQUE,
    epigenetic_age REAL,
    fitness_score REAL,
    epigenetic_pdf TEXT,
    inbody_pdf TEXT,
    biological_age REAL,
    age_acceleration REAL,
    epigenetic_lab_url TEXT,
    inbody_lab_url TEXT,
    inbody_score REAL
);

CREATE TABLE IF NOT EXISTS questionnaires (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT,
    type TEXT DEFAULT 'pre',
    age INTEGER,
    bmi REAL,
    waist REAL,
    answers_json TEXT,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS post_test_status (
    user_id BIGINT PRIMARY KEY,
    is_unlocked INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS exercises (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT,
    type TEXT,
    steps INTEGER,
    distance REAL,
    duration INTEGER,
    calories INTEGER DEFAULT 0,
    date TEXT
);

CREATE TABLE IF NOT EXISTS daily_logs (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT,
    date TEXT,
    sleep_hours REAL,
    stress_level TEXT,
    food_note TEXT,
    water_glasses INTEGER
);

CREATE TABLE IF NOT EXISTS challenges (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT,
    type TEXT,
    date TEXT
);

CREATE TABLE IF NOT EXISTS notifications (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT,
    message TEXT,
    type TEXT,
    is_read INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS social_shares (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL,
    share_text TEXT,
    file_name TEXT,
    file_type TEXT,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS app_settings (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS custom_questions (
    id BIGSERIAL PRIMARY KEY,
    q_number INTEGER NOT NULL UNIQUE,
    dimension INTEGER NOT NULL,
    q_text TEXT NOT NULL,
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS lab_results (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL,
    filename TEXT,
    original_name TEXT,
    notes TEXT,
    lab_type TEXT DEFAULT 'other',
    uploaded_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS certificates (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL,
    filename TEXT,
    original_name TEXT,
    notes TEXT,
    issued_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS video_watches (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL,
    material_id BIGINT NOT NULL,
    watched_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(user_id, material_id)
);

-- =====================================================
-- RPC function to execute arbitrary SQL from the app
-- This lets the Flask app call PostgreSQL via REST API
-- =====================================================
CREATE OR REPLACE FUNCTION exec_sql(sql text)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    result jsonb;
    cmd text;
    row_count integer;
BEGIN
    cmd := upper(trim(split_part(regexp_replace(trim(sql), '\s+', ' '), ' ', 1)));

    IF cmd IN ('SELECT', 'WITH') THEN
        EXECUTE format(
            'SELECT COALESCE(jsonb_agg(row_to_json(t)), ''[]''::jsonb) FROM (%s) t',
            sql
        ) INTO result;
        RETURN COALESCE(result, '[]'::jsonb);

    ELSIF cmd = 'INSERT' THEN
        IF strpos(upper(sql), 'RETURNING') = 0 THEN
            sql := sql || ' RETURNING id';
        END IF;
        EXECUTE format(
            'SELECT COALESCE(jsonb_agg(row_to_json(t)), ''[]''::jsonb) FROM (%s) t',
            sql
        ) INTO result;
        IF result IS NOT NULL AND result != '[]'::jsonb AND jsonb_array_length(result) > 0 THEN
            RETURN result->0;
        END IF;
        RETURN '{}'::jsonb;

    ELSE
        EXECUTE sql;
        GET DIAGNOSTICS row_count = ROW_COUNT;
        RETURN jsonb_build_object('ok', true, 'affected', row_count);
    END IF;

EXCEPTION WHEN OTHERS THEN
    RETURN jsonb_build_object('error', SQLERRM, 'state', SQLSTATE);
END;
$$;
