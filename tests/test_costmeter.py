"""Cost-meter unit tests: price math, peak/off-peak windows, the JSONL record
loop, the provider wrapper, and the DeepSeek pricing-page parser."""

from __future__ import annotations

import os
import uuid
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
import pytest

from coworker.costmeter import (
    CostMeter,
    MeteredProvider,
    PriceEntry,
    compute_cost,
    in_peak_window,
    parse_deepseek_pricing,
)


@pytest.fixture()
def tmpdir() -> Path:
    """Workspace-local temp dir. The sandbox blocks writes to `mkdtemp` dirs and
    the OS temp area, so we create a unique plain-mkdir dir under the repo."""
    base = Path("tests") / ".costmeter-tmp"
    base.mkdir(parents=True, exist_ok=True)
    d = base / f"cm-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    d.mkdir()
    yield d
    try:
        for child in d.iterdir():
            child.unlink(missing_ok=True)
        d.rmdir()
    except OSError:
        pass  # best-effort; the sandbox may deny removal


def _ts(hh: int, mm: int = 0) -> float:
    return datetime(2026, 8, 17, hh, mm, tzinfo=ZoneInfo("Asia/Shanghai")).timestamp()


def _prices() -> dict[str, PriceEntry]:
    return {
        "deepseek:deepseek-v4-flash": PriceEntry(
            input=3.0, cache_read=0.10, cache_write=3.0, output=9.0, currency="CNY"
        ),
        "openai:gpt-5.6-sol": PriceEntry(
            input=1.25, cache_read=0.125, cache_write=1.25, output=10.0, currency="USD"
        ),
    }


def test_in_peak_window_day_and_midnight_wrap() -> None:
    windows = [["09:00", "11:00"], ["22:00", "02:00"]]
    assert in_peak_window(_ts(9, 30), windows, "Asia/Shanghai") is True
    assert in_peak_window(_ts(10, 59), windows, "Asia/Shanghai") is True
    assert in_peak_window(_ts(11, 0), windows, "Asia/Shanghai") is False
    assert in_peak_window(_ts(14, 0), windows, "Asia/Shanghai") is False
    assert in_peak_window(_ts(23, 0), windows, "Asia/Shanghai") is True
    assert in_peak_window(_ts(1, 0), windows, "Asia/Shanghai") is True
    assert in_peak_window(_ts(3, 0), windows, "Asia/Shanghai") is False
    assert in_peak_window(_ts(9, 0), [], "Asia/Shanghai") is False


def test_compute_cost_deepseek_peak_vs_offpeak() -> None:
    prices = _prices()
    off_peak = {
        "enabled": True,
        "tz": "Asia/Shanghai",
        "windows": [["09:00", "11:00"], ["14:00", "16:00"]],
        "multiplier": 0.5,
    }
    usage = {
        "input": 1_000_000,
        "output": 500_000,
        "cache_read": 100_000,
        "cache_write": 0,
    }
    # Peak (10:00): input 1M x 3.0 + cache_read 100k x 0.10 + output 0.5M x 9.0
    #             = 3.0 + 0.01 + 4.5 = 7.51
    peak_cost, cur, peak, priced = compute_cost(
        "deepseek:deepseek-v4-flash", usage, _ts(10, 0), prices, off_peak=off_peak
    )
    assert cur == "CNY" and peak is True and priced is True
    assert peak_cost == pytest.approx(3.0 + 0.01 + 4.5, abs=1e-6)
    # Off-peak (02:00): everything x 0.5
    off_cost, _, peak2, _ = compute_cost(
        "deepseek:deepseek-v4-flash", usage, _ts(2, 0), prices, off_peak=off_peak
    )
    assert peak2 is False
    assert off_cost == pytest.approx(peak_cost * 0.5, abs=1e-6)


def test_compute_cost_usd_ignores_offpeak() -> None:
    prices = _prices()
    off_peak = {
        "enabled": True,
        "tz": "Asia/Shanghai",
        "windows": [["09:00", "11:00"]],
        "multiplier": 0.5,
    }
    usage = {"input": 1_000_000, "output": 100_000}
    cost, cur, peak, _ = compute_cost(
        "openai:gpt-5.6-sol", usage, _ts(10, 0), prices, off_peak=off_peak
    )
    assert cur == "USD" and peak is True
    assert cost == pytest.approx(1.25 + 1.0, abs=1e-6)


def test_unknown_model_is_unpriced() -> None:
    cost, cur, peak, priced = compute_cost(
        "nobody:model",
        {"input": 1000, "output": 1000},
        _ts(10, 0),
        _prices(),
        off_peak={"enabled": True},
    )
    assert cost == 0.0 and priced is False


def test_unknown_model_uses_provider_fallback() -> None:
    # No exact entry for openai:gpt-whatever → billed at the openai provider default.
    prices = _prices()
    prices["*openai"] = PriceEntry(
        input=1.25, cache_read=0.125, cache_write=1.25, output=10.0, currency="USD"
    )
    cost, cur, peak, priced = compute_cost(
        "openai:gpt-custom-model",
        {"input": 1_000_000, "output": 100_000},
        _ts(10, 0),
        prices,
        off_peak={"enabled": True},
    )
    assert priced is True and cur == "USD"
    assert cost == pytest.approx(1.25 + 1.0, abs=1e-6)


