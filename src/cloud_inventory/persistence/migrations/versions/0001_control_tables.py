"""Create control database tables.

Revision ID: 0001_control_tables
Revises:
Create Date: 2026-07-28
"""

from alembic import op

from cloud_inventory.persistence import models as persistence_models  # noqa: F401
from cloud_inventory.persistence.base import Base

revision = "0001_control_tables"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    Base.metadata.create_all(bind=op.get_bind())


def downgrade() -> None:
    Base.metadata.drop_all(bind=op.get_bind())
