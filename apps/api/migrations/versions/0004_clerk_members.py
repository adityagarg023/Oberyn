"""Attach Clerk user IDs to organization members."""
from alembic import op
import sqlalchemy as sa

revision = "0004_clerk_members"
down_revision = "0003_issue_fingerprints"
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.add_column("members", sa.Column("clerk_user_id", sa.String(255), nullable=True))
    op.create_index("uq_members_clerk_user_org", "members", ["organization_id", "clerk_user_id"],
                    unique=True, postgresql_where=sa.text("clerk_user_id IS NOT NULL"))

def downgrade() -> None:
    op.drop_index("uq_members_clerk_user_org", table_name="members")
    op.drop_column("members", "clerk_user_id")
