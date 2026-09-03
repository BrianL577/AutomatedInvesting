import sys
import tempfile
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jj_bot.models import Direction, Phase, Signal, SetupGrade, TradeResult
from jj_bot.trade_logger import TradeLogger


def _trade_result():
    signal = Signal(
        timestamp=datetime(2026, 8, 26, 9, 31), direction=Direction.LONG, entry_price=29000.0,
        stop_price=28975.0, target_price=29038.0, phase=Phase.CONTINUATION, grade=SetupGrade.A, reason="test",
    )
    return TradeResult(signal=signal, exit_price=29038.0, exit_timestamp=datetime(2026, 8, 26, 9, 45), win=True, pnl_points=38.0, qty=2)


def _logger(tmp_path, monkeypatch, configured=True):
    log_path = tmp_path / "trades.json"
    log_path.write_text("[]")
    if configured:
        monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
        monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "test-key")
    else:
        monkeypatch.delenv("SUPABASE_URL", raising=False)
        monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
    return TradeLogger(path=log_path, dollar_per_point=20.0, source="test")


def test_chart_uploaded_and_public_url_stored_when_supabase_configured(tmp_path, monkeypatch):
    logger = _logger(tmp_path, monkeypatch)
    chart_path = tmp_path / "Virtual-01_20260826_093100_WIN.png"
    chart_path.write_bytes(b"fake png bytes")

    with patch("jj_bot.trade_logger.requests.post") as mock_post:
        mock_post.return_value = MagicMock(status_code=200, raise_for_status=lambda: None)
        logger.log_trade(_trade_result(), account_name="Virtual-01", chart_path=str(chart_path))

    import json
    records = json.loads(logger.path.read_text())
    assert records[0]["chart_url"] == (
        "https://example.supabase.co/storage/v1/object/public/trade-charts/Virtual-01_20260826_093100_WIN.png"
    )


def test_no_chart_url_when_supabase_not_configured(tmp_path, monkeypatch):
    logger = _logger(tmp_path, monkeypatch, configured=False)
    chart_path = tmp_path / "chart.png"
    chart_path.write_bytes(b"fake png bytes")

    logger.log_trade(_trade_result(), account_name="Virtual-01", chart_path=str(chart_path))

    import json
    records = json.loads(logger.path.read_text())
    assert records[0]["chart_url"] is None


def test_upload_failure_does_not_block_trade_logging(tmp_path, monkeypatch):
    """A Supabase Storage hiccup must never prevent the trade itself from
    being logged -- same contract as the existing Supabase-row-write path."""
    logger = _logger(tmp_path, monkeypatch)
    chart_path = tmp_path / "chart.png"
    chart_path.write_bytes(b"fake png bytes")

    with patch("jj_bot.trade_logger.requests.post", side_effect=Exception("network error")):
        logger.log_trade(_trade_result(), account_name="Virtual-01", chart_path=str(chart_path))

    import json
    records = json.loads(logger.path.read_text())
    assert len(records) == 1
    assert records[0]["chart_url"] is None


def test_no_chart_path_means_no_upload_attempted(tmp_path, monkeypatch):
    logger = _logger(tmp_path, monkeypatch)
    with patch("jj_bot.trade_logger.requests.post") as mock_post:
        mock_post.return_value = MagicMock(status_code=200, raise_for_status=lambda: None)
        logger.log_trade(_trade_result(), account_name="Virtual-01", chart_path=None)
    # Only the Supabase row-write POST should have fired, not any storage call.
    urls_called = [call.args[0] for call in mock_post.call_args_list]
    assert not any("/storage/" in u for u in urls_called)


if __name__ == "__main__":
    print("Run via pytest.")
