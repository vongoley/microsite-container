import hashlib
import json
import sqlite3

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app import microsites, runtime_data


@pytest.fixture()
def runtime_client(tmp_path, monkeypatch):
    db_path = tmp_path / "runtime.db"
    data_root = tmp_path / "microsites"
    monkeypatch.setattr(microsites, "DATA_ROOT", data_root)
    monkeypatch.setattr(microsites, "BLOBS_DIR", data_root / "blobs")
    monkeypatch.setattr(microsites, "TEMP_DIR", data_root / "tmp")
    monkeypatch.setattr(microsites, "ACCEL_PREFIX", "")
    microsites.init_microsite_schema(db_path)

    con = sqlite3.connect(db_path)
    con.execute("CREATE TABLE users (id TEXT PRIMARY KEY, role TEXT NOT NULL)")
    con.executemany(
        "INSERT INTO users (id, role) VALUES (?, ?)",
        [("owner-1", "admin"), ("owner-2", "admin"), ("root-1", "super_admin")],
    )
    con.commit()
    con.close()

    def get_db():
        connection = sqlite3.connect(db_path, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=5000")
        try:
            yield connection
        finally:
            connection.close()

    def get_api_actor():
        return "owner-1"

    def get_runtime_actor(request: Request):
        actor_id = request.headers.get("X-Runtime-Actor")
        site_id = request.headers.get("X-Runtime-Site")
        document_key = request.headers.get("X-Runtime-Document")
        if actor_id and (site_id or document_key):
            return runtime_data.RuntimeActor(
                user_id=actor_id,
                site_id=site_id,
                document_key=document_key,
                token_id="test-writer",
            )
        return actor_id

    app = FastAPI()
    app.include_router(microsites.create_microsite_router(get_db, get_api_actor))
    app.include_router(runtime_data.create_runtime_router(get_db, get_runtime_actor))
    with TestClient(app) as test_client:
        yield test_client, db_path


def deploy(runtime_client, slug, files, *, create_site=True):
    client, _db_path = runtime_client
    if create_site:
        response = client.post("/api/sites", json={"slug": slug, "title": slug.title()})
        assert response.status_code == 201
    manifest = {
        "entrypoint": "index.html",
        "spa_fallback": False,
        "files": [
            {
                "path": path,
                "sha256": hashlib.sha256(content).hexdigest(),
                "size": len(content),
                "content_type": content_type,
            }
            for path, content, content_type in files
        ],
    }
    response = client.post(f"/api/sites/{slug}/deployments", json=manifest)
    assert response.status_code == 201
    deployment = response.json()
    for _path, content, _content_type in files:
        digest = hashlib.sha256(content).hexdigest()
        response = client.put(
            f"/api/sites/{slug}/deployments/{deployment['id']}/blobs/{digest}",
            content=content,
        )
        assert response.status_code == 200
    finalized = client.post(
        f"/api/sites/{slug}/deployments/{deployment['id']}/finalize"
    )
    assert finalized.status_code == 200, finalized.text
    activated = client.post(
        f"/api/sites/{slug}/deployments/{deployment['id']}/activate"
    )
    assert activated.status_code == 200, activated.text
    return deployment["id"]


def runtime_site_files(seed=None, *, read="public", schema_type="object"):
    seed = seed if seed is not None else {"2026-08-25": ["shoulders", "core"]}
    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": schema_type,
    }
    config = {
        "runtimeData": {
            "documents": {
                "training-plan": {
                    "scope": "site",
                    "read": read,
                    "write": "owner",
                    "schemaVersion": 1,
                    "schema": "schemas/training-plan.schema.json",
                    "seed": "data/plan.json",
                    "maxBytes": 65536,
                }
            }
        }
    }
    return [
        ("index.html", b"<!doctype html><title>Runtime</title>", "text/html"),
        (
            "microsite.json",
            json.dumps(config).encode(),
            "application/json",
        ),
        (
            "schemas/training-plan.schema.json",
            json.dumps(schema).encode(),
            "application/schema+json",
        ),
        ("data/plan.json", json.dumps(seed).encode(), "application/json"),
    ]


