"""SQLAlchemy tables of schema ``kb`` (M6 owns them): cards, card platforms, chunks."""

from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    ARRAY,
    BigInteger,
    CheckConstraint,
    Computed,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import TSVECTOR
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

SCHEMA = "kb"
SOURCES = "('hackernews','github','lobsters','stackexchange','reddit','producthunt','yc')"
KINDS = "('product','complaint','request','discussion','repo')"
STAGES = "('launched','seed','series_a','series_b_plus','acquired','dead','unknown')"
_TSV = "to_tsvector('english', coalesce(name,'') || ' ' || coalesce(summary,'') || ' ' || text)"


class Base(DeclarativeBase):
    metadata = MetaData(
        schema=SCHEMA,
        naming_convention={
            "ix": "ix_%(column_0_label)s",
            "uq": "uq_%(table_name)s_%(column_0_name)s",
            "ck": "ck_%(table_name)s_%(constraint_name)s",
            "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
            "pk": "pk_%(table_name)s",
        },
    )


class CardRow(Base):
    __tablename__ = "cards"
    __table_args__ = (
        CheckConstraint("id ~ '^card_[0-9a-f]{8,12}$'", name="id_format"),
        CheckConstraint(f"source IN {SOURCES}", name="source"),
        CheckConstraint(f"kind IN {KINDS}", name="kind"),
        CheckConstraint(f"stage IN {STAGES}", name="stage"),
        Index("ix_cards_source", "source"),
        Index("ix_cards_kind", "kind"),
        Index("ix_cards_published_at", "published_at"),
        Index("ix_cards_niche_tags", "niche_tags", postgresql_using="gin"),
        Index("ix_cards_tsv", "tsv", postgresql_using="gin"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    url: Mapped[str] = mapped_column(Text, unique=True)  # the canonical url: one card per page
    name: Mapped[str] = mapped_column(Text)
    summary: Mapped[str] = mapped_column(Text)
    problem: Mapped[str | None] = mapped_column(Text)
    customer: Mapped[str | None] = mapped_column(Text)
    niche_tags: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default="{}")
    stage: Mapped[str] = mapped_column(Text, server_default="unknown")
    funding_usd: Mapped[int | None] = mapped_column(BigInteger)
    source: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(Text)
    published_at: Mapped[date | None] = mapped_column(Date)
    fetched_at: Mapped[date] = mapped_column(Date)
    text: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(Text)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    tsv: Mapped[Any] = mapped_column(TSVECTOR, Computed(_TSV, persisted=True))


class CardSourceRow(Base):
    """Every platform that has mentioned a card (independence counting in M7)."""

    __tablename__ = "card_sources"
    __table_args__ = (CheckConstraint(f"source IN {SOURCES}", name="source"),)

    card_id: Mapped[str] = mapped_column(
        Text, ForeignKey("kb.cards.id", ondelete="CASCADE"), primary_key=True
    )
    source: Mapped[str] = mapped_column(Text, primary_key=True)


class ChunkRow(Base):
    __tablename__ = "chunks"

    id: Mapped[str] = mapped_column(Text, primary_key=True)  # the evidence id: card_<hex>#c<n>
    card_id: Mapped[str] = mapped_column(
        Text, ForeignKey("kb.cards.id", ondelete="CASCADE"), index=True
    )
    idx: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
