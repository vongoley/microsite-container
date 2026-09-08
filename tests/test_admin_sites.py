import hashlib
import io
import json
import re
import sqlite3
import uuid
import zipfile

import pytest
from fastapi.testclient import TestClient

from app import main, microsites


@pytest.fixture()
def admin_client(tmp_path, monkeypatch):
    db_path = tmp_path / "admin-sites.db"
    uploads = tmp_path / "uploads"
    data_root = tmp_path / "microsites"
    monkeypatch.setattr(main, "DB_PATH", db_path)
    monkeypatch.setattr(main, "UPLOADS_DIR", uploads)
    monkeypatch.setattr(microsites, "DATA_ROOT", data_root)
    monkeypatch.setattr(microsites, "BLOBS_DIR", data_root / "blobs")
    monkeypatch.setattr(microsites, "TEMP_DIR", data_root / "tmp")
    monkeypatch.setattr(microsites, "ACCEL_PREFIX", "")

    with TestClient(main.app) as client:
        con = sqlite3.connect(db_path)
        con.row_factory = sqlite3.Row
        owner = con.execute(
            "SELECT id, username FROM users WHERE role = 'super_admin' LIMIT 1"
        ).fetchone()
        token = "test-site-deploy-token"
        con.execute(
            """
            INSERT INTO user_tokens (id, user_id, token, name, created_at, is_active)
            VALUES (?, ?, ?, 'tests', '2026-08-25T00:00:00+00:00', 1)
            """,
            (uuid.uuid4().hex[:8], owner["id"], token),
        )
        con.commit()
        con.close()
        client.cookies.set(
            main.SESSION_COOKIE,
            main.make_session_token(owner["id"], owner["username"]),
        )
        yield client, db_path, token, owner["id"]


def deploy_runtime_site(client: TestClient, token: str):
    assets = {
        "index.html": b"<!doctype html><title>Export me</title>",
        "assets/app.js": b"console.log('export');",
        "data/settings.json": json.dumps({"theme": "green"}).encode(),
        "microsite.json": json.dumps(
            {
                "runtimeData": {
                    "documents": {
                        "settings": {
                            "scope": "site",
                            "read": "public",
                            "write": "owner",
                            "schemaVersion": 1,
                            "seed": "data/settings.json",
                            "maxBytes": 4096,
                        }
                    }
                }
            }
        ).encode(),
    }
    headers = {"Authorization": f"Bearer {token}"}
    source_buffer = io.BytesIO()
    with zipfile.ZipFile(source_buffer, "w") as archive:
        for path, content in assets.items():
            archive.writestr(path, content)
    source = source_buffer.getvalue()
    created = client.post(
        "/api/sites",
        json={"slug": "export-site", "title": "Export Site"},
        headers=headers,
    )
    assert created.status_code == 201
    site = created.json()
    manifest = {
        "entrypoint": "index.html",
        "spa_fallback": True,
        "files": [
            {
                "path": path,
                "sha256": hashlib.sha256(content).hexdigest(),
                "size": len(content),
                "content_type": "application/json" if path.endswith(".json") else None,
            }
            for path, content in assets.items()
        ],
        "source": {
            "sha256": hashlib.sha256(source).hexdigest(),
            "size": len(source),
            "format": "zip",
        },
    }
    deployment = client.post(
        "/api/sites/export-site/deployments", json=manifest, headers=headers
    ).json()
    for content in assets.values():
        digest = hashlib.sha256(content).hexdigest()
        response = client.put(
            f"/api/sites/export-site/deployments/{deployment['id']}/blobs/{digest}",
            content=content,
            headers=headers,
        )
        assert response.status_code == 200
    source_response = client.put(
        f"/api/sites/export-site/deployments/{deployment['id']}/blobs/"
        f"{hashlib.sha256(source).hexdigest()}",
        content=source,
        headers=headers,
    )
    assert source_response.status_code == 200
    assert client.post(
        f"/api/sites/export-site/deployments/{deployment['id']}/finalize",
        headers=headers,
    ).status_code == 200
    assert client.post(
        f"/api/sites/export-site/deployments/{deployment['id']}/activate",
        headers=headers,
    ).status_code == 200
    return site, deployment


