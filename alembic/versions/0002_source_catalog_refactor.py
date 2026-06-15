"""source catalog refactor

Revision ID: 0002_source_catalog_refactor
Revises: 0001_initial
Create Date: 2026-03-23 15:10:00
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0002_source_catalog_refactor"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "source_adapters",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("is_builtin", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_source_adapters_id", "source_adapters", ["id"])
    op.create_index("ix_source_adapters_code", "source_adapters", ["code"], unique=True)

    op.create_table(
        "source_templates",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("adapter_id", sa.Integer(), sa.ForeignKey("source_adapters.id"), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("template_schema_json", sa.JSON(), nullable=False),
        sa.Column("default_config_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_source_templates_id", "source_templates", ["id"])
    op.create_index("ix_source_templates_adapter_id", "source_templates", ["adapter_id"])
    op.create_index("ix_source_templates_code", "source_templates", ["code"], unique=True)

    with op.batch_alter_table("sources", recreate="auto") as batch_op:
        batch_op.add_column(sa.Column("organization_name", sa.String(length=255), nullable=True))
        batch_op.add_column(
            sa.Column("source_type", sa.String(length=64), nullable=False, server_default="notice"),
        )
        batch_op.add_column(sa.Column("adapter_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("template_id", sa.Integer(), nullable=True))
        batch_op.add_column(
            sa.Column("start_urls_json", sa.JSON(), nullable=False, server_default="[]"),
        )
        batch_op.add_column(
            sa.Column(
                "onboarding_status",
                sa.String(length=32),
                nullable=False,
                server_default="ready",
            ),
        )
        batch_op.add_column(sa.Column("confidence_score", sa.Float(), nullable=True))
        batch_op.add_column(sa.Column("last_validated_at", sa.DateTime(), nullable=True))
        batch_op.create_index("ix_sources_adapter_id", ["adapter_id"])
        batch_op.create_index("ix_sources_template_id", ["template_id"])
        batch_op.create_foreign_key(
            "fk_sources_adapter_id_source_adapters",
            "source_adapters",
            ["adapter_id"],
            ["id"],
        )
        batch_op.create_foreign_key(
            "fk_sources_template_id_source_templates",
            "source_templates",
            ["template_id"],
            ["id"],
        )

    op.create_table(
        "source_validation_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source_id", sa.Integer(), sa.ForeignKey("sources.id"), nullable=False),
        sa.Column("validation_type", sa.String(length=64), nullable=False),
        sa.Column("sample_count", sa.Integer(), nullable=False),
        sa.Column("success_count", sa.Integer(), nullable=False),
        sa.Column("success_rate", sa.Float(), nullable=False),
        sa.Column("error_summary", sa.Text(), nullable=True),
        sa.Column("report_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_source_validation_runs_id", "source_validation_runs", ["id"])
    op.create_index("ix_source_validation_runs_source_id", "source_validation_runs", ["source_id"])


def downgrade() -> None:
    op.drop_index("ix_source_validation_runs_source_id", table_name="source_validation_runs")
    op.drop_index("ix_source_validation_runs_id", table_name="source_validation_runs")
    op.drop_table("source_validation_runs")

    with op.batch_alter_table("sources", recreate="auto") as batch_op:
        batch_op.drop_constraint("fk_sources_template_id_source_templates", type_="foreignkey")
        batch_op.drop_constraint("fk_sources_adapter_id_source_adapters", type_="foreignkey")
        batch_op.drop_index("ix_sources_template_id")
        batch_op.drop_index("ix_sources_adapter_id")
        batch_op.drop_column("last_validated_at")
        batch_op.drop_column("confidence_score")
        batch_op.drop_column("onboarding_status")
        batch_op.drop_column("start_urls_json")
        batch_op.drop_column("template_id")
        batch_op.drop_column("adapter_id")
        batch_op.drop_column("source_type")
        batch_op.drop_column("organization_name")

    op.drop_index("ix_source_templates_code", table_name="source_templates")
    op.drop_index("ix_source_templates_adapter_id", table_name="source_templates")
    op.drop_index("ix_source_templates_id", table_name="source_templates")
    op.drop_table("source_templates")

    op.drop_index("ix_source_adapters_code", table_name="source_adapters")
    op.drop_index("ix_source_adapters_id", table_name="source_adapters")
    op.drop_table("source_adapters")
