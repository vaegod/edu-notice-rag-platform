"""source discovery pipeline

Revision ID: 0003_source_discovery_pipeline
Revises: 0002_source_catalog_refactor
Create Date: 2026-03-23 18:30:00
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0003_source_discovery_pipeline"
down_revision = "0002_source_catalog_refactor"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "source_discovery_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("input_type", sa.String(length=32), nullable=False, server_default="homepage_url"),
        sa.Column("input_value", sa.Text(), nullable=False),
        sa.Column("resolved_homepage_url", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
        sa.Column("selected_candidate_url", sa.Text(), nullable=True),
        sa.Column("draft_source_id", sa.Integer(), sa.ForeignKey("sources.id"), nullable=True),
        sa.Column("summary_json", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_source_discovery_runs_id", "source_discovery_runs", ["id"])
    op.create_index(
        "ix_source_discovery_runs_draft_source_id",
        "source_discovery_runs",
        ["draft_source_id"],
    )

    op.create_table(
        "source_discovery_candidates",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "run_id",
            sa.Integer(),
            sa.ForeignKey("source_discovery_runs.id"),
            nullable=False,
        ),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("normalized_url", sa.Text(), nullable=False),
        sa.Column("page_type", sa.String(length=32), nullable=False, server_default="invalid"),
        sa.Column("render_mode", sa.String(length=32), nullable=False, server_default="static"),
        sa.Column("confidence_score", sa.Float(), nullable=False, server_default="0"),
        sa.Column(
            "discovery_channel",
            sa.String(length=64),
            nullable=False,
            server_default="manual",
        ),
        sa.Column("features_json", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("is_recommended", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_source_discovery_candidates_id", "source_discovery_candidates", ["id"])
    op.create_index("ix_source_discovery_candidates_run_id", "source_discovery_candidates", ["run_id"])

    op.create_table(
        "source_schema_candidates",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "candidate_id",
            sa.Integer(),
            sa.ForeignKey("source_discovery_candidates.id"),
            nullable=False,
        ),
        sa.Column("schema_type", sa.String(length=16), nullable=False),
        sa.Column("config_json", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("confidence_score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("generated_by", sa.String(length=32), nullable=False, server_default="rules"),
        sa.Column("validation_report_json", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_source_schema_candidates_id", "source_schema_candidates", ["id"])
    op.create_index(
        "ix_source_schema_candidates_candidate_id",
        "source_schema_candidates",
        ["candidate_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_source_schema_candidates_candidate_id", table_name="source_schema_candidates")
    op.drop_index("ix_source_schema_candidates_id", table_name="source_schema_candidates")
    op.drop_table("source_schema_candidates")

    op.drop_index("ix_source_discovery_candidates_run_id", table_name="source_discovery_candidates")
    op.drop_index("ix_source_discovery_candidates_id", table_name="source_discovery_candidates")
    op.drop_table("source_discovery_candidates")

    op.drop_index("ix_source_discovery_runs_draft_source_id", table_name="source_discovery_runs")
    op.drop_index("ix_source_discovery_runs_id", table_name="source_discovery_runs")
    op.drop_table("source_discovery_runs")
