#!/usr/bin/env bash

set -euo pipefail

script_directory="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
echo "scripts/poc_import.sh is deprecated; use scripts/test_integration.sh" >&2
exec "$script_directory/test_integration.sh" "$@"
