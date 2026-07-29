#!/usr/bin/env bash

repository_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"

require_commands() {
  local command_name
  for command_name in "$@"; do
    if ! command -v "$command_name" >/dev/null 2>&1; then
      echo "required command is missing: $command_name" >&2
      return 1
    fi
  done
}

require_compose_v2() {
  if ! docker compose version >/dev/null 2>&1; then
    echo "Docker Compose v2 is required" >&2
    return 1
  fi
}

ensure_local_env() {
  if [[ -e "$repository_root/.env" ]]; then
    return
  fi

  local temporary_env
  local control_postgres_password
  local netbox_postgres_password
  local netbox_redis_password
  local netbox_redis_cache_password
  local netbox_secret_key
  local netbox_api_token_pepper
  local netbox_superuser_password
  local netbox_superuser_api_key
  local netbox_superuser_api_token
  local inventory_csrf_secret

  control_postgres_password="$(openssl rand -hex 32)"
  netbox_postgres_password="$(openssl rand -hex 32)"
  netbox_redis_password="$(openssl rand -hex 32)"
  netbox_redis_cache_password="$(openssl rand -hex 32)"
  netbox_secret_key="$(openssl rand -hex 64)"
  netbox_api_token_pepper="$(openssl rand -hex 64)"
  netbox_superuser_password="$(openssl rand -hex 32)"
  netbox_superuser_api_key="$(openssl rand -hex 6)"
  netbox_superuser_api_token="$(openssl rand -hex 32)"
  inventory_csrf_secret="$(openssl rand -hex 32)"
  temporary_env="$(mktemp "${TMPDIR:-/tmp}/netbox-cloud-inventory-env.XXXXXX")"
  trap 'rm -f -- "${temporary_env:-}"' EXIT

  {
    printf 'CONTROL_POSTGRES_PASSWORD=%s\n' "$control_postgres_password"
    printf 'NETBOX_POSTGRES_PASSWORD=%s\n' "$netbox_postgres_password"
    printf 'NETBOX_REDIS_PASSWORD=%s\n' "$netbox_redis_password"
    printf 'NETBOX_REDIS_CACHE_PASSWORD=%s\n' "$netbox_redis_cache_password"
    printf 'NETBOX_SECRET_KEY=%s\n' "$netbox_secret_key"
    printf 'SECRET_KEY=%s\n' "$netbox_secret_key"
    printf 'NETBOX_API_TOKEN_PEPPER=%s\n' "$netbox_api_token_pepper"
    printf 'API_TOKEN_PEPPER_1=%s\n' "$netbox_api_token_pepper"
    printf 'NETBOX_SUPERUSER_PASSWORD=%s\n' "$netbox_superuser_password"
    printf 'SUPERUSER_PASSWORD=%s\n' "$netbox_superuser_password"
    printf 'NETBOX_SUPERUSER_API_KEY=%s\n' "$netbox_superuser_api_key"
    printf 'SUPERUSER_API_KEY=%s\n' "$netbox_superuser_api_key"
    printf 'NETBOX_SUPERUSER_API_TOKEN=%s\n' "$netbox_superuser_api_token"
    printf 'SUPERUSER_API_TOKEN=%s\n' "$netbox_superuser_api_token"
    printf 'INVENTORY_CSRF_SECRET=%s\n' "$inventory_csrf_secret"
    printf 'INVENTORY_DATABASE_URL=postgresql+psycopg://inventory:%s@control-postgres:5432/inventory\n' "$control_postgres_password"
    printf 'INVENTORY_NETBOX_URL=http://127.0.0.1:8000\n'
    printf 'INVENTORY_NETBOX_TOKEN=nbt_%s.%s\n' \
      "$netbox_superuser_api_key" \
      "$netbox_superuser_api_token"
  } >"$temporary_env"

  chmod 0600 "$temporary_env"
  mv "$temporary_env" "$repository_root/.env"
  trap - EXIT
  echo "created local .env with mode 0600"
}

load_local_env() {
  set -a
  # shellcheck disable=SC1091
  source "$repository_root/.env"
  set +a

  : "${CONTROL_POSTGRES_PASSWORD:?missing CONTROL_POSTGRES_PASSWORD in .env}"
  : "${NETBOX_POSTGRES_PASSWORD:?missing NETBOX_POSTGRES_PASSWORD in .env}"
  : "${SECRET_KEY:?missing SECRET_KEY in .env}"
  : "${API_TOKEN_PEPPER_1:?missing API_TOKEN_PEPPER_1 in .env}"
  : "${SUPERUSER_PASSWORD:?missing SUPERUSER_PASSWORD in .env}"
  : "${SUPERUSER_API_KEY:?missing SUPERUSER_API_KEY in .env}"
  : "${SUPERUSER_API_TOKEN:?missing SUPERUSER_API_TOKEN in .env}"
  : "${INVENTORY_CSRF_SECRET:?missing INVENTORY_CSRF_SECRET in .env}"
  : "${INVENTORY_NETBOX_TOKEN:?missing INVENTORY_NETBOX_TOKEN in .env}"
}

wait_for_url() {
  local service_name="$1"
  local url="$2"
  local attempts="${LOCAL_STACK_HEALTH_ATTEMPTS:-90}"
  local delay_seconds="${LOCAL_STACK_HEALTH_DELAY_SECONDS:-2}"
  local attempt

  for attempt in $(seq 1 "$attempts"); do
    if curl --fail --silent --show-error "$url" >/dev/null 2>&1; then
      return 0
    fi
    sleep "$delay_seconds"
  done
  echo "$service_name did not become healthy: $url" >&2
  return 1
}
