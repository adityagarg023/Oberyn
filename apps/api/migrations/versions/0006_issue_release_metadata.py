"""Track issue release history and resolution metadata.

Revision ID: 0006_issue_release_metadata
"""
from alembic import op
import sqlalchemy as sa

revision = "0006_issue_release_metadata"
down_revision = "0005_raw_event_issue_link"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("issues", sa.Column("first_release", sa.String(length=200), nullable=True))
    op.add_column("issues", sa.Column("last_release", sa.String(length=200), nullable=True))
    op.add_column("issues", sa.Column("resolved_in_release", sa.String(length=200), nullable=True))
    op.add_column("issues", sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index(
        "ix_raw_events_project_release",
        "raw_events",
        ["project_id", sa.text("(payload->>'release')")],
    )
    op.create_index(
        "ix_raw_events_project_environment",
        "raw_events",
        ["project_id", sa.text("(payload->>'environment')")],
    )
    op.create_index(
        "ix_raw_events_project_level",
        "raw_events",
        ["project_id", sa.text("(payload->>'level')")],
    )
    op.create_index(
        "ix_raw_events_payload_tags",
        "raw_events",
        [sa.text("(payload->'tags')")],
        postgresql_using="gin",
    )


def downgrade() -> None:
    op.drop_index("ix_raw_events_payload_tags", table_name="raw_events")
    op.drop_index("ix_raw_events_project_level", table_name="raw_events")
    op.drop_index("ix_raw_events_project_environment", table_name="raw_events")
    op.drop_index("ix_raw_events_project_release", table_name="raw_events")
    op.drop_column("issues", "resolved_at")
    op.drop_column("issues", "resolved_in_release")
    op.drop_column("issues", "last_release")
    op.drop_column("issues", "first_release")
