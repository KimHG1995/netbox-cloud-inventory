import argparse
import json
from pathlib import Path

from cloud_inventory.domain.models import ImportBundle

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TARGET = REPOSITORY_ROOT / "schemas" / "import-bundle-v1.schema.json"


def render_schema() -> str:
    schema = ImportBundle.model_json_schema(mode="validation")
    return json.dumps(schema, indent=2, sort_keys=True) + "\n"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export the Import Bundle JSON Schema")
    parser.add_argument("--check", action="store_true", help="fail when the schema is stale")
    parser.add_argument("--output", type=Path, default=DEFAULT_TARGET)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    rendered = render_schema()

    if args.check:
        if not args.output.exists() or args.output.read_text() != rendered:
            print(f"schema is stale: {args.output}")
            return 1
        return 0

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
