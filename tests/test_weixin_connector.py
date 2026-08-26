from __future__ import annotations

import asyncio
import base64

import httpx
import pytest

from coworker.connectors import (
    BasePlatformAdapter,
    ConnectorSettings,
    Gateway,
    SendResult,
    connector_list,
    load_settings,
    make_adapter,
)
from coworker.connectors.descriptors import get_descriptor
from coworker.connectors.weixin import (
    ILINK_APP_CLIENT_VERSION,
    WeixinAdapter,
    WeixinClient,
    WeixinLoginCoordinator,
    WeixinStateStore,
    send_weixin,
    weixin_message_to_event,
)
from coworker.secrets import SecretStore


def _profile() -> dict:
    return {
        "type": "qr",
        "enabled": True,
        "bot_token": "bot-secret",
        "ilink_bot_id": "bot@im.bot",
        "ilink_user_id": "owner@im.wechat",
        "base_url": "https://ilinkai.weixin.qq.com",
        "allowed_users": ["owner@im.wechat"],
    }


def _message(message_id: int = 42, *, context: str = "ctx-42") -> dict:
    return {
        "message_id": message_id,
        "from_user_id": "owner@im.wechat",
        "to_user_id": "bot@im.bot",
        "message_type": 1,
        "message_state": 2,
        "context_token": context,
        "item_list": [{"type": 1, "text_item": {"text": "你好"}}],
    }


def test_descriptor_requires_complete_qr_profile(tmp_path):
    descriptor = get_descriptor("weixin")
    assert descriptor is not None
    assert descriptor.auth == "qr"
    assert descriptor.two_way is True
    assert descriptor.channels is False
    assert descriptor.fields == []

    secrets = SecretStore(tmp_path / "secrets.json")
    listed = {item["name"]: item for item in connector_list(secrets)}["weixin"]
    assert listed["connected"] is False
    assert listed["access"]

    secrets.put("weixin:default", {"bot_token": "partial"})
    assert {item["name"]: item for item in connector_list(secrets)}["weixin"][
        "connected"
    ] is False
    secrets.put("weixin:default", _profile())
    settings = load_settings(secrets)["weixin"]
    assert settings.enabled is True
    assert settings.allowed_users == {"owner@im.wechat"}
    assert isinstance(make_adapter("weixin", _profile(), secrets=secrets), WeixinAdapter)


def test_message_mapping_uses_public_message_id_not_context():
    raw = _message()
    event = weixin_message_to_event(raw)
    assert event is not None
    assert event.text == "你好"
    assert event.source.target == "weixin:owner@im.wechat:42"
    assert "ctx-42" not in event.tagged_text()
    assert event.raw is None

    voice = _message()
    voice["item_list"] = [{"type": 3, "voice_item": {"text": "语音转写"}}]
    assert weixin_message_to_event(voice).text == "语音转写"
    assert weixin_message_to_event({**raw, "message_type": 2}) is None
    assert weixin_message_to_event({**raw, "message_state": 1}) is None


def test_state_store_selects_exact_message_context_and_persists(tmp_path):
    path = tmp_path / "weixin-runtime.json"
    state = WeixinStateStore(path)
    state.remember_context("bot", "user", "ctx-one", "1")
    state.remember_context("bot", "user", "ctx-two", "2")
    state.set_cursor("bot", "cursor-1")
    state.mark_seen("bot", "1")

    restored = WeixinStateStore(path)
    assert restored.context("bot", "user", "1") == "ctx-one"
    assert restored.context("bot", "user", "2") == "ctx-two"
    assert restored.context("bot", "user") == "ctx-two"
    assert restored.cursor("bot") == "cursor-1"
    assert restored.seen("bot", "1") is True


