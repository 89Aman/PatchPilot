-- Migration: 20261125000000_create_contact_inquiries.sql
-- Description: Create contact_inquiries and contact_rate_limits with service_role RLS and indexes.

BEGIN;

CREATE TABLE IF NOT EXISTS public.contact_inquiries (
    id VARCHAR(64) PRIMARY KEY,
    requested_plan VARCHAR(32) NOT NULL,
    routed_plan VARCHAR(32) NOT NULL,
    name VARCHAR(200) NOT NULL,
    email VARCHAR(320) NOT NULL,
    company_or_org VARCHAR(255) NOT NULL,
    team_size VARCHAR(32) NOT NULL,
    billing_cycle VARCHAR(16) NOT NULL,
    deployment_pref VARCHAR(32) NOT NULL,
    message TEXT NOT NULL DEFAULT '',
    status VARCHAR(32) NOT NULL DEFAULT 'new',
    created_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc'::text, now())
);

CREATE INDEX IF NOT EXISTS idx_contact_inquiries_status_created_at
    ON public.contact_inquiries (status, created_at DESC);

ALTER TABLE public.contact_inquiries ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON public.contact_inquiries FROM anon, authenticated, public;
GRANT ALL ON public.contact_inquiries TO service_role;

DROP POLICY IF EXISTS "Allow service role full access on contact_inquiries" ON public.contact_inquiries;
CREATE POLICY "Allow service role full access on contact_inquiries"
    ON public.contact_inquiries FOR ALL TO service_role USING (true) WITH CHECK (true);


CREATE TABLE IF NOT EXISTS public.contact_rate_limits (
    id BIGSERIAL PRIMARY KEY,
    client_ip VARCHAR(128) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc'::text, now())
);

CREATE INDEX IF NOT EXISTS idx_contact_rate_limits_client_ip_created_at
    ON public.contact_rate_limits (client_ip, created_at DESC);

ALTER TABLE public.contact_rate_limits ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON public.contact_rate_limits FROM anon, authenticated, public;
GRANT ALL ON public.contact_rate_limits TO service_role;

DROP POLICY IF EXISTS "Allow service role full access on contact_rate_limits" ON public.contact_rate_limits;
CREATE POLICY "Allow service role full access on contact_rate_limits"
    ON public.contact_rate_limits FOR ALL TO service_role USING (true) WITH CHECK (true);

COMMIT;
