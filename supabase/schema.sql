-- =============================================================================
-- PatchPilot — Supabase Schema
-- Table: vault_configs
-- Stores BYOK (Bring-Your-Own-Key) AI provider settings encrypted via AES-256-GCM
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
    updated_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc'::text, now())
);

-- 2. Explicit index for owner lookups
CREATE INDEX IF NOT EXISTS idx_vault_configs_owner ON public.vault_configs (owner);

-- 3. Trigger to maintain updated_at timestamp on row changes
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

-- 4. Enable Row Level Security (RLS)
ALTER TABLE public.vault_configs ENABLE ROW LEVEL SECURITY;

-- 5. Restrict access to service_role to protect encrypted secrets
DROP POLICY IF EXISTS "Allow service role full access" ON public.vault_configs;
CREATE POLICY "Allow service role full access"
    ON public.vault_configs
    FOR ALL
    TO service_role
    USING (true)
    WITH CHECK (true);