def test_send_weixin_requires_context_and_checks_business_result(tmp_path, monkeypatch):
    state_path = tmp_path / "runtime.json"
    creds = {
        "bot_token": "secret",
        "ilink_bot_id": "bot",
        "base_url": "https://ilinkai.weixin.qq.com",
        "state_path": str(state_path),
    }
    missing = send_weixin(creds, "user", "hello")
    assert missing.ok is False and "context" in missing.error.lower()

    WeixinStateStore(state_path).remember_context("bot", "user", "exact", "99")
    calls: list[dict] = []

    def fake_post(url, *, headers, json, timeout):
        calls.append({"url": url, "headers": headers, "json": json, "timeout": timeout})
        return httpx.Response(200, json={"ret": 0}, request=httpx.Request("POST", url))

    monkeypatch.setattr("coworker.connectors.weixin.httpx.post", fake_post)
    sent = send_weixin(creds, "user", "hello", "99")
    assert sent.ok is True
    assert calls[0]["json"]["msg"]["context_token"] == "exact"
    assert calls[0]["json"]["msg"]["to_user_id"] == "user"
    assert calls[0]["json"]["msg"]["message_type"] == 2
    assert calls[0]["headers"]["Authorization"] == "Bearer secret"
    assert calls[0]["headers"]["iLink-App-ClientVersion"] == ILINK_APP_CLIENT_VERSION
    decoded_uin = base64.b64decode(calls[0]["headers"]["X-WECHAT-UIN"]).decode()
    assert decoded_uin.isdigit() and 0 <= int(decoded_uin) <= 0xFFFFFFFF

    def rejected(url, *, headers, json, timeout):
        return httpx.Response(
            200,
            json={"ret": -2, "errmsg": "prepare failed"},
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr("coworker.connectors.weixin.httpx.post", rejected)
    failed = send_weixin(creds, "user", "hello", "99")
    assert failed.ok is False and "-2" in failed.error


@pytest.mark.asyncio
async def test_client_qr_request_uses_current_protocol():
    seen: dict = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen["request"] = request
        return httpx.Response(
            200,
            json={"ret": 0, "qrcode": "opaque", "qrcode_img_content": "wx://qr"},
        )

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = WeixinClient("https://ilinkai.weixin.qq.com", http=http)
    response = await client.fetch_qr(["old-token"])
    await http.aclose()

    request = seen["request"]
    assert request.method == "POST"
    assert request.url.path == "/ilink/bot/get_bot_qrcode"
    assert request.url.params["bot_type"] == "3"
    assert request.headers["ilink-app-id"] == "bot"
    assert request.headers["ilink-app-clientversion"] == ILINK_APP_CLIENT_VERSION
    assert response["qrcode"] == "opaque"


class _AdapterClient:
    def __init__(self) -> None:
        self.calls = 0
        self.started = False
        self.stopped = False
        self.block = asyncio.Event()

    async def notify_start(self):
        self.started = True

    async def notify_stop(self):
        self.stopped = True

    async def get_updates(self, cursor, *, timeout_ms):
        self.calls += 1
        if self.calls == 1:
            return {
                "ret": 0,
                "msgs": [_message()],
                "get_updates_buf": "cursor-next",
                "longpolling_timeout_ms": 1000,
            }
        await self.block.wait()
        return {"ret": 0, "msgs": [], "get_updates_buf": cursor}

    async def close(self):
        return None


@pytest.mark.asyncio
async def test_adapter_persists_context_cursor_and_stops(tmp_path):
    fake = _AdapterClient()
    adapter = WeixinAdapter(
        _profile(),
        state_path=tmp_path / "runtime.json",
        client_factory=lambda _base, _token: fake,
    )
    received = asyncio.Event()
    events = []

    async def handler(event):
        events.append(event)
        received.set()

    adapter.set_message_handler(handler)
    assert await adapter.connect() is True
    await asyncio.wait_for(received.wait(), 1)
    await adapter.disconnect()

    state = WeixinStateStore(tmp_path / "runtime.json")
    assert fake.started is True and fake.stopped is True
    assert state.cursor("bot@im.bot") == "cursor-next"
    assert state.context("bot@im.bot", "owner@im.wechat", "42") == "ctx-42"
    assert events[0].source.target == "weixin:owner@im.wechat:42"


class _StartupAdapter(BasePlatformAdapter):
    def __init__(self, platform: str, *, blocked: bool = False) -> None:
        super().__init__()
        self.platform = platform
        self.blocked = blocked
        self.connected = False

    async def connect(self) -> bool:
        if self.blocked:
            await asyncio.Event().wait()
        self.connected = True
        return True

    async def disconnect(self) -> None:
        return None

    async def send(self, chat_id, text, *, thread_id=None) -> SendResult:
        return SendResult(True)


@pytest.mark.asyncio
async def test_gateway_stalled_connector_does_not_block_weixin(monkeypatch):
    monkeypatch.setattr("coworker.connectors.gateway._CONNECT_TIMEOUT", 0.05)
    gateway = Gateway(
        settings={
            "feishu": ConnectorSettings("feishu", enabled=True),
            "weixin": ConnectorSettings("weixin", enabled=True),
        }
    )
    feishu = _StartupAdapter("feishu", blocked=True)
    weixin = _StartupAdapter("weixin")
    gateway.register(feishu)
    gateway.register(weixin)

    live = await gateway.start()
    assert live == ["weixin"]
    assert weixin.connected is True


class _LoginClient:
    def __init__(self, statuses: list[dict]) -> None:
        self.base_url = "https://ilinkai.weixin.qq.com"
        self.statuses = statuses
        self.closed = False

    async def fetch_qr(self, local_tokens):
        return {"ret": 0, "qrcode": "opaque", "qrcode_img_content": "wx://qr"}

    async def poll_qr_status(self, qrcode, verify_code=None):
        status = self.statuses.pop(0)
        if status.get("status") == "scaned":
            assert verify_code == "123456"
        return status

    async def close(self):
        self.closed = True


@pytest.mark.asyncio
async def test_qr_login_verification_and_confirmed_callback():
    statuses = [
        {"status": "need_verifycode"},
        {"status": "scaned"},
        {
            "status": "confirmed",
            "bot_token": "new-secret",
            "ilink_bot_id": "bot@im.bot",
            "ilink_user_id": "owner@im.wechat",
            "baseurl": "https://ilinkai.weixin.qq.com",
        },
    ]
    clients: list[_LoginClient] = []

    def factory(_base, _token):
        client = _LoginClient(statuses)
        clients.append(client)
        return client

    saved = []

    async def connected(credentials):
        saved.append(credentials)
        return {"account": credentials["ilink_bot_id"]}

    coordinator = WeixinLoginCoordinator(client_factory=factory)
    started = await coordinator.start(on_connected=connected)
    session_id = started["session_id"]
    for _ in range(100):
        if coordinator.status(session_id).get("state") == "need_verify_code":
            break
        await asyncio.sleep(0.01)
    assert coordinator.status(session_id)["needs_verify_code"] is True
    verified = coordinator.submit_verify_code(session_id, "123456")
    assert verified["state"] == "waiting_confirm"
    for _ in range(200):
        if coordinator.status(session_id).get("state") == "connected":
            break
        await asyncio.sleep(0.01)
    final = coordinator.status(session_id)
    assert final["state"] == "connected"
    assert final["account"] == "bot@im.bot"
    assert final["qr_content"] is None
    assert saved[0]["bot_token"] == "new-secret"
    assert "bot_token" not in final
    await coordinator.close()


@pytest.mark.asyncio
async def test_manager_completion_saves_secret_allows_scanner_and_refreshes(
    tmp_path, monkeypatch
):
    from coworker.server.manager import SessionManager

    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "state"))
    manager = SessionManager(data_dir=tmp_path / "data")
    refreshed = []

    async def refresh():
        refreshed.append(True)
        return ["weixin"]

    monkeypatch.setattr(manager, "refresh_gateway", refresh)
    result = await manager._complete_weixin_login(
        {
            "bot_token": "saved-secret",
            "ilink_bot_id": "bot@im.bot",
            "ilink_user_id": "owner@im.wechat",
            "base_url": "https://ilinkai.weixin.qq.com",
        }
    )
    profile = manager.secrets.get("weixin:default")
    assert result == {"account": "bot@im.bot"}
    assert profile["bot_token"] == "saved-secret"
    assert profile["allowed_users"] == ["owner@im.wechat"]
    assert refreshed == [True]
    await manager.aclose()


