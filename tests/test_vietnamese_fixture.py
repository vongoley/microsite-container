import json
import os
import re
from pathlib import Path

import pytest


FIXTURE_ENV = "VIETNAMESE_LEARNING_HTML"
SOURCE_URL = "https://html.orcacalf.site/view/53cd5401"


def test_real_vietnamese_learning_page_shape(tmp_path):
    source = os.environ.get(FIXTURE_ENV)
    if not source:
        pytest.skip(f"set {FIXTURE_ENV} to the downloaded page from {SOURCE_URL}")
    html_path = Path(source)
    html = html_path.read_text(encoding="utf-8")
    audio_keys = re.findall(r'data-audio-key=["\x27]([^"\x27]+)', html)
    audio_kinds = re.findall(r'data-audio-kind=["\x27]([^"\x27]+)', html)

    assert html_path.stat().st_size > 9_000_000
    assert len(audio_keys) == 6_895
    assert len(audio_kinds) == 6_895

    site_dir = tmp_path / "vietnamese-learning"
    site_dir.mkdir()
    (site_dir / "index.html").write_text(html, encoding="utf-8")
    external_manifest = {
        "version": 1,
        "source": SOURCE_URL,
        "slots": [
            {"key": key, "kind": kind, "src": None}
            for key, kind in zip(audio_keys, audio_kinds)
        ],
    }
    manifest_path = site_dir / "audio-manifest.json"
    manifest_path.write_text(json.dumps(external_manifest, ensure_ascii=False), encoding="utf-8")

    assert len(external_manifest["slots"]) == 6_895
    assert manifest_path.stat().st_size > 100_000
