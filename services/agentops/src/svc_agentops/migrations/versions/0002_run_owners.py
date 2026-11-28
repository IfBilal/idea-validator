"""ops.run_owners: which user started a run (the orchestrator traces it as a run_owner decision)

Revision ID: 0002
Revises: 0001
"""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE ops.run_owners (
            run_id text PRIMARY KEY,
            user_id text NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now()
        )""")
    op.execute("CREATE INDEX ix_run_owners_user ON ops.run_owners (user_id)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS ops.run_owners")
