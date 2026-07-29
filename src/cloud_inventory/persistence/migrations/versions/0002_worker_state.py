"""Add worker parser version and immutable preview constraints.

Revision ID: 0002_worker_state
Revises: 0001_control_tables
Create Date: 2026-07-28
"""

import sqlalchemy as sa
from alembic import op

revision = "0002_worker_state"
down_revision = "0001_control_tables"
branch_labels = None
depends_on = None


def _columns(table_name: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {str(item["name"]) for item in inspector.get_columns(table_name)}


def _unique_constraints(table_name: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {
        str(item["name"])
        for item in inspector.get_unique_constraints(table_name)
        if item.get("name") is not None
    }


def upgrade() -> None:
    if "parser_version" not in _columns("source_file"):
        op.add_column(
            "source_file",
            sa.Column("parser_version", sa.String(length=64), nullable=True),
        )
    if "uq_import_preview_import_id" not in _unique_constraints("import_preview"):
        op.create_unique_constraint(
            "uq_import_preview_import_id",
            "import_preview",
            ["import_id"],
        )


def downgrade() -> None:
    if "uq_import_preview_import_id" in _unique_constraints("import_preview"):
        op.drop_constraint(
            "uq_import_preview_import_id",
            "import_preview",
            type_="unique",
        )
    if "parser_version" in _columns("source_file"):
        op.drop_column("source_file", "parser_version")
