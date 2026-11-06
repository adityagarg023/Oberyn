"""Track distinct users on issues for deterministic event grouping.

Revision ID: 0003_issue_fingerprints
Revises: 0002_raw_event_ingestion, 0002_raw_events
"""
import sqlalchemy as sa
from alembic import op

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
    op.execute("""
        CREATE FUNCTION increment_issue_user_count() RETURNS trigger AS $
        BEGIN
            UPDATE issues
            SET user_count = user_count + 1
            WHERE id = NEW.issue_id AND project_id = NEW.project_id;
            RETURN NEW;
        END;
        $ LANGUAGE plpgsql
    """)
    op.execute("""
        CREATE TRIGGER trg_issue_users_increment_count
        AFTER INSERT ON issue_users
        FOR EACH ROW EXECUTE FUNCTION increment_issue_user_count()
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_issue_users_increment_count ON issue_users")
    op.execute("DROP FUNCTION IF EXISTS increment_issue_user_count()")
    op.drop_index("ix_issue_users_project", table_name="issue_users")
    op.drop_table("issue_users")
    op.drop_column("issues", "user_count")
