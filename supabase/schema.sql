-- =============================================================================
-- PatchPilot — Supabase PostgreSQL Schema
-- Tables: vault_configs, users
-- Stores BYOK AI provider settings and user profiles/tokens encrypted via AES-256-GCM
-- =============================================================================

-- 1. Create vault_configs table
CREATE TABLE IF NOT EXISTS public.vault_configs (
    owner VARCHAR(255) PRIMARY KEY,
    provider VARCHAR(50) NOT NULL DEFAULT 'gemini',
    model VARCHAR(255) NOT NULL DEFAULT 'gemini-2.5-pro',
    encrypted_api_key TEXT NOT NULL DEFAULT '',
    base_url VARCHAR(1024),
    review_policy TEXT NOT NULL DEFAULT '# PatchPilot PR Review Guidelines
- Identify critical bugs, security vulnerabilities, memory/resource leaks, and edge-case exceptions.
- Highlight performance bottlenecks or unnecessary redundant operations.
- Flag breaking changes or deviations from repository architectural idioms.
- Keep feedback objective, constructive, and directly actionable.',
    fallbacks_json TEXT DEFAULT '[]',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc'::text, now())
);

ALTER TABLE public.vault_configs ADD COLUMN IF NOT EXISTS fallbacks_json TEXT DEFAULT '[]';

-- 2. Create users table
CREATE TABLE IF NOT EXISTS public.users (
    login VARCHAR(255) PRIMARY KEY,
    clerk_id VARCHAR(255),
    github_username VARCHAR(255),
    email VARCHAR(320),
    name VARCHAR(255),
    avatar_url VARCHAR(1024),
    encrypted_github_token TEXT NOT NULL DEFAULT '',
    auth_provider VARCHAR(50) NOT NULL DEFAULT 'github',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc'::text, now())
);

-- 3. Explicit indexes for lookups
CREATE INDEX IF NOT EXISTS idx_vault_configs_owner ON public.vault_configs (owner);
CREATE INDEX IF NOT EXISTS idx_users_login ON public.users (login);
CREATE INDEX IF NOT EXISTS idx_users_clerk_id ON public.users (clerk_id);
CREATE INDEX IF NOT EXISTS idx_users_github_username ON public.users (github_username);

-- 4. Trigger to maintain updated_at timestamp on row changes
CREATE OR REPLACE FUNCTION public.handle_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = timezone('utc'::text, now());
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trigger_vault_configs_updated_at ON public.vault_configs;
CREATE TRIGGER trigger_vault_configs_updated_at
    BEFORE UPDATE ON public.vault_configs
    FOR EACH ROW
    EXECUTE FUNCTION public.handle_updated_at();

DROP TRIGGER IF EXISTS trigger_users_updated_at ON public.users;
CREATE TRIGGER trigger_users_updated_at
    BEFORE UPDATE ON public.users
    FOR EACH ROW
    EXECUTE FUNCTION public.handle_updated_at();

-- 5. Enable Row Level Security (RLS)
ALTER TABLE public.vault_configs ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.users ENABLE ROW LEVEL SECURITY;

-- 6. Restrict access to service_role to protect encrypted secrets
DROP POLICY IF EXISTS "Allow service role full access" ON public.vault_configs;
CREATE POLICY "Allow service role full access"
    ON public.vault_configs
    FOR ALL
    TO service_role
    USING (true)
    WITH CHECK (true);

DROP POLICY IF EXISTS "Allow service role full access on users" ON public.users;
CREATE POLICY "Allow service role full access on users"
    ON public.users
    FOR ALL
    TO service_role
    USING (true)
    WITH CHECK (true);
