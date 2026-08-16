"""Content-addressed multi-file microsite deployments.

The existing ``pages`` feature deliberately remains a small, single-file hosting
surface.  This module adds a separate control plane for immutable multi-file
deployments while sharing the existing users and API-token authentication.
"""

from __future__ import annotations

import hashlib
import hmac
import mimetypes
import os
import re
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Callable
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, Field


SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")
CONTENT_TYPE_RE = re.compile(r"^[A-Za-z0-9!#$&^_.+-]+/[A-Za-z0-9!#$&^_.+-]+$")

DATA_ROOT = Path(
    os.environ.get(
        "MICROSITE_DATA_DIR",
        str(Path(__file__).parent / "data" / "microsites"),
    )
)
BLOBS_DIR = DATA_ROOT / "blobs"
TEMP_DIR = DATA_ROOT / "tmp"
MAX_FILES = int(os.environ.get("MICROSITE_MAX_FILES", "100000"))
MAX_TOTAL_BYTES = int(os.environ.get("MICROSITE_MAX_TOTAL_BYTES", str(50 * 1024**3)))
MAX_BLOB_BYTES = int(os.environ.get("MICROSITE_MAX_BLOB_BYTES", str(5 * 1024**3)))
PUBLIC_BASE_URL = os.environ.get("MICROSITE_PUBLIC_BASE_URL", "").rstrip("/")
ACCEL_PREFIX = os.environ.get("MICROSITE_ACCEL_PREFIX", "").rstrip("/")
CORS_ORIGIN = os.environ.get("MICROSITE_CORS_ORIGIN", "*")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _blob_path(digest: str) -> Path:
    return BLOBS_DIR / digest[:2] / digest


def normalize_asset_path(value: str) -> str:
    """Return a canonical relative POSIX path or reject unsafe/ambiguous input."""
    if not value or "\x00" in value or "\\" in value or value.startswith("/"):
        raise ValueError("asset path must be a non-empty relative POSIX path")
    path = PurePosixPath(value)
    if any(part in ("", ".", "..") for part in path.parts):
        raise ValueError("asset path contains an unsafe segment")
    normalized = path.as_posix()
    if normalized != value:
        raise ValueError("asset path must already be canonical")
    if len(normalized.encode("utf-8")) > 1024:
        raise ValueError("asset path is too long")
    return normalized


class SiteCreate(BaseModel):
    slug: str = Field(min_length=1, max_length=128)
    title: str = Field(min_length=1, max_length=200)


class ManifestFile(BaseModel):
    path: str
    sha256: str
    size: int = Field(ge=0)
    content_type: str | None = Field(default=None, max_length=255)


class DeploymentCreate(BaseModel):
    entrypoint: str = "index.html"
    spa_fallback: bool = True
    files: list[ManifestFile]


def init_microsite_schema(db_path: Path) -> None:
    """Create additive schema. Safe to run at every application startup."""
    DATA_ROOT.mkdir(parents=True, exist_ok=True)
    BLOBS_DIR.mkdir(parents=True, exist_ok=True)
    TEMP_DIR.mkdir(parents=True, exist_ok=True)

    con = sqlite3.connect(db_path)
    try:
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA busy_timeout=5000")
        con.executescript(
            """
            CREATE TABLE IF NOT EXISTS sites (
                id TEXT PRIMARY KEY,
                slug TEXT NOT NULL UNIQUE,
                title TEXT NOT NULL,
                owner_id TEXT NOT NULL,
                active_deployment_id TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS deployments (
                id TEXT PRIMARY KEY,
                site_id TEXT NOT NULL,
                state TEXT NOT NULL CHECK (state IN ('staging', 'ready', 'active', 'superseded')),
                entrypoint TEXT NOT NULL,
                spa_fallback INTEGER NOT NULL DEFAULT 1,
                file_count INTEGER NOT NULL,
                total_size INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                finalized_at TEXT,
                activated_at TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_deployments_site_created
                ON deployments(site_id, created_at DESC);

            CREATE TABLE IF NOT EXISTS blobs (
                sha256 TEXT PRIMARY KEY,
                size INTEGER NOT NULL,
                storage_path TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS deployment_files (
                deployment_id TEXT NOT NULL,
                path TEXT NOT NULL,
                blob_sha256 TEXT NOT NULL,
                size INTEGER NOT NULL,
                content_type TEXT,
                PRIMARY KEY (deployment_id, path)
            );

            CREATE INDEX IF NOT EXISTS idx_deployment_files_blob
                ON deployment_files(blob_sha256);
            """
        )
        con.commit()
    finally:
        con.close()


