"""ops.traces_index: one row per trace record (the full record lives in the JSONL file on the PVC)

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
        CREATE TABLE ops.traces_index (
            run_id text NOT NULL,
            record_id text NOT NULL,
            ts timestamptz NOT NULL,
            kind text NOT NULL,
            agent text NOT NULL,
            service text,
            step_id integer NOT NULL,
            parent_id integer,
            status text NOT NULL,
            cost numeric(12,6) NOT NULL DEFAULT 0,
            tokens_in integer NOT NULL DEFAULT 0,
            tokens_out integer NOT NULL DEFAULT 0,
            latency integer NOT NULL DEFAULT 0,
            ingested_at timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (run_id, record_id),
            CONSTRAINT ck_traces_kind CHECK (kind IN ('tool_call','llm_call','decision')),
            CONSTRAINT ck_traces_status CHECK (status IN ('ok','error'))
        )""")
    op.execute("CREATE INDEX ix_traces_index_run_ts ON ops.traces_index (run_id, ts)")
    op.execute("CREATE INDEX ix_traces_index_ts ON ops.traces_index (ts DESC)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS ops.traces_index")
