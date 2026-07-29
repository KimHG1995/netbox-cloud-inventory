#!/usr/bin/env bash

set -euo pipefail
umask 077

script_directory="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/lib/local_stack.sh
source "$script_directory/lib/local_stack.sh"
cd "$repository_root"

require_commands curl uv
if [[ ! -e .env ]]; then
  echo "local environment is not initialized; run ./scripts/start_local.sh first" >&2
  exit 1
fi
load_local_env

if ! wait_for_url "Inventory API" "http://127.0.0.1:8080/healthz"; then
  echo "run ./scripts/start_local.sh before loading demo data" >&2
  exit 1
fi
if ! wait_for_url "NetBox" "http://127.0.0.1:8000/login/"; then
  echo "run ./scripts/start_local.sh before loading demo data" >&2
  exit 1
fi

INVENTORY_API_URL=http://127.0.0.1:8080 \
  uv run python scripts/load_demo_data.py

echo "demo data loaded"
echo "Import UI: http://127.0.0.1:8080/ui/imports"
echo "NetBox: http://127.0.0.1:8000"
