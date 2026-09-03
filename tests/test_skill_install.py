import asyncio

from starlette.requests import Request

from app.main import install_skill


def make_request() -> Request:
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "GET",
            "scheme": "https",
            "path": "/api/install-skill",
            "raw_path": b"/api/install-skill",
            "query_string": b"",
            "headers": [],
            "client": ("127.0.0.1", 12345),
            "server": ("microsites.example.com", 443),
            "root_path": "",
        }
    )


def test_unix_skill_installer_contains_new_skill_and_cli():
    response = asyncio.run(install_skill(make_request(), token="secret-token"))
    script = response.body.decode("utf-8")
    assert "skills/microsite-container" in script
    assert "scripts/deploy.py" in script
    assert '"$AGENTS_DIR/openai.yaml"' in script
    assert ".config/microsite-container" in script
    assert "API_KEY=secret-token" in script
    assert "runtime-token create" in script
    assert "runtime put" in script
    assert "--source-dir" in script
    assert "command_pull" in script
    assert "MICROSITE_RUNTIME_TOKEN" in script
    assert "skills/html-container" not in script


def test_windows_skill_installer_contains_new_skill_and_cli():
    response = asyncio.run(install_skill(make_request(), token="secret-token", os="win"))
    script = response.body.decode("utf-8")
    assert "skills\\microsite-container" in script
    assert '"$ScriptsDir\\deploy.py"' in script
    assert '"$AgentsDir\\openai.yaml"' in script
    assert ".config\\microsite-container" in script
