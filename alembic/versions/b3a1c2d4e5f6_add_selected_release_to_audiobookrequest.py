"""add selected_release to audiobookrequest

Revision ID: b3a1c2d4e5f6
Revises: bb0ee00ba98e
Create Date: 2026-11-03 21:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b3a1c2d4e5f6"
down_revision: str | None = "bb0ee00ba98e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "audiobookrequest",
        sa.Column("selected_release", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("audiobookrequest", "selected_release")