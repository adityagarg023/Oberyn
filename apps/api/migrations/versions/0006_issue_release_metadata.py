"""Track issue release history and resolution metadata.

Revision ID: 0006_issue_release_metadata
"""
import sqlalchemy as sa
from alembic import op

revision = "0006_issue_release_metadata"
down_revision = "0005_raw_event_issue_link"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("issues", sa.Column("first_release", sa.String(length=200), nullable=True))
    op.add_column("issues", sa.Column("last_release", sa.String(length=200), nullable=True))
    op.add_column("issues", sa.Column("resolved_in_release", sa.String(length=200), nullable=True))
    op.add_column("issues", sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True))
    op.execute("""
        WITH release_bounds AS (
            SELECT issue_id,
                   (array_agg(payload->>'release' ORDER BY received_at ASC)
                       FILTER (WHERE payload->>'release' IS NOT NULL))[1] AS first_release,
                   (array_agg(payload->>'release' ORDER BY received_at DESC)
                       FILTER (WHERE payload->>'release' IS NOT NULL))[1] AS last_release
            FROM raw_events
            WHERE issue_id IS NOT NULL
            GROUP BY issue_id
        )
        UPDATE issues AS i
        SET first_release = b.first_release,
            last_release = b.last_release
        FROM release_bounds AS b
        WHERE i.id = b.issue_id
    """)
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
