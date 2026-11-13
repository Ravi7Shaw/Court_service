#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TF_DIR="$ROOT/deploy/terraform"

if ! command -v terraform >/dev/null || ! command -v ansible-playbook >/dev/null || ! command -v rsync >/dev/null; then
  echo "Install Terraform >=1.9, Ansible and rsync before deploying." >&2; exit 1
fi
if [[ ! -f "$HOME/.ssh/id_ed25519.pub" ]]; then
  echo "Create an SSH key first: ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519" >&2; exit 1
fi
if [[ ! -f "$TF_DIR/terraform.tfvars" ]]; then
  echo "Missing $TF_DIR/terraform.tfvars" >&2
  echo "Copy terraform.tfvars.example to terraform.tfvars and set admin_cidr and ami_id." >&2
  exit 1
fi
if ! grep -Eq '^[[:space:]]*ami_id[[:space:]]*=[[:space:]]*"ami-[0-9a-f]{8,17}"' "$TF_DIR/terraform.tfvars"; then
  echo "Set ami_id in $TF_DIR/terraform.tfvars to a real x86_64 Amazon Linux 2023 AMI ID from ap-southeast-2." >&2
  exit 1
fi
echo "This deployment creates billable AWS resources (EC2, ALB, EBS and public IPv4). Free Tier eligibility is account-specific."
read -r -p "Continue only if you have reviewed the AWS charges? [y/N] " ACK
[[ "$ACK" =~ ^[Yy]$ ]] || { echo "Deployment cancelled."; exit 1; }

if [[ -z "${ADMIN_CIDR:-}" ]]; then
  if command -v curl >/dev/null; then ADMIN_CIDR="$(curl -fsS https://checkip.amazonaws.com | tr -d '\n')/32";
  else echo "Set ADMIN_CIDR to your public IPv4 CIDR, e.g. 203.0.113.10/32" >&2; exit 1; fi
fi

terraform -chdir="$TF_DIR" init
terraform -chdir="$TF_DIR" fmt -recursive
terraform -chdir="$TF_DIR" validate
terraform -chdir="$TF_DIR" apply \
  -var="admin_cidr=$ADMIN_CIDR" \
  -var="ssh_public_key_path=$HOME/.ssh/id_ed25519.pub" \
  -var="ssh_private_key_path=$HOME/.ssh/id_ed25519" \
  -var="acknowledge_possible_charges=true"

out() { terraform -chdir="$TF_DIR" output -raw "$1"; }
export BOOTSTRAP_ADMIN_PASSWORD="$(out admin_password)"
export DB_PASSWORD="$(out db_password)"
export REDIS_PASSWORD="$(out redis_password)"
export JWT_SECRET="$(out jwt_secret)"
export GRAFANA_ADMIN_PASSWORD="$(out grafana_admin_password)"
export APP_BASE_URL="$(out application_url)"
export PRIMARY_PRIVATE_IP="$(out primary_private_ip)"
export SECONDARY_PRIVATE_IP="$(out secondary_private_ip)"
export ADMIN_EMAIL="${BOOTSTRAP_ADMIN_EMAIL:-$(out admin_email)}"

umask 077
cat > "$ROOT/deploy/terraform/deployment-credentials.txt" <<EOF
Application: $APP_BASE_URL
Admin email: $ADMIN_EMAIL
Admin password: $BOOTSTRAP_ADMIN_PASSWORD
Grafana: http://$(out secondary_public_ip):3000
Grafana user: admin
Grafana password: $GRAFANA_ADMIN_PASSWORD
Prometheus: http://$(out secondary_public_ip):9090
EOF
chmod 600 "$ROOT/deploy/terraform/deployment-credentials.txt"

ansible-galaxy collection install -r "$ROOT/deploy/ansible/requirements.yml"
VARS_FILE="$(mktemp)"
chmod 600 "$VARS_FILE"
trap 'rm -f "$VARS_FILE"' EXIT
python3 - "$VARS_FILE" <<'PYVARS'
import json, os, sys
keys = ["DB_PASSWORD", "REDIS_PASSWORD", "JWT_SECRET", "BOOTSTRAP_ADMIN_PASSWORD",
        "GRAFANA_ADMIN_PASSWORD", "APP_BASE_URL", "ADMIN_EMAIL",
        "PRIMARY_PRIVATE_IP", "SECONDARY_PRIVATE_IP"]
values = {key.lower(): os.environ[key] for key in keys}
values["admin_password"] = values.pop("bootstrap_admin_password")
values["grafana_password"] = values.pop("grafana_admin_password")
values["application_url"] = values.pop("app_base_url")
values["admin_email"] = values.pop("admin_email")
with open(sys.argv[1], "w", encoding="utf-8") as stream:
    json.dump(values, stream)
PYVARS
ANSIBLE_CONFIG="$ROOT/deploy/ansible/ansible.cfg" ansible-playbook --syntax-check \
  -i "$ROOT/deploy/ansible/inventory.ini" "$ROOT/deploy/ansible/site.yml" --extra-vars "@$VARS_FILE"
ANSIBLE_CONFIG="$ROOT/deploy/ansible/ansible.cfg" ansible-playbook -i "$ROOT/deploy/ansible/inventory.ini" \
  "$ROOT/deploy/ansible/site.yml" --extra-vars "@$VARS_FILE"

echo
echo "Deployment finished. Credentials saved with mode 0600 at: deploy/terraform/deployment-credentials.txt"
echo "Application: $APP_BASE_URL"
echo "Grafana: http://$(out secondary_public_ip):3000"
echo "Prometheus: http://$(out secondary_public_ip):9090"
