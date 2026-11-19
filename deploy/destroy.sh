#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
TF="$ROOT/terraform"

command -v terraform >/dev/null || {
    echo "Terraform is not installed"
    exit 1
}

cd "$TF"
umask 077
WORK="$(mktemp -d)"
trap 'rm -rf -- "$WORK"' EXIT
PLAN="$WORK/destroy.tfplan"

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
terraform plan -destroy -input=false -var="allow_http_demo=${ALLOW_HTTP_DEMO:-true}" -out="$PLAN"

terraform show "$PLAN"

echo
read -r -p "Type DESTROY to delete AWS resources: " CONFIRM

if [[ "$CONFIRM" != "DESTROY" ]]; then
    echo "Cancelled. Nothing destroyed."
    exit 0
fi

terraform apply -input=false "$PLAN"

echo "Terraform destruction completed."
