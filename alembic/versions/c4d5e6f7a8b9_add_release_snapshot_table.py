"""add release_snapshot table

Revision ID: c4d5e6f7a8b9
Revises: b3a1c2d4e5f6
Create Date: 2026-11-04 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c4d5e6f7a8b9"
down_revision: str | None = "b3a1c2d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "releasesnapshot",
        sa.Column("book_id", sa.Integer(), nullable=False),
        sa.Column("sources", sa.JSON(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["book_id"], ["audiobook.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("book_id"),
    )


def downgrade() -> None:
    op.drop_table("releasesnapshot")
