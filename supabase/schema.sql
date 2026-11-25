-- =============================================================================
-- PatchPilot — Supabase PostgreSQL Schema
-- Tables: workspaces, workspace_members, workspace_active_committers,
--         review_usage_events, billing_webhook_events, vault_configs, users
-- Enforces Row Level Security (RLS) restricting access to service_role.
-- =============================================================================

-- 1. Workspaces (multi-tenant root keyed by immutable GitHub account & installation)
CREATE TABLE IF NOT EXISTS public.workspaces (
    id VARCHAR(64) PRIMARY KEY,
    github_account_id BIGINT NOT NULL UNIQUE,
    github_login VARCHAR(255) NOT NULL,
    account_type VARCHAR(32) NOT NULL DEFAULT 'User',
    github_installation_id BIGINT UNIQUE,
    installation_status VARCHAR(32) NOT NULL DEFAULT 'active',
    key_mode VARCHAR(16) NOT NULL DEFAULT 'byok',
    plan_tier VARCHAR(32) NOT NULL DEFAULT 'unsubscribed',
    subscription_status VARCHAR(32) NOT NULL DEFAULT 'none',
    current_period_start TIMESTAMPTZ,
    current_period_end TIMESTAMPTZ,
    cancel_at_period_end BOOLEAN NOT NULL DEFAULT FALSE,
    polar_customer_id VARCHAR(128),
    polar_subscription_id VARCHAR(128) UNIQUE,
    billing_email VARCHAR(320),
    seat_cap INTEGER,
    created_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc'::text, now()),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc'::text, now())
);

-- 2. Workspace Members (verified Clerk user <-> GitHub Workspace RBAC cache)
CREATE TABLE IF NOT EXISTS public.workspace_members (
    workspace_id VARCHAR(64) NOT NULL REFERENCES public.workspaces(id) ON DELETE CASCADE,
    clerk_user_id VARCHAR(255) NOT NULL,
    github_user_id BIGINT,
    github_login VARCHAR(255),
    role VARCHAR(32) NOT NULL DEFAULT 'member',
    verified_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc'::text, now()),
    PRIMARY KEY (workspace_id, clerk_user_id)
);

-- 3. Active Committers Ledger (unique human PR authors per billing cycle + Polar meter outbox)
CREATE TABLE IF NOT EXISTS public.workspace_active_committers (
    workspace_id VARCHAR(64) NOT NULL REFERENCES public.workspaces(id) ON DELETE CASCADE,
    billing_period VARCHAR(32) NOT NULL,
    github_author_id BIGINT NOT NULL,
    github_author_login VARCHAR(255) NOT NULL,
    review_count INTEGER NOT NULL DEFAULT 1,
    managed_review_count INTEGER NOT NULL DEFAULT 0,
    meter_event_id VARCHAR(128) UNIQUE,
    meter_reported_at TIMESTAMPTZ,
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc'::text, now()),
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc'::text, now()),
    PRIMARY KEY (workspace_id, billing_period, github_author_id)
);

-- 4. Review Usage Events (audit trail + token telemetry)
CREATE TABLE IF NOT EXISTS public.review_usage_events (
    id VARCHAR(64) PRIMARY KEY,
    workspace_id VARCHAR(64) NOT NULL REFERENCES public.workspaces(id) ON DELETE CASCADE,
    repo_full_name VARCHAR(512) NOT NULL,
    is_private BOOLEAN NOT NULL DEFAULT FALSE,
    pr_number INTEGER NOT NULL DEFAULT 0,
    github_author_id BIGINT,
    github_author_login VARCHAR(255) NOT NULL DEFAULT '',
    trigger_type VARCHAR(32) NOT NULL DEFAULT 'pr_review',
    key_mode VARCHAR(16) NOT NULL DEFAULT 'byok',
    provider VARCHAR(50) NOT NULL DEFAULT '',
    model VARCHAR(255) NOT NULL DEFAULT '',
    prompt_tokens INTEGER,
    completion_tokens INTEGER,
    created_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc'::text, now())
);

-- 5. Billing & Auth Webhook Idempotency Ledger
CREATE TABLE IF NOT EXISTS public.billing_webhook_events (
    event_id VARCHAR(128) PRIMARY KEY,
    provider VARCHAR(32) NOT NULL,
    event_type VARCHAR(128) NOT NULL,
    processed_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc'::text, now())
);