def test_admin_lists_deployed_sites_without_manual_upload(admin_client):
    client, _db_path, token, _owner_id = admin_client
    site, deployment = deploy_runtime_site(client, token)

    response = client.get("/admin")
    assert response.status_code == 200
    assert "站点管理" in response.text
    assert "Export Site" in response.text
    assert "/sites/export-site/" in response.text
    assert deployment["id"] in response.text
    assert "下载 ZIP" in response.text
    assert "部署时间从" in response.text
    assert "上传 HTML" not in response.text
    assert "/admin/upload" not in response.text
    assert "超过 50 MB" in response.text
    assert f'/admin/sites/{site["id"]}/download' in response.text


def test_site_zip_contains_assets_runtime_data_and_history(admin_client):
    client, _db_path, token, _owner_id = admin_client
    site, deployment = deploy_runtime_site(client, token)

    response = client.get(f'/admin/sites/{site["id"]}/download')
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    assert "export-site-export.zip" in response.headers["content-disposition"]
    assert int(response.headers["x-microsite-export-size"]) == len(response.content)

    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        names = set(archive.namelist())
        assert "export-site/site/index.html" in names
        assert "export-site/site/assets/app.js" in names
        assert "export-site/site/data/settings.json" in names
        assert "export-site/export.json" in names
        assert "export-site/runtime-data/documents/settings.json" in names
        assert "export-site/runtime-data/history/settings/rev-1.json" in names

        metadata = json.loads(archive.read("export-site/export.json"))
        document = json.loads(
            archive.read("export-site/runtime-data/documents/settings.json")
        )
        history = json.loads(
            archive.read("export-site/runtime-data/history/settings/rev-1.json")
        )
        assert metadata["deployment"]["id"] == deployment["id"]
        assert document["value"] == {"theme": "green"}
        assert document["revision"] == 1
        assert history["value"] == {"theme": "green"}


def test_non_owner_cannot_download_site_export(admin_client):
    client, db_path, token, _owner_id = admin_client
    site, _deployment = deploy_runtime_site(client, token)

    con = sqlite3.connect(db_path)
    other_id = "other-admin"
    con.execute(
        """
        INSERT INTO users (id, username, password_hash, role, created_at, is_active)
        VALUES (?, 'other', ?, 'admin', '2026-08-25T00:00:00+00:00', 1)
        """,
        (other_id, main.hash_password("password")),
    )
    con.commit()
    con.close()
    client.cookies.set(main.SESSION_COOKIE, main.make_session_token(other_id, "other"))

    response = client.get(f'/admin/sites/{site["id"]}/download')
    assert response.status_code == 403