def _site_for_actor(db: sqlite3.Connection, slug: str, actor_id: str) -> sqlite3.Row:
    site = db.execute("SELECT * FROM sites WHERE slug = ?", (slug,)).fetchone()
    if not site:
        raise HTTPException(status_code=404, detail="Site not found")
    if site["owner_id"] != actor_id:
        actor = db.execute("SELECT role FROM users WHERE id = ?", (actor_id,)).fetchone()
        if not actor or actor["role"] != "super_admin":
            raise HTTPException(status_code=403, detail="Not allowed to manage this site")
    return site


def _site_url(slug: str, deployment_id: str | None = None) -> str:
    path = f"/sites/{quote(slug)}/"
    if deployment_id:
        path = f"/_deployments/{quote(deployment_id)}/"
    return f"{PUBLIC_BASE_URL}{path}" if PUBLIC_BASE_URL else path


def _deployment_payload(db: sqlite3.Connection, deployment: sqlite3.Row) -> dict:
    missing = [
        row["blob_sha256"]
        for row in db.execute(
            """
            SELECT DISTINCT df.blob_sha256
            FROM deployment_files df
            LEFT JOIN blobs b ON b.sha256 = df.blob_sha256
            WHERE df.deployment_id = ? AND b.sha256 IS NULL
            ORDER BY df.blob_sha256
            """,
            (deployment["id"],),
        ).fetchall()
    ]
    return {
        "id": deployment["id"],
        "state": deployment["state"],
        "entrypoint": deployment["entrypoint"],
        "spa_fallback": bool(deployment["spa_fallback"]),
        "file_count": deployment["file_count"],
        "total_size": deployment["total_size"],
        "created_at": deployment["created_at"],
        "missing_blobs": missing,
        "immutable_url": _site_url("", deployment["id"]),
    }


def _validate_deployment_files(db: sqlite3.Connection, deployment_id: str) -> list[str]:
    """Return missing or corrupt blob hashes for a deployment."""
    missing: list[str] = []
    rows = db.execute(
        """
        SELECT DISTINCT df.blob_sha256, df.size AS expected_size,
                        b.size AS stored_size, b.storage_path
        FROM deployment_files df
        LEFT JOIN blobs b ON b.sha256 = df.blob_sha256
        WHERE df.deployment_id = ?
        """,
        (deployment_id,),
    ).fetchall()
    for row in rows:
        try:
            path = Path(row["storage_path"]) if row["storage_path"] else None
            valid = (
                row["stored_size"] == row["expected_size"]
                and path is not None
                and path.is_file()
                and path.stat().st_size == row["expected_size"]
            )
        except OSError:
            valid = False
        if not valid:
            missing.append(row["blob_sha256"])
    return sorted(missing)


