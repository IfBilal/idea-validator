"""idea versions: nullable card, idempotency key (unique per idea)

Existing rows keep working: both columns are nullable and NULL keys never collide.

Revision ID: 0002
Revises: 0001
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "idea_versions",
        sa.Column("card", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        schema="app",
    )
    op.add_column(
        "idea_versions", sa.Column("idempotency_key", sa.Text(), nullable=True), schema="app"
    )
    op.create_unique_constraint(
        op.f("uq_idea_versions_idea_idempotency_key"),
        "idea_versions",
        ["idea_id", "idempotency_key"],
        schema="app",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("uq_idea_versions_idea_idempotency_key"), "idea_versions", schema="app", type_="unique"
    )
    op.drop_column("idea_versions", "idempotency_key", schema="app")
    op.drop_column("idea_versions", "card", schema="app")
