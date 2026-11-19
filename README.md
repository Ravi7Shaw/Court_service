<!-- coderabbit-ai-generated:v1 -->

<!-- coderabbit-ai-generated-section:v1:070890aaf79f13d3409cb196d4f4b4de3bcc11ff7a85502f904f387aeb235cbb -->

# Court Service — deployable demo platform

A Python 3.14 court-booking service expanded with account registration, email verification, role-based admin endpoints, Redis rate limiting/cache, a transactional email outbox worker, simulated payments/refunds, opt-in location search, Nginx, and an AWS deployment using Terraform + Ansible.

> **Important:** this is a learning/demo deployment, not a production payment or highly-available booking platform. Payment and refund actions are simulated; no card data is collected and no real money moves. The database and Redis run on the primary EC2 node, so that node is a single point of failure. Read [LIMITATIONS.md](LIMITATIONS.md) before deploying.

## What is included

- **API:** FastAPI, SQLAlchemy 2, PostgreSQL 16, Alembic migrations, original booking exclusion constraint and idempotency behavior.
- **Authentication:** email/password registration, Argon2id password hashes, one-time email verification tokens, short-lived JWT access tokens, database-backed role checks (`member`, `staff`, `admin`).
- **Abuse controls:** Nginx request limits plus shared Redis rate limits on registration/login (IP and login-email buckets). In production, auth requests fail closed if Redis is unavailable. Passwords are never logged.
- **Payments:** members report a demo payment; admins approve the payment and final reservation together. Eligible cancelled bookings require admin refund approval. No real money moves. The provider protocol remains available for a future integration.

- **Notifications:** automatic transactional PostgreSQL outbox with leased `FOR UPDATE SKIP LOCKED` claims, stale-worker fencing, five-attempt retry/backoff, admin dead-letter replay, and in-app notifications. Console delivery is labelled simulated; SMTP is optional.

- **Location:** browser geolocation is requested only after the user clicks Share; consent is stored, nearby search uses the saved location, and users can delete it.
- **Admin:** dashboard queues for demo account verification, payment/booking and refund decisions, message status and dead-letter retry, plus court and venue management.

