"""replace asin with source and external_id

Revision ID: bb0ee00ba98e
Revises: babaede58935
Create Date: 2026-11-03 19:07:21.144872

"""

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "bb0ee00ba98e"
down_revision: str | None = "babaede58935"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # The fork drops Audible, so cached rows are keyed differently from here on.
    # Rebuild the two tables rather than migrate stale ASIN-keyed rows.
    op.drop_table("audiobookrequest")
    op.drop_table("audiobook")

    op.create_table(
        "audiobook",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("source", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("external_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("url", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("title", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("subtitle", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("authors", sa.JSON(), nullable=True),
        sa.Column("narrators", sa.JSON(), nullable=True),
        sa.Column("cover_image", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("release_date", sa.DateTime(), nullable=False),
        sa.Column("runtime_length_min", sa.Integer(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column("downloaded", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source", "external_id", name="uq_audiobook_source_external_id"
        ),
    )

    op.create_table(
        "audiobookrequest",
        sa.Column("book_id", sa.Integer(), nullable=False),
        sa.Column("user_username", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["book_id"], ["audiobook.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["user_username"], ["user.username"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("book_id", "user_username"),
    )


def downgrade() -> None:
    op.drop_table("audiobookrequest")
    op.drop_table("audiobook")

    op.create_table(
        "audiobook",
        sa.Column("asin", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("title", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("subtitle", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("authors", sa.JSON(), nullable=True),
        sa.Column("narrators", sa.JSON(), nullable=True),
        sa.Column("cover_image", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("release_date", sa.DateTime(), nullable=False),
        sa.Column("runtime_length_min", sa.Integer(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column("downloaded", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("asin"),
    )

    op.create_table(
        "audiobookrequest",
        sa.Column("asin", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("user_username", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["asin"], ["audiobook.asin"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["user_username"], ["user.username"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("asin", "user_username"),
    )