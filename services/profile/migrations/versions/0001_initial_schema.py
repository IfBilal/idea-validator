"""initial schema: every application table (docs/harness/04 §1)

The schema `app` and the `citext` extension are created in migrations/env.py before this runs.

Revision ID: 0001
Revises:
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("email", postgresql.CITEXT(), nullable=False),
        sa.Column("hash", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failed_logins", sa.Integer(), server_default="0", nullable=False),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
        sa.UniqueConstraint("email", name=op.f("uq_users_email")),
        schema="app",
    )
    op.create_table(
        "approvals",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("run_id", sa.Text(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("decision", sa.Text(), server_default="pending", nullable=False),
        sa.Column("estimated_cost_usd", sa.Numeric(precision=10, scale=5), nullable=True),
        sa.Column("budget_usd", sa.Numeric(precision=10, scale=5), nullable=True),
        sa.Column("spent_usd", sa.Numeric(precision=10, scale=5), nullable=True),
        sa.Column(
            "requested_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decided_by", sa.UUID(), nullable=True),
        sa.CheckConstraint(
            "decision IN ('pending','approved','declined','expired')",
            name=op.f("ck_approvals_decision"),
        ),
        sa.ForeignKeyConstraint(
            ["decided_by"], ["app.users.id"], name=op.f("fk_approvals_decided_by_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_approvals")),
        schema="app",
    )
    op.create_index("ix_approvals_run", "approvals", ["run_id"], unique=False, schema="app")
    op.create_table(
        "ideas",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("niche_id", sa.Text(), nullable=True),
        sa.Column("origin_run_id", sa.Text(), nullable=True),
        sa.CheckConstraint("source IN ('own','generated')", name=op.f("ck_ideas_source")),
        sa.ForeignKeyConstraint(["user_id"], ["app.users.id"], name=op.f("fk_ideas_user_id_users")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ideas")),
        schema="app",
    )
    op.create_index(
        "ix_ideas_user_created",
        "ideas",
        ["user_id", sa.literal_column("created_at DESC")],
        unique=False,
        schema="app",
    )
    op.create_table(
        "profiles",
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("skills", sa.ARRAY(sa.Text()), nullable=False),
        sa.Column("hours_per_week", sa.Integer(), nullable=False),
        sa.Column("weeks_available", sa.Integer(), nullable=False),
        sa.Column("team_size", sa.Integer(), nullable=False),
        sa.Column("compute", sa.Text(), nullable=False),
        sa.Column("goal", sa.Text(), nullable=False),
        sa.Column("interests", sa.ARRAY(sa.Text()), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "compute IN ('laptop','free_gpu','paid')", name=op.f("ck_profiles_compute")
        ),
        sa.CheckConstraint(
            "goal IN ('startup','portfolio','course_project')", name=op.f("ck_profiles_goal")
        ),
        sa.CheckConstraint("hours_per_week BETWEEN 0 AND 80", name=op.f("ck_profiles_hours")),
        sa.CheckConstraint("team_size BETWEEN 1 AND 10", name=op.f("ck_profiles_team")),
        sa.CheckConstraint("version >= 1", name=op.f("ck_profiles_version")),
        sa.CheckConstraint("weeks_available BETWEEN 1 AND 52", name=op.f("ck_profiles_weeks")),
        sa.ForeignKeyConstraint(
            ["user_id"], ["app.users.id"], name=op.f("fk_profiles_user_id_users")
        ),
        sa.PrimaryKeyConstraint("user_id", "version", name=op.f("pk_profiles")),
        schema="app",
    )
    op.create_table(
        "runs",
        sa.Column("run_id", sa.Text(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("input", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "context", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False
        ),
        sa.Column("pending_question", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["app.users.id"], name=op.f("fk_runs_user_id_users")),
        sa.PrimaryKeyConstraint("run_id", name=op.f("pk_runs")),
        schema="app",
    )
    op.create_index(
        "ix_runs_user_created", "runs", ["user_id", "created_at"], unique=False, schema="app"
    )
    op.create_table(
        "conversations",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("idea_id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column(
            "turns", postgresql.JSONB(astext_type=sa.Text()), server_default="[]", nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["idea_id"],
            ["app.ideas.id"],
            name=op.f("fk_conversations_idea_id_ideas"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["app.users.id"], name=op.f("fk_conversations_user_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_conversations")),
        schema="app",
    )
    op.create_table(
        "idea_versions",
        sa.Column("idea_id", sa.UUID(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("change_note", sa.Text(), nullable=True),
        sa.Column("parent_version", sa.Integer(), nullable=True),
        sa.Column("clarifications", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.CheckConstraint("version >= 1", name=op.f("ck_idea_versions_version")),
        sa.ForeignKeyConstraint(
            ["idea_id"],
            ["app.ideas.id"],
            name=op.f("fk_idea_versions_idea_id_ideas"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("idea_id", "version", name=op.f("pk_idea_versions")),
        schema="app",
    )
    op.create_table(
        "verdicts",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("idea_id", sa.UUID(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("verdict", sa.Text(), nullable=False),
        sa.Column("score", sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column("confidence", sa.Numeric(precision=4, scale=3), nullable=True),
        sa.Column("run_id", sa.Text(), nullable=False),
        sa.Column("stability", sa.Text(), nullable=False),
        sa.Column("profile_version", sa.Integer(), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("engine_version", sa.Text(), nullable=False),
        sa.Column("rubrics_version", sa.Text(), nullable=False),
        sa.Column("weights_version", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "stability IN ('STABLE','BORDERLINE')", name=op.f("ck_verdicts_stability")
        ),
        sa.CheckConstraint(
            "verdict IN ('GO','PIVOT','NO-GO','INSUFFICIENT_EVIDENCE')",
            name=op.f("ck_verdicts_verdict"),
        ),
        sa.ForeignKeyConstraint(
            ["idea_id", "version"],
            ["app.idea_versions.idea_id", "app.idea_versions.version"],
            name="fk_verdicts_version",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_verdicts")),
        schema="app",
    )
    op.create_index(
        "ix_verdicts_version", "verdicts", ["idea_id", "version"], unique=False, schema="app"
    )
    op.create_table(
        "dimension_scores",
        sa.Column("verdict_id", sa.UUID(), nullable=False),
        sa.Column("dimension", sa.Text(), nullable=False),
        sa.Column("score", sa.Numeric(precision=3, scale=1), nullable=True),
        sa.Column("conf", sa.Numeric(precision=4, scale=3), nullable=False),
        sa.Column("evidence", sa.ARRAY(sa.Text()), nullable=False),
        sa.Column("features", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("rubric_version", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column(
            "adjusted_by_challenge",
            sa.Numeric(precision=3, scale=1),
            server_default="0",
            nullable=False,
        ),
        sa.CheckConstraint(
            "dimension IN ('demand_pain','competition','differentiation','feasibility','risk','learning')",
            name=op.f("ck_dimension_scores_dimension"),
        ),
        sa.ForeignKeyConstraint(
            ["verdict_id"],
            ["app.verdicts.id"],
            name=op.f("fk_dimension_scores_verdict_id_verdicts"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("verdict_id", "dimension", name=op.f("pk_dimension_scores")),
        schema="app",
    )


def downgrade() -> None:
    op.drop_table("dimension_scores", schema="app")
    op.drop_index("ix_verdicts_version", table_name="verdicts", schema="app")
    op.drop_table("verdicts", schema="app")
    op.drop_table("idea_versions", schema="app")
    op.drop_table("conversations", schema="app")
    op.drop_index("ix_runs_user_created", table_name="runs", schema="app")
    op.drop_table("runs", schema="app")
    op.drop_table("profiles", schema="app")
    op.drop_index("ix_ideas_user_created", table_name="ideas", schema="app")
    op.drop_table("ideas", schema="app")
    op.drop_index("ix_approvals_run", table_name="approvals", schema="app")
    op.drop_table("approvals", schema="app")
    op.drop_table("users", schema="app")