def create_microsite_router(
    get_db: Callable,
    get_api_user: Callable,
) -> APIRouter:
    router = APIRouter()

    @router.post("/api/sites", status_code=201)
    async def create_site(
        body: SiteCreate,
        db: sqlite3.Connection = Depends(get_db),
        owner_id: str = Depends(get_api_user),
    ):
        slug = body.slug.strip()
        if not SLUG_RE.fullmatch(slug):
            raise HTTPException(400, "Site slug must contain lowercase letters, digits, dots, dashes, or underscores")
        title = body.title.strip()
        if not title:
            raise HTTPException(400, "Site title cannot be blank")
        now = _now()
        site_id = _id("site")
        try:
            db.execute(
                "INSERT INTO sites (id, slug, title, owner_id, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                (site_id, slug, title, owner_id, now, now),
            )
            db.commit()
        except sqlite3.IntegrityError:
            db.rollback()
            raise HTTPException(409, "Site slug already exists")
        return {"id": site_id, "slug": slug, "title": title, "url": _site_url(slug)}

    @router.get("/api/sites")
    async def list_sites(
        db: sqlite3.Connection = Depends(get_db),
        owner_id: str = Depends(get_api_user),
    ):
        actor = db.execute("SELECT role FROM users WHERE id = ?", (owner_id,)).fetchone()
        if actor and actor["role"] == "super_admin":
            rows = db.execute("SELECT * FROM sites ORDER BY updated_at DESC").fetchall()
        else:
            rows = db.execute(
                "SELECT * FROM sites WHERE owner_id = ? ORDER BY updated_at DESC", (owner_id,)
            ).fetchall()
        return [
            {
                "id": row["id"],
                "slug": row["slug"],
                "title": row["title"],
                "active_deployment_id": row["active_deployment_id"],
                "url": _site_url(row["slug"]),
                "updated_at": row["updated_at"],
            }
            for row in rows
        ]

    @router.get("/api/sites/{site_slug}")
    async def get_site(
        site_slug: str,
        db: sqlite3.Connection = Depends(get_db),
        owner_id: str = Depends(get_api_user),
    ):
        site = _site_for_actor(db, site_slug, owner_id)
        deployments = db.execute(
            "SELECT * FROM deployments WHERE site_id = ? ORDER BY created_at DESC",
            (site["id"],),
        ).fetchall()
        return {
            "id": site["id"],
            "slug": site["slug"],
            "title": site["title"],
            "active_deployment_id": site["active_deployment_id"],
            "url": _site_url(site["slug"]),
            "deployments": [_deployment_payload(db, row) for row in deployments],
        }

    @router.post("/api/sites/{site_slug}/deployments", status_code=201)
    async def create_deployment(
        site_slug: str,
        body: DeploymentCreate,
        db: sqlite3.Connection = Depends(get_db),
        owner_id: str = Depends(get_api_user),
    ):
        site = _site_for_actor(db, site_slug, owner_id)
        if not body.files:
            raise HTTPException(400, "Manifest must contain at least one file")
        if len(body.files) > MAX_FILES:
            raise HTTPException(413, f"Manifest exceeds the {MAX_FILES} file limit")

        try:
            entrypoint = normalize_asset_path(body.entrypoint)
        except ValueError as exc:
            raise HTTPException(400, f"Invalid entrypoint: {exc}")

        normalized: list[tuple[str, str, int, str | None]] = []
        seen_paths: set[str] = set()
        hash_sizes: dict[str, int] = {}
        total_size = 0
        for item in body.files:
            try:
                path = normalize_asset_path(item.path)
            except ValueError as exc:
                raise HTTPException(400, f"Invalid asset path {item.path!r}: {exc}")
            digest = item.sha256.lower()
            if not SHA256_RE.fullmatch(digest):
                raise HTTPException(400, f"Invalid SHA-256 for {path}")
            if item.size > MAX_BLOB_BYTES:
                raise HTTPException(413, f"Blob {path} exceeds the per-file size limit")
            if path in seen_paths:
                raise HTTPException(400, f"Duplicate manifest path: {path}")
            if digest in hash_sizes and hash_sizes[digest] != item.size:
                raise HTTPException(400, f"Conflicting sizes for blob {digest}")
            seen_paths.add(path)
            hash_sizes[digest] = item.size
            total_size += item.size
            content_type = item.content_type or mimetypes.guess_type(path)[0]
            if content_type and not CONTENT_TYPE_RE.fullmatch(content_type):
                raise HTTPException(400, f"Invalid content type for {path}")
            normalized.append((path, digest, item.size, content_type))

        if entrypoint not in seen_paths:
            raise HTTPException(400, "Entrypoint is not present in the manifest")
        if total_size > MAX_TOTAL_BYTES:
            raise HTTPException(413, "Deployment exceeds the total size limit")

        digests = list(hash_sizes)
        for offset in range(0, len(digests), 500):
            batch = digests[offset : offset + 500]
            existing = db.execute(
                f"SELECT sha256, size FROM blobs WHERE sha256 IN ({','.join('?' for _ in batch)})",
                batch,
            ).fetchall()
            for blob in existing:
                if blob["size"] != hash_sizes[blob["sha256"]]:
                    raise HTTPException(409, f"Stored blob size mismatch for {blob['sha256']}")

        deployment_id = _id("dep")
        now = _now()
        try:
            db.execute(
                """
                INSERT INTO deployments
                    (id, site_id, state, entrypoint, spa_fallback, file_count, total_size, created_at)
                VALUES (?, ?, 'staging', ?, ?, ?, ?, ?)
                """,
                (
                    deployment_id,
                    site["id"],
                    entrypoint,
                    int(body.spa_fallback),
                    len(normalized),
                    total_size,
                    now,
                ),
            )
            db.executemany(
                """
                INSERT INTO deployment_files
                    (deployment_id, path, blob_sha256, size, content_type)
                VALUES (?, ?, ?, ?, ?)
                """,
                [(deployment_id, *item) for item in normalized],
            )
            db.commit()
        except Exception:
            db.rollback()
            raise

        deployment = db.execute("SELECT * FROM deployments WHERE id = ?", (deployment_id,)).fetchone()
        return _deployment_payload(db, deployment)

    @router.get("/api/sites/{site_slug}/deployments/{deployment_id}")
    async def get_deployment(
        site_slug: str,
        deployment_id: str,
        db: sqlite3.Connection = Depends(get_db),
        owner_id: str = Depends(get_api_user),
    ):
        site = _site_for_actor(db, site_slug, owner_id)
        deployment = db.execute(
            "SELECT * FROM deployments WHERE id = ? AND site_id = ?",
            (deployment_id, site["id"]),
        ).fetchone()
        if not deployment:
            raise HTTPException(404, "Deployment not found")
        return _deployment_payload(db, deployment)

    @router.put("/api/sites/{site_slug}/deployments/{deployment_id}/blobs/{digest}")
    async def upload_blob(
        site_slug: str,
        deployment_id: str,
        digest: str,
        request: Request,
        db: sqlite3.Connection = Depends(get_db),
        owner_id: str = Depends(get_api_user),
    ):
        site = _site_for_actor(db, site_slug, owner_id)
        digest = digest.lower()
        if not SHA256_RE.fullmatch(digest):
            raise HTTPException(400, "Invalid SHA-256")
        deployment = db.execute(
            "SELECT state FROM deployments WHERE id = ? AND site_id = ?",
            (deployment_id, site["id"]),
        ).fetchone()
        if not deployment:
            raise HTTPException(404, "Deployment not found")
        if deployment["state"] != "staging":
            raise HTTPException(409, "Only staging deployments accept uploads")
        expected = db.execute(
            "SELECT size FROM deployment_files WHERE deployment_id = ? AND blob_sha256 = ? LIMIT 1",
            (deployment_id, digest),
        ).fetchone()
        if not expected:
            raise HTTPException(400, "Blob is not referenced by this deployment")
        expected_size = expected["size"]

        existing = db.execute(
            "SELECT size, storage_path FROM blobs WHERE sha256 = ?", (digest,)
        ).fetchone()
        if existing:
            if existing["size"] != expected_size:
                raise HTTPException(409, "Stored blob size does not match manifest")
            try:
                existing_path = Path(existing["storage_path"])
                if existing_path.is_file() and existing_path.stat().st_size == expected_size:
                    return {"sha256": digest, "size": expected_size, "status": "already_present"}
            except OSError:
                pass

        content_length = request.headers.get("content-length")
        if content_length:
            try:
                if int(content_length) != expected_size:
                    raise HTTPException(400, "Content-Length does not match manifest")
            except ValueError:
                raise HTTPException(400, "Invalid Content-Length")

        temp_path = TEMP_DIR / f"{deployment_id}-{uuid.uuid4().hex}.upload"
        hasher = hashlib.sha256()
        written = 0
        try:
            with temp_path.open("xb") as handle:
                async for chunk in request.stream():
                    written += len(chunk)
                    if written > expected_size or written > MAX_BLOB_BYTES:
                        raise HTTPException(413, "Uploaded blob exceeds manifest size")
                    hasher.update(chunk)
                    handle.write(chunk)
            if written != expected_size:
                raise HTTPException(400, "Uploaded size does not match manifest")
            if not hmac.compare_digest(hasher.hexdigest(), digest):
                raise HTTPException(400, "Uploaded content does not match SHA-256")

            destination = _blob_path(digest)
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists():
                if destination.stat().st_size == written:
                    temp_path.unlink(missing_ok=True)
                else:
                    os.replace(temp_path, destination)
            else:
                os.replace(temp_path, destination)
            db.execute(
                """
                INSERT INTO blobs (sha256, size, storage_path, created_at) VALUES (?, ?, ?, ?)
                ON CONFLICT(sha256) DO UPDATE SET
                    size = excluded.size, storage_path = excluded.storage_path
                """,
                (digest, written, str(destination), _now()),
            )
            db.commit()
        except Exception:
            temp_path.unlink(missing_ok=True)
            raise
        return {"sha256": digest, "size": written, "status": "stored"}

    @router.post("/api/sites/{site_slug}/deployments/{deployment_id}/finalize")
    async def finalize_deployment(
        site_slug: str,
        deployment_id: str,
        db: sqlite3.Connection = Depends(get_db),
        owner_id: str = Depends(get_api_user),
    ):
        site = _site_for_actor(db, site_slug, owner_id)
        deployment = db.execute(
            "SELECT * FROM deployments WHERE id = ? AND site_id = ?",
            (deployment_id, site["id"]),
        ).fetchone()
        if not deployment:
            raise HTTPException(404, "Deployment not found")
        if deployment["state"] != "staging":
            raise HTTPException(409, "Deployment is not staging")
        missing_blobs = _validate_deployment_files(db, deployment_id)
        if missing_blobs:
            raise HTTPException(
                409,
                {"message": "Deployment has missing or corrupt blobs", "missing_blobs": missing_blobs},
            )
        db.execute(
            "UPDATE deployments SET state = 'ready', finalized_at = ? WHERE id = ? AND state = 'staging'",
            (_now(), deployment_id),
        )
        db.commit()
        deployment = db.execute("SELECT * FROM deployments WHERE id = ?", (deployment_id,)).fetchone()
        return _deployment_payload(db, deployment)

    @router.post("/api/sites/{site_slug}/deployments/{deployment_id}/activate")
    async def activate_deployment(
        site_slug: str,
        deployment_id: str,
        db: sqlite3.Connection = Depends(get_db),
        owner_id: str = Depends(get_api_user),
    ):
        site = _site_for_actor(db, site_slug, owner_id)
        try:
            db.execute("BEGIN IMMEDIATE")
            site = db.execute("SELECT * FROM sites WHERE id = ?", (site["id"],)).fetchone()
            deployment = db.execute(
                "SELECT * FROM deployments WHERE id = ? AND site_id = ?",
                (deployment_id, site["id"]),
            ).fetchone()
            if not deployment:
                raise HTTPException(404, "Deployment not found")
            if deployment["state"] == "active" and site["active_deployment_id"] == deployment_id:
                db.rollback()
                return _deployment_payload(db, deployment)
            if deployment["state"] not in ("ready", "superseded"):
                raise HTTPException(409, "Only a ready or superseded deployment can be activated")
            now = _now()
            db.execute(
                "UPDATE deployments SET state = 'superseded' WHERE site_id = ? AND state = 'active'",
                (site["id"],),
            )
            db.execute(
                "UPDATE deployments SET state = 'active', activated_at = ? WHERE id = ?",
                (now, deployment_id),
            )
            db.execute(
                "UPDATE sites SET active_deployment_id = ?, updated_at = ? WHERE id = ?",
                (deployment_id, now, site["id"]),
            )
            db.commit()
        except Exception:
            db.rollback()
            raise
        deployment = db.execute("SELECT * FROM deployments WHERE id = ?", (deployment_id,)).fetchone()
        payload = _deployment_payload(db, deployment)
        payload["url"] = _site_url(site_slug)
        return payload

    return router


