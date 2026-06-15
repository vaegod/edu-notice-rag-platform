"""initial schema

Revision ID: 0001_initial
Revises: None
Create Date: 2026-03-23 11:20:00
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "llm_logs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("biz_type", sa.String(length=64), nullable=False),
        sa.Column("related_id", sa.Integer(), nullable=True),
        sa.Column("model_name", sa.String(length=128), nullable=False),
        sa.Column("prompt_text", sa.Text(), nullable=False),
        sa.Column("response_text", sa.Text(), nullable=True),
        sa.Column("parsed_json", sa.JSON(), nullable=True),
        sa.Column("success", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_llm_logs_biz_type", "llm_logs", ["biz_type"])
    op.create_index("ix_llm_logs_related_id", "llm_logs", ["related_id"])

    op.create_table(
        "nl_tasks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_input", sa.Text(), nullable=False),
        sa.Column("intent", sa.String(length=64), nullable=False),
        sa.Column("parsed_payload", sa.JSON(), nullable=False),
        sa.Column("validation_status", sa.String(length=32), nullable=False),
        sa.Column("validation_message", sa.Text(), nullable=True),
        sa.Column("created_task_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_nl_tasks_id", "nl_tasks", ["id"])
    op.create_index("ix_nl_tasks_intent", "nl_tasks", ["intent"])

    op.create_table(
        "sources",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("base_url", sa.Text(), nullable=False),
        sa.Column("site_type", sa.String(length=64), nullable=False),
        sa.Column("crawl_mode", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("config_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_sources_id", "sources", ["id"])
    op.create_index("ix_sources_name", "sources", ["name"], unique=True)

    op.create_table(
        "crawl_tasks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("task_type", sa.String(length=64), nullable=False),
        sa.Column("source_id", sa.Integer(), sa.ForeignKey("sources.id"), nullable=False),
        sa.Column("trigger_mode", sa.String(length=32), nullable=False),
        sa.Column("task_payload", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_crawl_tasks_id", "crawl_tasks", ["id"])
    op.create_index("ix_crawl_tasks_source_id", "crawl_tasks", ["source_id"])
    op.create_index("ix_crawl_tasks_status", "crawl_tasks", ["status"])

    op.create_table(
        "raw_pages",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source_id", sa.Integer(), sa.ForeignKey("sources.id"), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=True),
        sa.Column("raw_html", sa.Text(), nullable=False),
        sa.Column("raw_text", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("http_status", sa.Integer(), nullable=False),
        sa.Column("crawled_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_raw_pages_id", "raw_pages", ["id"])
    op.create_index("ix_raw_pages_source_id", "raw_pages", ["source_id"])
    op.create_index("ix_raw_pages_content_hash", "raw_pages", ["content_hash"])
    op.create_index("ix_raw_pages_url", "raw_pages", ["url"], unique=True)

    op.create_table(
        "documents",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("raw_page_id", sa.Integer(), sa.ForeignKey("raw_pages.id"), nullable=False),
        sa.Column("doc_type", sa.String(length=64), nullable=True),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("publish_date", sa.Date(), nullable=True),
        sa.Column("deadline", sa.Date(), nullable=True),
        sa.Column("department", sa.String(length=255), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("event_time", sa.DateTime(), nullable=True),
        sa.Column("event_location", sa.String(length=255), nullable=True),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("model_output", sa.JSON(), nullable=False),
        sa.Column("model_name", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_documents_id", "documents", ["id"])
    op.create_index("ix_documents_raw_page_id", "documents", ["raw_page_id"], unique=True)
    op.create_index("ix_documents_doc_type", "documents", ["doc_type"])
    op.create_index("ix_documents_title", "documents", ["title"])
    op.create_index("ix_documents_publish_date", "documents", ["publish_date"])
    op.create_index("ix_documents_deadline", "documents", ["deadline"])
    op.create_index("ix_documents_department", "documents", ["department"])
    op.create_index("ix_documents_source_url", "documents", ["source_url"], unique=True)

    op.create_table(
        "document_tags",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("document_id", sa.Integer(), sa.ForeignKey("documents.id"), nullable=False),
        sa.Column("tag", sa.String(length=128), nullable=False),
    )
    op.create_index("ix_document_tags_document_id", "document_tags", ["document_id"])
    op.create_index("ix_document_tags_tag", "document_tags", ["tag"])

    op.create_table(
        "attachments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("document_id", sa.Integer(), sa.ForeignKey("documents.id"), nullable=False),
        sa.Column("file_name", sa.String(length=255), nullable=False),
        sa.Column("file_url", sa.Text(), nullable=False),
        sa.Column("file_type", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_attachments_document_id", "attachments", ["document_id"])


def downgrade() -> None:
    op.drop_index("ix_attachments_document_id", table_name="attachments")
    op.drop_table("attachments")
    op.drop_index("ix_document_tags_tag", table_name="document_tags")
    op.drop_index("ix_document_tags_document_id", table_name="document_tags")
    op.drop_table("document_tags")
    op.drop_index("ix_documents_source_url", table_name="documents")
    op.drop_index("ix_documents_department", table_name="documents")
    op.drop_index("ix_documents_deadline", table_name="documents")
    op.drop_index("ix_documents_publish_date", table_name="documents")
    op.drop_index("ix_documents_title", table_name="documents")
    op.drop_index("ix_documents_doc_type", table_name="documents")
    op.drop_index("ix_documents_raw_page_id", table_name="documents")
    op.drop_index("ix_documents_id", table_name="documents")
    op.drop_table("documents")
    op.drop_index("ix_raw_pages_url", table_name="raw_pages")
    op.drop_index("ix_raw_pages_content_hash", table_name="raw_pages")
    op.drop_index("ix_raw_pages_source_id", table_name="raw_pages")
    op.drop_index("ix_raw_pages_id", table_name="raw_pages")
    op.drop_table("raw_pages")
    op.drop_index("ix_crawl_tasks_status", table_name="crawl_tasks")
    op.drop_index("ix_crawl_tasks_source_id", table_name="crawl_tasks")
    op.drop_index("ix_crawl_tasks_id", table_name="crawl_tasks")
    op.drop_table("crawl_tasks")
    op.drop_index("ix_sources_name", table_name="sources")
    op.drop_index("ix_sources_id", table_name="sources")
    op.drop_table("sources")
    op.drop_index("ix_nl_tasks_intent", table_name="nl_tasks")
    op.drop_index("ix_nl_tasks_id", table_name="nl_tasks")
    op.drop_table("nl_tasks")
    op.drop_index("ix_llm_logs_related_id", table_name="llm_logs")
    op.drop_index("ix_llm_logs_biz_type", table_name="llm_logs")
    op.drop_table("llm_logs")
