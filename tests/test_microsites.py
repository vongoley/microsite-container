import hashlib
import importlib.util
import io
import json
import os
import re
from argparse import Namespace
import sqlite3
import zipfile
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import microsites


def source_zip(files=None):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        for path, content in (files or {"index.html": b"source"}).items():
            archive.writestr(path, content)
    return output.getvalue()


def source_descriptor(content):
    return {
        "sha256": hashlib.sha256(content).hexdigest(),
        "size": len(content),
        "format": "zip",
    }


def upload_source(client, slug, deployment_id, content):
    digest = hashlib.sha256(content).hexdigest()
    response = client.put(
        f"/api/sites/{slug}/deployments/{deployment_id}/blobs/{digest}",
        content=content,
    )
    assert response.status_code == 200


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    data_root = tmp_path / "microsites"
    monkeypatch.setattr(microsites, "DATA_ROOT", data_root)
    monkeypatch.setattr(microsites, "BLOBS_DIR", data_root / "blobs")
    monkeypatch.setattr(microsites, "TEMP_DIR", data_root / "tmp")
    monkeypatch.setattr(microsites, "ACCEL_PREFIX", "")
    microsites.init_microsite_schema(db_path)

    con = sqlite3.connect(db_path)
    con.execute("CREATE TABLE users (id TEXT PRIMARY KEY, role TEXT NOT NULL)")
    con.execute("INSERT INTO users (id, role) VALUES ('owner-1', 'admin')")
    con.commit()
    con.close()

    def get_db():
        connection = sqlite3.connect(db_path, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
        finally:
            connection.close()

    def get_actor():
        return "owner-1"

    app = FastAPI()
    app.include_router(microsites.create_microsite_router(get_db, get_actor))
    app.include_router(microsites.create_data_router(get_db))
    with TestClient(app) as test_client:
        yield test_client


def test_rejects_unsafe_asset_paths():
    for path in ("", "/index.html", "../index.html", "a/../b", "a\\b"):
        with pytest.raises(ValueError):
            microsites.normalize_asset_path(path)


def test_new_deployment_requires_source_snapshot(client):
    content = b"<!doctype html>"
    client.post("/api/sites", json={"slug": "source-required", "title": "Source Required"})
    response = client.post(
        "/api/sites/source-required/deployments",
        json={
            "entrypoint": "index.html",
            "files": [
                {
                    "path": "index.html",
                    "sha256": hashlib.sha256(content).hexdigest(),
                    "size": len(content),
                }
            ],
        },
    )
    assert response.status_code == 422


def test_manifest_upload_finalize_activate_and_range(client):
    index = b"<!doctype html><h1>Vietnamese learning</h1>"
    audio = b"0123456789"
    files = [
        ("index.html", index, "text/html"),
        ("audio/sample.mp3", audio, "audio/mpeg"),
    ]
    source = source_zip({"index.html": index, "src/app.js": b"source logic"})
    manifest = {
        "entrypoint": "index.html",
        "spa_fallback": True,
        "files": [
            {
                "path": path,
                "sha256": hashlib.sha256(content).hexdigest(),
                "size": len(content),
                "content_type": content_type,
            }
            for path, content, content_type in files
        ],
        "source": source_descriptor(source),
    }

    response = client.post("/api/sites", json={"slug": "vietnamese", "title": "Vietnamese"})
    assert response.status_code == 201
    response = client.post("/api/sites/vietnamese/deployments", json=manifest)
    assert response.status_code == 201
    deployment = response.json()
    assert len(deployment["missing_blobs"]) == 2
    assert deployment["missing_source_blob"] == hashlib.sha256(source).hexdigest()

    for _path, content, _content_type in files:
        digest = hashlib.sha256(content).hexdigest()
        response = client.put(
            f"/api/sites/vietnamese/deployments/{deployment['id']}/blobs/{digest}",
            content=content,
        )
        assert response.status_code == 200
    upload_source(client, "vietnamese", deployment["id"], source)

    response = client.post(f"/api/sites/vietnamese/deployments/{deployment['id']}/finalize")
    assert response.status_code == 200
    assert response.json()["state"] == "ready"
    response = client.post(f"/api/sites/vietnamese/deployments/{deployment['id']}/activate")
    assert response.status_code == 200
    assert response.json()["state"] == "active"

    response = client.get("/sites/vietnamese/")
    assert response.status_code == 200
    assert response.content == index
    response = client.get("/sites/vietnamese/audio/sample.mp3", headers={"Range": "bytes=2-5"})
    assert response.status_code == 206
    assert response.content == b"2345"
    assert response.headers["accept-ranges"] == "bytes"

    response = client.get("/sites/vietnamese/lesson/one")
    assert response.status_code == 200
    assert response.content == index


def test_second_deployment_reuses_existing_blob(client):
    content = b"<!doctype html><title>same</title>"
    digest = hashlib.sha256(content).hexdigest()
    source = source_zip({"index.html": content})
    manifest = {
        "entrypoint": "index.html",
        "files": [{"path": "index.html", "sha256": digest, "size": len(content)}],
        "source": source_descriptor(source),
    }
    client.post("/api/sites", json={"slug": "reuse", "title": "Reuse"})
    first = client.post("/api/sites/reuse/deployments", json=manifest).json()
    client.put(f"/api/sites/reuse/deployments/{first['id']}/blobs/{digest}", content=content)
    upload_source(client, "reuse", first["id"], source)
    client.post(f"/api/sites/reuse/deployments/{first['id']}/finalize")
    client.post(f"/api/sites/reuse/deployments/{first['id']}/activate")

    second = client.post("/api/sites/reuse/deployments", json=manifest)
    assert second.status_code == 201
    assert second.json()["missing_blobs"] == []


def test_superseded_deployment_can_be_activated_for_rollback(client):
    client.post("/api/sites", json={"slug": "rollback", "title": "Rollback"})
    deployment_ids = []
    for version in (b"version one", b"version two"):
        digest = hashlib.sha256(version).hexdigest()
        source = source_zip({"index.html": version})
        manifest = {
            "entrypoint": "index.html",
            "files": [{"path": "index.html", "sha256": digest, "size": len(version)}],
            "source": source_descriptor(source),
        }
        deployment = client.post("/api/sites/rollback/deployments", json=manifest).json()
        client.put(
            f"/api/sites/rollback/deployments/{deployment['id']}/blobs/{digest}",
            content=version,
        )
        upload_source(client, "rollback", deployment["id"], source)
        client.post(f"/api/sites/rollback/deployments/{deployment['id']}/finalize")
        client.post(f"/api/sites/rollback/deployments/{deployment['id']}/activate")
        deployment_ids.append(deployment["id"])

    assert client.get("/sites/rollback/").content == b"version two"
    rollback = client.post(f"/api/sites/rollback/deployments/{deployment_ids[0]}/activate")
    assert rollback.status_code == 200
    assert rollback.json()["state"] == "active"
    assert client.get("/sites/rollback/").content == b"version one"


def test_skill_cli_builds_manifest(tmp_path):
    module_path = Path(__file__).parents[1] / "app" / "skill" / "deploy.py"
    spec = importlib.util.spec_from_file_location("microsite_deploy_cli", module_path)
    deploy_cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(deploy_cli)

    (tmp_path / "index.html").write_text("<h1>Hello</h1>", encoding="utf-8")
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "data.json").write_text(json.dumps({"ok": True}), encoding="utf-8")
    (tmp_path / ".microsite-origin.json").write_text(
        json.dumps({"slug": "local-only"}), encoding="utf-8"
    )
    manifest, sources = deploy_cli.build_manifest(tmp_path, "index.html", True)
    assert len(manifest["files"]) == 2
    assert manifest["entrypoint"] == "index.html"
    assert len(sources) == 2
    assert ".microsite-origin.json" not in {item["path"] for item in manifest["files"]}


