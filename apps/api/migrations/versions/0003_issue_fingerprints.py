"""Track distinct users on issues for deterministic event grouping.

Revision ID: 0003_issue_fingerprints
Revises: 0002_raw_event_ingestion, 0002_raw_events
"""
from alembic import op
import sqlalchemy as sa

revision = "0003_issue_fingerprints"
down_revision = ("0002_raw_event_ingestion", "0002_raw_events")
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("issues", sa.Column("user_count", sa.BigInteger(), nullable=False, server_default="0"))
    op.create_table(
        "issue_users",
        sa.Column("issue_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("issue_id", "user_id", name="pk_issue_users"),
        sa.ForeignKeyConstraint(
            ["issue_id", "project_id"], ["issues.id", "issues.project_id"],
            ondelete="CASCADE", name="fk_issue_users_issue_project",
        ),
    )
    op.create_index("ix_issue_users_project", "issue_users", ["project_id"])


def downgrade() -> None:
    op.drop_index("ix_issue_users_project", table_name="issue_users")
    op.drop_table("issue_users")
    op.drop_column("issues", "user_count")
