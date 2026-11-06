from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger, Boolean, CheckConstraint, DateTime, ForeignKeyConstraint,
    Index, Integer, PrimaryKeyConstraint, String, Text, UniqueConstraint, func, text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Organization(Base):
    __tablename__ = "organizations"
    __table_args__ = (
        UniqueConstraint("slug", name="uq_organizations_slug"),
        ForeignKeyConstraint(
            ["owner_member_id", "id"], ["members.id", "members.organization_id"],
            name="fk_organizations_owner_same_org", deferrable=True, initially="DEFERRED",
            use_alter=True,
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    slug: Mapped[str] = mapped_column(String(80), nullable=False)
    owner_member_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Member(Base):
    __tablename__ = "members"
    __table_args__ = (
        UniqueConstraint("organization_id", "email", name="uq_members_org_email"),
        UniqueConstraint("id", "organization_id", name="uq_members_id_org"),
        CheckConstraint("role IN ('owner', 'admin', 'member')", name="ck_members_role"),
        ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], ondelete="CASCADE",
            name="fk_members_organization",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    clerk_user_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    name: Mapped[str | None] = mapped_column(String(160))
    role: Mapped[str] = mapped_column(String(16), nullable=False, server_default="member")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Project(Base):
    __tablename__ = "projects"
    __table_args__ = (
        UniqueConstraint("organization_id", "slug", name="uq_projects_org_slug"),
        UniqueConstraint("id", "organization_id", name="uq_projects_id_org"),
        ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE", name="fk_projects_organization"),
        ForeignKeyConstraint(
            ["created_by_member_id", "organization_id"], ["members.id", "members.organization_id"],
            ondelete="RESTRICT", name="fk_projects_creator_same_org",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    created_by_member_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    slug: Mapped[str] = mapped_column(String(80), nullable=False)
    platform: Mapped[str | None] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ProjectKey(Base):
    __tablename__ = "project_keys"
    __table_args__ = (
        ForeignKeyConstraint(["project_id", "organization_id"], ["projects.id", "projects.organization_id"], ondelete="CASCADE", name="fk_project_keys_project_tenant"),
        UniqueConstraint("key_hash", name="uq_project_keys_hash"),
        Index("ix_project_keys_project", "project_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    key_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    label: Mapped[str] = mapped_column(String(100), nullable=False, server_default="default")
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Release(Base):
    __tablename__ = "releases"
    __table_args__ = (
        UniqueConstraint("project_id", "version", name="uq_releases_project_version"),
        UniqueConstraint("id", "project_id", name="uq_releases_id_project"),
        ForeignKeyConstraint(["project_id", "organization_id"], ["projects.id", "projects.organization_id"], ondelete="CASCADE", name="fk_releases_project_tenant"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    version: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SourceMap(Base):
    __tablename__ = "source_maps"
    __table_args__ = (
        ForeignKeyConstraint(["project_id", "organization_id"], ["projects.id", "projects.organization_id"], ondelete="CASCADE", name="fk_source_maps_project_tenant"),
        ForeignKeyConstraint(["release_id", "project_id"], ["releases.id", "releases.project_id"], ondelete="CASCADE", name="fk_source_maps_release_project"),
        UniqueConstraint("project_id", "release_id", "file_path", name="uq_source_maps_file"),
        Index("ix_source_maps_project_release", "project_id", "release_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    release_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    file_path: Mapped[str] = mapped_column(Text, nullable=False)
    artifact_url: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Issue(Base):
    __tablename__ = "issues"
    __table_args__ = (
        UniqueConstraint("project_id", "fingerprint", name="uq_issues_project_fingerprint"),
        UniqueConstraint("id", "project_id", name="uq_issues_id_project"),
        CheckConstraint("status IN ('unresolved', 'resolved', 'ignored', 'regressed')", name="ck_issues_status"),
        CheckConstraint("event_count >= 0", name="ck_issues_event_count_nonnegative"),
        CheckConstraint("user_count >= 0", name="ck_issues_user_count_nonnegative"),
        ForeignKeyConstraint(["project_id", "organization_id"], ["projects.id", "projects.organization_id"], ondelete="CASCADE", name="fk_issues_project_tenant"),
        Index("ix_issues_project_status_last_seen", "project_id", "status", text("last_seen DESC")),
        Index("ix_issues_organization", "organization_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(128), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="unresolved")
    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    event_count: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    user_count: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class IssueUser(Base):
    __tablename__ = "issue_users"
    __table_args__ = (
        PrimaryKeyConstraint("issue_id", "user_id", name="pk_issue_users"),
        ForeignKeyConstraint(
            ["issue_id", "project_id"], ["issues.id", "issues.project_id"],
            ondelete="CASCADE", name="fk_issue_users_issue_project",
        ),
        Index("ix_issue_users_project", "project_id"),
    )

    issue_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    user_id: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Event(Base):
    __tablename__ = "events"
    __table_args__ = (
        ForeignKeyConstraint(["issue_id", "project_id"], ["issues.id", "issues.project_id"], ondelete="CASCADE", name="fk_events_issue_project"),
        ForeignKeyConstraint(["project_id", "organization_id"], ["projects.id", "projects.organization_id"], ondelete="CASCADE", name="fk_events_project_tenant"),
        CheckConstraint("jsonb_typeof(payload) = 'object'", name="ck_events_payload_object"),
        Index("ix_events_issue_received_at", "issue_id", text("received_at DESC")),
        Index("ix_events_project_received_at", "project_id", text("received_at DESC")),
        Index("ix_events_organization_received_at", "organization_id", text("received_at DESC")),
        {"postgresql_partition_by": "RANGE (received_at)"},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    issue_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    release: Mapped[str | None] = mapped_column(String(200))
    environment: Mapped[str] = mapped_column(String(64), nullable=False, server_default="production")
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RawEvent(Base):
    __tablename__ = "raw_events"
    __table_args__ = (
        PrimaryKeyConstraint("project_id", "event_id", name="pk_raw_events"),
        ForeignKeyConstraint(
            ["project_id", "organization_id"],
            ["projects.id", "projects.organization_id"],
            ondelete="CASCADE",
            name="fk_raw_events_project_tenant",
        ),
        CheckConstraint(
            "jsonb_typeof(payload) = 'object'",
            name="ck_raw_events_payload_object",
        ),
        ForeignKeyConstraint(
            ["issue_id", "project_id"], ["issues.id", "issues.project_id"],
            ondelete="CASCADE", name="fk_raw_events_issue_project",
        ),
        Index("ix_raw_events_received_at", "received_at"),
        Index("ix_raw_events_issue_received_at", "issue_id", text("received_at DESC")),
    )

    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    event_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    issue_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)


class AlertRule(Base):
    __tablename__ = "alert_rules"
    __table_args__ = (
        ForeignKeyConstraint(["project_id", "organization_id"], ["projects.id", "projects.organization_id"], ondelete="CASCADE", name="fk_alert_rules_project_tenant"),
        CheckConstraint("threshold > 0", name="ck_alert_rules_threshold_positive"),
        CheckConstraint("window_seconds > 0", name="ck_alert_rules_window_positive"),
        UniqueConstraint("id", "project_id", name="uq_alert_rules_id_project"),
        Index("ix_alert_rules_project_enabled", "project_id", "enabled"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    threshold: Mapped[int] = mapped_column(Integer, nullable=False)
    window_seconds: Mapped[int] = mapped_column(Integer, nullable=False, server_default="300")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AlertHistory(Base):
    __tablename__ = "alert_history"
    __table_args__ = (
        ForeignKeyConstraint(["alert_rule_id", "project_id"], ["alert_rules.id", "alert_rules.project_id"], ondelete="CASCADE", name="fk_alert_history_rule_project"),
        Index("ix_alert_history_project_created", "project_id", text("created_at DESC")),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    alert_rule_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    triggered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