def test_skill_cli_runs_complete_deployment_workflow(client, tmp_path, monkeypatch):
    module_path = Path(__file__).parents[1] / "app" / "skill" / "deploy.py"
    spec = importlib.util.spec_from_file_location("microsite_deploy_workflow", module_path)
    deploy_cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(deploy_cli)

    site_dir = tmp_path / "site"
    site_dir.mkdir()
    (site_dir / "index.html").write_text("<h1>Skill deploy</h1>", encoding="utf-8")
    (site_dir / "audio").mkdir()
    (site_dir / "audio" / "sample.mp3").write_bytes(b"audio bytes")

    monkeypatch.setattr(deploy_cli, "load_config", lambda: ("http://testserver", "test-token"))

    def fake_api_json(_base_url, _api_key, method, path, body=None):
        response = client.request(method, path, json=body)
        if response.status_code >= 400:
            raise deploy_cli.CliError(str(response.json()))
        return response.json()

    def fake_upload(_base_url, _api_key, site_slug, deployment_id, digest, file_path, _size):
        response = client.put(
            f"/api/sites/{site_slug}/deployments/{deployment_id}/blobs/{digest}",
            content=file_path.read_bytes(),
        )
        assert response.status_code == 200
        return response.json()

    monkeypatch.setattr(deploy_cli, "api_json", fake_api_json)
    monkeypatch.setattr(deploy_cli, "upload_blob", fake_upload)
    result = deploy_cli.command_deploy(
        Namespace(
            source_dir=str(site_dir),
            publish_dir=str(site_dir),
            entrypoint="index.html",
            spa_fallback=True,
            slug="Skill-Site",
            title="Skill Site",
        )
    )

    assert result["state"] == "active"
    assert result["url"] == "/sites/skill-site/"
    assert client.get("/sites/skill-site/audio/sample.mp3").content == b"audio bytes"

    def fake_download(_base_url, _api_key, path, destination):
        response = client.get(path)
        assert response.status_code == 200
        destination.write_bytes(response.content)
        return dict(response.headers)

    monkeypatch.setattr(deploy_cli, "download_api_file", fake_download)
    restored_dir = tmp_path / "restored"
    pulled = deploy_cli.command_pull(Namespace(slug="Skill-Site", out=str(restored_dir)))
    assert pulled["source_mode"] == "source"
    assert (restored_dir / "index.html").read_text(encoding="utf-8") == "<h1>Skill deploy</h1>"
    assert not (restored_dir / ".git").exists()
    origin = json.loads((restored_dir / ".microsite-origin.json").read_text(encoding="utf-8"))
    assert origin["slug"] == "skill-site"
    assert origin["deploymentId"] == result["id"]