def _asset_response(row: sqlite3.Row, immutable: bool) -> Response:
    path = Path(row["storage_path"])
    if not path.is_file():
        raise HTTPException(404, "Asset not found")
    content_type = row["content_type"] or "application/octet-stream"
    headers = {
        "ETag": f'"sha256-{row["blob_sha256"]}"',
        "Accept-Ranges": "bytes",
        "Access-Control-Allow-Origin": CORS_ORIGIN,
        "Cross-Origin-Resource-Policy": "cross-origin",
        "Cache-Control": "public, max-age=31536000, immutable" if immutable else "no-cache",
    }
    if ACCEL_PREFIX:
        headers["X-Accel-Redirect"] = f"{ACCEL_PREFIX}/{row['blob_sha256'][:2]}/{row['blob_sha256']}"
        return Response(status_code=200, media_type=content_type, headers=headers)
    return FileResponse(path, media_type=content_type, headers=headers)


def _resolve_asset(
    db: sqlite3.Connection,
    deployment: sqlite3.Row,
    requested_path: str,
) -> sqlite3.Row | None:
    path = requested_path or deployment["entrypoint"]
    if path.endswith("/"):
        path += deployment["entrypoint"]
    try:
        path = normalize_asset_path(path)
    except ValueError:
        return None
    row = db.execute(
        """
        SELECT df.*, b.storage_path
        FROM deployment_files df
        JOIN blobs b ON b.sha256 = df.blob_sha256
        WHERE df.deployment_id = ? AND df.path = ?
        """,
        (deployment["id"], path),
    ).fetchone()
    if row or not deployment["spa_fallback"] or PurePosixPath(path).suffix:
        return row
    return db.execute(
        """
        SELECT df.*, b.storage_path
        FROM deployment_files df
        JOIN blobs b ON b.sha256 = df.blob_sha256
        WHERE df.deployment_id = ? AND df.path = ?
        """,
        (deployment["id"], deployment["entrypoint"]),
    ).fetchone()


