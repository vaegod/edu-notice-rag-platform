"""task reliability fields

Revision ID: 0007_task_reliability
Revises: 0006_c9_source_knowledge_json_sync
Create Date: 2026-07-17 12:00:00
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0007_task_reliability"
down_revision = "0006_c9_source_knowledge_json_sync"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("crawl_tasks") as batch_op:
        batch_op.add_column(sa.Column("trace_id", sa.String(length=36), nullable=True))
        batch_op.add_column(
            sa.Column("idempotency_key", sa.String(length=128), nullable=True)
        )
        batch_op.add_column(
            sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0")
        )
        batch_op.add_column(
            sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3")
        )
        batch_op.create_index("ix_crawl_tasks_trace_id", ["trace_id"], unique=False)
        batch_op.create_unique_constraint(
            "uq_crawl_tasks_source_id_idempotency_key",
            ["source_id", "idempotency_key"],
        )


def downgrade() -> None:
    with op.batch_alter_table("crawl_tasks") as batch_op:
        batch_op.drop_constraint(
            "uq_crawl_tasks_source_id_idempotency_key",
            type_="unique",
        )
        batch_op.drop_index("ix_crawl_tasks_trace_id")
        batch_op.drop_column("max_attempts")
        batch_op.drop_column("attempt_count")
        batch_op.drop_column("idempotency_key")
        batch_op.drop_column("trace_id")
