"""Shared mutable JSON documents for deployed microsites.

Static deployments remain immutable.  A site's active ``microsite.json`` may
declare runtime documents that are stored separately in SQLite and accessed by
browser clients through a small, reusable SDK.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError
from pydantic import BaseModel


DOCUMENT_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")
CONFIG_PATH = "microsite.json"
MAX_CONFIG_BYTES = 256 * 1024
MAX_DOCUMENTS = 100
MAX_DOCUMENT_BYTES = int(
    os.environ.get("MICROSITE_RUNTIME_MAX_DOCUMENT_BYTES", str(1024 * 1024))
)
MAX_VERSIONS = int(os.environ.get("MICROSITE_RUNTIME_MAX_VERSIONS", "100"))
SDK_PATH = Path(__file__).parent / "static" / "microsite-runtime-v1.js"


class RuntimeConfigError(ValueError):
    """Raised when a deployment contains an invalid runtime-data declaration."""


@dataclass(frozen=True)
class RuntimeDocumentConfig:
    key: str
    read_policy: str
    write_policy: str
    max_bytes: int
    schema_version: int
    schema_json: str | None
    seed_json: str | None


@dataclass(frozen=True)
class RuntimeActor:
    """Authenticated runtime actor, optionally restricted to one document."""

    user_id: str
    site_id: str | None = None
    document_key: str | None = None
    token_id: str | None = None


class RuntimeDocumentWrite(BaseModel):
    value: Any


class RuntimeWriterTokenCreate(BaseModel):
    name: str = "scheduled-writer"


def runtime_token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def init_runtime_schema(db: sqlite3.Connection) -> None:
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS site_document_configs (
            site_id TEXT NOT NULL,
            document_key TEXT NOT NULL,
            read_policy TEXT NOT NULL CHECK (read_policy IN ('public', 'owner')),
            write_policy TEXT NOT NULL CHECK (write_policy IN ('owner')),
            max_bytes INTEGER NOT NULL,
            schema_version INTEGER NOT NULL DEFAULT 1,
            schema_json TEXT,
            active_deployment_id TEXT NOT NULL,
            PRIMARY KEY (site_id, document_key)
        );

        CREATE TABLE IF NOT EXISTS site_documents (
            site_id TEXT NOT NULL,
            document_key TEXT NOT NULL,
            value_json TEXT NOT NULL,
            revision INTEGER NOT NULL,
            schema_version INTEGER NOT NULL DEFAULT 1,
            updated_at TEXT NOT NULL,
            updated_by TEXT NOT NULL,
            PRIMARY KEY (site_id, document_key)
        );

        CREATE TABLE IF NOT EXISTS site_document_versions (
            site_id TEXT NOT NULL,
            document_key TEXT NOT NULL,
            revision INTEGER NOT NULL,
            value_json TEXT NOT NULL,
            schema_version INTEGER NOT NULL,
            updated_at TEXT NOT NULL,
            updated_by TEXT NOT NULL,
            source_deployment_id TEXT,
            PRIMARY KEY (site_id, document_key, revision)
        );

        CREATE INDEX IF NOT EXISTS idx_site_document_versions_recent
            ON site_document_versions(site_id, document_key, revision DESC);

        CREATE TABLE IF NOT EXISTS runtime_writer_tokens (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            site_id TEXT NOT NULL,
            document_key TEXT NOT NULL,
            token_hash TEXT NOT NULL UNIQUE,
            token_prefix TEXT NOT NULL,
            name TEXT NOT NULL,
            created_at TEXT NOT NULL,
            revoked_at TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_runtime_writer_tokens_site
            ON runtime_writer_tokens(site_id, document_key, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_runtime_writer_tokens_hash
            ON runtime_writer_tokens(token_hash);
        """
    )


def _canonical_json(value: Any) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as exc:
        raise RuntimeConfigError(f"value is not valid JSON: {exc}") from exc


def _normalize_asset_path(value: str) -> str:
    if not value or "\x00" in value or "\\" in value or value.startswith("/"):
        raise RuntimeConfigError("asset path must be a relative POSIX path")
    path = PurePosixPath(value)
    if any(part in ("", ".", "..") for part in path.parts) or path.as_posix() != value:
        raise RuntimeConfigError("asset path must be canonical and cannot contain dot segments")
    return value


