"""prevent overlapping appointments

Revision ID: b456a4b8bd93
Revises: cbb88a1dcd86
"""
from typing import Sequence, Union

from alembic import op

revision: str = "b456a4b8bd93"
down_revision: Union[str, None] = "cbb88a1dcd86"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")
    op.execute(
        """
        ALTER TABLE appointments
        ADD CONSTRAINT no_overlapping_appointments
        EXCLUDE USING gist (
            doctor_id WITH =,
            tstzrange(starts_at, ends_at) WITH &&
        )
        WHERE (status <> 'cancelled')
        """
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE appointments DROP CONSTRAINT no_overlapping_appointments"
    )
