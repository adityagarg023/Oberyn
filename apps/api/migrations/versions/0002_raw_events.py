"""Create raw event storage for the ingestion endpoint.

Revision ID: 0002_raw_events
Revises: 0001_initial_schema
"""
from alembic import op

revision = "0002_raw_events"
down_revision = "0001_initial_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # This legacy revision overlaps 0002_raw_event_ingestion. IF NOT EXISTS
    # lets both Alembic heads converge on the same durable table safely.
    op.execute("""
        CREATE TABLE IF NOT EXISTS raw_events (
            project_id uuid NOT NULL,
            organization_id uuid NOT NULL,
            event_id uuid NOT NULL,
            received_at timestamptz NOT NULL DEFAULT now(),
            payload jsonb NOT NULL,
            CONSTRAINT pk_raw_events PRIMARY KEY (project_id, event_id),
            CONSTRAINT ck_raw_events_payload_object CHECK (jsonb_typeof(payload) = 'object'),
            CONSTRAINT fk_raw_events_project_tenant
                FOREIGN KEY (project_id, organization_id)
                REFERENCES projects(id, organization_id) ON DELETE CASCADE
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_raw_events_received_at ON raw_events (received_at)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS raw_events")