def _read_deployment_asset(
    db: sqlite3.Connection,
    deployment_id: str,
    asset_path: str,
    *,
    max_bytes: int,
) -> bytes | None:
    row = db.execute(
        """
        SELECT df.size, b.storage_path
        FROM deployment_files df
        JOIN blobs b ON b.sha256 = df.blob_sha256
        WHERE df.deployment_id = ? AND df.path = ?
        """,
        (deployment_id, asset_path),
    ).fetchone()
    if not row:
        return None
    if row["size"] > max_bytes:
        raise RuntimeConfigError(f"{asset_path} exceeds the {max_bytes} byte limit")
    path = Path(row["storage_path"])
    try:
        content = path.read_bytes()
    except OSError as exc:
        raise RuntimeConfigError(f"cannot read {asset_path}: {exc}") from exc
    if len(content) != row["size"]:
        raise RuntimeConfigError(f"stored size mismatch for {asset_path}")
    return content


def _read_json_asset(
    db: sqlite3.Connection,
    deployment_id: str,
    asset_path: str,
    *,
    max_bytes: int,
) -> Any:
    content = _read_deployment_asset(
        db, deployment_id, asset_path, max_bytes=max_bytes
    )
    if content is None:
        raise RuntimeConfigError(f"referenced asset is missing: {asset_path}")
    try:
        return json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeConfigError(f"{asset_path} must contain valid UTF-8 JSON") from exc


def _schema_validator(schema_json: str | None) -> Draft202012Validator | None:
    if schema_json is None:
        return None
    schema = json.loads(schema_json)
    _reject_external_schema_refs(schema)
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as exc:
        raise RuntimeConfigError(f"invalid JSON Schema: {exc.message}") from exc
    return Draft202012Validator(schema)


def _reject_external_schema_refs(value: Any) -> None:
    """Keep deployment validation deterministic and prevent remote schema fetches."""
    if isinstance(value, dict):
        for key, child in value.items():
            if key in ("$ref", "$dynamicRef"):
                if not isinstance(child, str) or not child.startswith("#"):
                    raise RuntimeConfigError("JSON Schema only supports local # references")
            _reject_external_schema_refs(child)
    elif isinstance(value, list):
        for child in value:
            _reject_external_schema_refs(child)


def _validate_document_value(
    value: Any,
    config: RuntimeDocumentConfig,
) -> str:
    value_json = _canonical_json(value)
    if len(value_json.encode("utf-8")) > config.max_bytes:
        raise RuntimeConfigError(
            f"document {config.key!r} exceeds the {config.max_bytes} byte limit"
        )
    validator = _schema_validator(config.schema_json)
    if validator:
        try:
            validator.validate(value)
        except ValidationError as exc:
            location = "/".join(str(part) for part in exc.absolute_path)
            suffix = f" at {location}" if location else ""
            raise RuntimeConfigError(
                f"document {config.key!r} fails schema validation{suffix}: {exc.message}"
            ) from exc
    return value_json


