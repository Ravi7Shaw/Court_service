#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
TF="$ROOT/terraform"
ANSIBLE="$ROOT/ansible"
EMAIL="${ADMIN_EMAIL:-ravi@gmail.com}"
KEY="${SSH_KEY:-$HOME/.ssh/id_ed25519}"

fail() { echo "ERROR: $*" >&2; exit 1; }

for cmd in terraform ansible ansible-playbook aws jq curl ansible-galaxy rsync; do
    command -v "$cmd" >/dev/null || fail "$cmd is not installed"
done

[[ -f "$KEY" ]] || fail "SSH key not found: $KEY"
[[ -f "$ANSIBLE/site.yml" ]] || fail "Ansible site.yml not found"

umask 077
WORK="$(mktemp -d)"
trap 'rm -rf -- "$WORK"' EXIT
VARS="$WORK/vars.json"
INV="$WORK/inventory.ini"
PLAN="$WORK/deploy.tfplan"
HTTP_DEMO="${ALLOW_HTTP_DEMO:-true}"
[[ "$HTTP_DEMO" == "true" || "$HTTP_DEMO" == "false" ]] || fail "ALLOW_HTTP_DEMO must be true or false"
TF_ARGS=("-var=allow_http_demo=$HTTP_DEMO")
[[ -z "${DOMAIN_NAME:-}" ]] || TF_ARGS+=("-var=domain_name=$DOMAIN_NAME")
[[ -z "${ACM_CERTIFICATE_ARN:-}" ]] || TF_ARGS+=("-var=acm_certificate_arn=$ACM_CERTIFICATE_ARN")

MY_IP="$(curl -4fsS https://checkip.amazonaws.com | tr -d '[:space:]')"
ADMIN_CIDR="${MY_IP}/32"

echo "=========================================="
echo " COURT SERVICE DEPLOYMENT"
echo " Region: ap-southeast-2"
echo " Your IP: $MY_IP"
echo " SSH CIDR: $ADMIN_CIDR"
echo "=========================================="

cd "$TF"

terraform init
terraform fmt -recursive
terraform validate

TF_ARGS+=("-var=admin_cidr=$ADMIN_CIDR")
CONFIG="$(terraform console -no-color "${TF_ARGS[@]}" <<<'jsonencode({domain = var.domain_name, certificate = var.acm_certificate_arn, costs = var.acknowledge_possible_charges, http_demo = var.allow_http_demo})' | jq -er 'fromjson')"
DOMAIN="$(jq -r '.domain' <<<"$CONFIG")"
CERTIFICATE="$(jq -r '.certificate' <<<"$CONFIG")"
if [[ -z "$DOMAIN" && -z "$CERTIFICATE" && "$HTTP_DEMO" == "true" ]]; then
    echo "HTTP DEMO: using the AWS load balancer hostname; login traffic is unencrypted."
    echo "Browser location sharing may require HTTPS."
elif [[ -z "$DOMAIN" || -z "$CERTIFICATE" ]]; then
    fail "HTTPS configuration missing. Set DOMAIN_NAME and ACM_CERTIFICATE_ARN together, or clear both for the default HTTP demo. Set ALLOW_HTTP_DEMO=false to require HTTPS."
fi
[[ "$(jq -r '.costs' <<<"$CONFIG")" == "true" ]] || fail "Review AWS costs and set acknowledge_possible_charges=true in terraform/terraform.tfvars."
terraform plan -input=false "${TF_ARGS[@]}" -out="$PLAN"

read -r -p "Type APPLY to provision AWS: " CONFIRM
[[ "$CONFIRM" == "APPLY" ]] || fail "Cancelled"

terraform apply -input=false "$PLAN"

terraform output -json |
jq --arg email "$EMAIL" '
    with_entries(.value = .value.value) |
    . + {
        grafana_password: .grafana_admin_password,
        admin_email: $email
    }
' > "$VARS"

jq -e '
    .primary_public_ip and
    .secondary_public_ip and
    .primary_private_ip and
    .secondary_private_ip and
    .admin_password and
    .db_password and
    .redis_password and
    .jwt_secret and
    .grafana_password and
    ((.application_url | startswith("https://")) or
     (.allow_http_demo == true and (.application_url | startswith("http://"))))
' "$VARS" >/dev/null || fail "Required Terraform outputs missing"

PRIMARY_PUBLIC="$(jq -r '.primary_public_ip' "$VARS")"
SECONDARY_PUBLIC="$(jq -r '.secondary_public_ip' "$VARS")"
PRIMARY_PRIVATE="$(jq -r '.primary_private_ip' "$VARS")"
SECONDARY_PRIVATE="$(jq -r '.secondary_private_ip' "$VARS")"

cat > "$INV" <<EOF
[primary]
primary_node ansible_host=$PRIMARY_PUBLIC node_private_ip=$PRIMARY_PRIVATE node_role=primary

[secondary]
secondary_node ansible_host=$SECONDARY_PUBLIC node_private_ip=$SECONDARY_PRIVATE node_role=secondary

[court_nodes:children]
primary
secondary

[court_nodes:vars]
ansible_user=ec2-user
ansible_ssh_private_key_file=$KEY
ansible_python_interpreter=/usr/bin/python3
ansible_ssh_common_args='-o StrictHostKeyChecking=accept-new -o ServerAliveInterval=30'
primary_private_ip=$PRIMARY_PRIVATE
secondary_private_ip=$SECONDARY_PRIVATE
EOF

install -m 600 "$INV" "$ANSIBLE/inventory.ini"

echo
echo "Testing server connectivity..."

cd "$ANSIBLE"
ansible-galaxy collection install -r requirements.yml
ansible -i inventory.ini court_nodes -m ping

echo
echo "Deploying application and monitoring..."

ansible-playbook -i inventory.ini site.yml -e "@$VARS"

echo
echo "Checking Docker containers..."

ansible -i inventory.ini primary -b -m command -a 'docker compose --env-file /opt/court-service/deploy/.env -f /opt/court-service/deploy/compose-primary.yml ps'
ansible -i inventory.ini secondary -b -m command -a 'docker compose --env-file /opt/court-service/deploy/.env -f /opt/court-service/deploy/compose-secondary.yml ps'

APP="$(jq -r '.application_url' "$VARS")"
GRAFANA="$(jq -r '.grafana_url' "$VARS")"
PROM="$(jq -r '.prometheus_url' "$VARS")"

echo
echo "=========================================="
echo " DEPLOYMENT PLAYBOOK COMPLETED"
echo "=========================================="
echo "Website:    $APP"
echo "API Docs:   $APP/docs"
echo "Grafana:    $GRAFANA"
echo "Prometheus: $PROM"
echo
echo "Verify ALB target health and application readiness."
