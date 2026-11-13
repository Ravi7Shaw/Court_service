# Deployment and production limitations

This repository is a working demo/staging foundation, not a claim of production readiness.

## Payment

- `SimulatedPaymentProvider` only returns deterministic mock capture/refund results. It never contacts a bank, gateway or wallet.
- The `PaymentProvider` protocol is the adapter boundary. A real provider integration still needs provider checkout/session creation, signature-verified webhooks, replay protection, reconciliation, provider idempotency, refund retries and operational support.
- The payment simulation marks a hold confirmed in the same database transaction as its payment ledger row. Do not treat this as proof of a real-world payment.
- Recurring bookings are disabled in the production deployment because their current domain operation creates confirmed reservations without a payment checkout flow.

## Email

- The outbox is durable and uses row locking/skip-locked claims, retry backoff and a dead-letter state.
- Console mode logs a simulated message and creates an in-app “Email sent” notification. It does not send email. The demo exposes the verification token in this mode so registration can be tested end-to-end.
- Configure Gmail SMTP using a Gmail App Password or replace `deliver_email` with a real provider adapter. SMTP delivery is at-least-once: a worker crash after the remote server accepts a message but before the outbox row is marked sent can produce a duplicate.
- There is no admin UI for dead-letter inspection/replay yet; inspect `notification_outbox` and worker logs.

## Infrastructure

- The two EC2 nodes share one PostgreSQL and Redis host. That host is a single point of failure and is not a multi-AZ database cluster.
- The database, Redis, monitoring and logs are self-hosted. Backups, restore drills, replication, retention policy, secrets rotation and patching are operator responsibilities.
- ALB, EC2, EBS, public IPv4 and transfer charges depend on account eligibility, region, usage and current AWS pricing. Terraform deliberately requires an explicit charge acknowledgement.
- HTTP is used when no ACM certificate ARN is supplied. Do not use that mode for real passwords on a public website.
- Grafana and Prometheus ports are reachable only from the `admin_cidr` supplied to Terraform. Change that CIDR if your public IP changes.
- T3 instances use Standard CPU credit mode to avoid unlimited-mode surplus CPU credit charges. Under sustained CPU load, performance may be constrained.
- This demo has no WAF, bot challenge, CDN, DDoS advanced protection, multi-region recovery or automated security patch pipeline. The Nginx/Redis rate limits are baseline abuse controls, not a substitute for AWS WAF/Shield or application security review.

## Application

- Access JWTs are short-lived and there is no refresh-token rotation or password-reset flow yet.
- Role assignment is bootstrap-only; there is no public role-grant endpoint. This prevents self-promotion by normal users.
- Geolocation uses opt-in coordinates and a simple Haversine calculation. It is not a precise geospatial database index and does not track users in the background.
- The Prometheus instrumentation is request-level; exporters provide host, PostgreSQL and Redis metrics. Alert rules and alert delivery are not configured.
- Run database migrations as a controlled deployment step; do not run multiple competing migration jobs at once.
