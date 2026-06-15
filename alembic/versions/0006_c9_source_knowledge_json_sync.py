"""c9 source knowledge json sync

Revision ID: 0006_c9_source_knowledge_json_sync
Revises: 0005_source_governance_and_university_directory
Create Date: 2026-05-05 20:05:00
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0006_c9_source_knowledge_json_sync"
down_revision = "0005_source_governance_and_university_directory"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "university_directory",
        sa.Column("entrypoint_candidates_json", sa.JSON(), nullable=False, server_default="[]"),
    )
    op.add_column(
        "university_directory",
        sa.Column("selected_entrypoint_url", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("university_directory", "selected_entrypoint_url")
    op.drop_column("university_directory", "entrypoint_candidates_json")