def test_source_archive_excludes_secrets_and_generated_directories(tmp_path):
    module_path = Path(__file__).parents[1] / "app" / "skill" / "deploy.py"
    spec = importlib.util.spec_from_file_location("microsite_source_archive", module_path)
    deploy_cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(deploy_cli)

    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.ts").write_text("export const ok = true", encoding="utf-8")
    (tmp_path / ".env").write_text("SECRET=do-not-upload", encoding="utf-8")
    (tmp_path / ".env.example").write_text("SECRET=", encoding="utf-8")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "package.js").write_text("generated", encoding="utf-8")
    archive_path, descriptor = deploy_cli.build_source_archive(tmp_path)
    try:
        assert descriptor["format"] == "zip"
        with zipfile.ZipFile(archive_path) as archive:
            names = set(archive.namelist())
        assert "src/app.ts" in names
        assert ".env.example" in names
        assert ".env" not in names
        assert "node_modules/package.js" not in names
    finally:
        archive_path.unlink(missing_ok=True)


def test_skill_cli_runtime_commands_use_scoped_token_and_revision(tmp_path, monkeypatch):
    module_path = Path(__file__).parents[1] / "app" / "skill" / "deploy.py"
    spec = importlib.util.spec_from_file_location("microsite_runtime_cli", module_path)
    deploy_cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(deploy_cli)
    value_path = tmp_path / "latest.json"
    value_path.write_text(json.dumps({"as_of": "2026-09-03"}), encoding="utf-8")
    monkeypatch.setattr(
        deploy_cli,
        "load_runtime_config",
        lambda _explicit=None: ("https://microsites.example.com", "writer-token"),
    )
    calls = []

    def fake_runtime_json(base_url, token, method, path, body=None, *, revision=None):
        calls.append((base_url, token, method, path, body, revision))
        if method == "GET":
            return {"value": {"old": True}, "revision": 7}
        return {"value": body["value"], "revision": 8}

    monkeypatch.setattr(deploy_cli, "runtime_json", fake_runtime_json)
    result = deploy_cli.command_runtime_put(
        Namespace(
            slug="Investment-Report",
            document="latest-analysis",
            file=str(value_path),
            revision=None,
            token=None,
        )
    )

    assert result["revision"] == 8
    assert calls[0][2:] == (
        "GET",
        "/api/runtime/sites/investment-report/documents/latest-analysis",
        None,
        None,
    )
    assert calls[1][2:] == (
        "PUT",
        "/api/runtime/sites/investment-report/documents/latest-analysis",
        {"value": {"as_of": "2026-09-03"}},
        7,
    )

    output_path = tmp_path / "downloaded" / "latest.json"
    downloaded = deploy_cli.command_runtime_get(
        Namespace(
            slug="Investment-Report",
            document="latest-analysis",
            token=None,
            out=str(output_path),
        )
    )
    assert downloaded["revision"] == 7
    assert json.loads(output_path.read_text(encoding="utf-8")) == {"old": True}