- **Frontend:** responsive vanilla HTML/CSS/JS website served by Nginx.
- **Observability:** Prometheus metrics, Grafana dashboard, Loki log store, Grafana Alloy log shipper, Node Exporter, Postgres Exporter and Redis Exporter. Alloy is used instead of Promtail because Promtail reached end of life on March 2, 2026; see [Grafana migration guidance](https://grafana.com/docs/alloy/latest/set-up/migrate/from-promtail/).
- **AWS:** VPC, two public subnets in separate AZs, ALB, two EC2 nodes, restricted security groups, encrypted EBS data volume, instance role/SSM, random credentials and generated Ansible inventory.

## Run locally with Docker Compose

Requirements: Docker Engine with the Compose plugin.

```bash
cp .env.example .env
# Edit .env and replace every LocalOnly / local password and JWT_SECRET.
docker compose up -d --build
```

Open:

- Website: <http://localhost:8080>
- API docs: <http://localhost:8080/docs>
- Grafana: <http://localhost:3000>
- Prometheus: <http://localhost:9090>

The local seed creates a demo venue with three courts and an administrator. Local-only demo defaults are:

- Email: `admin@court.local`
- Password: `LocalOnly-ChangeMe-123!`

Change this password immediately if you use the local setup beyond a disposable demo. The API's local environment allows legacy `X-Member-Id` and `X-Staff-Key` headers so the original tests continue to work; the AWS Ansible deployment explicitly disables those compatibility headers.

Run the complete test suite in its isolated PostgreSQL test container:

```bash
docker compose --profile test run --rm tests
```

For host-side testing, install the dev extra with `uv sync --python 3.14 --extra dev` and set `TEST_DATABASE_URL` to a disposable PostgreSQL database whose name ends in `_test` before running `uv run pytest -q`.

## Demo approval walkthrough

Read [DESIGN.md](DESIGN.md) for the state machine, assumptions, intentional API response change and unresolved original-brief gaps.

1. Register a member; sign in as the seeded administrator and choose **Verify demo account**.
2. Sign in as the member, reserve a court and submit a payment-success message. This is a claim for review, not payment settlement. The reservation shows **Awaiting admin**.
3. In the admin dashboard, approve or reject the request. Approval atomically confirms the booking and its demo ledger entry. Rejection frees the slot. Repeat decisions cannot create another payment.
4. Cancel at least 24 hours before the start for full refund eligibility, then request a refund. An admin must approve it; otherwise no refund is recorded.
5. Notifications queue automatically for members and admins. In console mode the outbox shows **simulated**, never actual email/SMS delivery. Dead messages can be retried from the admin dashboard.

Initial holds last five minutes. A first payment report changes the deadline to at most 15 minutes from reporting, capped by booking start; retries never extend it. Expired approvals cannot revive a reservation. Pending reviews count toward the three future booking limit.

Docker Compose already runs one shared worker. For host development with the same configured environment:

```bash
PYTHON_BIN=.venv/bin/python bash scripts/run-worker.sh
```

PostgreSQL stores offline members' messages. Hidden tabs and tabs idle for 60 seconds stop polling. Returning to the tab or interacting fetches current data; active tabs refresh every 15–20 seconds. There is no per-idle-user process or reserved connection. Run Ruff, mypy and pytest using the CI commands before delivery. Capacity and AWS deployment have not been certified by this patch.

For a live change walkthrough, adjust the review deadline in `app/services/approvals.py`, explain why retries retain the first deadline, and run `pytest -q tests/test_admin_review.py`. This script is a rehearsal guide; a live client walkthrough remains to be held.

## AWS two-node deployment

### Architecture

```text
Internet
   |
AWS Application Load Balancer (2 AZs)
   |----------------------------|
   |                            |
EC2 primary                  EC2 secondary
Nginx + API                  Nginx + API
PostgreSQL                   Prometheus
Redis                        Grafana
Outbox worker                Loki + Grafana Alloy
Grafana Alloy                     Node Exporter
Postgres/Redis exporters
```

Both web nodes serve the same frontend and API. The primary node owns PostgreSQL and Redis; the secondary node owns the monitoring UI and metrics/log storage. API requests on either node use the same primary PostgreSQL and Redis endpoints.

### AWS cost warning

Terraform intentionally requires `acknowledge_possible_charges=true`. This stack creates two EC2 instances, an Application Load Balancer, public IPv4 addresses, EBS volumes, and data transfer. These resources are **not guaranteed to be free**. Eligibility varies by AWS account creation date and Free Tier/credit status. AWS's EC2 Free Tier rules distinguish accounts created before and after July 15, 2025; ALB has its own limited offer and hourly/LCU pricing. Check the AWS Billing console and Pricing Calculator before applying.

- Default instance type is `t3.small` because the API, database, cache and monitoring stack need more than a tiny memory budget. Accounts created before July 15, 2025 had a 12-month legacy offer; by November 2026 that offer has expired for those accounts. Accounts created on or after that date can have `t3.small` eligibility for 6 months or until credits are used. See the [official EC2 Free Tier eligibility table](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/ec2-free-tier-usage.html).
- **Two always-on EC2 nodes consume about 1,440 instance-hours per 30-day month.** The legacy EC2 offer was 750 micro-instance hours per month, so this two-node design exceeded that allowance even if both nodes were changed to `t3.micro`. The new-account plan uses credits and lasts up to 6 months; check your actual balance and expiration in Billing before deploying. AWS documents the legacy allowance and new-account model in its [EC2 Free Tier guide](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/ec2-free-tier-usage.html) and [T2 Free Tier page](https://aws.amazon.com/ec2/instance-types/t2/).
- The ALB has a limited offer (750 hours and 15 LCUs for eligible new customers), but it is billed hourly/LCU outside that offer. AWS also charges **$0.005 per in-use public IPv4 address per hour**. This topology uses public IPs on both EC2 nodes and the public ALB; those address charges alone can be around $14.40 per 30-day month if four addresses are allocated continuously, before EC2/ALB/EBS/data-transfer charges. See [ALB pricing](https://aws.amazon.com/elasticloadbalancing/pricing/) and [VPC public IPv4 pricing](https://aws.amazon.com/vpc/pricing/).
- No NAT Gateway or managed ElastiCache/RDS resources are created. Redis and PostgreSQL are self-hosted to avoid managed-service costs.
- This topology is for a demo/staging environment. It is not multi-AZ database HA: if the primary EC2 node fails, both app nodes lose their database/cache connection.

### Prerequisites

- AWS account with permission to create VPC, EC2, EBS, ALB, IAM instance profile, security groups and key pairs.
- AWS credentials configured locally (`aws configure` or environment/SSO credentials).
- Terraform >= 1.9, Ansible, SSH and `curl`.
- An SSH key. If needed:

```bash
ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519
```

- Configure an issued ACM certificate and domain in `terraform.tfvars`; both are required for public HTTPS.

Without SMTP, new demo accounts wait for admin verification. This does not prove mailbox ownership.

- Set the ingress CIDR to your current public IPv4 `/32`. SSH, Grafana and Prometheus are restricted to this CIDR.

### One-command deployment

The `deploy/deploy.sh` entry point also accepts `DOMAIN_NAME` and `ACM_CERTIFICATE_ARN`; these override the corresponding Terraform variables for both configuration checks and the saved deployment plan. For example, after setting `acknowledge_possible_charges=true` in `deploy/terraform/terraform.tfvars`:

```bash
DOMAIN_NAME=courts.your-domain.com \
ACM_CERTIFICATE_ARN=arn:aws:acm:ap-southeast-2:YOUR_ACCOUNT:certificate/YOUR_CERTIFICATE \
bash deploy/deploy.sh
```

Replace the example values with your domain and an issued ACM certificate covering that domain in the deployment region. Point the domain at the ALB DNS name. With both HTTPS inputs empty, the launcher defaults to the HTTP demo; a partial pair fails before planning. Set `ALLOW_HTTP_DEMO=false` to require HTTPS. No certificate is created automatically. The script applies the exact saved plan after you type APPLY. A plan proposing creation of all resources may mean a new deployment or a different state/workspace/account: check before applying to an existing environment.

`bash deploy/destroy.sh` requires PLAN to create/show a saved destruction plan and DESTROY to apply that same plan. Temporary plan files are private and removed on exit. Review the resource list and back up any database you need before destruction. Neither script is a database backup facility.

From the repository root (optionally set your own bootstrap admin email):

```bash
BOOTSTRAP_ADMIN_EMAIL=you@gmail.com ./scripts/deploy-aws.sh
```

The script:

1. Detects your public IPv4 CIDR (override with `ADMIN_CIDR=x.x.x.x/32`).
2. Runs Terraform init/apply.
3. Creates two EC2 nodes, the ALB and encrypted volumes.
4. Generates an Ansible inventory and random database, Redis, JWT, admin and Grafana credentials.
5. Runs Ansible to install Docker, mount the primary data volume, copy the application, migrate the database, seed the demo venue/admin, and launch the containers.
6. Writes the credentials to `deploy/terraform/deployment-credentials.txt` with owner-only permissions.

Review the plan and AWS costs before confirming Terraform. The script sets the Terraform cost acknowledgement variable because you explicitly chose to deploy this infrastructure; that acknowledgement does not make resources free.

After deployment, the script prints the website, Grafana and Prometheus URLs. The generated credentials are stored in:

```text
deploy/terraform/deployment-credentials.txt
```

Keep this file and `terraform.tfstate` private. Do not commit either file.

### Separate Terraform and Ansible commands

The script above automates both. To run them separately:

```bash
cd deploy/terraform
terraform init
terraform fmt -recursive
terraform validate
terraform plan \
  -var="admin_cidr=$(curl -fsS https://checkip.amazonaws.com | tr -d '\n')/32" \
  -var="ssh_public_key_path=$HOME/.ssh/id_ed25519.pub" \
  -var="ssh_private_key_path=$HOME/.ssh/id_ed25519" \
  -var="acknowledge_possible_charges=true"
terraform apply \
  -var="admin_cidr=$(curl -fsS https://checkip.amazonaws.com | tr -d '\n')/32" \
  -var="ssh_public_key_path=$HOME/.ssh/id_ed25519.pub" \
  -var="ssh_private_key_path=$HOME/.ssh/id_ed25519" \
  -var="acknowledge_possible_charges=true"
cd ../..
```

Before running the AWS deployment, configure `deploy/terraform/terraform.tfvars` from `terraform.tfvars.example`. The Terraform configuration uses the fixed region `ap-southeast-2` and explicit Availability Zones to avoid `ec2:DescribeAvailabilityZones`. Set `ami_id` to an x86_64 Amazon Linux 2023 AMI ID copied from the EC2 console in Sydney; Terraform no longer calls `ec2:DescribeImages`. The two AZ names must be enabled and available in your account.

Then use `./scripts/deploy-aws.sh` to complete Ansible configuration. If Terraform is already applied, the script's `terraform apply` will be a no-op except for changes. Alternatively export the generated Terraform outputs and invoke Ansible directly:

```bash
export BOOTSTRAP_ADMIN_PASSWORD="$(terraform -chdir=deploy/terraform output -raw admin_password)"
export DB_PASSWORD="$(terraform -chdir=deploy/terraform output -raw db_password)"
export REDIS_PASSWORD="$(terraform -chdir=deploy/terraform output -raw redis_password)"
export JWT_SECRET="$(terraform -chdir=deploy/terraform output -raw jwt_secret)"
export GRAFANA_ADMIN_PASSWORD="$(terraform -chdir=deploy/terraform output -raw grafana_admin_password)"
export APP_BASE_URL="$(terraform -chdir=deploy/terraform output -raw application_url)"
export PRIMARY_PRIVATE_IP="$(terraform -chdir=deploy/terraform output -raw primary_private_ip)"
export SECONDARY_PRIVATE_IP="$(terraform -chdir=deploy/terraform output -raw secondary_private_ip)"
export ADMIN_EMAIL="$(terraform -chdir=deploy/terraform output -raw admin_email)"
ansible-playbook -i deploy/ansible/inventory.ini deploy/ansible/site.yml \
  --extra-vars "db_password=$DB_PASSWORD redis_password=$REDIS_PASSWORD jwt_secret=$JWT_SECRET admin_password=$BOOTSTRAP_ADMIN_PASSWORD grafana_password=$GRAFANA_ADMIN_PASSWORD application_url=$APP_BASE_URL admin_email=$ADMIN_EMAIL primary_private_ip=$PRIMARY_PRIVATE_IP secondary_private_ip=$SECONDARY_PRIVATE_IP"
```

### HTTP demo or HTTPS with a domain

The one-command demo launcher now defaults to HTTP using the AWS load balancer hostname, as requested. Run `bash deploy/deploy.sh` to deploy and `bash deploy/destroy.sh` to remove it (review their plan/confirmation prompts). No custom domain or certificate is needed for that demo. Login traffic is unencrypted; browser geolocation generally requires HTTPS. Use synthetic demo accounts.

Direct Terraform and application configuration remain HTTPS by default. For HTTPS, configure a domain and an issued ACM certificate in the same AWS region:

```bash
-var="acm_certificate_arn=arn:aws:acm:REGION:ACCOUNT:certificate/CERTIFICATE_ID" \
-var="domain_name=courts.example.com"
```

Terraform then creates an HTTPS listener and redirects HTTP to HTTPS. The certificate must already be issued and cover `domain_name`; point that DNS name to the ALB DNS output (CNAME/alias).

### Real email delivery with Gmail SMTP (optional)

With the default console backend, registration creates an unverified account for the admin dashboard to approve. No verification token is exposed in this review flow, including development. Admin approval grants demo access and does not verify mailbox ownership. SMTP is optional for real email verification and delivery. There is no SMS provider.

To send actual email, use a Gmail App Password (requires 2-Step Verification), not your normal Gmail password:

```bash
SMTP_HOST=smtp.gmail.com \
SMTP_PORT=587 \
SMTP_USERNAME=your-address@gmail.com \
SMTP_PASSWORD=your-16-character-app-password \
SMTP_FROM=your-address@gmail.com \
./scripts/deploy-aws.sh
```

With SMTP configured, the user can verify using the emailed link; an admin can also approve a demo account.

 SMTP STARTTLS validates the server certificate and hostname. SMTP secrets are passed only to the background worker environment, not the public API container.

### Destroy AWS resources

Set the same `ADMIN_CIDR` and key path used for deployment:

```bash
ADMIN_CIDR="$(curl -fsS https://checkip.amazonaws.com | tr -d '\n')/32" \
  ./scripts/destroy-aws.sh
```

Terraform destroy removes the ALB, EC2 instances, EBS data volume and generated resources. Export or back up any data you need first.

## Main application endpoints

| Method | Endpoint | Purpose |
|---|---|---|
| POST | `/api/auth/register` | Create member account; rate-limited |
| POST | `/api/auth/verify-email` | Consume one-time email verification token |
| POST | `/api/auth/login` | Sign in; rate-limited |
| GET | `/api/auth/me` | Current authenticated user and role |
| GET | `/api/courts` | Active courts; Redis cached briefly |
| POST | `/holds` | Create a temporary booking hold |
| POST | `/holds/{id}/confirm` | Submit for admin payment/booking review (202) |
| GET | `/courts/{id}/availability` | Current availability and venue prices |
| POST | `/api/payments/{reservation_id}/report` | Report a demo payment for admin review |
| POST | `/api/payments/{reservation_id}/simulate` | Compatibility alias; still requires admin review |
| GET | `/api/payments/{reservation_id}` | Read payment status |
| POST | `/bookings/{booking_id}/cancel` | Cancel booking and calculate refund eligibility |
| POST | `/api/payments/{reservation_id}/refund/request` | Request an eligible simulated refund for admin review |
| POST | `/api/payments/{reservation_id}/refund/simulate` | Compatibility alias; still requires admin review |
| GET | `/api/admin/approvals` | Oldest 100 pending reviews (admin only) |
| POST | `/api/admin/approvals/{id}/decision` | Final idempotent approve/reject decision |
| GET | `/api/admin/users/pending` | Demo accounts waiting for approval |
| POST | `/api/admin/users/{id}/verify` | Approve demo account access |
| GET | `/api/admin/outbox` | Recent message status, dead letters first |
| POST | `/api/admin/outbox/{id}/retry` | Requeue a dead message |
| GET | `/api/bookings/me` | List the current user's bookings |
| GET | `/api/notifications` | In-app notification feed |
| POST | `/api/me/location` | Save location after explicit consent |
| DELETE | `/api/me/location` | Revoke and delete saved location |
| GET | `/api/courts/nearby` | Nearby courts using saved opt-in location |
| POST | `/api/admin/courts` | Create court (admin only) |
| PUT | `/api/admin/venues/{venue_id}/coordinates` | Set venue coordinates (admin only) |
| GET | `/metrics` | Prometheus metrics |
| GET | `/docs` | OpenAPI documentation |

## Admin seed

`python -m app.seed` is idempotent. It creates the `Riverside Courts` demo venue and three courts if absent. It creates the first admin only when `BOOTSTRAP_ADMIN_EMAIL` and `BOOTSTRAP_ADMIN_PASSWORD` are supplied (AWS Ansible supplies a Terraform-generated password). In local development only, it uses a clearly warned demo password when the password variable is absent.

## Security notes

- Production disables legacy identity headers and automatic confirmation. All demo payment paths require admin review. Recurring creation is disabled whenever review is required, until series checkout/review exists.

- Access tokens are short-lived; role and account status are re-read from PostgreSQL on each authenticated request.
- Demo registration supports manual admin approval without SMTP. Production never exposes verification tokens.

- The simulated payment provider is not PCI payment processing. Integrate a real provider with signed webhooks, idempotency, reconciliation and provider-side refund handling before accepting money.
- Location is never collected in the background. It is only saved after explicit consent and can be deleted.
- Do not expose Grafana/Prometheus admin ports to `0.0.0.0/0`; Terraform restricts them to `admin_cidr`.

## Security configuration

- Production refuses debug email tokens, legacy identity headers, direct confirmation and short JWT secrets. HTTP application URLs require the explicit `ALLOW_HTTP_DEMO=true` transport exception, which the demo launcher propagates to API and worker. Supply `APP_BASE_URL` to both.
- The public Nginx entry point blocks `/metrics`; Prometheus scrapes the private API port. Unknown URLs share a bounded metrics label. Keep port 8000 restricted to trusted nodes: Uvicorn trusts proxy headers there, and Nginx overwrites forwarded client addresses after resolving the ALB address.
- Per-email authentication limits are shared across client IPs. Redis remains mandatory for production authentication.
- The website uses a same-origin Content Security Policy, local system font fallbacks, accessible labels and venue-timezone availability. The API docs use a separate Swagger distribution policy.
- Local Compose publishes the website and monitoring ports on loopback. Do not expose demo credentials or compatibility identity headers publicly.
- Existing AWS deployments need a reviewed Terraform plan and Ansible rollout to receive these fixes. No infrastructure is changed by editing this repository.


<!-- /coderabbit-ai-generated:v1 -->