def test_runtime_document_seed_save_conflict_and_schema(runtime_client):
    client, db_path = runtime_client
    deploy(runtime_client, "training", runtime_site_files())

    initial = client.get("/api/runtime/sites/training/documents/training-plan")
    assert initial.status_code == 200
    assert initial.headers["etag"] == '"rev-1"'
    assert initial.json()["revision"] == 1
    assert initial.json()["value"]["2026-08-25"] == ["shoulders", "core"]

    anonymous_write = client.put(
        "/api/runtime/sites/training/documents/training-plan",
        headers={"If-Match": '"rev-1"'},
        json={"value": {"2026-08-26": ["chest"]}},
    )
    assert anonymous_write.status_code == 401

    saved = client.put(
        "/api/runtime/sites/training/documents/training-plan",
        headers={"If-Match": '"rev-1"', "X-Runtime-Actor": "owner-1"},
        json={"value": {"2026-08-26": ["chest"]}},
    )
    assert saved.status_code == 200
    assert saved.headers["etag"] == '"rev-2"'
    assert saved.json()["revision"] == 2

    other_terminal = client.get("/api/runtime/sites/training/documents/training-plan")
    assert other_terminal.json()["value"] == {"2026-08-26": ["chest"]}
    assert other_terminal.json()["revision"] == 2

    stale = client.put(
        "/api/runtime/sites/training/documents/training-plan",
        headers={"If-Match": '"rev-1"', "X-Runtime-Actor": "owner-1"},
        json={"value": {"stale": True}},
    )
    assert stale.status_code == 409
    assert stale.json()["detail"]["currentRevision"] == 2

    invalid = client.put(
        "/api/runtime/sites/training/documents/training-plan",
        headers={"If-Match": '"rev-2"', "X-Runtime-Actor": "owner-1"},
        json={"value": ["not-an-object"]},
    )
    assert invalid.status_code == 422

    con = sqlite3.connect(db_path)
    versions = con.execute(
        "SELECT revision FROM site_document_versions ORDER BY revision"
    ).fetchall()
    con.close()
    assert versions == [(1,), (2,)]


def test_runtime_data_survives_redeployment_and_seed_is_not_reapplied(runtime_client):
    client, _db_path = runtime_client
    deploy(runtime_client, "persistent", runtime_site_files())
    saved = client.put(
        "/api/runtime/sites/persistent/documents/training-plan",
        headers={"If-Match": '"rev-1"', "X-Runtime-Actor": "owner-1"},
        json={"value": {"2026-09-01": ["back"]}},
    )
    assert saved.status_code == 200

    deploy(
        runtime_client,
        "persistent",
        runtime_site_files(seed={"replacement-seed": []}),
        create_site=False,
    )
    current = client.get("/api/runtime/sites/persistent/documents/training-plan")
    assert current.json()["revision"] == 2
    assert current.json()["value"] == {"2026-09-01": ["back"]}


def test_machine_writer_is_restricted_to_its_site_and_document(runtime_client):
    client, db_path = runtime_client
    deploy(runtime_client, "training", runtime_site_files())
    deploy(runtime_client, "other", runtime_site_files())
    con = sqlite3.connect(db_path)
    training_site_id = con.execute(
        "SELECT id FROM sites WHERE slug = 'training'"
    ).fetchone()[0]
    con.close()

    wrong_site = client.put(
        "/api/runtime/sites/other/documents/training-plan",
        headers={
            "If-Match": '"rev-1"',
            "X-Runtime-Actor": "owner-1",
            "X-Runtime-Site": training_site_id,
            "X-Runtime-Document": "training-plan",
        },
        json={"value": {"should": "fail"}},
    )
    assert wrong_site.status_code == 403

    wrong_document = client.put(
        "/api/runtime/sites/training/documents/training-plan",
        headers={
            "If-Match": '"rev-1"',
            "X-Runtime-Actor": "owner-1",
            "X-Runtime-Site": training_site_id,
            "X-Runtime-Document": "some-other-document",
        },
        json={"value": {"should": "fail"}},
    )
    assert wrong_document.status_code == 403