def test_skill_cli_runtime_token_create_uses_deployment_credentials(monkeypatch):
    module_path = Path(__file__).parents[1] / "app" / "skill" / "deploy.py"
    spec = importlib.util.spec_from_file_location("microsite_writer_token_cli", module_path)
    deploy_cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(deploy_cli)
    monkeypatch.setattr(
        deploy_cli,
        "load_config",
        lambda: ("https://microsites.example.com", "deployment-token"),
    )
    captured = {}

    def fake_api_json(base_url, api_key, method, path, body=None):
        captured.update(
            base_url=base_url,
            api_key=api_key,
            method=method,
            path=path,
            body=body,
        )
        return {"token": "mcw_secret"}

    monkeypatch.setattr(deploy_cli, "api_json", fake_api_json)
    result = deploy_cli.command_runtime_token_create(
        Namespace(
            slug="investment-report",
            document="latest-analysis",
            name="daily-job",
        )
    )

    assert result == {"token": "mcw_secret"}
    assert captured == {
        "base_url": "https://microsites.example.com",
        "api_key": "deployment-token",
        "method": "POST",
        "path": "/api/runtime/sites/investment-report/documents/latest-analysis/writer-tokens",
        "body": {"name": "daily-job"},
    }


def test_real_vietnamese_page_full_deployment(client):
    source = os.environ.get("VIETNAMESE_LEARNING_HTML")
    if not source:
        pytest.skip("set VIETNAMESE_LEARNING_HTML to run the real 9.4 MB fixture")
    html = Path(source).read_bytes()
    decoded = html.decode("utf-8")
    keys = re.findall(r'data-audio-key=["\x27]([^"\x27]+)', decoded)
    kinds = re.findall(r'data-audio-kind=["\x27]([^"\x27]+)', decoded)
    audio_manifest = json.dumps(
        {
            "version": 1,
            "slots": [
                {"key": key, "kind": kind, "src": None}
                for key, kind in zip(keys, kinds)
            ],
        },
        ensure_ascii=False,
    ).encode("utf-8")
    assets = [
        ("index.html", html, "text/html"),
        ("audio-manifest.json", audio_manifest, "application/json"),
    ]
    source = source_zip({"index.html": html, "audio-manifest.json": audio_manifest})
    manifest = {
        "entrypoint": "index.html",
        "spa_fallback": True,
        "files": [
            {
                "path": path,
                "sha256": hashlib.sha256(content).hexdigest(),
                "size": len(content),
                "content_type": content_type,
            }
            for path, content, content_type in assets
        ],
        "source": source_descriptor(source),
    }

    assert len(keys) == 6_895
    assert len(html) == 9_427_088
    client.post("/api/sites", json={"slug": "vietnamese-real", "title": "Vietnamese Real"})
    deployment = client.post("/api/sites/vietnamese-real/deployments", json=manifest).json()
    assert len(deployment["missing_blobs"]) == 2
    for _path, content, _content_type in assets:
        digest = hashlib.sha256(content).hexdigest()
        response = client.put(
            f"/api/sites/vietnamese-real/deployments/{deployment['id']}/blobs/{digest}",
            content=content,
        )
        assert response.status_code == 200
    upload_source(client, "vietnamese-real", deployment["id"], source)
    assert client.post(
        f"/api/sites/vietnamese-real/deployments/{deployment['id']}/finalize"
    ).json()["state"] == "ready"
    assert client.post(
        f"/api/sites/vietnamese-real/deployments/{deployment['id']}/activate"
    ).json()["state"] == "active"
    assert client.get("/sites/vietnamese-real/").content == html
    ranged = client.get(
        "/sites/vietnamese-real/audio-manifest.json", headers={"Range": "bytes=0-127"}
    )
    assert ranged.status_code == 206
    assert ranged.content == audio_manifest[:128]
