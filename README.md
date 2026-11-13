# Court Service — deployable demo platform

A Python 3.14 court-booking service expanded with account registration, email verification, role-based admin endpoints, Redis rate limiting/cache, a transactional email outbox worker, simulated payments/refunds, opt-in location search, Nginx, and an AWS deployment using Terraform + Ansible.

> **Important:** this is a learning/demo deployment, not a production payment or highly-available booking platform. Payment and refund actions are simulated; no card data is collected and no real money moves. The database and Redis run on the primary EC2 node, so that node is a single point of failure. Read [LIMITATIONS.md](LIMITATIONS.md) before deploying.

## What is included

- **API:** FastAPI, SQLAlchemy 2, PostgreSQL 16, Alembic migrations, original booking exclusion constraint and idempotency behavior.
- **Authentication:** email/password registration, Argon2id password hashes, one-time email verification tokens, short-lived JWT access tokens, database-backed role checks (`member`, `staff`, `admin`).
- **Abuse controls:** Nginx request limits plus shared Redis rate limits on registration/login (IP and login-email buckets). In production, auth requests fail closed if Redis is unavailable. Passwords are never logged.
- **Payments:** provider-neutral `PaymentProvider` protocol and a deterministic simulated provider. The website has explicit **Confirm simulated payment** and **Simulate refund** actions.
- **Notifications:** transactional PostgreSQL outbox, worker using `FOR UPDATE SKIP LOCKED`, retry/backoff and dead-letter state, in-app notifications, console simulation or SMTP delivery.
- **Location:** browser geolocation is requested only after the user clicks Share; consent is stored, nearby search uses the saved location, and users can delete it.
- **Admin:** bootstrap admin, create-court endpoint, venue-coordinate endpoint, and demo courts created by the seed command.
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

- Set the ingress CIDR to your current public IPv4 `/32`. SSH, Grafana and Prometheus are restricted to this CIDR.

### One-command deployment

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

### HTTPS and a domain

Without an ACM certificate ARN, the ALB endpoint uses HTTP. For real user credentials, configure a domain and an ACM certificate in the same AWS region, then apply with:

```bash
-var="acm_certificate_arn=arn:aws:acm:REGION:ACCOUNT:certificate/CERTIFICATE_ID" \
-var="domain_name=courts.example.com"
```

Terraform then creates an HTTPS listener and redirects HTTP to HTTPS. The certificate must already be issued and cover `domain_name`; point that DNS name to the ALB DNS output (CNAME/alias).

### Real email delivery with Gmail SMTP (optional)

By default the AWS demo uses a **console/simulated email backend**. The outbox worker records delivery and adds an in-app “Email sent” notification; the verification token is exposed only in this demo mode so the frontend can complete verification. No email is sent to Gmail unless SMTP is configured.

To send actual email, use a Gmail App Password (requires 2-Step Verification), not your normal Gmail password:

```bash
SMTP_HOST=smtp.gmail.com \
SMTP_PORT=587 \
SMTP_USERNAME=your-address@gmail.com \
SMTP_PASSWORD=your-16-character-app-password \
SMTP_FROM=your-address@gmail.com \
./scripts/deploy-aws.sh
```

With SMTP configured, verification is not auto-completed and the user must click the emailed verification link. SMTP secrets are passed only to the background worker environment, not the public API container.

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
| POST | `/api/payments/{reservation_id}/simulate` | Simulate payment and confirm a hold |
| GET | `/api/payments/{reservation_id}` | Read payment status |
| POST | `/bookings/{booking_id}/cancel` | Cancel booking and calculate refund eligibility |
| POST | `/api/payments/{reservation_id}/refund/simulate` | Simulate eligible refund |
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

- Production deployment disables legacy identity headers and direct hold confirmation. Payment simulation is the only production demo path to confirm a hold. Recurring booking creation is disabled in production until recurring payment checkout exists.
- Access tokens are short-lived; role and account status are re-read from PostgreSQL on each authenticated request.
- Do not use the console email mode for real user accounts; configure SMTP or another real delivery provider.
- The simulated payment provider is not PCI payment processing. Integrate a real provider with signed webhooks, idempotency, reconciliation and provider-side refund handling before accepting money.
- Location is never collected in the background. It is only saved after explicit consent and can be deleted.
- Do not expose Grafana/Prometheus admin ports to `0.0.0.0/0`; Terraform restricts them to `admin_cidr`.
