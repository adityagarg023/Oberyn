"""Create Oberyn's tenant-aware PostgreSQL schema.

Revision ID: 0001_initial_schema
"""

from alembic import op

revision = "0001_initial_schema"
down_revision = None
branch_labels = None
depends_on = None


def execute_statements(sql: str) -> None:
    for statement in sql.split(";"):
        statement = statement.strip()
        if statement:
            op.execute(statement)


def upgrade() -> None:
    execute_statements("""
    CREATE TABLE organizations (
        id uuid PRIMARY KEY,
        name varchar(160) NOT NULL,
        slug varchar(80) NOT NULL UNIQUE,
        owner_member_id uuid,
        created_at timestamptz NOT NULL DEFAULT now()
    );
    CREATE TABLE members (
        id uuid PRIMARY KEY,
        organization_id uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
        email varchar(320) NOT NULL,
        name varchar(160),
        role varchar(16) NOT NULL DEFAULT 'member'
            CHECK (role IN ('owner', 'admin', 'member')),
        created_at timestamptz NOT NULL DEFAULT now(),
        CONSTRAINT uq_members_org_email UNIQUE (organization_id, email),
        CONSTRAINT uq_members_id_org UNIQUE (id, organization_id)
    );
    ALTER TABLE organizations ADD CONSTRAINT fk_organizations_owner_same_org
        FOREIGN KEY (owner_member_id, id) REFERENCES members(id, organization_id)
        DEFERRABLE INITIALLY DEFERRED;
    CREATE TABLE projects (
        id uuid PRIMARY KEY,
        organization_id uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
        created_by_member_id uuid NOT NULL,
        name varchar(160) NOT NULL,
        slug varchar(80) NOT NULL,
        platform varchar(40),
        created_at timestamptz NOT NULL DEFAULT now(),
        CONSTRAINT uq_projects_org_slug UNIQUE (organization_id, slug),
        CONSTRAINT uq_projects_id_org UNIQUE (id, organization_id),
        CONSTRAINT fk_projects_creator_same_org FOREIGN KEY (created_by_member_id, organization_id)
            REFERENCES members(id, organization_id) ON DELETE RESTRICT
    );
    CREATE TABLE project_keys (
        id uuid PRIMARY KEY,
        project_id uuid NOT NULL,
        organization_id uuid NOT NULL,
        key_hash varchar(128) NOT NULL UNIQUE,
        label varchar(100) NOT NULL DEFAULT 'default',
        revoked_at timestamptz,
        created_at timestamptz NOT NULL DEFAULT now(),
        CONSTRAINT fk_project_keys_project_tenant FOREIGN KEY (project_id, organization_id)
            REFERENCES projects(id, organization_id) ON DELETE CASCADE
    );
    CREATE INDEX ix_project_keys_project ON project_keys(project_id);
    CREATE TABLE releases (
        id uuid PRIMARY KEY,
        project_id uuid NOT NULL,
        organization_id uuid NOT NULL,
        version varchar(200) NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now(),
        CONSTRAINT uq_releases_project_version UNIQUE (project_id, version),
        CONSTRAINT uq_releases_id_project UNIQUE (id, project_id),
        CONSTRAINT fk_releases_project_tenant FOREIGN KEY (project_id, organization_id)
            REFERENCES projects(id, organization_id) ON DELETE CASCADE
    );
    CREATE TABLE source_maps (
        id uuid PRIMARY KEY,
        project_id uuid NOT NULL,
        organization_id uuid NOT NULL,
        release_id uuid NOT NULL,
        file_path text NOT NULL,
        artifact_url text,
        created_at timestamptz NOT NULL DEFAULT now(),
        CONSTRAINT uq_source_maps_file UNIQUE (project_id, release_id, file_path),
        CONSTRAINT fk_source_maps_project_tenant FOREIGN KEY (project_id, organization_id)
            REFERENCES projects(id, organization_id) ON DELETE CASCADE,
        CONSTRAINT fk_source_maps_release_project FOREIGN KEY (release_id, project_id)
            REFERENCES releases(id, project_id) ON DELETE CASCADE
    );
    CREATE INDEX ix_source_maps_project_release ON source_maps(project_id, release_id);
    CREATE TABLE issues (
        id uuid PRIMARY KEY,
        project_id uuid NOT NULL,
        organization_id uuid NOT NULL,
        fingerprint varchar(128) NOT NULL,
        title text NOT NULL,
        status varchar(16) NOT NULL DEFAULT 'unresolved'
            CHECK (status IN ('unresolved', 'resolved', 'ignored', 'regressed')),
        first_seen timestamptz NOT NULL,
        last_seen timestamptz NOT NULL,
        event_count bigint NOT NULL DEFAULT 0 CHECK (event_count >= 0),
        created_at timestamptz NOT NULL DEFAULT now(),
        CONSTRAINT uq_issues_project_fingerprint UNIQUE (project_id, fingerprint),
        CONSTRAINT uq_issues_id_project UNIQUE (id, project_id),
        CONSTRAINT fk_issues_project_tenant FOREIGN KEY (project_id, organization_id)
            REFERENCES projects(id, organization_id) ON DELETE CASCADE
    );
    CREATE INDEX ix_issues_project_status_last_seen
        ON issues(project_id, status, last_seen DESC);
    CREATE INDEX ix_issues_organization ON issues(organization_id);
    CREATE TABLE alert_rules (
        id uuid PRIMARY KEY,
        project_id uuid NOT NULL,
        organization_id uuid NOT NULL,
        name varchar(120) NOT NULL,
        threshold integer NOT NULL CHECK (threshold > 0),
        window_seconds integer NOT NULL DEFAULT 300 CHECK (window_seconds > 0),
        enabled boolean NOT NULL DEFAULT true,
        config jsonb NOT NULL DEFAULT '{}'::jsonb,
        created_at timestamptz NOT NULL DEFAULT now(),
        CONSTRAINT uq_alert_rules_id_project UNIQUE (id, project_id),
        CONSTRAINT fk_alert_rules_project_tenant FOREIGN KEY (project_id, organization_id)
            REFERENCES projects(id, organization_id) ON DELETE CASCADE
    );
    CREATE INDEX ix_alert_rules_project_enabled ON alert_rules(project_id, enabled);
    CREATE TABLE alert_history (
        id uuid PRIMARY KEY,
        alert_rule_id uuid NOT NULL,
        project_id uuid NOT NULL,
        triggered_at timestamptz NOT NULL,
        payload jsonb NOT NULL DEFAULT '{}'::jsonb,
        created_at timestamptz NOT NULL DEFAULT now(),
        CONSTRAINT fk_alert_history_rule_project FOREIGN KEY (alert_rule_id, project_id)
            REFERENCES alert_rules(id, project_id) ON DELETE CASCADE
    );
    CREATE INDEX ix_alert_history_project_created ON alert_history(project_id, created_at DESC);
    CREATE TABLE events (
        id uuid NOT NULL,
        issue_id uuid NOT NULL,
        project_id uuid NOT NULL,
        organization_id uuid NOT NULL,
        received_at timestamptz NOT NULL,
        release varchar(200),
        environment varchar(64) NOT NULL DEFAULT 'production',
        payload jsonb NOT NULL CHECK (jsonb_typeof(payload) = 'object'),
        created_at timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY (id, received_at),
        CONSTRAINT fk_events_issue_project FOREIGN KEY (issue_id, project_id)
            REFERENCES issues(id, project_id) ON DELETE CASCADE,
        CONSTRAINT fk_events_project_tenant FOREIGN KEY (project_id, organization_id)
            REFERENCES projects(id, organization_id) ON DELETE CASCADE
    ) PARTITION BY RANGE (received_at);
    CREATE TABLE events_default PARTITION OF events DEFAULT;
    CREATE INDEX ix_events_issue_received_at ON events(issue_id, received_at DESC);
    CREATE INDEX ix_events_project_received_at ON events(project_id, received_at DESC);
    CREATE INDEX ix_events_organization_received_at ON events(organization_id, received_at DESC);
    """)


def downgrade() -> None:
    execute_statements("""
    DROP TABLE IF EXISTS events CASCADE;
    DROP TABLE IF EXISTS alert_history CASCADE;
    DROP TABLE IF EXISTS alert_rules CASCADE;
    DROP TABLE IF EXISTS source_maps CASCADE;
    DROP TABLE IF EXISTS issues CASCADE;
    DROP TABLE IF EXISTS releases CASCADE;
    DROP TABLE IF EXISTS project_keys CASCADE;
    DROP TABLE IF EXISTS projects CASCADE;
    ALTER TABLE organizations DROP CONSTRAINT IF EXISTS fk_organizations_owner_same_org;
    DROP TABLE IF EXISTS members CASCADE;
    DROP TABLE IF EXISTS organizations CASCADE;
    """)
