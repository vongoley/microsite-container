import json
from pathlib import Path

from jsonschema import Draft202012Validator


ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "examples" / "training-log"


def test_training_log_site_has_deployable_shape():
    expected = {
        "index.html",
        "microsite.json",
        "assets/app.js",
        "assets/styles.css",
        "data/training-plan.json",
        "schemas/training-plan.schema.json",
    }
    assert expected <= {
        path.relative_to(SITE).as_posix()
        for path in SITE.rglob("*")
        if path.is_file()
    }


def test_training_log_seed_preserves_embedded_plan():
    plan = json.loads((SITE / "data/training-plan.json").read_text(encoding="utf-8"))

    assert len(plan) == 40
    assert plan["2026-08-16"] == ["legs", "glutes"]
    assert plan["2026-08-20"] == ["back", "biceps", "cardio"]
    assert plan["2026-08-25"] == ["shoulders", "core", "cardio"]
    assert plan["2026-09-24"] == ["cardio"]


def test_training_log_seed_matches_declared_schema():
    plan = json.loads((SITE / "data/training-plan.json").read_text(encoding="utf-8"))
    schema = json.loads(
        (SITE / "schemas/training-plan.schema.json").read_text(encoding="utf-8")
    )
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(plan)


def test_training_log_runtime_config_is_public_read_owner_write():
    config = json.loads((SITE / "microsite.json").read_text(encoding="utf-8"))
    document = config["runtimeData"]["documents"]["training-plan"]

    assert document == {
        "scope": "site",
        "read": "public",
        "write": "owner",
        "schemaVersion": 1,
        "schema": "schemas/training-plan.schema.json",
        "seed": "data/training-plan.json",
        "maxBytes": 1048576,
    }


def test_training_log_frontend_uses_runtime_data_and_readonly_share_mode():
    html = (SITE / "index.html").read_text(encoding="utf-8")
    script = (SITE / "assets/app.js").read_text(encoding="utf-8")
    styles = (SITE / "assets/styles.css").read_text(encoding="utf-8")

    assert '/_microsite/sdk/v1.js' in html
    assert 'MicrositeData.document(DOCUMENT_KEY' in script
    assert 'params.get("view") === "share"' in script
    assert 'url.searchParams.set("month", monthKey(viewDate))' in script
    assert 'url.searchParams.set("view", "share")' in script
    assert 'planDocument.saveDraft' in script
    assert 'planDocument.save(plan)' in script
    assert "保存到 HTML" not in html + script
    assert "training-calendar-plan-v1" not in html + script

    assert "min-height: 100dvh" in styles
    assert "@media (min-width: 768px)" in styles
    assert '[data-theme="dark"]' in styles
    assert 'body[data-view-mode="share"] .edit-navigation' in styles


def test_training_log_keeps_original_typography_and_training_palette():
    styles = (SITE / "assets/styles.css").read_text(encoding="utf-8")

    original_tokens = {
        "--bg: #f3f5ef",
        "--text: #20241f",
        "--muted: #6c7469",
        "--accent: #355c4a",
        "--today: #c77341",
        "--chest: #c9574d",
        "--back: #4b73bd",
        "--legs: #7d5bb8",
        "--glutes: #b65b87",
        "--shoulders: #c87932",
        "--biceps: #9b7927",
        "--triceps: #58778d",
        "--core: #4e8a52",
        "--cardio: #2a8b91",
        "--rest: #717970",
    }
    assert all(token in styles for token in original_tokens)
    assert '"PingFang SC", "Hiragino Sans GB", "Microsoft YaHei"' in styles
    assert "font-size: 0.68rem" in styles
    assert "color: var(--training-color)" in styles
    assert "font-weight: 700" in styles
    assert "Georgia" not in styles
