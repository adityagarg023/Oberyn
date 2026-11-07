"""Clerk-authenticated, tenant-scoped dashboard endpoints."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import re
import secrets
from datetime import UTC, datetime, timedelta
from collections.abc import Mapping
from typing import Any, Literal
from uuid import UUID, uuid4

import asyncpg
import httpx
import jwt
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from jwt import PyJWKClient
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])
_jwks: PyJWKClient | None = None


async def current_user(
    authorization: str | None = Header(default=None),
) -> dict[str, str]:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")
    issuer = os.getenv("CLERK_ISSUER", "").rstrip("/")
    if not issuer:
        raise HTTPException(status_code=503, detail="CLERK_ISSUER is not configured")
    global _jwks
    if _jwks is None:
        _jwks = PyJWKClient(issuer + "/.well-known/jwks.json", cache_jwk_set=True, lifespan=300)
    try:
        token = authorization[7:]
        key = await asyncio.to_thread(_jwks.get_signing_key_from_jwt, token)
        claims = jwt.decode(
            token,
            key.key,
            algorithms=["RS256"],
            issuer=issuer,
            options={"require": ["sub", "exp", "iss"]},
        )
    except Exception as exc:
        raise HTTPException(status_code=401, detail="Invalid or expired session") from exc
    return {"id": str(claims["sub"]), "email": str(claims.get("email", ""))}


def get_pool(request: Request) -> asyncpg.Pool:
    return request.app.state.pg_pool


async def membership(db: asyncpg.Pool, org_id: UUID, user: dict[str, str]) -> asyncpg.Record:
    row = await db.fetchrow(
        """SELECT m.id,m.role,o.id AS organization_id FROM members m
           JOIN organizations o ON o.id=m.organization_id
           WHERE m.organization_id=$1 AND m.clerk_user_id=$2""",
        org_id,
        user["id"],
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Organization not found")
    return row


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")[:70]
    if not slug:
        raise HTTPException(status_code=422, detail="Name must contain letters or numbers")
    return slug


def encode_cursor(seen: datetime, issue_id: UUID) -> str:
    raw = f"{seen.isoformat()}|{issue_id}"
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def decode_cursor(value: str) -> tuple[datetime, UUID]:
    try:
        raw = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4)).decode()
        timestamp, issue_id = raw.split("|", 1)
        return datetime.fromisoformat(timestamp), UUID(issue_id)
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail="Invalid pagination cursor") from exc


class OrganizationInput(BaseModel):
    name: str = Field(min_length=2, max_length=160)


class ProjectInput(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    platform: str = Field(default="javascript", max_length=40)


class IssueStatusInput(BaseModel):
    status: Literal["unresolved", "resolved", "ignored"]


class IssueMergeInput(BaseModel):
    target_issue_id: UUID


def require_admin(member: Mapping[str, Any], action: str) -> None:
    if member["role"] not in {"owner", "admin"}:
        raise HTTPException(status_code=403, detail=f"Only organization admins can {action}")


def parse_tag_filter(value: str | None) -> dict[str, str]:
    if not value:
        return {}
    parsed: dict[str, str] = {}
    for pair in value.split(","):
        key, separator, tag_value = pair.partition("=")
        if not separator or not key.strip() or not tag_value.strip():
            raise HTTPException(status_code=422, detail="Tags must use key=value pairs")
        parsed[key.strip()] = tag_value.strip()
    return parsed


@router.get("/organizations")
async def list_organizations(
    user: dict[str, str] = Depends(current_user), db: asyncpg.Pool = Depends(get_pool)
) -> dict[str, Any]:
    rows = await db.fetch(
        """SELECT o.id,o.name,o.slug,m.role FROM organizations o
           JOIN members m ON m.organization_id=o.id
           WHERE m.clerk_user_id=$1 ORDER BY o.created_at""",
        user["id"],
    )
    return {"data": [dict(row) for row in rows]}


@router.post("/organizations", status_code=201)
async def create_organization(
    body: OrganizationInput,
    user: dict[str, str] = Depends(current_user),
    db: asyncpg.Pool = Depends(get_pool),
) -> dict[str, Any]:
    secret_key = os.getenv("CLERK_SECRET_KEY", "")
    if not secret_key:
        raise HTTPException(status_code=503, detail="CLERK_SECRET_KEY is not configured")
    async with httpx.AsyncClient(timeout=5) as client:
        profile = await client.get(
            "https://api.clerk.com/v1/users/" + user["id"],
            headers={"Authorization": "Bearer " + secret_key},
        )
    if profile.status_code != 200:
        raise HTTPException(status_code=502, detail="Could not retrieve Clerk user profile")
    profile_data = profile.json()
    addresses = profile_data.get("email_addresses", [])
    email = next(
        (
            item["email_address"]
            for item in addresses
            if item["id"] == profile_data.get("primary_email_address_id")
        ),
        None,
    )
    if not email:
        raise HTTPException(status_code=422, detail="Add an email address to your Clerk account")
    org_id, member_id = uuid4(), uuid4()
    base, slug, suffix = slugify(body.name), slugify(body.name), 1
    async with db.acquire() as conn:
        async with conn.transaction():
            while await conn.fetchval("SELECT 1 FROM organizations WHERE slug=$1", slug):
                suffix += 1
                slug = f"{base}-{suffix}"
            await conn.execute(
                "INSERT INTO organizations(id,name,slug) VALUES($1,$2,$3)",
                org_id,
                body.name.strip(),
                slug,
            )
            await conn.execute(
                """INSERT INTO members(id,organization_id,clerk_user_id,email,role)
                   VALUES($1,$2,$3,$4,'owner')""",
                member_id,
                org_id,
                user["id"],
                email,
            )
            await conn.execute(
                "UPDATE organizations SET owner_member_id=$1 WHERE id=$2",
                member_id,
                org_id,
            )
    return {"id": str(org_id), "name": body.name.strip(), "slug": slug, "role": "owner"}


@router.get("/organizations/{org_id}/projects")
async def list_projects(
    org_id: UUID,
    user: dict[str, str] = Depends(current_user),
    db: asyncpg.Pool = Depends(get_pool),
) -> dict[str, Any]:
    await membership(db, org_id, user)
    rows = await db.fetch(
        "SELECT id,name,slug,platform,created_at FROM projects WHERE organization_id=$1 ORDER BY "
        "created_at",
        org_id,
    )
    return {"data": [dict(row) for row in rows]}


@router.post("/organizations/{org_id}/projects", status_code=201)
async def create_project(
    org_id: UUID,
    body: ProjectInput,
    user: dict[str, str] = Depends(current_user),
    db: asyncpg.Pool = Depends(get_pool),
) -> dict[str, Any]:
    member = await membership(db, org_id, user)
    if member["role"] not in ("owner", "admin"):
        raise HTTPException(status_code=403, detail="Only organization admins can create projects")
    project_id, key_id = uuid4(), uuid4()
    secret = "ob_live_" + secrets.token_urlsafe(32)
    base, slug, suffix = slugify(body.name), slugify(body.name), 1
    while await db.fetchval(
        "SELECT 1 FROM projects WHERE organization_id=$1 AND slug=$2", org_id, slug
    ):
        suffix += 1
        slug = f"{base}-{suffix}"
    async with db.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                """INSERT INTO projects(id,organization_id,created_by_member_id,name,slug,platform)
                   VALUES($1,$2,$3,$4,$5,$6)""",
                project_id,
                org_id,
                member["id"],
                body.name.strip(),
                slug,
                body.platform,
            )
            await conn.execute(
                """INSERT INTO project_keys(id,project_id,organization_id,key_hash,label)
                   VALUES($1,$2,$3,$4,'default')""",
                key_id,
                project_id,
                org_id,
                hashlib.sha256(secret.encode()).hexdigest(),
            )
    return {
        "project": {
            "id": str(project_id),
            "name": body.name.strip(),
            "slug": slug,
            "platform": body.platform,
        },
        "dsn": os.getenv("OBERYN_INGEST_URL", "http://localhost:8000").rstrip("/")
        + "/"
        + secret
        + "@"
        + str(project_id),
    }


@router.get("/organizations/{org_id}/projects/{project_id}/issues")
async def list_issues(
    org_id: UUID,
    project_id: UUID,
    status: str = Query("all"),
    release: str | None = None,
    environment: str | None = None,
    level: str | None = None,
    tags: str | None = None,
    search: str | None = Query(None, max_length=200),
    cursor: str | None = None,
    limit: int = Query(25, ge=1, le=100),
    user: dict[str, str] = Depends(current_user),
    db: asyncpg.Pool = Depends(get_pool),
) -> dict[str, Any]:
    await membership(db, org_id, user)
    if status not in {"all", "unresolved", "resolved", "ignored", "regressed"}:
        raise HTTPException(status_code=422, detail="Invalid issue status")
    if not await db.fetchval(
        "SELECT 1 FROM projects WHERE id=$1 AND organization_id=$2", project_id, org_id
    ):
        raise HTTPException(status_code=404, detail="Project not found")

    args: list[Any] = [project_id]
    where = ["issues.project_id=$1"]
    if status != "all":
        args.append(status)
        where.append(f"issues.status=${len(args)}")

    event_filters = ["re.project_id=issues.project_id", "re.issue_id=issues.id"]
    if release:
        args.append(release)
        event_filters.append(f"re.payload->>'release'=${len(args)}")
    if environment:
        args.append(environment)
        event_filters.append(f"re.payload->>'environment'=${len(args)}")
    if level:
        args.append(level)
        event_filters.append(f"re.payload->>'level'=${len(args)}")
    tag_filter = parse_tag_filter(tags)
    if tag_filter:
        args.append(json.dumps(tag_filter))
        event_filters.append(f"re.payload->'tags' @> ${len(args)}::jsonb")
    if len(event_filters) > 2:
        where.append("EXISTS (SELECT 1 FROM raw_events re WHERE " + " AND ".join(event_filters) + ")")

    if search:
        args.append("%" + search.strip() + "%")
        search_param = f"${len(args)}"
        where.append(
            "(issues.title ILIKE " + search_param
            + " OR issues.fingerprint ILIKE " + search_param
            + " OR EXISTS (SELECT 1 FROM raw_events rs WHERE rs.project_id=issues.project_id "
            + "AND rs.issue_id=issues.id AND rs.payload::text ILIKE " + search_param + "))"
        )

    if cursor:
        seen, issue_id = decode_cursor(cursor)
        args.extend([seen, issue_id])
        where.append(f"(issues.last_seen,issues.id) < (${len(args)-1},${len(args)})")

    args.append(limit + 1)
    rows = await db.fetch(
        """SELECT issues.id,issues.title,issues.status,issues.first_seen,issues.last_seen,
                  issues.event_count,issues.user_count,issues.first_release,issues.last_release
           FROM issues WHERE """
        + " AND ".join(where)
        + f" ORDER BY issues.last_seen DESC,issues.id DESC LIMIT ${len(args)}",
        *args,
    )
    more = len(rows) > limit
    rows = rows[:limit]
    return {
        "data": [dict(row) for row in rows],
        "next_cursor": (
            encode_cursor(rows[-1]["last_seen"], rows[-1]["id"]) if more and rows else None
        ),
    }


@router.get("/organizations/{org_id}/projects/{project_id}/issues/{issue_id}")
async def issue_detail(
    org_id: UUID,
    project_id: UUID,
    issue_id: UUID,
    user: dict[str, str] = Depends(current_user),
    db: asyncpg.Pool = Depends(get_pool),
) -> dict[str, Any]:
    await membership(db, org_id, user)
    issue = await db.fetchrow(
        """SELECT id,title,status,first_seen,last_seen,event_count,user_count,fingerprint,
                  first_release,last_release,resolved_in_release,resolved_at
           FROM issues WHERE id=$1 AND project_id=$2 AND organization_id=$3""",
        issue_id,
        project_id,
        org_id,
    )
    if issue is None:
        raise HTTPException(status_code=404, detail="Issue not found")
    event = await db.fetchrow(
        """SELECT event_id,received_at,payload FROM raw_events
           WHERE project_id=$1 AND issue_id=$2
           ORDER BY received_at DESC LIMIT 1""",
        project_id,
        issue_id,
    )
    return {
        "issue": dict(issue),
        "latest_event": dict(event) if event else None,
        "breadcrumbs": (event["payload"].get("breadcrumbs", []) if event else []),
    }


@router.patch("/organizations/{org_id}/projects/{project_id}/issues/{issue_id}")
async def update_issue_status(
    org_id: UUID,
    project_id: UUID,
    issue_id: UUID,
    body: IssueStatusInput,
    user: dict[str, str] = Depends(current_user),
    db: asyncpg.Pool = Depends(get_pool),
) -> dict[str, Any]:
    member = await membership(db, org_id, user)
    require_admin(member, "change issue status")
    row = await db.fetchrow(
        """UPDATE issues
           SET status=$4,
               resolved_at=CASE WHEN $4='resolved' THEN now() ELSE NULL END,
               resolved_in_release=CASE WHEN $4='resolved' THEN last_release ELSE NULL END
           WHERE id=$1 AND project_id=$2 AND organization_id=$3
           RETURNING id,status,resolved_at,resolved_in_release""",
        issue_id,
        project_id,
        org_id,
        body.status,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Issue not found")
    return {"issue": dict(row)}


@router.post("/organizations/{org_id}/projects/{project_id}/issues/{issue_id}/merge")
async def merge_issues(
    org_id: UUID,
    project_id: UUID,
    issue_id: UUID,
    body: IssueMergeInput,
    user: dict[str, str] = Depends(current_user),
    db: asyncpg.Pool = Depends(get_pool),
) -> dict[str, Any]:
    member = await membership(db, org_id, user)
    require_admin(member, "merge issues")
    if body.target_issue_id == issue_id:
        raise HTTPException(status_code=422, detail="An issue cannot be merged into itself")

    async with db.acquire() as conn:
        async with conn.transaction():
            source = await conn.fetchrow(
                "SELECT * FROM issues WHERE id=$1 AND project_id=$2 AND organization_id=$3 FOR UPDATE",
                issue_id, project_id, org_id,
            )
            target = await conn.fetchrow(
                "SELECT * FROM issues WHERE id=$1 AND project_id=$2 AND organization_id=$3 FOR UPDATE",
                body.target_issue_id, project_id, org_id,
            )
            if source is None or target is None:
                raise HTTPException(status_code=404, detail="Issue not found in this project")
            await conn.execute(
                "UPDATE raw_events SET issue_id=$1 WHERE project_id=$2 AND issue_id=$3",
                target["id"], project_id, source["id"],
            )
            await conn.execute(
                "UPDATE events SET issue_id=$1 WHERE project_id=$2 AND issue_id=$3",
                target["id"], project_id, source["id"],
            )
            await conn.execute(
                """INSERT INTO issue_users(issue_id,project_id,user_id)
                   SELECT $1,project_id,user_id FROM issue_users WHERE issue_id=$2
                   ON CONFLICT(issue_id,user_id) DO NOTHING""",
                target["id"], source["id"],
            )
            merged = await conn.fetchrow(
                """UPDATE issues AS target SET
                       first_seen=LEAST(target.first_seen,source.first_seen),
                       last_seen=GREATEST(target.last_seen,source.last_seen),
                       event_count=target.event_count+source.event_count,
                       user_count=(SELECT count(*) FROM issue_users WHERE issue_id=target.id),
                       first_release=CASE WHEN source.first_seen<target.first_seen THEN source.first_release ELSE target.first_release END,
                       last_release=CASE WHEN source.last_seen>target.last_seen
                                         THEN source.last_release ELSE target.last_release END,
                       status=CASE WHEN target.status='resolved'
                                        AND source.status IN ('unresolved','regressed')
                                   THEN source.status ELSE target.status END
                   FROM issues AS source
                   WHERE target.id=$1 AND source.id=$2
                   RETURNING target.id,target.title,target.status,target.event_count,target.user_count,
                             target.first_seen,target.last_seen,target.first_release,target.last_release""",
                target["id"], source["id"],
            )
            await conn.execute("DELETE FROM issues WHERE id=$1 AND project_id=$2", source["id"], project_id)
    return {"issue": dict(merged), "merged_issue_id": str(issue_id)}


@router.get("/organizations/{org_id}/projects/{project_id}/events/counts")
async def event_counts(
    org_id: UUID,
    project_id: UUID,
    days: int = Query(14, ge=1, le=90),
    user: dict[str, str] = Depends(current_user),
    db: asyncpg.Pool = Depends(get_pool),
) -> dict[str, Any]:
    await membership(db, org_id, user)
    if not await db.fetchval(
        "SELECT 1 FROM projects WHERE id=$1 AND organization_id=$2", project_id, org_id
    ):
        raise HTTPException(status_code=404, detail="Project not found")
    rows = await db.fetch(
        """SELECT date_trunc('day',received_at) AS day,count(*)::bigint AS count
           FROM raw_events WHERE project_id=$1 AND received_at >= $2
           GROUP BY 1 ORDER BY 1""",
        project_id,
        datetime.now(UTC) - timedelta(days=days - 1),
    )
    return {"data": [{"day": row["day"].date().isoformat(), "count": row["count"]} for row in rows]}
