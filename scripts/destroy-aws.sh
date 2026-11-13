#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TF_DIR="$ROOT/deploy/terraform"
: "${ADMIN_CIDR:?Set ADMIN_CIDR to your public IPv4 CIDR}"
: "${SSH_PUBLIC_KEY_PATH:=$HOME/.ssh/id_ed25519.pub}"
terraform -chdir="$TF_DIR" init
terraform -chdir="$TF_DIR" destroy \
  -var="admin_cidr=$ADMIN_CIDR" \
  -var="ssh_public_key_path=$SSH_PUBLIC_KEY_PATH" \
  -var="ssh_private_key_path=$HOME/.ssh/id_ed25519" \
  -var="acknowledge_possible_charges=true"
rm -f "$ROOT/deploy/terraform/deployment-credentials.txt" "$ROOT/deploy/ansible/inventory.ini"
