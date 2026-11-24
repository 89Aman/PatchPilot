# Review worker operations

Webhook requests persist encrypted payloads in `review_jobs` before acknowledgement.
Each API instance runs a polling worker. Claims use a conditional SQL update and a
10-minute lease; an 8-minute execution deadline bounds each attempt. Jobs retry at
most three times with exponential delay. A crashed worker's lease is reclaimed.
Terminal jobs erase their payload and retain metadata for deduplication.

The app creates `review_jobs`, `review_budgets`, and `review_budget_reservations` at startup using its existing
schema initialization. The database user needs permission to create these tables.
All API replicas must use the same PostgreSQL database and encryption key.
Budgets are shared across instances: 25 newly processed jobs per repository in a
rolling hour. A repository row serializes reservations, and each job's reservation
is saved atomically with the debit. Unauthorized jobs do not consume the budget;
retries reuse a persisted reservation, while an interrupted job without a reservation
must acquire one. Existing fixed-window usage is conservatively retained until its
original expiry during upgrade.

Cloud Run allocates CPU outside requests while instances are active via
`--no-cpu-throttling`. Cloud Build defaults `_MIN_INSTANCES` to `1` because a SQL
poller cannot wake a service that has scaled to zero. Webhooks return immediately
after the enqueue transaction commits; the lifespan-managed worker drains jobs.
This incurs idle instance charges. Setting `_MIN_INSTANCES=0` is only appropriate
with a separately provisioned durable wake-up mechanism or continuously running
worker; otherwise pending retries can stall until another request starts an instance.
See [Cloud Run billing and CPU allocation](https://docs.cloud.google.com/run/docs/configuring/billing-settings).
Docker Compose already runs a persistent process and needs no separate worker.

HTTP permission/validation failures are terminal. Rate limits and server failures
use bounded attempt counts and exponential delays, honoring `Retry-After` and
rate-limit reset times. Graceful worker cancellation finalizes available handles
and releases the job for retry; abrupt termination still recovers through lease
expiry. GitHub check-completion failures now propagate to the queue instead of
marking the job successful. If GitHub remains unavailable or rejects permissions,
finalization is best effort and requires operational follow-up after retry exhaustion.

Monitor jobs with status `failed`, `rate_limited`, and expired `running` leases.
After fixing a provider/configuration failure, a new maintainer comment creates a
new job. Redelivery of the same event is deduplicated, including completed jobs.
Automatic PR jobs for older heads/closed PRs are skipped before analysis. Retries
reuse persisted check/review handles. GitHub writes and SQL commits are not a
single transaction: a crash after a GitHub write but before its handle is saved can
still duplicate an external side effect. This is at-least-once processing, not an
exactly-once delivery guarantee.

Known secret patterns are redacted before LLM calls, but queued raw webhook payloads
are encrypted at rest. Backups containing queued jobs need the same access controls
as the credential vault. Retain terminal IDs as long as redelivery deduplication is
needed; removing them allows old deliveries to be processed again.

Validation uses SQLite and mocked HTTP services. Exercise a worker restart and
concurrent workers against staging PostgreSQL before promoting to production.
