# PatchPilot pricing, four-edition comparison and Contact Sales implementation plan

The [approved pricing and design specification](../specs/2026-11-25-pricing-comparison-contact-design.md) is the implementation contract. Prices are decided. Self-Hosted is $0, Solo is $9 monthly or $84 annually ($7/month equivalent), Team is $15 monthly or $144 annually per active developer ($12/month equivalent), and Enterprise is custom.

## 1. Integrate the redesigned site before editing pricing

- [ ] Use the Antigravity feat/auth-and-billing checkout as the integration branch. Import the reviewed UI changes from the **working tree** at C:/Users/Asus/Documents/ChatGPT/patchpilot. Some redesign files are uncommitted. Inspect both diffs and port presentation changes selectively. Preserve the billing branch’s workspace authorization, backend routes, provider integrations and environment-driven links.
- [ ] Bring across the shared guide base and header, site-navigation.js, theme.css, landing.css, local font and license, themed dashboard styles and build configuration, icons and share preview. Reconcile affected templates with the branch’s existing tests for tenant-neutral public URLs. Verify dashboard/settings and legal pages still use the same palette. Use existing theme tokens for the comparison, toggle and dialog. No runtime Tailwind CDN or second theme.
- [ ] Establish a passing baseline for both the billing branch tests and the redesigned public navigation before adding pricing changes.

## 2. Align billing, seats and advertised features

- [ ] Use one server-side edition catalog for the teaser, pricing table and checkout mapping. Test all decided amounts, their units and annual totals. Show subscription actions only when the selected cycle has a configured, verified provider price with the matching amount, interval and resulting workspace entitlement. Exercise checkout in provider test mode through entitlement fulfillment. For unavailable cycles keep prices visible and give an availability message.
- [ ] Implement the five-author hard ceiling in the actual review entitlement path. Apply a lower optional workspace cap when configured. Test the fifth author, sixth distinct author, returning author at capacity, bot exclusion, concurrent first reviews at the boundary, and billing-period rollover. Confirm the annual Team seat reconciliation rule before enabling collection.
- [ ] Audit every feature cell against shipped code and entitlement checks. Record available versus planned or negotiated items, especially SSO, dedicated hosting and SLAs. Verify managed caps and BYOK wording against enforced limits.

## 3. Deploy inquiry storage and implement contact intake

- [ ] Add ContactInquiry and the fields defined in the spec. Deliver a versioned PostgreSQL migration that creates the table, status/creation index, grants and service-role-only RLS in one transaction. Update supabase/schema.sql for clean installs. Prevent production create_all from creating this table ahead of the migration. Run the migration before enabling the contact endpoint.
- [ ] Test an existing PostgreSQL installation and a fresh bootstrap. Verify that the backend role can persist an inquiry and that anonymous and authenticated client roles cannot read or insert it. Keep SQLite tests for validation and persistence.
- [ ] Implement POST /api/contact and ContactInquiryRequest according to the spec. Derive form size bands and backend routing from the same Team boundary. Test Team at five, Enterprise routing at six, explicit Enterprise at smaller sizes, malformed input, blank contact settings, honeypot no-op and post-commit webhook failure.
- [ ] Enforce the five-per-15-minute limit in shared storage or at trusted ingress. Use the normalized client identity from that boundary. Test trusted proxy traffic, untrusted forwarded headers, independent clients, expiry and a sixth submission split across two instances. Verify the rate-limited request produces no inquiry or notification.

## 4. Build the pricing view inside the existing tabs

- [ ] Extend /pricing in the shared shell and add the compact teaser to Overview. Preserve all existing product content and replace the old proposed-plan text. Use accessible monthly/annual controls and a semantic four-column comparison with row and column headers. Render useful prices and comparison content without JavaScript.
- [ ] Build a labeled native contact dialog for Team and Enterprise with the fields and response contract in the spec. Preserve the originally requested plan when recommending Enterprise. Include loading, validation, rate-limit, success and retry states. Prevent repeat clicks during submission. Show email and booking fallbacks only when configured.
- [ ] Initialize behavior on direct load and after site-navigation.js swaps a section. Handle /pricing?contact=team and enterprise, browser history, Escape, focus return and repeated tab visits. Keep listeners single-instance and reset stale dialog state between visits. Preserve the billing cycle selected when the dialog opens.

## 5. Release checks

- [ ] Run the complete branch test suite, PostgreSQL role checks and checkout test-mode flow. Verify tenant-neutral generated URLs and absence of notification secrets from public HTML.
- [ ] Browser-test Overview → Pricing → Team inquiry → success, asserting exactly one POST and one stored row. Repeat after switching tabs twice. Cover direct query links, pricing reload, Back/Forward, keyboard-only use, mobile table scrolling, no-JavaScript pricing and both configured and empty contact fallbacks.
- [ ] Compare desktop and 320px screenshots of Overview, Pricing, the dialog and Dashboard against the redesigned site. Verify canonical and share metadata after a tab switch, local stylesheet/font delivery and no page-wide horizontal overflow.
