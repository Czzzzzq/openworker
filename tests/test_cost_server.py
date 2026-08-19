"""Cost-meter server integration: /v1/cost/* endpoints wired through SessionManager.

Uses FastAPI's in-process TestClient (no subprocesses), so it runs under the
sandbox; the meter's outbound fetches are stubbed.
"""

from __future__ import annotations

import io
import json
import os
import uuid
from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from coworker.costmeter import DEEPSEEK_PRICING_URLS
from coworker.server import SessionManager, create_app


@pytest.fixture()
def tmpdir() -> Path:
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
        pass


def _make_manager(tmpdir: Path, *, fetch, secrets=None) -> SessionManager:
    manager = SessionManager(data_dir=tmpdir, model="deepseek:deepseek-v4-flash")
    manager.cost_meter._fetch = fetch  # stub outbound network
    if secrets is not None:
        manager.cost_meter._secrets = secrets
    return manager


class _StubSecrets:
    """Minimal SecretStore lookalike with a DeepSeek key."""

    def __init__(self, key: str) -> None:
        self._key = key

    def get(self, profile: str):
        if profile == "provider:deepseek":
            return {"api_key": self._key}
        return None


class _StubResponse:
    def __init__(self, body: bytes) -> None:
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _ds_html() -> bytes:
    return (
        "<html><p>高峰时段为每日 9:00 与 14:00，空闲时段价格为高峰的一半。</p>"
        "<table><tr><td>deepseek-v4-flash</td><td>高峰 0.10 元</td>"
        "<td>高峰 3.00 元</td><td>高峰 9.00 元</td></tr>"
        "<tr><td>deepseek-v4-flash 空闲</td><td>0.05 元</td><td>1.50 元</td><td>4.50 元</td></tr>"
        "<tr><td>deepseek-v4-pro</td><td>高峰 0.30 元</td><td>高峰 9.00 元</td><td>高峰 27.00 元</td></tr>"
        "<tr><td>deepseek-v4-pro 空闲</td><td>0.15 元</td><td>4.50 元</td><td>13.50 元</td></tr></table></html>"
    ).encode()


def test_cost_endpoints(tmpdir) -> None:
    balance_json = json.dumps(
        {
            "is_available": True,
            "balance_infos": [
                {"currency": "CNY", "total_balance": "123.45", "granted_balance": "0.00", "topped_up_balance": "123.45"}
            ],
        }
    ).encode()

    def fetch(url, timeout=15):
        s = getattr(url, "full_url", None) or str(url)
        if "user/balance" in s:
            return _StubResponse(balance_json)
        if s in DEEPSEEK_PRICING_URLS or getattr(url, "full_url", None) in DEEPSEEK_PRICING_URLS:
            return _StubResponse(_ds_html())
        if "openrouter.ai" in s:
            return _StubResponse(
                json.dumps(
                    {
                        "data": [
                            {
                                "id": "deepseek/deepseek-v4-flash",
                                "pricing": {"prompt": 0.42, "completion": 1.26},
                            }
                        ]
                    }
                ).encode()
            )
        raise AssertionError(f"unexpected url {s}")

    manager = _make_manager(
        tmpdir,
        fetch=fetch,
        secrets=_StubSecrets("sk-deepseek-test"),
    )
    # Pre-record one call so stats are non-empty (peak at 10:00 Asia/Shanghai).
    from zoneinfo import ZoneInfo

    ts = datetime(2026, 8, 17, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai")).timestamp()
    manager.cost_meter.record(
        "s1", "deepseek:deepseek-v4-flash", {"input": 1_000_000, "output": 500_000}, ts=ts
    )

    app = create_app(manager)
    with TestClient(app) as c:
        stats = c.get("/v1/cost/stats", params={"session_id": "s1"}).json()
        assert stats["ok"] is True
        assert stats["session"]["calls"] == 1
        assert stats["session"]["cost"] == pytest.approx(3.0 + 4.5, abs=1e-6)
        assert stats["budget"]["period"] == "month"
        assert stats["peak"]["enabled"] is True

        bal = c.get("/v1/cost/balance").json()
        assert bal["ok"] is True
        assert bal["balances"][0]["total"] == pytest.approx(123.45, abs=1e-6)

        st = c.get("/v1/cost/settings").json()
        assert st["ok"] is True
        assert "deepseek:deepseek-v4-flash" in st["settings"]["prices"]

        # Save a budget + custom price; reload through the settings endpoint.
        saved = c.post(
            "/v1/cost/settings",
            json={"budget": {"amount": 88.0, "currency": "CNY", "period": "day"}},
        ).json()
        assert saved["settings"]["budget"]["amount"] == 88.0

        # One-click official price sync updates deepseek prices + peak windows.
        synced = c.post("/v1/cost/sync").json()
        assert synced["ok"] is True
        assert "deepseek:deepseek-v4-flash" in synced["updated"]
        st2 = c.get("/v1/cost/settings").json()
        assert st2["settings"]["prices"]["deepseek:deepseek-v4-flash"]["input"] == pytest.approx(3.0, abs=1e-6)
        assert st2["settings"]["off_peak"]["windows"] is not None


def test_cost_balance_without_key(tmpdir) -> None:
    def fetch(url, timeout=15):
        raise AssertionError("must not hit the network without a key")

    manager = _make_manager(tmpdir, fetch=fetch, secrets=_StubSecrets(""))
    app = create_app(manager)
    with TestClient(app) as c:
        bal = c.get("/v1/cost/balance").json()
        assert bal["ok"] is False
        assert bal["error"]