-- 6. Vault Configs (BYOK encrypted provider credentials)
CREATE TABLE IF NOT EXISTS public.vault_configs (
    owner VARCHAR(255) PRIMARY KEY,
    workspace_id VARCHAR(64) UNIQUE REFERENCES public.workspaces(id) ON DELETE SET NULL,
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
ALTER TABLE public.vault_configs ADD COLUMN IF NOT EXISTS workspace_id VARCHAR(64);

-- 7. Users (authenticated Clerk / GitHub identities)
CREATE TABLE IF NOT EXISTS public.users (
    login VARCHAR(255) PRIMARY KEY,
    clerk_id VARCHAR(255),
    github_user_id BIGINT,
    github_username VARCHAR(255),
    email VARCHAR(320),
    name VARCHAR(255),
    avatar_url VARCHAR(1024),
    encrypted_github_token TEXT NOT NULL DEFAULT '',
    auth_provider VARCHAR(50) NOT NULL DEFAULT 'github',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc'::text, now())
);

ALTER TABLE public.users ADD COLUMN IF NOT EXISTS github_user_id BIGINT;

-- 8. Explicit Indexes
CREATE INDEX IF NOT EXISTS idx_workspaces_login ON public.workspaces (github_login);
CREATE INDEX IF NOT EXISTS idx_workspaces_polar_customer ON public.workspaces (polar_customer_id);
CREATE INDEX IF NOT EXISTS idx_workspace_members_clerk_user ON public.workspace_members (clerk_user_id);
CREATE INDEX IF NOT EXISTS idx_workspace_members_github_user ON public.workspace_members (github_user_id);
CREATE INDEX IF NOT EXISTS idx_active_committers_unreported
    ON public.workspace_active_committers (workspace_id)
    WHERE meter_reported_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_review_usage_workspace_created
    ON public.review_usage_events (workspace_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_review_usage_repo ON public.review_usage_events (repo_full_name);
CREATE INDEX IF NOT EXISTS idx_vault_configs_owner ON public.vault_configs (owner);
CREATE UNIQUE INDEX IF NOT EXISTS idx_vault_configs_workspace_id
    ON public.vault_configs (workspace_id)
    WHERE workspace_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_users_login ON public.users (login);
CREATE INDEX IF NOT EXISTS idx_users_clerk_id ON public.users (clerk_id);
CREATE INDEX IF NOT EXISTS idx_users_github_user_id ON public.users (github_user_id);
CREATE INDEX IF NOT EXISTS idx_users_github_username ON public.users (github_username);

-- 9. Updated-at Triggers
CREATE OR REPLACE FUNCTION public.handle_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = timezone('utc'::text, now());
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trigger_workspaces_updated_at ON public.workspaces;
CREATE TRIGGER trigger_workspaces_updated_at
    BEFORE UPDATE ON public.workspaces
    FOR EACH ROW
    EXECUTE FUNCTION public.handle_updated_at();

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

-- 10. Row Level Security (RLS) — lock all multi-tenant secrets & billing rows to service_role
ALTER TABLE public.workspaces ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.workspace_members ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.workspace_active_committers ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.review_usage_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.billing_webhook_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.vault_configs ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.users ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Allow service role full access on workspaces" ON public.workspaces;
CREATE POLICY "Allow service role full access on workspaces"
    ON public.workspaces FOR ALL TO service_role USING (true) WITH CHECK (true);

DROP POLICY IF EXISTS "Allow service role full access on workspace_members" ON public.workspace_members;
CREATE POLICY "Allow service role full access on workspace_members"
    ON public.workspace_members FOR ALL TO service_role USING (true) WITH CHECK (true);

DROP POLICY IF EXISTS "Allow service role full access on workspace_active_committers" ON public.workspace_active_committers;
CREATE POLICY "Allow service role full access on workspace_active_committers"
    ON public.workspace_active_committers FOR ALL TO service_role USING (true) WITH CHECK (true);

DROP POLICY IF EXISTS "Allow service role full access on review_usage_events" ON public.review_usage_events;
CREATE POLICY "Allow service role full access on review_usage_events"
    ON public.review_usage_events FOR ALL TO service_role USING (true) WITH CHECK (true);

DROP POLICY IF EXISTS "Allow service role full access on billing_webhook_events" ON public.billing_webhook_events;
CREATE POLICY "Allow service role full access on billing_webhook_events"
    ON public.billing_webhook_events FOR ALL TO service_role USING (true) WITH CHECK (true);

DROP POLICY IF EXISTS "Allow service role full access" ON public.vault_configs;
CREATE POLICY "Allow service role full access"
    ON public.vault_configs FOR ALL TO service_role USING (true) WITH CHECK (true);

DROP POLICY IF EXISTS "Allow service role full access on users" ON public.users;
CREATE POLICY "Allow service role full access on users"
    ON public.users FOR ALL TO service_role USING (true) WITH CHECK (true);
