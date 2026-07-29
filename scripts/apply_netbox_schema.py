import argparse
import asyncio
import json
import os
from pathlib import Path

from cloud_inventory.netbox.bootstrap import (
    HttpNetBoxBootstrapClient,
    apply_custom_object_schema,
    bootstrap_core_fields,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SCHEMA = REPOSITORY_ROOT / "schemas" / "netbox" / "custom-objects-v1.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Apply the NetBox cloud inventory schema")
    parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA)
    return parser.parse_args()


async def apply(schema_path: Path) -> None:
    netbox_url = os.environ.get("INVENTORY_NETBOX_URL", "http://localhost:8000")
    token = os.environ.get("INVENTORY_NETBOX_TOKEN")
    if not token:
        raise ValueError("INVENTORY_NETBOX_TOKEN is required")
    schema_document = json.loads(schema_path.read_text())

    async with HttpNetBoxBootstrapClient(netbox_url, token) as client:
        core_result = await bootstrap_core_fields(client)
        schema_result = await apply_custom_object_schema(client, schema_document)

    print(
        "core fields: "
        f"created={core_result.created} "
        f"changed={core_result.changed} "
        f"unchanged={core_result.unchanged}"
    )
    print(
        "custom object schema: "
        f"created={schema_result.created} "
        f"changed={schema_result.changed} "
        f"unchanged={schema_result.unchanged} "
        f"destructive={schema_result.destructive}"
    )


def main() -> int:
    args = parse_args()
    asyncio.run(apply(args.schema))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
