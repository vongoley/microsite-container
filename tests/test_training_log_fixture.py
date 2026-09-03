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

    august = {date: items for date, items in plan.items() if date.startswith("2026-08-")}
    assert len(august) == 16
    assert sum(any(item != "rest" for item in items) for items in august.values()) == 14


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
    assert "navigator.share" not in script
    assert 'class="share-action"' in html
    assert 'aria-controls="sharePanel"' in html
    assert "position: absolute" in styles
    assert 'event.key === "Escape"' in script
    assert "#copyShareLink" in styles
    assert "white-space: nowrap" in styles
    assert "flex: 0 0 auto" in styles
    assert 'planDocument.saveDraft' in script
    assert 'planDocument.save(plan)' in script
    assert "保存到 HTML" not in html + script
    assert "training-calendar-plan-v1" not in html + script
    assert "已从服务器读取" not in html + script
    assert " · Revision " not in html + script
    assert "status-row" not in html + styles
    assert "saveIndicator" not in script
    assert "TRAINING LOG" not in html
    assert "可编辑站点" not in html
    assert "训练安排" not in html
    assert "训练日志：把每一次训练，留在日历里" in html
    assert "trainingDays" in script

    assert "min-height: 100dvh" in styles
    assert "@media (min-width: 768px)" in styles
    assert '[data-theme="dark"]' in styles
    assert 'body[data-view-mode="share"] .edit-navigation' in styles


def test_training_log_keeps_original_typography_and_training_palette():
    html = (SITE / "index.html").read_text(encoding="utf-8")
    styles = (SITE / "assets/styles.css").read_text(encoding="utf-8")
    script = (SITE / "assets/app.js").read_text(encoding="utf-8")

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
    assert "font-size: 1.1rem" in styles
    assert "font-size: 0.8rem" in styles
    assert "min-height: 114px" in styles
    assert "gap: 0.5px" not in styles
    assert "gap: 4px" in styles
    assert "padding: 2px 6px" in styles
    assert "min-width: max-content" in styles
    assert "grid-auto-rows: 30px" not in styles
    assert "max-width: none" in styles
    assert "font-size: clamp(1.8rem, 4vw, 2.6rem)" in styles
    assert "color: var(--training-color)" in styles
    assert "font-weight: 700" in styles
    assert "Georgia" not in styles
    assert "flex-direction: column" in styles
    assert "writing-mode: vertical-rl" not in styles
    assert "border: 0;" in styles
    assert ".calendar-grid" in styles and "gap: 0;" in styles
    assert "grid-template-columns: repeat(7, minmax(0, 1fr))" in styles
    assert "min-width: 332px" not in styles
    assert "touch-action: pan-y" in styles
    assert "overscroll-behavior-x: none" in styles
    assert "training-overflow" in script
    assert '<span class="empty-hint" aria-hidden="true"></span>' in script
    assert "background: color-mix(in srgb, var(--muted) 16%, transparent)" in styles
    assert 'id="clearDay"' not in html
    assert "elements.clear" not in script
