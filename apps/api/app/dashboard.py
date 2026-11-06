"""Clerk-authenticated, tenant-scoped dashboard endpoints."""
from __future__ import annotations
import asyncio, base64, hashlib, os, re, secrets
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID, uuid4
import asyncpg, httpx, jwt
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from jwt import PyJWKClient
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])
_jwks: PyJWKClient | None = None

async def current_user(authorization: str | None = Header(default=None)) -> dict[str, str]:
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
        claims = jwt.decode(token, key.key, algorithms=["RS256"], issuer=issuer,
                            options={"require": ["sub", "exp", "iss"]})
    except Exception as exc:
        raise HTTPException(status_code=401, detail="Invalid or expired session") from exc
    return {"id": str(claims["sub"]), "email": str(claims.get("email", ""))}

def get_pool(request: Request) -> asyncpg.Pool:
    return request.app.state.pg_pool

async def membership(db: asyncpg.Pool, org_id: UUID, user: dict[str, str]) -> asyncpg.Record:
    row = await db.fetchrow(
        """SELECT m.id,m.role,o.id AS organization_id FROM members m
           JOIN organizations o ON o.id=m.organization_id
           WHERE m.organization_id=$1 AND m.clerk_user_id=$2""", org_id, user["id"])
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

@router.get("/organizations")
async def list_organizations(user: dict[str, str] = Depends(current_user),
                             db: asyncpg.Pool = Depends(get_pool)) -> dict[str, Any]:
    rows = await db.fetch(
        """SELECT o.id,o.name,o.slug,m.role FROM organizations o
           JOIN members m ON m.organization_id=o.id
           WHERE m.clerk_user_id=$1 ORDER BY o.created_at""", user["id"])
    return {"data": [dict(row) for row in rows]}

@router.post("/organizations", status_code=201)
async def create_organization(body: OrganizationInput, user: dict[str, str] = Depends(current_user),
                              db: asyncpg.Pool = Depends(get_pool)) -> dict[str, Any]:
    secret_key = os.getenv("CLERK_SECRET_KEY", "")
    if not secret_key:
        raise HTTPException(status_code=503, detail="CLERK_SECRET_KEY is not configured")
    async with httpx.AsyncClient(timeout=5) as client:
        profile = await client.get("https://api.clerk.com/v1/users/" + user["id"],
                                   headers={"Authorization": "Bearer " + secret_key})
    if profile.status_code != 200:
        raise HTTPException(status_code=502, detail="Could not retrieve Clerk user profile")
    profile_data = profile.json()
    addresses = profile_data.get("email_addresses", [])
    email = next((item["email_address"] for item in addresses
                  if item["id"] == profile_data.get("primary_email_address_id")), None)
    if not email:
        raise HTTPException(status_code=422, detail="Add an email address to your Clerk account")
    org_id, member_id = uuid4(), uuid4()
    base, slug, suffix = slugify(body.name), slugify(body.name), 1
    async with db.acquire() as conn:
        async with conn.transaction():
            while await conn.fetchval("SELECT 1 FROM organizations WHERE slug=$1", slug):
                suffix += 1
                slug = f"{base}-{suffix}"
            await conn.execute("INSERT INTO organizations(id,name,slug) VALUES($1,$2,$3)",
                               org_id, body.name.strip(), slug)
            await conn.execute(
                """INSERT INTO members(id,organization_id,clerk_user_id,email,role)
                   VALUES($1,$2,$3,$4,'owner')""", member_id, org_id, user["id"], email)
            await conn.execute("UPDATE organizations SET owner_member_id=$1 WHERE id=$2", member_id, org_id)
    return {"id": str(org_id), "name": body.name.strip(), "slug": slug, "role": "owner"}

@router.get("/organizations/{org_id}/projects")
async def list_projects(org_id: UUID, user: dict[str, str] = Depends(current_user),
                        db: asyncpg.Pool = Depends(get_pool)) -> dict[str, Any]:
    await membership(db, org_id, user)
    rows = await db.fetch(
        "SELECT id,name,slug,platform,created_at FROM projects WHERE organization_id=$1 ORDER BY created_at",
        org_id)
    return {"data": [dict(row) for row in rows]}

