from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Mapping
import json
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol
from uuid import UUID

import asyncpg
import httpx

logger = logging.getLogger("oberyn.alerts")


class Notifier(Protocol):
    """A provider that delivers one alert payload to one destination."""

    async def send(self, payload: Mapping[str, Any], destination: str) -> None: ...


class ResendNotifier:
    """Deliver alert email through the Resend HTTP API."""

    def __init__(
        self,
        api_key: str | None = None,
        *,
        sender: str | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.api_key = api_key if api_key is not None else os.getenv("RESEND_API_KEY", "")
        self.sender = sender if sender is not None else os.getenv(
            "ALERT_EMAIL_FROM", "Oberyn Alerts <alerts@resend.dev>"
        )
        self.client = client

    async def send(self, payload: Mapping[str, Any], destination: str) -> None:
        if not self.api_key:
            raise RuntimeError("RESEND_API_KEY is not configured")
        body = {
            "from": self.sender,
            "to": [destination],
            "subject": str(payload.get("title", "Oberyn alert")),
            "text": str(payload.get("message", "An alert was triggered.")),
        }
        if self.client is not None:
            response = await self.client.post(
                "https://api.resend.com/emails",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json=body,
            )
            response.raise_for_status()
            return
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(
                "https://api.resend.com/emails",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json=body,
            )
            response.raise_for_status()


class SlackWebhookNotifier:
    """Deliver alert messages to an incoming Slack webhook."""

    def __init__(self, *, client: httpx.AsyncClient | None = None) -> None:
        self.client = client

    async def send(self, payload: Mapping[str, Any], destination: str) -> None:
        body = {
            "text": f"*{payload.get('title', 'Oberyn alert')}*\n"
            f"{payload.get('message', 'An alert was triggered.')}"
        }
        if self.client is not None:
            response = await self.client.post(destination, json=body)
            response.raise_for_status()
            return
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(destination, json=body)
            response.raise_for_status()


def _destinations(config: Mapping[str, Any]) -> list[tuple[Notifier, str]]:
    destinations: list[tuple[Notifier, str]] = []
    channels = config.get("channels", [])
    if not isinstance(channels, list):
        return destinations
    for channel in channels:
        if channel == "email":
            recipients = config.get("email_to", [])
            if isinstance(recipients, str):
                recipients = [recipients]
            if isinstance(recipients, list):
                destinations.extend((ResendNotifier(), str(value)) for value in recipients if value)
        elif channel == "slack":
            webhook = config.get("slack_webhook_url")
            if isinstance(webhook, str) and webhook.startswith("https://"):
                destinations.append((SlackWebhookNotifier(), webhook))
    return destinations


async def evaluate_rules(
    connection: asyncpg.Connection,
    *,
    project_id: UUID,
    issue_id: UUID,
    occurred_at: datetime,
    is_new_issue: bool,
    is_regression: bool,
) -> int:
    """Insert durable outbox rows for matching rules in the event transaction."""
    issue = await connection.fetchrow(
        "SELECT title,event_count,user_count,status FROM issues "
        "WHERE id=$1 AND project_id=$2",
        issue_id,
        project_id,
    )
    if issue is None:
        return 0

    rules = await connection.fetch(
        "SELECT id,name,threshold,window_seconds,config FROM alert_rules "
        "WHERE project_id=$1 AND enabled=true",
        project_id,
    )
    queued = 0
    for rule in rules:
        config = rule["config"] or {}
        rule_type = config.get("type")
        if rule_type not in {"new_issue", "regression", "event_count", "user_count"}:
            continue

        now = datetime.now(UTC)
        since = now - timedelta(seconds=rule["window_seconds"])
        if rule_type == "new_issue" and not is_new_issue:
            continue
        if rule_type == "regression" and not is_regression:
            continue
        if rule_type == "event_count":
            count = await connection.fetchval(
                "SELECT count(*) FROM raw_events WHERE project_id=$1 AND issue_id=$2 "
                "AND received_at >= $3",
                project_id,
                issue_id,
                since,
            )
            if count < rule["threshold"]:
                continue
        if rule_type == "user_count":
            count = await connection.fetchval(
                "SELECT count(DISTINCT user_id) FROM issue_users WHERE issue_id=$1 "
                "AND created_at >= $2",
                issue_id,
                since,
            )
            if count < rule["threshold"]:
                continue

        cooldown = max(1, int(config.get("cooldown_seconds", rule["window_seconds"])))
        bucket = int(now.timestamp()) // cooldown
        dedupe_key = f"{rule['id']}:{issue_id}:{bucket}"
        message = (
            f"{issue['title']} | events: {issue['event_count']} | "
            f"users: {issue['user_count']} | status: {issue['status']}"
        )
        payload = {
            "rule_name": rule["name"],
            "rule_type": rule_type,
            "issue_id": str(issue_id),
            "title": f"Oberyn: {rule['name']}",
            "message": message,
            "channels": config.get("channels", []),
            "email_to": config.get("email_to", []),
            "slack_webhook_url": config.get("slack_webhook_url"),
        }
        result = await connection.fetchval(
            """
            INSERT INTO alert_history (
                id, alert_rule_id, project_id, issue_id, triggered_at,
                payload, status, attempts, next_attempt_at, dedupe_key
            )
            VALUES (gen_random_uuid(), $1, $2, $3, $4, $5::jsonb,
                    'pending', 0, now(), $6)
            ON CONFLICT (dedupe_key) DO NOTHING
            RETURNING id
            """,
            rule["id"],
            project_id,
            issue_id,
            occurred_at,
            json.dumps(payload),
            dedupe_key,
        )
        queued += int(result is not None)
    return queued


async def deliver_pending(
    pool: asyncpg.Pool,
    *,
    batch_size: int = 20,
    max_attempts: int = 8,
    retry_base_seconds: int = 5,
    notifier_factory: Any = _destinations,
) -> int:
    """Claim and deliver a batch; failures are persisted for exponential retry."""
    async with pool.acquire() as connection:
        async with connection.transaction():
            rows = await connection.fetch(
                """
                SELECT id,payload,attempts FROM alert_history
                WHERE (status='pending' AND next_attempt_at <= now())
                   OR (status='sending' AND updated_at < now() - interval '5 minutes')
                ORDER BY triggered_at
                FOR UPDATE SKIP LOCKED
                LIMIT $1
                """,
                batch_size,
            )
            for row in rows:
                await connection.execute(
                    "UPDATE alert_history SET status='sending',updated_at=now() WHERE id=$1",
                    row["id"],
                )

    delivered = 0
    for row in rows:
        payload = row["payload"] or {}
        destinations = notifier_factory(payload)
        try:
            if not destinations:
                raise RuntimeError("Alert has no valid notification destinations")
            for notifier, destination in destinations:
                await notifier.send(payload, destination)
        except Exception as error:
            attempts = int(row["attempts"]) + 1
            terminal = attempts >= max_attempts
            delay = retry_base_seconds * (2 ** min(attempts - 1, 10))
            async with pool.acquire() as connection:
                await connection.execute(
                    """
                    UPDATE alert_history
                    SET status=$2, attempts=$3, last_error=$4,
                        next_attempt_at=now() + ($5 * interval '1 second'),
                        updated_at=now()
                    WHERE id=$1
                    """,
                    row["id"],
                    "failed" if terminal else "pending",
                    attempts,
                    str(error)[:2000],
                    delay,
                )
            logger.warning("alert delivery failed id=%s attempts=%d", row["id"], attempts)
            continue

        async with pool.acquire() as connection:
            await connection.execute(
                """
                UPDATE alert_history
                SET status='delivered', attempts=$2, delivered_at=now(),
                    last_error=NULL, updated_at=now()
                WHERE id=$1
                """,
                row["id"],
                int(row["attempts"]) + 1,
            )
        delivered += 1
    return delivered


async def delivery_loop(
    pool: asyncpg.Pool,
    stop: asyncio.Event,
    *,
    poll_seconds: float = 1.0,
) -> None:
    """Background loop; provider failures never escape into event ingestion."""
    while not stop.is_set():
        try:
            await deliver_pending(pool)
        except Exception:
            logger.exception("alert delivery batch failed")
        try:
            await asyncio.wait_for(stop.wait(), timeout=poll_seconds)
        except TimeoutError:
            pass
