<!-- coderabbit-ai-generated:v1 -->

<!-- coderabbit-ai-generated-section:v1:ce3d71a29860de9db01555610f3da0b97bad68f6159e9eca68588585ddc6157b -->

# Deployment and production limitations

This repository is a working demo/staging foundation, not a claim of production readiness.

## Payment

- `SimulatedPaymentProvider` only returns deterministic mock capture/refund results. It never contacts a bank, gateway or wallet.
- The `PaymentProvider` protocol is the adapter boundary. A real provider integration still needs provider checkout/session creation, signature-verified webhooks, replay protection, reconciliation, provider idempotency, refund retries and operational support.
- The member payment-success message creates a pending review, not a payment. Admin approval confirms the reservation and demo payment ledger in one transaction. There is no independent settlement evidence; a mistaken or compromised admin can approve an untrue claim.

- Recurring creation is disabled whenever admin review is required because the legacy series operation confirms without checkout/review. This remains an original-brief gap.

## Email

- The outbox is durable and uses row locking/skip-locked claims, retry backoff and a dead-letter state.
- Console mode records a simulated message and creates a truthful “Demo message recorded” notification. It sends neither email nor SMS. Without SMTP, demo registration requires admin approval, which does not prove mailbox ownership. Review mode does not expose verification tokens.

- Configure Gmail SMTP using a Gmail App Password or replace `deliver_email` with a real provider adapter. SMTP delivery is at-least-once: a worker crash after the remote server accepts a message but before the outbox row is marked sent can produce a duplicate.
- The admin dashboard shows up to 100 outbox records (dead first) and allows retry of dead messages. Workers use five-minute leases and UUID fencing, retry up to five attempts, then stop until an admin retries. There is no automatic alert delivery, archive/retention policy or complete paginated message history.

## Infrastructure

- The two EC2 nodes share one PostgreSQL and Redis host. That host is a single point of failure and is not a multi-AZ database cluster.
- The database, Redis, monitoring and logs are self-hosted. Backups, restore drills, replication, retention policy, secrets rotation and patching are operator responsibilities.
- ALB, EC2, EBS, public IPv4 and transfer charges depend on account eligibility, region, usage and current AWS pricing. Terraform deliberately requires an explicit charge acknowledgement.
- The demo launcher defaults to HTTP on the AWS ALB hostname. This explicit exception permits unencrypted login/session traffic; browser geolocation generally requires HTTPS. For transport protection use a matching domain/certificate and set ALLOW_HTTP_DEMO=false. Direct Terraform/application defaults still require HTTPS. DNS and certificate issuance for HTTPS remain operator steps.
- Grafana and Prometheus ports are reachable only from the `admin_cidr` supplied to Terraform. Change that CIDR if your public IP changes.
- T3 instances use Standard CPU credit mode to avoid unlimited-mode surplus CPU credit charges. Under sustained CPU load, performance may be constrained.
- This demo has no WAF, bot challenge, CDN, DDoS advanced protection, multi-region recovery or automated security patch pipeline. The Nginx/Redis rate limits are baseline abuse controls, not a substitute for AWS WAF/Shield or application security review.

## Review and client freshness

- Review deadlines are at most 15 minutes from the first report and never later than booking start. A slow/unavailable admin can lose the slot. There is no indefinite reservation or approval override after expiry.
- PostgreSQL durability depends on its storage/backups. In-app notification rows and outbox entries grow until an operator introduces a retention policy. A queue backlog can delay delivery; no throughput/SLA claim or load benchmark is provided.
- Idle/hidden tabs pause polling. Active tabs can show data up to a refresh interval old; court metadata can be cached for 30 seconds. Offline tabs cannot receive current data. Every mutation rechecks database state and constraints.
- Lists are bounded (latest 100 bookings, 50 notifications, 100 admin rows); this demo does not yet offer full-history pagination or search. Old records remain in PostgreSQL.
- Migration 0004 adds the review table and simulated outbox state. Its downgrade deletes review history and maps simulated messages to the old sent state; back up first and do not treat downgraded history as delivery evidence.
- Runtime declarations still target Python 3.14/PostgreSQL 16. The original Python 3.11/PostgreSQL 15 deployment requirement and the original pre-build design review milestone are not fulfilled by this patch. No real payment, SMS, AWS rollout or client live walkthrough is claimed.

## Application

- Access JWTs are short-lived and there is no refresh-token rotation or password-reset flow yet.
- Role assignment is bootstrap-only; there is no public role-grant endpoint. This prevents self-promotion by normal users.
- Geolocation uses opt-in coordinates and a simple Haversine calculation. It is not a precise geospatial database index and does not track users in the background.
- The Prometheus instrumentation is request-level; exporters provide host, PostgreSQL and Redis metrics. Alert rules and alert delivery are not configured.
- Run database migrations as a controlled deployment step; do not run multiple competing migration jobs at once.


<!-- /coderabbit-ai-generated:v1 -->