@router.post("/organizations/{org_id}/projects", status_code=201)
async def create_project(org_id: UUID, body: ProjectInput, user: dict[str, str] = Depends(current_user),
                         db: asyncpg.Pool = Depends(get_pool)) -> dict[str, Any]:
    member = await membership(db, org_id, user)
    if member["role"] not in ("owner", "admin"):
        raise HTTPException(status_code=403, detail="Only organization admins can create projects")
    project_id, key_id = uuid4(), uuid4()
    secret = "ob_live_" + secrets.token_urlsafe(32)
    base, slug, suffix = slugify(body.name), slugify(body.name), 1
    while await db.fetchval("SELECT 1 FROM projects WHERE organization_id=$1 AND slug=$2", org_id, slug):
        suffix += 1
        slug = f"{base}-{suffix}"
    async with db.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                """INSERT INTO projects(id,organization_id,created_by_member_id,name,slug,platform)
                   VALUES($1,$2,$3,$4,$5,$6)""",
                project_id, org_id, member["id"], body.name.strip(), slug, body.platform)
            await conn.execute(
                """INSERT INTO project_keys(id,project_id,organization_id,key_hash,label)
                   VALUES($1,$2,$3,$4,'default')""",
                key_id, project_id, org_id, hashlib.sha256(secret.encode()).hexdigest())
    return {"project": {"id": str(project_id), "name": body.name.strip(), "slug": slug,
                        "platform": body.platform},
            "dsn": os.getenv("OBERYN_INGEST_URL", "http://localhost:8000").rstrip("/") + "/" + secret + "@" + str(project_id)}

@router.get("/organizations/{org_id}/projects/{project_id}/issues")
async def list_issues(org_id: UUID, project_id: UUID, status: str = Query("all"),
                      cursor: str | None = None, limit: int = Query(25, ge=1, le=100),
                      user: dict[str, str] = Depends(current_user),
                      db: asyncpg.Pool = Depends(get_pool)) -> dict[str, Any]:
    await membership(db, org_id, user)
    if status not in {"all", "unresolved", "resolved", "ignored", "regressed"}:
        raise HTTPException(status_code=422, detail="Invalid issue status")
    if not await db.fetchval("SELECT 1 FROM projects WHERE id=$1 AND organization_id=$2", project_id, org_id):
        raise HTTPException(status_code=404, detail="Project not found")
    args: list[Any] = [project_id]
    where = "project_id=$1"
    if status != "all":
        args.append(status)
        where += f" AND status=$" + str(len(args))
    if cursor:
        seen, issue_id = decode_cursor(cursor)
        args.extend([seen, issue_id])
        where += f" AND (last_seen,id) < ($" + str(len(args)-1) + ",$" + str(len(args)) + ")"
    args.append(limit + 1)
    rows = await db.fetch(
        """SELECT id,title,status,first_seen,last_seen,event_count,user_count FROM issues
           WHERE """ + where + " ORDER BY last_seen DESC,id DESC LIMIT $" + str(len(args)), *args)
    more = len(rows) > limit
    rows = rows[:limit]
    return {"data": [dict(row) for row in rows],
            "next_cursor": encode_cursor(rows[-1]["last_seen"], rows[-1]["id"]) if more and rows else None}

@router.get("/organizations/{org_id}/projects/{project_id}/issues/{issue_id}")
async def issue_detail(org_id: UUID, project_id: UUID, issue_id: UUID,
                       user: dict[str, str] = Depends(current_user),
                       db: asyncpg.Pool = Depends(get_pool)) -> dict[str, Any]:
    await membership(db, org_id, user)
    issue = await db.fetchrow(
        """SELECT id,title,status,first_seen,last_seen,event_count,user_count,fingerprint
           FROM issues WHERE id=$1 AND project_id=$2 AND organization_id=$3""",
        issue_id, project_id, org_id)
    if issue is None:
        raise HTTPException(status_code=404, detail="Issue not found")
    event = await db.fetchrow(
        """SELECT event_id,received_at,payload FROM raw_events
           WHERE project_id=$1 AND payload->'exception'->>'type'=split_part($2, ': ', 1)
           ORDER BY received_at DESC LIMIT 1""", project_id, issue["title"])
    return {"issue": dict(issue), "latest_event": dict(event) if event else None}

@router.get("/organizations/{org_id}/projects/{project_id}/events/counts")
async def event_counts(org_id: UUID, project_id: UUID, days: int = Query(14, ge=1, le=90),
                       user: dict[str, str] = Depends(current_user),
                       db: asyncpg.Pool = Depends(get_pool)) -> dict[str, Any]:
    await membership(db, org_id, user)
    if not await db.fetchval("SELECT 1 FROM projects WHERE id=$1 AND organization_id=$2", project_id, org_id):
        raise HTTPException(status_code=404, detail="Project not found")
    rows = await db.fetch(
        """SELECT date_trunc('day',received_at) AS day,count(*)::bigint AS count
           FROM raw_events WHERE project_id=$1 AND received_at >= $2
           GROUP BY 1 ORDER BY 1""",
        project_id, datetime.now(timezone.utc) - timedelta(days=days - 1))
    return {"data": [{"day": row["day"].date().isoformat(), "count": row["count"]} for row in rows]}
