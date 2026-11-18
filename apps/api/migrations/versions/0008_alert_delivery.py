"""Add durable alert delivery state and cooldown deduplication."""

from alembic import op

revision = "0008_alert_delivery"
down_revision = "0007_ingestion_hardening"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE alert_history
            ADD COLUMN issue_id uuid,
            ADD COLUMN status varchar(16) NOT NULL DEFAULT 'pending',
            ADD COLUMN attempts integer NOT NULL DEFAULT 0,
            ADD COLUMN next_attempt_at timestamptz NOT NULL DEFAULT now(),
            ADD COLUMN delivered_at timestamptz,
            ADD COLUMN last_error text,
            ADD COLUMN dedupe_key varchar(255),
            ADD COLUMN updated_at timestamptz NOT NULL DEFAULT now(),
            ADD CONSTRAINT ck_alert_history_status
                CHECK (status IN ('pending', 'sending', 'delivered', 'failed')),
            ADD CONSTRAINT ck_alert_history_attempts
                CHECK (attempts >= 0),
            ADD CONSTRAINT fk_alert_history_issue_project
                FOREIGN KEY (issue_id, project_id)
                REFERENCES issues(id, project_id) ON DELETE CASCADE
    """)
    op.execute(
        "CREATE UNIQUE INDEX uq_alert_history_dedupe_key "
        "ON alert_history(dedupe_key) WHERE dedupe_key IS NOT NULL"
    )
    op.execute(
        "CREATE INDEX ix_alert_history_delivery "
        "ON alert_history(status, next_attempt_at, triggered_at)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_alert_history_delivery")
    op.execute("DROP INDEX IF EXISTS uq_alert_history_dedupe_key")
    op.execute("""
        ALTER TABLE alert_history
            DROP CONSTRAINT IF EXISTS fk_alert_history_issue_project,
            DROP CONSTRAINT IF EXISTS ck_alert_history_attempts,
            DROP CONSTRAINT IF EXISTS ck_alert_history_status,
            DROP COLUMN IF EXISTS updated_at,
            DROP COLUMN IF EXISTS dedupe_key,
            DROP COLUMN IF EXISTS last_error,
            DROP COLUMN IF EXISTS delivered_at,
            DROP COLUMN IF EXISTS next_attempt_at,
            DROP COLUMN IF EXISTS attempts,
            DROP COLUMN IF EXISTS status,
            DROP COLUMN IF EXISTS issue_id
    """)
