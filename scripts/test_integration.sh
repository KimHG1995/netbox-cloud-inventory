#!/usr/bin/env bash

set -euo pipefail

script_directory="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
"$script_directory/start_local.sh"

cd "$(cd -- "$script_directory/.." && pwd)"
set -a
# shellcheck disable=SC1091
source .env
set +a

RUN_MANUAL_IMPORT_E2E=1 \
  INVENTORY_API_URL=http://127.0.0.1:8080 \
  uv run pytest tests/integration/test_manual_import_flow.py -q
