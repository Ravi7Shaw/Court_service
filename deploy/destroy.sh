#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
TF="$ROOT/terraform"

command -v terraform >/dev/null || {
    echo "Terraform is not installed"
    exit 1
}

cd "$TF"

echo "=========================================="
echo " WARNING: DESTROY COURT SERVICE AWS"
echo "=========================================="
echo
echo "This can permanently delete EC2, ALB,"
echo "networking and database resources."
echo "Back up important data first."
echo

read -r -p "Type PLAN to inspect destruction: " CONFIRM
[[ "$CONFIRM" == "PLAN" ]] || exit 1

terraform init
terraform plan -destroy -out=tfplan-destroy

terraform show tfplan-destroy

echo
read -r -p "Type DESTROY to delete AWS resources: " CONFIRM

if [[ "$CONFIRM" != "DESTROY" ]]; then
    rm -f tfplan-destroy
    echo "Cancelled. Nothing destroyed."
    exit 0
fi

terraform apply tfplan-destroy
rm -f tfplan-destroy

echo "Terraform destruction completed."
