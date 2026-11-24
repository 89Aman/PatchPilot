# Follow-up review of open issues

Reviewed all nine open issues (#2–#10) against `main` at `c0544f0`, both existing
pull requests (#1 and #11), and the discussion on #11. Both prior PRs were already
merged; no open PR duplicated this work. PR #11 landed in `developement`, with its
changes subsequently included in `main`. This follow-up targets `main`.

| Issue | Result and remaining action |
| --- | --- |
| #2: Clerk authentication | Existing signature, issuer, expiry, subject, and server-derived identity fixes remain in place and are covered by the regression suite. Deployment owners must still rotate `SESSION_SECRET`, audit legacy account links, and migrate any username-keyed vaults through a verified identity flow. This task does not access production configuration. |
| #3: Owner isolation | Removed the later single-paid-vault fallback. A missing owner never borrows an unrelated key, even if only one paid vault exists. Exact owners and explicit SQL account links remain supported. |
| #4: Interactive authorization and budgets | Existing comment association and bot checks are retained. Automatic and interactive jobs now share rolling database reservations that survive interruption without bypassing or double-charging the limit. |
| #5: OAuth state | Existing browser-bound, short-lived HttpOnly state cookie and callback validation remain in place. Both callback aliases are revalidated for missing/mismatched state and successful sign-in. |
| #6: Prompt redaction | Redact raw diffs before excerpting so a PEM body cannot be separated from its header. Preserve line counts and diff prefixes when masking multiline keys. All existing prompt-boundary transport tests remain enabled. Recognized patterns are not a guarantee of detecting unknown secret formats. |
| #7: Durable processing | Preserve the durable queue; make reservations atomic, release cancelled work, propagate check-completion failures, remove request-spawned workers, and keep a polling instance available by default. PostgreSQL multi-instance/restart validation and deployment rollout remain operational prerequisites. |
| #8: Truthful review results | Validate typed finding and optional review fields in both passes; invalid locations produce an inconclusive warning instead of PASS. Only a successful verifier gets the two-pass label. |
| #9: Prompt budgets | Remove minimum file allocations; budget separators/headings/omission notices and retain complete head/tail lines across files. Use a common 32,000-character automatic diff budget for both passes. Reject an oversized verifier prompt before transport instead of cutting candidate JSON or source lines. |
| #10: Publication errors | Preserve existing inline 422 fallback; report summary-only publication for fresh reviews too. Permanent HTTP errors stop retrying, transient errors respect backoff, and failed check updates are surfaced. |

The secret findings in PR #11's discussion point to synthetic test fixtures, not
production credentials. Its purported malformed PEM test assertion does not
reproduce: the original tests pass. The duplicate error-handler guard is removed.

## Rollout requirements

- Rotate the session-signing secret and inspect legacy identity links before
  treating #2 as fully remediated in a deployed environment.
- All workers need the same PostgreSQL database and vault encryption key. Startup
  must be allowed to create the new reservation table. Use a coordinated rollout
  so old fixed-window workers do not run alongside the new rolling-budget workers.
- The default Cloud Run minimum changes from zero to one, with ongoing idle cost.
  No deployment or billing setting was changed by creating this PR.
- Validate concurrency and forced restart recovery on staging PostgreSQL before
  closing #7 operationally. GitHub writes remain at-least-once; a crash between a
  successful remote write and saving its handle can duplicate a side effect.
- If GitHub permissions prevent finalizing a check/card even after retries, restore
  permissions and follow up on the failed job. A database cannot force a rejected
  remote write to succeed.