def create_data_router(get_db: Callable) -> APIRouter:
    router = APIRouter()

    @router.api_route("/sites/{site_slug}", methods=["GET", "HEAD"], include_in_schema=False)
    async def site_without_slash(site_slug: str):
        return RedirectResponse(f"/sites/{quote(site_slug)}/", status_code=308)

    @router.api_route(
        "/sites/{site_slug}/{asset_path:path}", methods=["GET", "HEAD"], include_in_schema=False
    )
    async def serve_active_site(
        site_slug: str,
        asset_path: str,
        db: sqlite3.Connection = Depends(get_db),
    ):
        deployment = db.execute(
            """
            SELECT d.* FROM sites s
            JOIN deployments d ON d.id = s.active_deployment_id
            WHERE s.slug = ? AND d.state = 'active'
            """,
            (site_slug,),
        ).fetchone()
        if not deployment:
            raise HTTPException(404, "Site not found")
        asset = _resolve_asset(db, deployment, asset_path)
        if not asset:
            raise HTTPException(404, "Asset not found")
        return _asset_response(asset, immutable=False)

    @router.api_route(
        "/_deployments/{deployment_id}/{asset_path:path}",
        methods=["GET", "HEAD"],
        include_in_schema=False,
    )
    async def serve_immutable_deployment(
        deployment_id: str,
        asset_path: str,
        db: sqlite3.Connection = Depends(get_db),
    ):
        deployment = db.execute(
            "SELECT * FROM deployments WHERE id = ? AND state IN ('active', 'superseded')",
            (deployment_id,),
        ).fetchone()
        if not deployment:
            raise HTTPException(404, "Deployment not found")
        asset = _resolve_asset(db, deployment, asset_path)
        if not asset:
            raise HTTPException(404, "Asset not found")
        return _asset_response(asset, immutable=True)

    return router
