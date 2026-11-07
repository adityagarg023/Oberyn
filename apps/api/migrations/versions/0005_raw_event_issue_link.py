"""Link raw ingested events to their grouped issue."""

from alembic import op
import sqlalchemy as sa

revision = "0005_raw_event_issue_link"
down_revision = "0004_clerk_members"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("raw_events", sa.Column("issue_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_raw_events_issue_project",
        "raw_events",
        "issues",
        ["issue_id", "project_id"],
        ["id", "project_id"],
        ondelete="CASCADE",
    )
    op.create_index(
        "ix_raw_events_issue_received_at",
        "raw_events",
        ["issue_id", sa.text("received_at DESC")],
    )


def downgrade() -> None:
    op.drop_index("ix_raw_events_issue_received_at", table_name="raw_events")
    op.drop_constraint("fk_raw_events_issue_project", "raw_events", type_="foreignkey")
    op.drop_column("raw_events", "issue_id")
