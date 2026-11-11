import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jj_bot.config import load_config
from jj_bot.live_runner_yahoo_virtual import YahooVirtualPracticeRunner, parse_yahoo_bars

# 2024-01-02 14:30:00 UTC == 09:30 ET
T0 = int(datetime(2024, 1, 2, 14, 30, tzinfo=timezone.utc).timestamp())
PAYLOAD = {"chart": {"result": [{
    "timestamp": [T0, T0 + 60, T0 + 120, T0 + 180],
    "indicators": {"quote": [{
        "open": [100.0, 101.0, None, 103.0], "high": [102.0, 103.0, None, 104.0],
        "low": [99.0, 100.5, None, 102.0], "close": [101.0, 102.5, None, 103.5],
        "volume": [10, 20, None, 5]}]}}]}}


def test_parse_skips_null_minutes_and_converts_to_et():
    bars = parse_yahoo_bars(PAYLOAD)
    assert [b.timestamp.strftime("%H:%M") for b in bars] == ["09:30", "09:31", "09:33"]
    assert bars[0].open == 100.0 and bars[1].close == 102.5


def test_parse_handles_empty_or_error_payloads():
    assert parse_yahoo_bars({}) == []
    assert parse_yahoo_bars({"chart": {"result": None}}) == []


def _runner():
    r = YahooVirtualPracticeRunner(load_config(), num_accounts=2)
    return r


def test_forming_bar_excluded_and_old_bars_not_reprocessed():
    r = _runner()
    bars = parse_yahoo_bars(PAYLOAD)
    now = datetime(2024, 1, 2, 14, 33, 30, tzinfo=timezone.utc)  # 09:33:30 ET: 09:33 bar still forming
    got = r._new_closed_bars(bars, now=now)
    assert [b.timestamp.strftime("%H:%M") for b in got] == ["09:30", "09:31"]
    r._last_bar_ts = got[-1].timestamp
    later = datetime(2024, 1, 2, 14, 35, tzinfo=timezone.utc)
    got2 = r._new_closed_bars(bars, now=later)
    assert [b.timestamp.strftime("%H:%M") for b in got2] == ["09:33"]


def test_runner_needs_no_topstepx_credentials():
    r = _runner()
    assert r.client is None and r.symbol == "NQ=F"