def test_api_source_download_falls_back_to_active_artifacts_for_legacy_site(admin_client):
    client, db_path, token, _owner_id = admin_client
    _site, deployment = deploy_runtime_site(client, token)
    con = sqlite3.connect(db_path)
    con.execute(
        """
        UPDATE deployments
        SET source_blob_sha256 = NULL, source_size = NULL, source_format = NULL
        WHERE id = ?
        """,
        (deployment["id"],),
    )
    con.commit()
    con.close()

    response = client.get(
        "/api/sites/export-site/source",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    assert response.headers["x-microsite-source-mode"] == "artifact-recovery"
    assert response.headers["x-microsite-deployment-id"] == deployment["id"]
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert archive.read("index.html") == b"<!doctype html><title>Export me</title>"
        assert "runtime-data/documents/settings.json" not in archive.namelist()


def test_large_site_estimate_triggers_confirmation_threshold(admin_client):
    client, db_path, token, _owner_id = admin_client
    _site, deployment = deploy_runtime_site(client, token)
    con = sqlite3.connect(db_path)
    con.execute(
        "UPDATE deployments SET total_size = ? WHERE id = ?",
        (main.SITE_EXPORT_WARNING_BYTES + 1, deployment["id"]),
    )
    con.commit()
    con.close()

    response = client.get("/admin")
    assert response.status_code == 200
    sizes = [int(value) for value in re.findall(r'data-size="(\d+)"', response.text)]
    assert sizes and max(sizes) > main.SITE_EXPORT_WARNING_BYTES
    assert "导出包较大" in response.text


def test_document_scoped_writer_token_can_update_and_be_revoked(admin_client):
    client, db_path, deploy_token, _owner_id = admin_client
    deploy_runtime_site(client, deploy_token)
    deploy_headers = {"Authorization": f"Bearer {deploy_token}"}

    created = client.post(
        "/api/runtime/sites/export-site/documents/settings/writer-tokens",
        headers=deploy_headers,
        json={"name": "daily-investment-report"},
    )
    assert created.status_code == 201, created.text
    writer = created.json()
    assert writer["token"].startswith("mcw_")
    assert writer["document"] == "settings"

    con = sqlite3.connect(db_path)
    stored = con.execute(
        "SELECT token_hash, token_prefix FROM runtime_writer_tokens WHERE id = ?",
        (writer["id"],),
    ).fetchone()
    con.close()
    assert stored is not None
    assert stored[0] == main.runtime_token_digest(writer["token"])
    assert stored[0] != writer["token"]
    assert stored[1] == writer["token_prefix"]

    con = sqlite3.connect(db_path)
    other_id = "runtime-token-outsider"
    con.execute(
        """
        INSERT INTO users (id, username, password_hash, role, created_at, is_active)
        VALUES (?, 'runtime-outsider', ?, 'admin', '2026-09-03T00:00:00+00:00', 1)
        """,
        (other_id, main.hash_password("password")),
    )
    con.execute(
        """
        INSERT INTO user_tokens (id, user_id, token, name, created_at, is_active)
        VALUES ('outsider-token-id', ?, 'outsider-deploy-token', 'tests',
                '2026-09-03T00:00:00+00:00', 1)
        """,
        (other_id,),
    )
    con.commit()
    con.close()
    forbidden = client.post(
        "/api/runtime/sites/export-site/documents/settings/writer-tokens",
        headers={"Authorization": "Bearer outsider-deploy-token"},
        json={"name": "should-not-exist"},
    )
    assert forbidden.status_code == 403

    client.cookies.clear()
    writer_headers = {
        "Authorization": f"Bearer {writer['token']}",
        "If-Match": '"rev-1"',
    }
    updated = client.put(
        "/api/runtime/sites/export-site/documents/settings",
        headers=writer_headers,
        json={"value": {"as_of": "2026-09-03", "signal": "hold"}},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["revision"] == 2

    listed = client.get(
        "/api/runtime/sites/export-site/writer-tokens",
        headers=deploy_headers,
    )
    assert listed.status_code == 200
    assert listed.json()[0]["token_prefix"] == writer["token_prefix"]
    assert "token" not in listed.json()[0]

    revoked = client.delete(
        f"/api/runtime/sites/export-site/writer-tokens/{writer['id']}",
        headers=deploy_headers,
    )
    assert revoked.status_code == 200
    denied = client.put(
        "/api/runtime/sites/export-site/documents/settings",
        headers={
            "Authorization": f"Bearer {writer['token']}",
            "If-Match": '"rev-2"',
        },
        json={"value": {"as_of": "2026-09-04"}},
    )
    assert denied.status_code == 401


def test_admin_history_orders_versions_and_marks_current(admin_client):
    client, db_path, token, owner_id = admin_client
    site, deployment = deploy_runtime_site(client, token)
    with sqlite3.connect(db_path) as con:
        for version_id, state, created_at in (
            ('old-version', 'superseded', '2020-01-01T00:00:00'),
            ('next-version', 'staging', '2099-01-01T00:00:00'),
        ):
            con.execute(
                """INSERT INTO deployments
                   (id, site_id, state, entrypoint, file_count, total_size, created_at)
                   VALUES (?, ?, ?, 'index.html', 1, 100, ?)""",
                (version_id, site['id'], state, created_at),
            )
    response = client.get(f'/admin/sites/{site["id"]}/history')
    assert response.status_code == 200
    versions = response.json()['deployments']
    assert [v['id'] for v in versions] == ['next-version', deployment['id'], 'old-version']
    assert [v['current'] for v in versions] == [False, True, False]
    assert versions[0]['url'] is None
    assert versions[-1]['url'] == '/_deployments/old-version/'
    assert client.get('/admin/sites/missing/history').status_code == 404
    with sqlite3.connect(db_path) as con:
        con.execute("UPDATE users SET role = 'admin' WHERE id = ?", (owner_id,))
    assert client.get(f'/admin/sites/{site["id"]}/history').status_code == 200
    with sqlite3.connect(db_path) as con:
        con.execute("UPDATE sites SET owner_id = 'someone-else' WHERE id = ?", (site['id'],))
    assert client.get(f'/admin/sites/{site["id"]}/history').status_code == 403
    client.cookies.clear()
    assert client.get(f'/admin/sites/{site["id"]}/history', follow_redirects=False).status_code in (302, 303, 401)


def test_admin_history_empty_site(admin_client):
    client, _db_path, token, _owner_id = admin_client
    site = client.post('/api/sites', json={'slug': 'empty-history', 'title': 'Empty'},
                       headers={'Authorization': f'Bearer {token}'}).json()
    response = client.get(f'/admin/sites/{site["id"]}/history')
    assert response.status_code == 200
    assert response.json() == {'deployments': []}


def test_session_renewal_and_site_login_return(admin_client):
    import time
    import hmac
    client, db_path, _token, owner_id = admin_client
    with sqlite3.connect(db_path) as con:
        username = con.execute('SELECT username FROM users WHERE id=?', (owner_id,)).fetchone()[0]
        con.execute('UPDATE users SET password_hash=? WHERE id=?', (main.hash_password('login-test'), owner_id))
    payload = f'{owner_id}:{username}:{int(time.time()) - 13 * 3600}'
    signature = hmac.new(main.SESSION_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
    client.cookies.set(main.SESSION_COOKIE, f'{payload}:{signature}')
    response = client.get('/api/session')
    assert response.json()['authenticated'] is True
    assert 'Max-Age=' + str(main.SESSION_TTL_HOURS * 3600) in response.headers['set-cookie']
    assert 'no-store' in response.headers['cache-control']
    client.cookies.clear()
    assert client.get('/api/session').json()['authenticated'] is False
    target = '/sites/sample-site/?month=2026-09'
    response = client.post('/admin/login', data={'username': username, 'password': 'login-test', 'redirect_to': target}, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers['location'] == target
    for bad in ['https://evil.example/', '//evil.example/', '/sites/\\evil', '/admin/logout']:
        assert main._safe_login_next(bad) == '/admin'


def test_expired_session_is_not_renewed(admin_client):
    import time
    import hmac
    client, _db_path, _token, owner_id = admin_client
    payload = f'{owner_id}:admin:{int(time.time()) - main.SESSION_TTL_HOURS * 3600 - 60}'
    signature = hmac.new(main.SESSION_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
    client.cookies.set(main.SESSION_COOKIE, f'{payload}:{signature}')
    response = client.get('/api/session')
    assert response.json()['authenticated'] is False
    assert 'set-cookie' not in response.headers


def test_all_anonymous_site_access_requires_login(admin_client):
    client, _db, token, _owner = admin_client
    deploy_runtime_site(client, token)
    client.cookies.clear()
    assert client.get('/sites/export-site/', follow_redirects=False).status_code == 303
    client.cookies.set(main.SESSION_HISTORY_COOKIE, '1')
    response = client.get('/sites/export-site/?month=2026-09', follow_redirects=False)
    assert response.status_code == 303
    assert 'next=%2Fsites%2Fexport-site%2F%3Fmonth%3D2026-09' in response.headers['location']
    assert client.get('/api/session').json()['login_required'] is True
    assert client.get('/api/runtime/sites/export-site/documents/settings').status_code == 401


def test_share_is_site_document_month_scoped_and_read_only(admin_client):
    client, db_path, token, owner_id = admin_client
    site, deployment = deploy_runtime_site(client, token)
    with sqlite3.connect(db_path) as db:
        db.execute("UPDATE site_documents SET value_json=? WHERE site_id=?", (json.dumps({'2026-09-01': ['chest'], '2026-08-01': ['back']}), site['id']))
    result = client.post('/api/sites/export-site/share-links', json={'document': 'settings', 'month': '2026-09'})
    assert result.status_code == 201
    url = result.json()['url']
    share_token = url.split('/')[2]
    client.cookies.clear()
    assert client.get(url).status_code == 200
    for path in ['/sites/export-site/', '/sites/export-site/?view=share', f"/_deployments/{deployment['id']}/"]:
        assert client.get(path, follow_redirects=False).status_code == 303
    assert client.get(f'/share/{share_token}/assets/app.js').status_code == 200
    assert client.get(f'/share/{share_token}/data/settings.json').status_code == 404
    assert client.get('/share/invalid/').status_code == 404
    headers = {'X-Microsite-Share': share_token}
    endpoint = '/api/runtime/sites/export-site/documents/settings'
    assert client.get(endpoint).status_code == 401
    assert client.get(endpoint, headers={'Authorization': 'Bearer invalid'}).status_code == 401
    data = client.get(endpoint, headers=headers)
    assert data.status_code == 200
    assert data.json()['value'] == {'2026-09-01': ['chest']}
    assert client.get('/api/runtime/sites/other-site/documents/settings', headers=headers).status_code == 401
    assert client.put(endpoint, headers={**headers, 'If-Match': '"rev-1"'}, json={'value': {}}).status_code == 401
    assert client.post('/api/sites/export-site/share-links', json={'document':'settings','month':'2026-09'}).status_code == 401


def test_site_permission_modes_and_revocation(admin_client):
    client, db_path, token, owner_id = admin_client
    site, deployment = deploy_runtime_site(client, token)
    url = f'/admin/sites/{site["id"]}/permissions'
    assert client.get(url).json()['visibility'] == 'users_all'
    with sqlite3.connect(db_path) as db:
        db.execute("INSERT INTO users (id, username, password_hash, role, created_at, is_active) VALUES ('viewer', 'viewer', '', 'admin', '2026-09-07', 1)")
    owner_cookie = main.make_session_token(owner_id, 'admin')
    def as_owner():
        client.cookies.clear()
        client.cookies.set(main.SESSION_COOKIE, owner_cookie)
    def as_viewer():
        client.cookies.clear()
        client.cookies.set(main.SESSION_COOKIE, main.make_session_token('viewer', 'viewer'))
    def set_mode(mode, **kw):
        as_owner()
        r = client.put(url, json={'visibility': mode, **kw})
        assert r.status_code == 200, r.text
    set_mode('private')
    as_viewer()
    assert client.get('/sites/export-site/').status_code == 403
    assert client.get(f'/_deployments/{deployment["id"]}/').status_code == 403
    assert client.get('/api/runtime/sites/export-site/documents/settings').status_code == 403
    assert client.put(url, json={'visibility':'public'}).status_code == 403
    set_mode('users_specific', allowed_user_ids=['viewer'])
    as_viewer()
    assert client.get('/sites/export-site/').status_code == 200
    assert client.put('/api/runtime/sites/export-site/documents/settings', json={'value':{}}, headers={'If-Match':'"rev-1"'}).status_code == 403
    set_mode('public')
    client.cookies.clear()
    assert client.get('/sites/export-site/').status_code == 200
    assert client.get('/api/runtime/sites/export-site/documents/settings').status_code == 200
    assert client.get('/api/session?site=export-site').json()['login_required'] is False
    set_mode('password', password='password-123')
    client.cookies.clear()
    response = client.get('/sites/export-site/', follow_redirects=False)
    assert response.status_code == 303
    unlock_url = response.headers['location']
    assert '/site-unlock/' in unlock_url
    assert client.post(unlock_url, data={'password':'wrong'}).status_code == 401
    assert client.post(unlock_url, data={'password':'password-123'}, follow_redirects=False).status_code == 303
    granted_cookie = client.cookies.get('site_pw_' + site['id'])
    assert client.get('/sites/export-site/').status_code == 200
    assert client.get('/api/runtime/sites/export-site/documents/settings').status_code == 200
    set_mode('password', password='new-password-123')
    client.cookies.clear()
    client.cookies.set('site_pw_' + site['id'], granted_cookie)
    assert client.get('/sites/export-site/', follow_redirects=False).status_code == 303
    set_mode('users_all')
    as_viewer()
    assert client.get('/sites/export-site/').status_code == 200
    client.cookies.clear()
    assert client.get('/sites/export-site/', follow_redirects=False).status_code == 303


def test_generic_share_scopes_do_not_require_date_keys(admin_client):
    client, db_path, token, owner_id = admin_client
    site, _ = deploy_runtime_site(client, token)
    value = {"public/item": {"text": "visible"}, "private/item": "hidden"}
    with sqlite3.connect(db_path) as db:
        db.execute("UPDATE site_documents SET value_json=? WHERE site_id=?", (json.dumps(value), site["id"]))
    endpoint = "/api/sites/export-site/share-links"
    scoped = client.post(endpoint, json={"document": "settings", "scope": {"type": "key-prefix", "prefix": "public/"}})
    assert scoped.status_code == 201
    whole = client.post(endpoint, json={"document": "settings", "scope": {"type": "document"}})
    assert whole.status_code == 201
    for scope in ({"type": "key-prefix", "prefix": ""}, {"type": "unknown"}, {"type": "document", "extra": True}):
        assert client.post(endpoint, json={"document": "settings", "scope": scope}).status_code == 422
    assert client.post(endpoint, json={"document": "settings", "month": "2026-09", "scope": {"type": "document"}}).status_code == 422
    client.cookies.clear()
    runtime = "/api/runtime/sites/export-site/documents/settings"
    for response, expected in ((scoped, {"public/item": value["public/item"]}), (whole, value)):
        url = response.json()["url"]
        assert client.get(url).status_code == 200
        headers = {"X-Microsite-Share": url.split("/")[2]}
        assert client.get(runtime + "?month=other&prefix=private/", headers=headers).json()["value"] == expected
        assert client.put(runtime, json={"value": {}}, headers={**headers, "If-Match": '\"rev-1\"'}).status_code == 401
        assert client.get("/api/runtime/sites/other/documents/settings", headers=headers).status_code == 401
    with sqlite3.connect(db_path) as db:
        db.execute("UPDATE site_documents SET value_json=? WHERE site_id=?", (json.dumps(["changed type"]), site["id"]))
    assert client.get(runtime, headers={"X-Microsite-Share": scoped.json()["url"].split("/")[2]}).status_code == 403
    assert client.get(runtime, headers={"X-Microsite-Share": whole.json()["url"].split("/")[2]}).json()["value"] == ["changed type"]


def test_existing_share_table_migrates_without_revoking_legacy_links(admin_client):
    client, db_path, token, _ = admin_client
    site, _ = deploy_runtime_site(client, token)
    with sqlite3.connect(db_path) as db:
        db.execute("UPDATE site_documents SET value_json=? WHERE site_id=?", (json.dumps({"2026-09-01": "visible"}), site["id"]))
    result = client.post("/api/sites/export-site/share-links", json={"document": "settings", "month": "2026-09"})
    url = result.json()["url"]
    with sqlite3.connect(db_path) as db:
        db.execute("ALTER TABLE site_share_links DROP COLUMN scope_json")
    with TestClient(main.app) as migrated:
        assert migrated.get(url).status_code == 200
        result = migrated.get("/api/runtime/sites/export-site/documents/settings", headers={"X-Microsite-Share": url.split("/")[2]})
        assert result.json()["value"] == {"2026-09-01": "visible"}