def test_runtime_owner_read_and_incompatible_activation(runtime_client):
    client, _db_path = runtime_client
    first_deployment = deploy(
        runtime_client,
        "private-runtime",
        runtime_site_files(read="owner"),
    )
    assert client.get(
        "/api/runtime/sites/private-runtime/documents/training-plan"
    ).status_code == 401
    assert client.get(
        "/api/runtime/sites/private-runtime/documents/training-plan",
        headers={"X-Runtime-Actor": "owner-2"},
    ).status_code == 403
    assert client.get(
        "/api/runtime/sites/private-runtime/documents/training-plan",
        headers={"X-Runtime-Actor": "owner-1"},
    ).status_code == 200

    files = runtime_site_files(seed=1, read="owner", schema_type="number")
    manifest = {
        "entrypoint": "index.html",
        "files": [
            {
                "path": path,
                "sha256": hashlib.sha256(content).hexdigest(),
                "size": len(content),
                "content_type": content_type,
            }
            for path, content, content_type in files
        ],
    }
    deployment = client.post(
        "/api/sites/private-runtime/deployments", json=manifest
    ).json()
    for _path, content, _content_type in files:
        digest = hashlib.sha256(content).hexdigest()
        client.put(
            f"/api/sites/private-runtime/deployments/{deployment['id']}/blobs/{digest}",
            content=content,
        )
    assert client.post(
        f"/api/sites/private-runtime/deployments/{deployment['id']}/finalize"
    ).status_code == 200
    incompatible = client.post(
        f"/api/sites/private-runtime/deployments/{deployment['id']}/activate"
    )
    assert incompatible.status_code == 409
    site = client.get("/api/sites/private-runtime").json()
    assert site["active_deployment_id"] == first_deployment


def test_runtime_sdk_is_served_without_credentials(runtime_client):
    client, _db_path = runtime_client
    response = client.get("/_microsite/sdk/v1.js")
    assert response.status_code == 200
    assert "MicrositeData" in response.text
    assert "API_KEY" not in response.text


def test_finalize_rejects_invalid_runtime_declaration(runtime_client):
    client, _db_path = runtime_client
    assert client.post(
        "/api/sites", json={"slug": "invalid-runtime", "title": "Invalid"}
    ).status_code == 201
    config = {
        "runtimeData": {
            "documents": {
                "plan": {
                    "scope": "site",
                    "read": "public",
                    "write": "owner",
                    "schema": "schemas/missing.schema.json",
                }
            }
        }
    }
    files = [
        ("index.html", b"<!doctype html>", "text/html"),
        ("microsite.json", json.dumps(config).encode(), "application/json"),
    ]
    manifest = {
        "entrypoint": "index.html",
        "files": [
            {
                "path": path,
                "sha256": hashlib.sha256(content).hexdigest(),
                "size": len(content),
                "content_type": content_type,
            }
            for path, content, content_type in files
        ],
    }
    deployment = client.post(
        "/api/sites/invalid-runtime/deployments", json=manifest
    ).json()
    for _path, content, _content_type in files:
        digest = hashlib.sha256(content).hexdigest()
        client.put(
            f"/api/sites/invalid-runtime/deployments/{deployment['id']}/blobs/{digest}",
            content=content,
        )
    response = client.post(
        f"/api/sites/invalid-runtime/deployments/{deployment['id']}/finalize"
    )
    assert response.status_code == 400
    assert "referenced asset is missing" in response.json()["detail"]