class _ApiLoginCoordinator:
    def __init__(self) -> None:
        self.state = "waiting_scan"

    async def start(self, **_kwargs):
        return {
            "ok": True,
            "session_id": "session-1",
            "state": self.state,
            "qr_content": "wx://qr",
            "expires_at": 123,
        }

    def status(self, session_id):
        assert session_id == "session-1"
        return {
            "ok": True,
            "session_id": session_id,
            "state": self.state,
            "qr_content": "wx://qr",
        }

    def submit_verify_code(self, session_id, code):
        assert session_id == "session-1" and code == "123456"
        self.state = "waiting_confirm"
        return self.status(session_id)

    async def cancel(self, session_id):
        self.state = "cancelled"
        return self.status(session_id)

    async def cancel_active(self):
        return None

    async def close(self):
        return None


def test_qr_session_routes_never_return_credentials(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from coworker.server.app import create_app
    from coworker.server.manager import SessionManager

    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "state"))
    manager = SessionManager(data_dir=tmp_path / "data")
    manager.weixin_logins = _ApiLoginCoordinator()
    with TestClient(create_app(manager)) as client:
        started = client.post("/v1/connectors/weixin/qr-sessions")
        assert started.status_code == 200
        assert started.json()["qr_content"] == "wx://qr"
        assert "bot_token" not in started.text

        verified = client.post(
            "/v1/connectors/weixin/qr-sessions/session-1/verify",
            json={"code": "123456"},
        )
        assert verified.status_code == 200
        assert verified.json()["state"] == "waiting_confirm"

        cancelled = client.delete(
            "/v1/connectors/weixin/qr-sessions/session-1"
        )
        assert cancelled.status_code == 200
        assert cancelled.json()["state"] == "cancelled"

        unsupported = client.post("/v1/connectors/telegram/qr-sessions")
        assert unsupported.status_code == 400
