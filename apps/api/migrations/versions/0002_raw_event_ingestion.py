"""Add durable raw event ingestion storage.

Revision ID: 0002_raw_event_ingestion
Revises: 0001_initial_schema
"""

from alembic import op

revision = "0002_raw_event_ingestion"
down_revision = "0001_initial_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE raw_events (
            project_id uuid NOT NULL,
            organization_id uuid NOT NULL,
            event_id uuid NOT NULL,
            received_at timestamptz NOT NULL DEFAULT now(),
            payload jsonb NOT NULL,
            CONSTRAINT pk_raw_events PRIMARY KEY (project_id, event_id),
            CONSTRAINT ck_raw_events_payload_object
                CHECK (jsonb_typeof(payload) = 'object'),
            CONSTRAINT fk_raw_events_project_tenant
                FOREIGN KEY (project_id, organization_id)
                REFERENCES projects(id, organization_id)
                ON DELETE CASCADE
        )
        """
    )
    op.execute(
        """
        CREATE INDEX ix_raw_events_received_at
        ON raw_events (received_at)
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS raw_events")
