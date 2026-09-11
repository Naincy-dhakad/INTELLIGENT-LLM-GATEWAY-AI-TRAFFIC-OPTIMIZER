"""create durable gateway usage records

Revision ID: 0001_create_usage_records
Revises:
Create Date: 2026-09-11
"""

from alembic import op
import sqlalchemy as sa

revision = "0001_create_usage_records"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "gateway_usage_records",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("request_id", sa.String(length=128), nullable=False),
        sa.Column("principal_id", sa.String(length=64), nullable=True),
        sa.Column("provider_id", sa.String(length=128), nullable=True),
        sa.Column("model_id", sa.String(length=128), nullable=True),
        sa.Column("outcome", sa.String(length=32), nullable=False),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_category", sa.String(length=64), nullable=True),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("fallback_used", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("routing_objective", sa.String(length=32), nullable=True),
        sa.Column("routing_policy_version", sa.String(length=128), nullable=True),
        sa.Column("classification_category", sa.String(length=64), nullable=True),
        sa.Column("classification_complexity_level", sa.String(length=16), nullable=True),
        sa.Column("classification_complexity_score", sa.Integer(), nullable=True),
        sa.Column("estimated_cost_usd", sa.Numeric(18, 8), nullable=True),
        sa.Column("input_tokens", sa.BigInteger(), nullable=True),
        sa.Column("output_tokens", sa.BigInteger(), nullable=True),
        sa.Column("total_tokens", sa.BigInteger(), nullable=True),
        sa.Column("estimated_latency_ms", sa.Integer(), nullable=True),
        sa.Column("actual_gateway_latency_ms", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_gateway_usage_records_request_id", "gateway_usage_records", ["request_id"])
    op.create_index("ix_gateway_usage_records_principal_id", "gateway_usage_records", ["principal_id"])


def downgrade() -> None:
    op.drop_index("ix_gateway_usage_records_principal_id", table_name="gateway_usage_records")
    op.drop_index("ix_gateway_usage_records_request_id", table_name="gateway_usage_records")
    op.drop_table("gateway_usage_records")
