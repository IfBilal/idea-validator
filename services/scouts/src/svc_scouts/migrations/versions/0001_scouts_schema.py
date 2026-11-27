"""scouts schema: raw_pages (input of M6i) and scout_state (cursor, circuit breaker)

Revision ID: 0001
Revises:
"""

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE scouts.raw_pages (
            id bigserial PRIMARY KEY,
            source text NOT NULL,
            url text NOT NULL,
            fetched_at timestamptz NOT NULL DEFAULT now(),
            status_code integer NOT NULL DEFAULT 200,
            body jsonb NOT NULL,
            content_hash text NOT NULL,
            processed boolean NOT NULL DEFAULT false,
            CONSTRAINT uq_raw_pages_source_url UNIQUE (source, url),
            CONSTRAINT ck_raw_pages_source CHECK (
                source IN ('hackernews','github','lobsters','stackexchange','reddit','producthunt','yc')
            )
        )""")
    op.execute(
        "CREATE INDEX ix_raw_pages_unprocessed ON scouts.raw_pages (source, id) WHERE NOT processed"
    )
    op.execute("""
        CREATE TABLE scouts.scout_state (
            source text PRIMARY KEY,
            cursor jsonb NOT NULL DEFAULT '{}',
            failures integer NOT NULL DEFAULT 0,
            opened_at timestamptz,
            last_run_at timestamptz,
            last_status text,
            last_error text
        )""")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS scouts.scout_state")
    op.execute("DROP TABLE IF EXISTS scouts.raw_pages")
