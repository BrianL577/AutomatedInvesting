import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jj_bot.bar_logger import BarLogger
from jj_bot.models import Bar


def _bar():
    return Bar(timestamp=datetime(2026, 9, 3, 9, 31), open=29000.0, high=29010.0, low=28995.0, close=29005.0, volume=120)


def test_disabled_when_supabase_not_configured(monkeypatch):
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
    logger = BarLogger()
    assert not logger.enabled
    with patch("jj_bot.bar_logger.requests.post") as mock_post:
        logger.log_bar(_bar())
    mock_post.assert_not_called()


def test_posts_bar_with_merge_duplicates_when_configured(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "test-key")
    logger = BarLogger()
    assert logger.enabled

    with patch("jj_bot.bar_logger.requests.post") as mock_post:
        mock_post.return_value = MagicMock(status_code=201, raise_for_status=lambda: None)
        logger.log_bar(_bar())

    mock_post.assert_called_once()
    args, kwargs = mock_post.call_args
    assert args[0] == "https://example.supabase.co/rest/v1/bars"
    assert kwargs["json"]["o"] == 29000.0
    assert kwargs["json"]["h"] == 29010.0
    assert "merge-duplicates" in kwargs["headers"]["Prefer"]


def test_failure_never_raises(monkeypatch):
    """A Supabase hiccup logging a bar must never bubble up into the bar/
    strategy loop that owns real trading decisions."""
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "test-key")
    logger = BarLogger()
    with patch("jj_bot.bar_logger.requests.post", side_effect=Exception("network error")):
        logger.log_bar(_bar())  # must not raise


if __name__ == "__main__":
    print("Run via pytest.")
