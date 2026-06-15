"""source governance and university directory

Revision ID: 0005_source_governance_and_university_directory
Revises: 0004_university_resolution_cache
Create Date: 2026-05-05 18:10:00
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0005_source_governance_and_university_directory"
down_revision = "0004_university_resolution_cache"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("sources", sa.Column("entrypoint_url", sa.Text(), nullable=True))
    op.add_column("sources", sa.Column("health_status", sa.String(length=16), nullable=False, server_default="healthy"))
    op.add_column("sources", sa.Column("last_discovered_at", sa.DateTime(), nullable=True))
    op.add_column("sources", sa.Column("last_success_at", sa.DateTime(), nullable=True))
    op.add_column("sources", sa.Column("last_failure_reason", sa.Text(), nullable=True))
    op.add_column("sources", sa.Column("validation_evidence", sa.JSON(), nullable=False, server_default="{}"))

    op.create_table(
        "university_directory",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("university_name", sa.String(length=255), nullable=False),
        sa.Column("normalized_name", sa.String(length=255), nullable=False),
        sa.Column("official_homepage_url", sa.Text(), nullable=False),
        sa.Column("admissions_entry_url", sa.Text(), nullable=False),
        sa.Column("scope", sa.String(length=64), nullable=False, server_default="graduate_admissions"),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="active"),
        sa.Column("last_checked_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_university_directory_id", "university_directory", ["id"])
    op.create_index(
        "ix_university_directory_normalized_name",
        "university_directory",
        ["normalized_name"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_university_directory_normalized_name", table_name="university_directory")
    op.drop_index("ix_university_directory_id", table_name="university_directory")
    op.drop_table("university_directory")

    op.drop_column("sources", "validation_evidence")
    op.drop_column("sources", "last_failure_reason")
    op.drop_column("sources", "last_success_at")
    op.drop_column("sources", "last_discovered_at")
    op.drop_column("sources", "health_status")
    op.drop_column("sources", "entrypoint_url")
