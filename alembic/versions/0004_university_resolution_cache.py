"""university resolution cache

Revision ID: 0004_university_resolution_cache
Revises: 0003_source_discovery_pipeline
Create Date: 2026-03-23 19:20:00
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0004_university_resolution_cache"
down_revision = "0003_source_discovery_pipeline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "university_resolution_cache",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("university_name", sa.String(length=255), nullable=False),
        sa.Column("normalized_name", sa.String(length=255), nullable=False),
        sa.Column("resolved_url", sa.Text(), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("confidence_score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("raw_candidates_json", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("last_verified_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_university_resolution_cache_id", "university_resolution_cache", ["id"])
    op.create_index(
        "ix_university_resolution_cache_normalized_name",
        "university_resolution_cache",
        ["normalized_name"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_university_resolution_cache_normalized_name", table_name="university_resolution_cache")
    op.drop_index("ix_university_resolution_cache_id", table_name="university_resolution_cache")
    op.drop_table("university_resolution_cache")