def load_runtime_config(
    db: sqlite3.Connection,
    deployment_id: str,
) -> list[RuntimeDocumentConfig]:
    """Read and fully validate a deployment's optional ``microsite.json``."""
    content = _read_deployment_asset(
        db, deployment_id, CONFIG_PATH, max_bytes=MAX_CONFIG_BYTES
    )
    if content is None:
        return []
    try:
        root = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeConfigError("microsite.json must contain valid UTF-8 JSON") from exc
    if not isinstance(root, dict):
        raise RuntimeConfigError("microsite.json must contain a JSON object")
    runtime = root.get("runtimeData")
    if runtime is None:
        return []
    if not isinstance(runtime, dict):
        raise RuntimeConfigError("runtimeData must be a JSON object")
    documents = runtime.get("documents", {})
    if not isinstance(documents, dict):
        raise RuntimeConfigError("runtimeData.documents must be a JSON object")
    if len(documents) > MAX_DOCUMENTS:
        raise RuntimeConfigError(f"runtimeData declares more than {MAX_DOCUMENTS} documents")

    parsed: list[RuntimeDocumentConfig] = []
    for key, raw in documents.items():
        if not isinstance(key, str) or not DOCUMENT_KEY_RE.fullmatch(key):
            raise RuntimeConfigError(f"invalid runtime document key: {key!r}")
        if not isinstance(raw, dict):
            raise RuntimeConfigError(f"runtime document {key!r} must be an object")
        scope = raw.get("scope", "site")
        read_policy = raw.get("read", "public")
        write_policy = raw.get("write", "owner")
        max_bytes = raw.get("maxBytes", MAX_DOCUMENT_BYTES)
        schema_version = raw.get("schemaVersion", 1)
        if scope != "site":
            raise RuntimeConfigError(f"runtime document {key!r} only supports scope='site'")
        if read_policy not in ("public", "owner"):
            raise RuntimeConfigError(f"invalid read policy for runtime document {key!r}")
        if write_policy != "owner":
            raise RuntimeConfigError(f"runtime document {key!r} only supports write='owner'")
        if not isinstance(max_bytes, int) or isinstance(max_bytes, bool) or not 1 <= max_bytes <= MAX_DOCUMENT_BYTES:
            raise RuntimeConfigError(
                f"maxBytes for runtime document {key!r} must be between 1 and {MAX_DOCUMENT_BYTES}"
            )
        if not isinstance(schema_version, int) or isinstance(schema_version, bool) or schema_version < 1:
            raise RuntimeConfigError(f"schemaVersion for runtime document {key!r} must be positive")

        schema_json = None
        schema_path = raw.get("schema")
        if schema_path is not None:
            if not isinstance(schema_path, str):
                raise RuntimeConfigError(f"schema for runtime document {key!r} must be a path")
            schema = _read_json_asset(
                db,
                deployment_id,
                _normalize_asset_path(schema_path),
                max_bytes=MAX_CONFIG_BYTES,
            )
            schema_json = _canonical_json(schema)
            _schema_validator(schema_json)

        seed_json = None
        seed_path = raw.get("seed")
        if seed_path is not None:
            if not isinstance(seed_path, str):
                raise RuntimeConfigError(f"seed for runtime document {key!r} must be a path")
            seed = _read_json_asset(
                db,
                deployment_id,
                _normalize_asset_path(seed_path),
                max_bytes=max_bytes,
            )
            seed_config = RuntimeDocumentConfig(
                key=key,
                read_policy=read_policy,
                write_policy=write_policy,
                max_bytes=max_bytes,
                schema_version=schema_version,
                schema_json=schema_json,
                seed_json=None,
            )
            seed_json = _validate_document_value(seed, seed_config)

        parsed.append(
            RuntimeDocumentConfig(
                key=key,
                read_policy=read_policy,
                write_policy=write_policy,
                max_bytes=max_bytes,
                schema_version=schema_version,
                schema_json=schema_json,
                seed_json=seed_json,
            )
        )
    return parsed


