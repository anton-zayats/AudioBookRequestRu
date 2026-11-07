"""remove book-centric requests: audiobookrequest, releasesnapshot, audiobook.downloaded

Revision ID: a1b2c3d4e5f6
Revises: e7f8a9b0c1d2
Create Date: 2026-11-04 12:30:00.000000

The release-centric model (P12) replaces book requests with release bookmarks
(`releaserequest`) and per-release download state (`downloadjob`). The
book-request table, the release-search snapshot and the single
`audiobook.downloaded` flag are no longer read or written.
"""

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: str | None = "e7f8a9b0c1d2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_table("releasesnapshot")
    op.drop_table("audiobookrequest")

    with op.batch_alter_table("audiobook") as batch_op:
        batch_op.drop_column("downloaded")


def downgrade() -> None:
    with op.batch_alter_table("audiobook") as batch_op:
        batch_op.add_column(
            sa.Column(
                "downloaded",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )

    op.create_table(
        "audiobookrequest",
        sa.Column("book_id", sa.Integer(), nullable=False),
        sa.Column("user_username", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("selected_release", sa.JSON(), nullable=True),
        sa.ForeignKeyConstraint(["book_id"], ["audiobook.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["user_username"], ["user.username"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("book_id", "user_username"),
    )

    op.create_table(
        "releasesnapshot",
        sa.Column("book_id", sa.Integer(), nullable=False),
        sa.Column("sources", sa.JSON(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["book_id"], ["audiobook.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("book_id"),
    )