def test_meter_records_and_aggregates(tmpdir) -> None:
    meter = CostMeter(tmpdir)
    meter.record(
        "s1", "deepseek:deepseek-v4-flash", {"input": 1_000_000, "output": 0}, ts=_ts(2, 0)
    )
    meter.record(
        "s1",
        "deepseek:deepseek-v4-flash",
        {"input": 1_000_000, "output": 1_000_000},
        ts=_ts(10, 0),
    )
    meter.record(
        "s2", "openai:gpt-5.6-sol", {"input": 100_000, "output": 10_000}, ts=_ts(12, 0)
    )

    stats = meter.stats("s1")
    assert stats["session"]["calls"] == 2
    assert stats["total"]["calls"] == 3
    assert stats["by_day"][0]["calls"] == 3
    # s1: off-peak (1.5) + peak (3.0 + 9.0) = 13.5 CNY
    assert stats["session"]["cost"] == pytest.approx(13.5, abs=1e-6)
    assert len(stats["by_model"]) == 2

    # Persistence: a fresh meter over the same dir sees the same records.
    meter2 = CostMeter(tmpdir)
    assert meter2.stats()["total"]["calls"] == 3


def test_settings_roundtrip_and_custom_price(tmpdir) -> None:
    meter = CostMeter(tmpdir)
    meter.update_settings(
        {
            "budget": {"amount": 50.0, "currency": "CNY", "period": "day"},
            # Peak/off-peak off so the recorded cost is the plain custom price.
            "off_peak": {"enabled": False},
            "prices": {
                "deepseek:deepseek-v4-flash": {
                    "input": 6.0,
                    "cache_read": 0.2,
                    "output": 18.0,
                    "currency": "CNY",
                    "source": "custom",
                }
            },
        }
    )
    meter.record(
        "s1",
        "deepseek:deepseek-v4-flash",
        {"input": 1_000_000, "output": 0},
        ts=datetime.now().timestamp(),
    )
    stats = meter.stats("s1")
    assert stats["session"]["cost"] == pytest.approx(6.0, abs=1e-6)
    assert stats["budget"]["period"] == "day"
    assert stats["budget"]["used_pct"] == pytest.approx(12.0, abs=1e-6)

    reloaded = CostMeter(tmpdir)
    assert reloaded.get_settings()["settings"]["prices"]["deepseek:deepseek-v4-flash"][
        "input"
    ] == pytest.approx(6.0)


class _FakeTurn:
    def __init__(self, usage) -> None:
        self.usage = usage


class _FakeProvider:
    def __init__(self, turns) -> None:
        self._turns = list(turns)

    def complete(self, **kwargs):
        return self._turns.pop(0)

    def stream(self, **kwargs):
        for t in self._turns:
            yield type("Chunk", (), {"turn": t})()
        yield type("Chunk", (), {"turn": None})()

    def capabilities(self, model):
        return model

    def invalidate(self, name=None):
        return "invalidated"


def _usage(input_t: int, output_t: int):
    return type("U", (), {"as_dict": lambda self: {"input": input_t, "output": output_t}})()


def test_metered_provider_records_and_passthrough(tmpdir) -> None:
    meter = CostMeter(tmpdir)
    seen: list[tuple[str, str, dict]] = []
    inner = _FakeProvider(
        [
            _FakeTurn(_usage(1_000_000, 0)),
            _FakeTurn(_usage(500_000, 500_000)),
        ]
    )
    wrapped = MeteredProvider(
        inner,
        lambda sid, model, usage: (
            seen.append((sid, model, usage)), meter.record(sid, model, usage)
        ),
        "s9",
    )

    turn = wrapped.complete(model="deepseek:deepseek-v4-flash", messages=[])
    assert turn is not None
    chunks = list(wrapped.stream(model="openai:gpt-5.6-sol", messages=[]))
    assert len(chunks) == 2
    assert wrapped.capabilities("x") == "x"
    assert wrapped.invalidate() == "invalidated"

    assert len(seen) == 2
    assert seen[0][0] == "s9" and seen[0][1] == "deepseek:deepseek-v4-flash"
    assert seen[1][1] == "openai:gpt-5.6-sol"
    assert meter.stats("s9")["session"]["calls"] == 2


_DS_HTML = """
<html><body>
<h2>模型与价格</h2>
<p>DeepSeek 于 2026-08-17 起实行峰谷定价：高峰时段为北京时间 9:00 - 12:00、14:00 - 18:00（其余为空闲时段）。空闲时段价格为高峰时段价格的一半。</p>
<table>
<tr><th>模型</th><th>DeepSeek-V4-Flash-0731</th><th>DeepSeek-V4-Pro-0813</th></tr>
<tr><td>百万tokens输入（缓存命中）</td><td>空闲时段 0.05元</td><td>空闲时段 0.15元</td><td>高峰时段 0.10元</td><td>高峰时段 0.30元</td></tr>
<tr><td>百万tokens输入（缓存未命中）</td><td>空闲时段 1.5元</td><td>空闲时段 4.5元</td><td>高峰时段 3.0元</td><td>高峰时段 9.0元</td></tr>
<tr><td>百万tokens输出</td><td>空闲时段 4.5元</td><td>空闲时段 13.5元</td><td>高峰时段 9.0元</td><td>高峰时段 27.0元</td></tr>
</table>
</body></html>
"""


def test_parse_deepseek_pricing() -> None:
    parsed = parse_deepseek_pricing(_DS_HTML)
    assert parsed["windows"] == [["9:00", "12:00"], ["14:00", "18:00"]]
    assert parsed.get("multiplier") == 0.5
    flash = parsed["models"]["deepseek:deepseek-v4-flash"]
    assert flash["input"] == pytest.approx(3.0)
    assert flash["output"] == pytest.approx(9.0)
    assert flash["cache_read"] == pytest.approx(0.10)
    assert flash["off_peak_prices"]["input"] == pytest.approx(1.5)
    pro = parsed["models"]["deepseek:deepseek-v4-pro"]
    assert pro["input"] == pytest.approx(9.0)
    assert pro["output"] == pytest.approx(27.0)
    assert pro["cache_read"] == pytest.approx(0.30)
