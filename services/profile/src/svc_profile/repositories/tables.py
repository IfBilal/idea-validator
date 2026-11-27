"""SQLAlchemy tables for the application schema ``app`` (docs/harness/04 §1). M2 owns them."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    ARRAY,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    MetaData,
    Numeric,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import CITEXT, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

SCHEMA = "app"
GOALS = "('startup','portfolio','course_project')"
DIMENSIONS = "('demand_pain','competition','differentiation','feasibility','risk','learning')"
VERDICTS = "('GO','PIVOT','NO-GO','INSUFFICIENT_EVIDENCE')"


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


def _now() -> Any:
    return func.now()


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    email: Mapped[str] = mapped_column(CITEXT, unique=True)
    hash: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=_now())
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failed_logins: Mapped[int] = mapped_column(Integer, server_default="0")
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Profile(Base):
    """One row per profile version; the current profile is the highest version."""

    __tablename__ = "profiles"
    __table_args__ = (
        CheckConstraint("hours_per_week BETWEEN 0 AND 80", name="hours"),
        CheckConstraint("weeks_available BETWEEN 1 AND 52", name="weeks"),
        CheckConstraint("team_size BETWEEN 1 AND 10", name="team"),
        CheckConstraint("compute IN ('laptop','free_gpu','paid')", name="compute"),
        CheckConstraint(f"goal IN {GOALS}", name="goal"),
        CheckConstraint("version >= 1", name="version"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey(f"{SCHEMA}.users.id"), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    skills: Mapped[list[str]] = mapped_column(ARRAY(Text))
    hours_per_week: Mapped[int] = mapped_column(Integer)
    weeks_available: Mapped[int] = mapped_column(Integer)
    team_size: Mapped[int] = mapped_column(Integer)
    compute: Mapped[str] = mapped_column(Text)
    goal: Mapped[str] = mapped_column(Text)
    interests: Mapped[list[str]] = mapped_column(ARRAY(Text))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=_now())


class Idea(Base):
    __tablename__ = "ideas"
    __table_args__ = (
        CheckConstraint("source IN ('own','generated')", name="source"),
        Index("ix_ideas_user_created", "user_id", text("created_at DESC")),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey(f"{SCHEMA}.users.id"))
    title: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=_now())
    niche_id: Mapped[str | None] = mapped_column(Text)
    origin_run_id: Mapped[str | None] = mapped_column(Text)


class IdeaVersion(Base):
    __tablename__ = "idea_versions"
    __table_args__ = (
        CheckConstraint("version >= 1", name="version"),
        UniqueConstraint(
            "idea_id", "idempotency_key", name="uq_idea_versions_idea_idempotency_key"
        ),
    )

    idea_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.ideas.id", ondelete="CASCADE"), primary_key=True
    )
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=_now())
    change_note: Mapped[str | None] = mapped_column(Text)
    parent_version: Mapped[int | None] = mapped_column(Integer)
    clarifications: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    card: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    idempotency_key: Mapped[str | None] = mapped_column(Text)


class VerdictRow(Base):
    __tablename__ = "verdicts"
    __table_args__ = (
        CheckConstraint(f"verdict IN {VERDICTS}", name="verdict"),
        CheckConstraint("stability IN ('STABLE','BORDERLINE')", name="stability"),
        ForeignKeyConstraint(
            ["idea_id", "version"],
            [f"{SCHEMA}.idea_versions.idea_id", f"{SCHEMA}.idea_versions.version"],
            ondelete="CASCADE",
            name="fk_verdicts_version",
        ),
        Index("ix_verdicts_version", "idea_id", "version"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    idea_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    version: Mapped[int] = mapped_column(Integer)
    verdict: Mapped[str] = mapped_column(Text)
    score: Mapped[float | None] = mapped_column(Numeric(5, 2))
    confidence: Mapped[float | None] = mapped_column(Numeric(4, 3))
    run_id: Mapped[str] = mapped_column(Text)
    stability: Mapped[str] = mapped_column(Text)
    profile_version: Mapped[int] = mapped_column(Integer)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    engine_version: Mapped[str] = mapped_column(Text)
    rubrics_version: Mapped[str] = mapped_column(Text)
    weights_version: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=_now())


class DimensionScoreRow(Base):
    __tablename__ = "dimension_scores"
    __table_args__ = (CheckConstraint(f"dimension IN {DIMENSIONS}", name="dimension"),)

    verdict_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.verdicts.id", ondelete="CASCADE"), primary_key=True
    )
    dimension: Mapped[str] = mapped_column(Text, primary_key=True)
    score: Mapped[float | None] = mapped_column(Numeric(3, 1))
    conf: Mapped[float] = mapped_column(Numeric(4, 3))
    evidence: Mapped[list[str]] = mapped_column(ARRAY(Text))
    features: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    reason: Mapped[str] = mapped_column(Text)
    rubric_version: Mapped[str] = mapped_column(Text)
    model: Mapped[str] = mapped_column(Text)
    adjusted_by_challenge: Mapped[float] = mapped_column(Numeric(3, 1), server_default="0")


class Approval(Base):
    __tablename__ = "approvals"
    __table_args__ = (
        CheckConstraint("decision IN ('pending','approved','declined','expired')", name="decision"),
        Index("ix_approvals_run", "run_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    run_id: Mapped[str] = mapped_column(Text)
    action: Mapped[str] = mapped_column(Text)
    decision: Mapped[str] = mapped_column(Text, server_default="pending")
    estimated_cost_usd: Mapped[float | None] = mapped_column(Numeric(10, 5))
    budget_usd: Mapped[float | None] = mapped_column(Numeric(10, 5))
    spent_usd: Mapped[float | None] = mapped_column(Numeric(10, 5))
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=_now())
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decided_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey(f"{SCHEMA}.users.id"))


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    idea_id: Mapped[uuid.UUID] = mapped_column(ForeignKey(f"{SCHEMA}.ideas.id", ondelete="CASCADE"))
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey(f"{SCHEMA}.users.id"))
    turns: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, server_default="[]")


class Run(Base):
    """Run state for the orchestrator (M3), persisted so a run can resume after an interrupt."""

    __tablename__ = "runs"
    __table_args__ = (Index("ix_runs_user_created", "user_id", "created_at"),)

    run_id: Mapped[str] = mapped_column(Text, primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey(f"{SCHEMA}.users.id"))
    kind: Mapped[str] = mapped_column(Text)
    state: Mapped[str] = mapped_column(Text)
    input: Mapped[dict[str, Any]] = mapped_column(JSONB)
    context: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")
    pending_question: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=_now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=_now())
