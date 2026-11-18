from __future__ import annotations

import json
import re
from typing import Any, Literal
from urllib.parse import urlsplit
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator

from app.dashboard import current_user, get_pool, membership, require_admin

router = APIRouter(
    prefix="/api/dashboard/organizations/{org_id}/projects/{project_id}/alert-rules",
    tags=["alerts"],
)


class AlertRuleInput(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    type: Literal["new_issue", "regression", "event_count", "user_count"]
    threshold: int = Field(default=1, gt=0, le=1_000_000)
    window_seconds: int = Field(default=300, gt=0, le=604800)
    cooldown_seconds: int = Field(default=300, gt=0, le=604800)
    enabled: bool = True
    channels: list[Literal["email", "slack"]] = Field(min_length=1, max_length=2)
    email_to: list[str] = Field(default_factory=list, max_length=20)
    slack_webhook_url: str | None = None

    @field_validator("email_to")
    @classmethod
    def validate_emails(cls, values: list[str]) -> list[str]:
        pattern = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
        normalized = sorted({value.strip().lower() for value in values})
        if any(not pattern.fullmatch(value) for value in normalized):
            raise ValueError("email_to must contain valid email addresses")
        return normalized

    @field_validator("slack_webhook_url")
    @classmethod
    def validate_slack_url(cls, value: str | None) -> str | None:
        if value is not None:
            parsed = urlsplit(value)
            if (
                parsed.scheme != "https"
                or parsed.hostname != "hooks.slack.com"
                or not parsed.path.startswith("/services/")
                or parsed.username
                or parsed.password
            ):
                raise ValueError("slack_webhook_url must be a Slack incoming webhook URL")
        return value

    def provider_config(self) -> dict[str, Any]:
        if "email" in self.channels and not self.email_to:
            raise ValueError("email channel requires at least one email_to recipient")
        if "slack" in self.channels and not self.slack_webhook_url:
            raise ValueError("slack channel requires slack_webhook_url")
        return {
            "type": self.type,
            "cooldown_seconds": self.cooldown_seconds,
            "channels": self.channels,
            "email_to": self.email_to,
            "slack_webhook_url": self.slack_webhook_url,
        }


def _public_rule(row: asyncpg.Record) -> dict[str, Any]:
    result = dict(row)
    raw_config = result.get("config") or {}
    config = json.loads(raw_config) if isinstance(raw_config, str) else dict(raw_config)
    config["slack_webhook_configured"] = bool(config.pop("slack_webhook_url", None))
    result["config"] = config
    return result


async def _authorize(
    org_id: UUID,
    project_id: UUID,
    user: dict[str, str],
    db: asyncpg.Pool,
) -> asyncpg.Record:
    member = await membership(db, org_id, user)
    require_admin(member, "manage alert rules")
    project = await db.fetchrow(
        "SELECT id FROM projects WHERE id=$1 AND organization_id=$2",
        project_id,
        org_id,
    )
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return member


@router.get("")
async def list_alert_rules(
    org_id: UUID,
    project_id: UUID,
    user: dict[str, str] = Depends(current_user),
    db: asyncpg.Pool = Depends(get_pool),
) -> dict[str, Any]:
    await _authorize(org_id, project_id, user, db)
    rows = await db.fetch(
        """
        SELECT id,name,threshold,window_seconds,enabled,config,created_at
        FROM alert_rules WHERE project_id=$1 ORDER BY created_at DESC
        """,
        project_id,
    )
    return {"data": [_public_rule(row) for row in rows]}


@router.post("", status_code=201)
async def create_alert_rule(
    org_id: UUID,
    project_id: UUID,
    body: AlertRuleInput,
    user: dict[str, str] = Depends(current_user),
    db: asyncpg.Pool = Depends(get_pool),
) -> dict[str, Any]:
    member = await _authorize(org_id, project_id, user, db)
    try:
        config = body.provider_config()
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    row = await db.fetchrow(
        """
        INSERT INTO alert_rules (
            id,project_id,organization_id,name,threshold,window_seconds,enabled,config
        )
        VALUES (gen_random_uuid(),$1,$2,$3,$4,$5,$6,$7::jsonb)
        RETURNING id,name,threshold,window_seconds,enabled,config,created_at
        """,
        project_id,
        member["organization_id"],
        body.name.strip(),
        body.threshold,
        body.window_seconds,
        body.enabled,
        json.dumps(config),
    )
    return {"rule": _public_rule(row)}


@router.put("/{rule_id}")
async def update_alert_rule(
    org_id: UUID,
    project_id: UUID,
    rule_id: UUID,
    body: AlertRuleInput,
    user: dict[str, str] = Depends(current_user),
    db: asyncpg.Pool = Depends(get_pool),
) -> dict[str, Any]:
    await _authorize(org_id, project_id, user, db)
    try:
        config = body.provider_config()
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    row = await db.fetchrow(
        """
        UPDATE alert_rules
        SET name=$4,threshold=$5,window_seconds=$6,enabled=$7,config=$8::jsonb
        WHERE id=$1 AND project_id=$2
          AND EXISTS (SELECT 1 FROM projects WHERE id=$2 AND organization_id=$3)
        RETURNING id,name,threshold,window_seconds,enabled,config,created_at
        """,
        rule_id,
        project_id,
        org_id,
        body.name.strip(),
        body.threshold,
        body.window_seconds,
        body.enabled,
        json.dumps(config),
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Alert rule not found")
    return {"rule": dict(row)}


@router.delete("/{rule_id}", status_code=204)
async def delete_alert_rule(
    org_id: UUID,
    project_id: UUID,
    rule_id: UUID,
    user: dict[str, str] = Depends(current_user),
    db: asyncpg.Pool = Depends(get_pool),
) -> None:
    await _authorize(org_id, project_id, user, db)
    result = await db.execute(
        "DELETE FROM alert_rules WHERE id=$1 AND project_id=$2",
        rule_id,
        project_id,
    )
    if result == "DELETE 0":
        raise HTTPException(status_code=404, detail="Alert rule not found")


@router.get("/history")
async def list_alert_history(
    org_id: UUID,
    project_id: UUID,
    user: dict[str, str] = Depends(current_user),
    db: asyncpg.Pool = Depends(get_pool),
) -> dict[str, Any]:
    await _authorize(org_id, project_id, user, db)
    rows = await db.fetch(
        """
        SELECT id,alert_rule_id,issue_id,triggered_at,status,attempts,
               next_attempt_at,delivered_at,last_error,created_at
        FROM alert_history
        WHERE project_id=$1
        ORDER BY triggered_at DESC
        LIMIT 100
        """,
        project_id,
    )
    return {"data": [dict(row) for row in rows]}
