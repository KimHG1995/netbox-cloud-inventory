import os
import shutil
import subprocess
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = REPOSITORY_ROOT / "scripts"
ENV_FILE = """\
CONTROL_POSTGRES_PASSWORD=control-password
NETBOX_POSTGRES_PASSWORD=netbox-password
SECRET_KEY=secret-key
API_TOKEN_PEPPER_1=token-pepper
SUPERUSER_PASSWORD=admin-password
SUPERUSER_API_KEY=abcdef123456
SUPERUSER_API_TOKEN=superuser-token
INVENTORY_CSRF_SECRET=csrf-secret
INVENTORY_NETBOX_TOKEN=nbt_abcdef123456.superuser-token
"""


def _require_script(relative_path: str) -> Path:
    path = SCRIPTS / relative_path
    assert path.exists(), f"missing workflow script: {relative_path}"
    return path


def _write_executable(path: Path, source: str) -> None:
    path.write_text(source, encoding="utf-8")
    path.chmod(0o755)


def _prepare_repository(
    tmp_path: Path,
    script_paths: tuple[str, ...],
) -> tuple[Path, Path, dict[str, str]]:
    repository = tmp_path / "repository"
    fake_bin = tmp_path / "bin"
    repository.mkdir()
    fake_bin.mkdir()
    (repository / ".env").write_text(ENV_FILE, encoding="utf-8")

    for relative_path in script_paths:
        source = _require_script(relative_path)
        destination = repository / "scripts" / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)

    command_log = tmp_path / "commands.log"
    for command_name in ("curl", "docker", "openssl"):
        _write_executable(
            fake_bin / command_name,
            f"""#!/usr/bin/env bash
printf '{command_name} %s\\n' "$*" >> "$COMMAND_LOG"
if [[ "{command_name}" == "curl" ]]; then
  exit "${{FAKE_CURL_STATUS:-0}}"
fi
""",
        )
    _write_executable(
        fake_bin / "uv",
        """#!/usr/bin/env bash
printf 'uv %s RUN_MANUAL_IMPORT_E2E=%s INVENTORY_API_URL=%s\\n' \
  "$*" \
  "${RUN_MANUAL_IMPORT_E2E:-}" \
  "${INVENTORY_API_URL:-}" >> "$COMMAND_LOG"
""",
    )
    environment = {
        **os.environ,
        "COMMAND_LOG": str(command_log),
        "PATH": f"{fake_bin}:/usr/bin:/bin",
        "LOCAL_STACK_HEALTH_ATTEMPTS": "1",
        "LOCAL_STACK_HEALTH_DELAY_SECONDS": "0",
    }
    return repository, command_log, environment


def _run_script(
    repository: Path,
    script_name: str,
    environment: dict[str, str],
    *arguments: str,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(repository / "scripts" / script_name), *arguments],
        cwd=repository,
        env=environment,
        capture_output=True,
        check=False,
        text=True,
    )


def test_start_local_starts_services_and_applies_schema_without_data(
    tmp_path: Path,
) -> None:
    repository, command_log, environment = _prepare_repository(
        tmp_path,
        ("lib/local_stack.sh", "start_local.sh"),
    )

    result = _run_script(
        repository,
        "start_local.sh",
        environment,
    )

    assert result.returncode == 0, result.stderr
    commands = command_log.read_text(encoding="utf-8").splitlines()
    assert "docker compose version" in commands
    assert "docker compose up -d --build" in commands
    assert (
        "uv run python scripts/apply_netbox_schema.py "
        "RUN_MANUAL_IMPORT_E2E= INVENTORY_API_URL="
    ) in commands
    assert all("pytest" not in command for command in commands)
    assert "Import UI: http://127.0.0.1:8080/ui/imports" in result.stdout


def test_start_local_scripts_have_valid_bash_syntax() -> None:
    for relative_path in ("lib/local_stack.sh", "start_local.sh"):
        script = _require_script(relative_path)
        result = subprocess.run(
            ["bash", "-n", str(script)],
            capture_output=True,
            check=False,
            text=True,
        )
        assert result.returncode == 0, result.stderr
