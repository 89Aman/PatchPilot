# PatchPilot pricing, comparison and Contact Sales design

## Decided prices

All amounts are USD. Display the annual equivalent alongside the annual amount. Prices can be published independently of checkout availability.

| Edition | Monthly | Annual | Scope and action |
| --- | --- | --- | --- |
| Self-Hosted (MIT) | $0 license fee | $0 license fee | Customer supplies infrastructure and model access. Link to self-hosting instructions. |
| Solo | $9/month | $7/month equivalent, $84/year | One personal GitHub account. Show Subscribe only for a verified billing cycle. Otherwise show the price and a plain availability message. |
| Team | $15 per active developer/month | $12 per active developer/month equivalent, $144 per active developer/year | Up to five distinct human PR authors per workspace billing cycle. Contact Sales. |
| Enterprise | Custom | Custom | Six or more active developers, or an explicit Enterprise inquiry at any size. Contact Sales. |

The annual Team figure is a unit price. A quote or checkout must explain how changes in active developer count affect the bill before accepting payment. Omit a shared percentage-discount badge because Solo and Team have different effective discounts.

## Public experience

Extend the redesigned Overview, Docs and Pricing shell. Keep the compact four-edition homepage teaser, existing setup and review examples, pricing cost explanation, docs, legal links and dashboard entry. Replace provisional pricing copy with the decided editions. The comparison table groups Limits & Billing, Core PR Review Engine, AI Models & Vault, and Governance, Hosting & Support. Use visible text for each cell. Label a feature Included only after verifying its implementation and edition entitlement. Mark proposed SSO/SAML, dedicated deployments, custom model endpoints and SLA terms as subject to agreement or planned until actually offered. Read managed review allowances from enforced configuration.

Use shared local theme tokens, plum surfaces, warm-white text, citron actions, the bundled Barlow Condensed display font and the existing status colors. Use the shared header, footer, stylesheet and section navigation. Server-render prices, comparison headings and both billing periods for direct visits and non-JavaScript readers. Preserve per-route canonical URLs, metadata, sitemap and share preview. On small screens constrain horizontal scrolling to the comparison table.

## Seats and contact routing

Five is the Team product ceiling. Define it once in entitlement policy and derive pricing labels, form bands and API routing from it. A workspace administrator can choose a lower cap. The effective Team cap is the lower of those limits, including when the optional workspace cap is unset. Count distinct eligible human authors atomically. Repeated reviews by the same author consume one seat within the cycle. Exclude bots. Sales routing records interest and never changes a subscription.

## Contact contract

Use optional, empty-by-default CONTACT_SALES_EMAIL, CONTACT_BOOKING_URL and CONTACT_WEBHOOK_URL. Use configured install and base URLs. Never expose the notification webhook or add tenant-specific defaults. POST /api/contact validates the inquiry fields below, plus a hidden website honeypot. Trim inputs, require a valid email and nonempty name and company, and limit notes to 2,000 characters.

- `id` (`VARCHAR(64)` PK)
  - `requested_plan` (`VARCHAR(32)` NOT NULL: `team` | `enterprise`)
  - `routed_plan` (`VARCHAR(32)` NOT NULL: `team` | `enterprise`)
  - `name` (`VARCHAR(200)` NOT NULL)
  - `email` (`VARCHAR(320)` NOT NULL)
  - `company_or_org` (`VARCHAR(255)` NOT NULL)
  - `team_size` (`VARCHAR(32)` NOT NULL: `2-5`, `6-15`, `16-50`, `51+`)
  - `billing_cycle` (`VARCHAR(16)` NOT NULL: `monthly` | `annual`)
  - `deployment_pref` (`VARCHAR(32)` NOT NULL: `hosted_cloud` | `byok_cloud` | `dedicated_vpc` | `self_hosted_support`)
  - `message` (`TEXT` NOT NULL DEFAULT `''`)
  - `status` (`VARCHAR(32)` NOT NULL DEFAULT `'new'`)
  - `created_at` (`TIMESTAMPTZ` NOT NULL DEFAULT now())

Return 201 with ok, id, requested_plan, routed_plan, upgraded_to_enterprise and contact_email after commit. Preserve initial Team interest when the form recommends Enterprise. Explicit Enterprise always stays Enterprise. Invalid requests return 422. The honeypot returns a benign 200 without persistence or notification. Apply a shared five-request limit per 15 minutes using the client address resolved by trusted ingress. Excess requests return 429. Send optional notifications after commit with a bounded timeout. Notification failure must preserve the inquiry.

Create the production table, index, privileges and row-level security together in a transactional, versioned PostgreSQL migration. Public and authenticated client roles have no table access. The server uses a privileged role. Production ORM startup must never create this table without its policy. Keep the bootstrap schema aligned and allow SQLite schema creation in isolated tests.
