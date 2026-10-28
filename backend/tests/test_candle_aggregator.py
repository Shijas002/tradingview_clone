from datetime import datetime, timezone

from app.models.market import Candle, Timeframe, bucket_start
from app.services.candle_aggregator import CandleAggregator


def _ts(y, mo, d, h=0, mi=0, s=0) -> int:
    return int(datetime(y, mo, d, h, mi, s, tzinfo=timezone.utc).timestamp())


def test_bucket_start_intraday():
    assert bucket_start(_ts(2026, 10, 28, 10, 7, 33), Timeframe.M5) == _ts(2026, 10, 28, 10, 5)
    assert bucket_start(_ts(2026, 10, 28, 10, 7, 33), Timeframe.H4) == _ts(2026, 10, 28, 8)
    assert bucket_start(_ts(2026, 10, 28, 23, 59, 59), Timeframe.D1) == _ts(2026, 10, 28)


def test_bucket_start_week_starts_sunday():
    # 2026-10-28 is a Wednesday -> week bar opens Sunday 2026-10-25
    assert bucket_start(_ts(2026, 10, 28, 12), Timeframe.W1) == _ts(2026, 10, 25)
    # Sunday itself maps to itself
    assert bucket_start(_ts(2026, 10, 25, 5), Timeframe.W1) == _ts(2026, 10, 25)


def test_bucket_start_month():
    assert bucket_start(_ts(2026, 10, 28, 12), Timeframe.MN1) == _ts(2026, 10, 1)


def test_aggregator_builds_and_rolls_candles():
    agg = CandleAggregator(Timeframe.M1)
    t0 = _ts(2026, 10, 28, 10, 0, 5) * 1000
    c = agg.update(1.1000, t0)
    assert (c.open, c.high, c.low, c.close, c.volume) == (1.1000, 1.1000, 1.1000, 1.1000, 1)
    agg.update(1.1010, t0 + 1000)
    c = agg.update(1.0990, t0 + 2000)
    assert (c.open, c.high, c.low, c.close, c.volume) == (1.1000, 1.1010, 1.0990, 1.0990, 3)

    c = agg.update(1.1005, t0 + 60_000)  # next minute
    assert c.time == _ts(2026, 10, 28, 10, 1)
    assert (c.open, c.close, c.volume) == (1.1005, 1.1005, 1)


def test_aggregator_continues_seed_and_ignores_late_ticks():
    seed = Candle(time=_ts(2026, 10, 28, 10, 0), open=1.0, high=1.2, low=0.9, close=1.1, volume=50)
    agg = CandleAggregator(Timeframe.M1, seed)
    c = agg.update(1.3, _ts(2026, 10, 28, 10, 0, 30) * 1000)
    assert (c.open, c.high, c.low, c.close, c.volume) == (1.0, 1.3, 0.9, 1.3, 51)
    assert agg.update(1.5, _ts(2026, 10, 28, 9, 59, 59) * 1000) is None
    assert agg.update(0, _ts(2026, 10, 28, 10, 0, 31) * 1000) is None


def test_mt5_server_offset_estimate():
    from app.providers.mt5_provider import estimate_server_offset

    now = 1_800_000_000.0
    assert estimate_server_offset(now + 7200 + 1.5, now) == 7200      # UTC+2, fresh tick
    assert estimate_server_offset(now + 10800 - 3, now) == 10800      # UTC+3
    assert estimate_server_offset(now - 2.0, now) == 0                # UTC server
    assert estimate_server_offset(now + 7200 - 900, now) is None      # 15-min-old tick: not trusted
