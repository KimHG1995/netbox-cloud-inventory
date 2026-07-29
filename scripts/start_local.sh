#!/usr/bin/env bash

set -euo pipefail
umask 077

script_directory="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/lib/local_stack.sh
source "$script_directory/lib/local_stack.sh"
cd "$repository_root"

require_commands curl docker openssl uv
require_compose_v2
ensure_local_env
load_local_env

docker compose up -d --build
wait_for_url "NetBox" "http://127.0.0.1:8000/login/"
wait_for_url "Inventory API" "http://127.0.0.1:8080/healthz"
uv run python scripts/apply_netbox_schema.py

echo "local stack is ready"
echo "Import UI: http://127.0.0.1:8080/ui/imports"
echo "NetBox: http://127.0.0.1:8000"
echo "Health: http://127.0.0.1:8080/healthz"
