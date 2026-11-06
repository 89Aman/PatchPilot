# Review worker operations

Webhook requests persist encrypted payloads in `review_jobs` before acknowledgement.
Each API instance runs a polling worker. Claims use a conditional SQL update and a
10-minute lease; an 8-minute execution deadline bounds each attempt. Jobs retry at
most three times with exponential delay. A crashed worker's lease is reclaimed.
Terminal jobs erase their payload and retain metadata for deduplication.

The app creates `review_jobs` and `review_budgets` at startup using its existing
schema initialization. The database user needs permission to create these tables.
All API replicas must use the same PostgreSQL database and encryption key.
Budgets are shared across instances: 25 newly processed jobs per repository per
hour. Unauthorized jobs do not consume the budget; retries do not reserve again.

Cloud Run allocates CPU outside requests while instances are active via
`--no-cpu-throttling`, while `--min-instances 0` allows idle instances to scale
to zero for cost efficiency. Each incoming webhook enqueues the job and kicks off
an immediate worker cycle before returning, while the background worker loop
drains any remaining or retried jobs while the instance is warm. Docker Compose
already runs a persistent process and does not need a separate worker service.

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
