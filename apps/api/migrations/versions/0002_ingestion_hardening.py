"""Add organization quotas and per-project CORS allowlists.

Revision ID: 0002_ingestion_hardening
"""
from alembic import op

revision = "0002_ingestion_hardening"
down_revision = "0001_initial_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE organizations ADD COLUMN monthly_event_quota bigint "
        "NOT NULL DEFAULT 1000000 CHECK (monthly_event_quota > 0)"
    )
    op.execute(
        "ALTER TABLE projects ADD COLUMN cors_origins jsonb NOT NULL "
        "DEFAULT '[]'::jsonb CHECK (jsonb_typeof(cors_origins) = 'array')"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE projects DROP COLUMN cors_origins")
    op.execute("ALTER TABLE organizations DROP COLUMN monthly_event_quota")