def apply_runtime_config(
    db: sqlite3.Connection,
    site: sqlite3.Row,
    deployment_id: str,
    configs: list[RuntimeDocumentConfig],
    *,
    now: str,
) -> None:
    """Atomically register configs and seed missing documents during activation."""
    for config in configs:
        current = db.execute(
            "SELECT value_json FROM site_documents WHERE site_id = ? AND document_key = ?",
            (site["id"], config.key),
        ).fetchone()
        if current:
            _validate_document_value(json.loads(current["value_json"]), config)
            db.execute(
                """
                UPDATE site_documents SET schema_version = ?
                WHERE site_id = ? AND document_key = ?
                """,
                (config.schema_version, site["id"], config.key),
            )

    db.execute("DELETE FROM site_document_configs WHERE site_id = ?", (site["id"],))
    for config in configs:
        db.execute(
            """
            INSERT INTO site_document_configs
                (site_id, document_key, read_policy, write_policy, max_bytes,
                 schema_version, schema_json, active_deployment_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                site["id"],
                config.key,
                config.read_policy,
                config.write_policy,
                config.max_bytes,
                config.schema_version,
                config.schema_json,
                deployment_id,
            ),
        )
        existing = db.execute(
            "SELECT 1 FROM site_documents WHERE site_id = ? AND document_key = ?",
            (site["id"], config.key),
        ).fetchone()
        if existing or config.seed_json is None:
            continue
        db.execute(
            """
            INSERT INTO site_documents
                (site_id, document_key, value_json, revision, schema_version, updated_at, updated_by)
            VALUES (?, ?, ?, 1, ?, ?, ?)
            """,
            (
                site["id"],
                config.key,
                config.seed_json,
                config.schema_version,
                now,
                site["owner_id"],
            ),
        )
        db.execute(
            """
            INSERT INTO site_document_versions
                (site_id, document_key, revision, value_json, schema_version,
                 updated_at, updated_by, source_deployment_id)
            VALUES (?, ?, 1, ?, ?, ?, ?, ?)
            """,
            (
                site["id"],
                config.key,
                config.seed_json,
                config.schema_version,
                now,
                site["owner_id"],
                deployment_id,
            ),
        )


def _runtime_site_config(
    db: sqlite3.Connection,
    site_slug: str,
    document_key: str,
) -> sqlite3.Row:
    if not DOCUMENT_KEY_RE.fullmatch(document_key):
        raise HTTPException(404, "Runtime document not found")
    row = db.execute(
        """
        SELECT s.id AS site_id, s.owner_id, c.*
        FROM sites s
        JOIN site_document_configs c ON c.site_id = s.id
        WHERE s.slug = ? AND c.document_key = ?
          AND s.active_deployment_id = c.active_deployment_id
        """,
        (site_slug, document_key),
    ).fetchone()
    if not row:
        raise HTTPException(404, "Runtime document not found")
    return row


def _actor_user_id(actor: str | RuntimeActor | None) -> str | None:
    return actor.user_id if isinstance(actor, RuntimeActor) else actor


def _actor_is_owner(
    db: sqlite3.Connection,
    config: sqlite3.Row,
    actor: str | RuntimeActor | None,
) -> bool:
    actor_id = _actor_user_id(actor)
    if not actor_id:
        return False
    if isinstance(actor, RuntimeActor):
        if actor.site_id is not None and actor.site_id != config["site_id"]:
            return False
        if actor.document_key is not None and actor.document_key != config["document_key"]:
            return False
    if actor_id == config["owner_id"]:
        return True
    actor_row = db.execute("SELECT role FROM users WHERE id = ?", (actor_id,)).fetchone()
    return bool(actor_row and actor_row["role"] == "super_admin")


def _require_owner(
    db: sqlite3.Connection,
    config: sqlite3.Row,
    actor: str | RuntimeActor | None,
) -> None:
    if not actor:
        raise HTTPException(401, "Login required to update runtime data")
    if not _actor_is_owner(db, config, actor):
        raise HTTPException(403, "Not allowed to update this site's runtime data")


def _site_for_actor(db: sqlite3.Connection, site_slug: str, actor_id: str) -> sqlite3.Row:
    site = db.execute("SELECT * FROM sites WHERE slug = ?", (site_slug,)).fetchone()
    if not site:
        raise HTTPException(404, "Site not found")
    if site["owner_id"] != actor_id:
        actor = db.execute("SELECT role FROM users WHERE id = ?", (actor_id,)).fetchone()
        if not actor or actor["role"] != "super_admin":
            raise HTTPException(403, "Not allowed to manage this site")
    return site


def _config_from_row(row: sqlite3.Row) -> RuntimeDocumentConfig:
    return RuntimeDocumentConfig(
        key=row["document_key"],
        read_policy=row["read_policy"],
        write_policy=row["write_policy"],
        max_bytes=row["max_bytes"],
        schema_version=row["schema_version"],
        schema_json=row["schema_json"],
        seed_json=None,
    )


def _etag(revision: int) -> str:
    return f'"rev-{revision}"'


def _parse_if_match(value: str | None) -> int:
    if not value:
        raise HTTPException(428, "If-Match with the current runtime revision is required")
    match = re.fullmatch(r'"?rev-(\d+)"?', value.strip())
    if not match:
        raise HTTPException(400, 'If-Match must use the format "rev-N"')
    return int(match.group(1))


def _document_payload(
    db: sqlite3.Connection,
    config: sqlite3.Row,
) -> tuple[dict, int]:
    row = db.execute(
        """
        SELECT value_json, revision, schema_version, updated_at, updated_by
        FROM site_documents WHERE site_id = ? AND document_key = ?
        """,
        (config["site_id"], config["document_key"]),
    ).fetchone()
    if not row:
        return (
            {
                "key": config["document_key"],
                "value": None,
                "revision": 0,
                "schemaVersion": config["schema_version"],
                "updatedAt": None,
            },
            0,
        )
    return (
        {
            "key": config["document_key"],
            "value": json.loads(row["value_json"]),
            "revision": row["revision"],
            "schemaVersion": row["schema_version"],
            "updatedAt": row["updated_at"],
        },
        row["revision"],
    )


def create_runtime_router(
    get_db: Callable,
    get_runtime_user: Callable,
    get_api_user: Callable | None = None,
) -> APIRouter:
    router = APIRouter()

    @router.get("/_microsite/sdk/v1.js", include_in_schema=False)
    async def runtime_sdk():
        return FileResponse(
            SDK_PATH,
            media_type="application/javascript",
            headers={"Cache-Control": "public, max-age=31536000, immutable"},
        )

    @router.get("/api/runtime/sites/{site_slug}/documents/{document_key}")
    async def get_runtime_document(
        site_slug: str,
        document_key: str,
        db: sqlite3.Connection = Depends(get_db),
        actor: str | RuntimeActor | None = Depends(get_runtime_user),
    ):
        config = _runtime_site_config(db, site_slug, document_key)
        if config["read_policy"] == "owner" and not _actor_is_owner(db, config, actor):
            if not actor:
                raise HTTPException(401, "Login required to read runtime data")
            raise HTTPException(403, "Not allowed to read this site's runtime data")
        payload, revision = _document_payload(db, config)
        return JSONResponse(
            payload,
            headers={"ETag": _etag(revision), "Cache-Control": "no-store"},
        )

    @router.put("/api/runtime/sites/{site_slug}/documents/{document_key}")
    async def put_runtime_document(
        site_slug: str,
        document_key: str,
        body: RuntimeDocumentWrite,
        request: Request,
        db: sqlite3.Connection = Depends(get_db),
        actor: str | RuntimeActor | None = Depends(get_runtime_user),
    ):
        config = _runtime_site_config(db, site_slug, document_key)
        _require_owner(db, config, actor)
        expected_revision = _parse_if_match(request.headers.get("if-match"))

        try:
            db.execute("BEGIN IMMEDIATE")
            # Activation and writes share the same SQLite write lock. Re-read the
            # active declaration so a concurrent deployment cannot be followed by
            # a write validated against its predecessor's schema.
            config = _runtime_site_config(db, site_slug, document_key)
            _require_owner(db, config, actor)
            try:
                value_json = _validate_document_value(body.value, _config_from_row(config))
            except RuntimeConfigError as exc:
                raise HTTPException(422, str(exc)) from exc
            current = db.execute(
                """
                SELECT revision FROM site_documents
                WHERE site_id = ? AND document_key = ?
                """,
                (config["site_id"], config["document_key"]),
            ).fetchone()
            current_revision = current["revision"] if current else 0
            if current_revision != expected_revision:
                raise HTTPException(
                    409,
                    {
                        "message": "Runtime document revision conflict",
                        "expectedRevision": expected_revision,
                        "currentRevision": current_revision,
                    },
                )
            new_revision = current_revision + 1
            now = datetime.now(timezone.utc).isoformat()
            db.execute(
                """
                INSERT INTO site_documents
                    (site_id, document_key, value_json, revision, schema_version,
                     updated_at, updated_by)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(site_id, document_key) DO UPDATE SET
                    value_json = excluded.value_json,
                    revision = excluded.revision,
                    schema_version = excluded.schema_version,
                    updated_at = excluded.updated_at,
                    updated_by = excluded.updated_by
                """,
                (
                    config["site_id"],
                    config["document_key"],
                    value_json,
                    new_revision,
                    config["schema_version"],
                    now,
                    _actor_user_id(actor),
                ),
            )
            db.execute(
                """
                INSERT INTO site_document_versions
                    (site_id, document_key, revision, value_json, schema_version,
                     updated_at, updated_by, source_deployment_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, NULL)
                """,
                (
                    config["site_id"],
                    config["document_key"],
                    new_revision,
                    value_json,
                    config["schema_version"],
                    now,
                    _actor_user_id(actor),
                ),
            )
            if MAX_VERSIONS > 0:
                db.execute(
                    """
                    DELETE FROM site_document_versions
                    WHERE site_id = ? AND document_key = ? AND revision <= ?
                    """,
                    (
                        config["site_id"],
                        config["document_key"],
                        new_revision - MAX_VERSIONS,
                    ),
                )
            db.commit()
        except Exception:
            db.rollback()
            raise

        payload, revision = _document_payload(db, config)
        return JSONResponse(
            payload,
            headers={"ETag": _etag(revision), "Cache-Control": "no-store"},
        )

    if get_api_user is not None:
        @router.post(
            "/api/runtime/sites/{site_slug}/documents/{document_key}/writer-tokens",
            status_code=201,
        )
        async def create_runtime_writer_token(
            site_slug: str,
            document_key: str,
            body: RuntimeWriterTokenCreate,
            db: sqlite3.Connection = Depends(get_db),
            actor_id: str = Depends(get_api_user),
        ):
            site = _site_for_actor(db, site_slug, actor_id)
            config = _runtime_site_config(db, site_slug, document_key)
            name = body.name.strip() or "scheduled-writer"
            if len(name) > 120:
                raise HTTPException(422, "Writer token name cannot exceed 120 characters")
            token_id = secrets.token_hex(8)
            token = f"mcw_{secrets.token_urlsafe(32)}"
            now = datetime.now(timezone.utc).isoformat()
            db.execute(
                """
                INSERT INTO runtime_writer_tokens
                    (id, user_id, site_id, document_key, token_hash, token_prefix, name, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    token_id,
                    actor_id,
                    site["id"],
                    config["document_key"],
                    runtime_token_digest(token),
                    token[:12],
                    name,
                    now,
                ),
            )
            db.commit()
            return {
                "id": token_id,
                "name": name,
                "site": site_slug,
                "document": config["document_key"],
                "token": token,
                "token_prefix": token[:12],
                "created_at": now,
            }

        @router.get("/api/runtime/sites/{site_slug}/writer-tokens")
        async def list_runtime_writer_tokens(
            site_slug: str,
            db: sqlite3.Connection = Depends(get_db),
            actor_id: str = Depends(get_api_user),
        ):
            site = _site_for_actor(db, site_slug, actor_id)
            rows = db.execute(
                """
                SELECT id, document_key, token_prefix, name, created_at, revoked_at
                FROM runtime_writer_tokens
                WHERE site_id = ?
                ORDER BY created_at DESC
                """,
                (site["id"],),
            ).fetchall()
            return [dict(row) for row in rows]

        @router.delete("/api/runtime/sites/{site_slug}/writer-tokens/{token_id}")
        async def revoke_runtime_writer_token(
            site_slug: str,
            token_id: str,
            db: sqlite3.Connection = Depends(get_db),
            actor_id: str = Depends(get_api_user),
        ):
            site = _site_for_actor(db, site_slug, actor_id)
            row = db.execute(
                "SELECT id FROM runtime_writer_tokens WHERE id = ? AND site_id = ?",
                (token_id, site["id"]),
            ).fetchone()
            if not row:
                raise HTTPException(404, "Runtime writer token not found")
            now = datetime.now(timezone.utc).isoformat()
            db.execute(
                "UPDATE runtime_writer_tokens SET revoked_at = ? WHERE id = ?",
                (now, token_id),
            )
            db.commit()
            return {"id": token_id, "revoked_at": now}

    return router
