"""kb schema: cards, card_sources, chunks (docs/harness/10 section 2)

The schema `kb` is created in migrations/env.py before this runs.

Revision ID: 0001
Revises:
"""

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

SOURCES = "('hackernews','github','lobsters','stackexchange','reddit','producthunt','yc')"


def upgrade() -> None:
    op.execute(f"""
        CREATE TABLE kb.cards (
            id text NOT NULL,
            url text NOT NULL,
            name text NOT NULL,
            summary text NOT NULL,
            problem text,
            customer text,
            niche_tags text[] NOT NULL DEFAULT '{{}}',
            stage text NOT NULL DEFAULT 'unknown',
            funding_usd bigint,
            source text NOT NULL,
            kind text NOT NULL,
            published_at date,
            fetched_at date NOT NULL,
            text text NOT NULL,
            content_hash text NOT NULL,
            first_seen_at timestamptz NOT NULL DEFAULT now(),
            last_seen_at timestamptz NOT NULL DEFAULT now(),
            tsv tsvector GENERATED ALWAYS AS (
                to_tsvector('english', coalesce(name,'') || ' ' || coalesce(summary,'') || ' ' || text)
            ) STORED,
            CONSTRAINT pk_cards PRIMARY KEY (id),
            CONSTRAINT uq_cards_url UNIQUE (url),
            CONSTRAINT ck_cards_id_format CHECK (id ~ '^card_[0-9a-f]{{8,12}}$'),
            CONSTRAINT ck_cards_source CHECK (source IN {SOURCES}),
            CONSTRAINT ck_cards_kind CHECK (kind IN ('product','complaint','request','discussion','repo')),
            CONSTRAINT ck_cards_stage CHECK (
                stage IN ('launched','seed','series_a','series_b_plus','acquired','dead','unknown')
            )
        )""")
    op.execute("CREATE INDEX ix_cards_source ON kb.cards (source)")
    op.execute("CREATE INDEX ix_cards_kind ON kb.cards (kind)")
    op.execute("CREATE INDEX ix_cards_published_at ON kb.cards (published_at)")
    op.execute("CREATE INDEX ix_cards_niche_tags ON kb.cards USING gin (niche_tags)")
    op.execute("CREATE INDEX ix_cards_tsv ON kb.cards USING gin (tsv)")
    op.execute(f"""
        CREATE TABLE kb.card_sources (
            card_id text NOT NULL REFERENCES kb.cards (id) ON DELETE CASCADE,
            source text NOT NULL,
            CONSTRAINT pk_card_sources PRIMARY KEY (card_id, source),
            CONSTRAINT ck_card_sources_source CHECK (source IN {SOURCES})
        )""")
    op.execute("""
        CREATE TABLE kb.chunks (
            id text NOT NULL,
            card_id text NOT NULL REFERENCES kb.cards (id) ON DELETE CASCADE,
            idx integer NOT NULL,
            text text NOT NULL,
            CONSTRAINT pk_chunks PRIMARY KEY (id)
        )""")
    op.execute("CREATE INDEX ix_chunks_card_id ON kb.chunks (card_id)")


def downgrade() -> None:
    op.execute("DROP TABLE kb.chunks")
    op.execute("DROP TABLE kb.card_sources")
    op.execute("DROP TABLE kb.cards")
