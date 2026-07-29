import os
import sys
from pathlib import Path

import httpx

from cloud_inventory.demo_loader import (
    DemoLoadError,
    default_demo_sources,
    load_demo_sources,
)


def main() -> int:
    repository_root = Path(__file__).resolve().parents[1]
    api_url = os.getenv(
        "INVENTORY_API_URL",
        "http://127.0.0.1:8080",
    ).rstrip("/")
    try:
        with httpx.Client(base_url=api_url, timeout=30) as client:
            results = load_demo_sources(
                client,
                default_demo_sources(repository_root),
            )
    except DemoLoadError as error:
        print(f"demo import failed: {error}", file=sys.stderr)
        return 1

    for result in results:
        print(
            f"{result.name}: import={result.import_id} "
            f"run={result.run_id} summary={result.summary}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
