import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src import storage  # noqa: E402


@pytest.fixture
def state_dir(tmp_path, monkeypatch):
    """Point all saved state at a temporary folder."""
    monkeypatch.setattr(storage, "DATA_DIR", tmp_path)
    return tmp_path


@pytest.fixture
def make_article():
    def _make(title="ADB approves $500 million loan for Vietnam solar project", url=None, **overrides):
        slug = "".join(ch for ch in title.lower() if ch.isalnum())[:60]
        article = {
            "title": title,
            "url": url or f"https://example.com/{slug}",
            "source": "Example News",
            "provider": "rss",
            "region": "APAC",
            "trust": 2,
            "description": "Financing for a power project in Asia.",
            "published_at": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),
        }
        article.update(overrides)
        return article

    return _make
