#!/usr/bin/env bash
# Run one shared durable notification/expiry worker, never one process per idle user.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
exec "${PYTHON_BIN:-python}" -m app.worker
