import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load_module(path):
    spec = importlib.util.spec_from_file_location("boundary_module", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_site_payloads_are_rejected_even_under_renamed_directories():
    guard = load_module(ROOT / "scripts/check_boundaries.py")
    assert guard.violations(["sites/demo/index.html", "fixtures/demo/microsite.json", "examples/demo.html"])
    assert not guard.violations(["app/main.py", "tests/test_runtime_data.py"])


def test_skill_rejects_platform_workspaces_before_any_network_call(tmp_path):
    cli = load_module(ROOT / "app/skill/deploy.py")
    with pytest.raises(cli.CliError, match="outside"):
        cli.require_site_workspace(ROOT / "scratch/site")
    cli.require_site_workspace(tmp_path / "external-site")


def test_pull_and_deploy_refuse_platform_before_credentials_are_loaded():
    cli = load_module(ROOT / "app/skill/deploy.py")
    with pytest.raises(cli.CliError, match="outside"):
        cli.command_pull(SimpleNamespace(out=str(ROOT / "scratch")))
    with pytest.raises(cli.CliError, match="outside"):
        cli.command_deploy(SimpleNamespace(source_dir=str(ROOT), publish_dir=str(ROOT)))
