from __future__ import annotations

import json
from types import SimpleNamespace

from coworker.connectors.config import load_settings
from coworker.connectors.adapters import make_adapter
from coworker.connectors.descriptors import get_descriptor
from coworker.connectors.feishu import (
    FeishuAdapter,
    _TOKEN_CACHE,
    feishu_message_to_event,
    list_feishu_chat_members,
    send_feishu,
)
from coworker.connectors.tools import make_send_message_tool
from coworker.secrets import SecretStore


class _Response:
    def __init__(self, data: dict, status_code: int = 200) -> None:
        self._data = data
        self.status_code = status_code

    def json(self) -> dict:
        return self._data


def test_descriptor_is_native_two_way_connector():
    descriptor = get_descriptor("feishu")
    assert descriptor is not None
    assert descriptor.available and descriptor.two_way and descriptor.channels
    assert [field.key for field in descriptor.fields] == [
        "app_id",
        "app_secret",
        "allowed_users",
    ]


def test_settings_enable_feishu_from_app_credentials(tmp_path):
    secrets = SecretStore(tmp_path / "secrets.json")
    secrets.put(
        "feishu:default",
        {
            "app_id": "cli_test",
            "app_secret": "secret",
            "allowed_users": ["ou_owner"],
        },
    )
    settings = load_settings(secrets)["feishu"]
    assert settings.enabled
    assert settings.allowed_users == {"ou_owner"}


def test_runtime_adapter_is_registered_from_app_credentials():
    adapter = make_adapter(
        "feishu", {"app_id": "cli_test", "app_secret": "secret"}
    )
    assert isinstance(adapter, FeishuAdapter)
    assert adapter.app_id == "cli_test"
    assert adapter.app_secret == "secret"


def test_channel_message_maps_to_replyable_event():
    message = SimpleNamespace(
        id="om_123",
        message_id="om_123",
        content_text="@OpenWorker 生成周报",
        conversation=SimpleNamespace(chat_id="oc_chat", chat_type="group"),
        sender=SimpleNamespace(open_id="ou_alice", display_name="Alice"),
        mentioned_bot=True,
        raw={"event_id": "evt_1"},
    )
    event = feishu_message_to_event(message)
    assert event is not None
    assert event.source.platform == "feishu"
    assert event.source.chat_id == "oc_chat"
    assert event.source.user_id == "ou_alice"
    assert event.source.thread_id == "om_123"
    assert event.source.target == "feishu:oc_chat:om_123"
    assert event.mentions_me


def test_channel_message_detects_bot_mention_by_open_id():
    message = SimpleNamespace(
        id="om_123",
        content_text="@智能助手",
        conversation=SimpleNamespace(chat_id="oc_chat", chat_type="group"),
        sender=SimpleNamespace(open_id="ou_alice", display_name="Alice"),
        mentions=[SimpleNamespace(open_id="ou_bot")],
        mentioned_bot=False,
        raw={},
    )
    event = feishu_message_to_event(message, bot_open_id="ou_bot")
    assert event is not None and event.mentions_me


def test_stateless_sender_replies_to_inbound_message(monkeypatch):
    _TOKEN_CACHE.clear()
    calls = []

    def post(url, **kwargs):
        calls.append((url, kwargs))
        if url.endswith("tenant_access_token/internal"):
            return _Response({"code": 0, "tenant_access_token": "t-token"})
        return _Response({"code": 0, "data": {"message_id": "om_reply"}})

    monkeypatch.setattr("httpx.post", post)
    result = send_feishu(
        {"app_id": "cli_test", "app_secret": "secret"},
        "oc_chat",
        "完成了",
        "om_123",
    )
    assert result.ok and result.message_id == "om_reply"
    url, request = calls[1]
    assert url.endswith("/im/v1/messages/om_123/reply")
    assert request["headers"]["Authorization"] == "Bearer t-token"
    assert json.loads(request["json"]["content"]) == {"text": "完成了"}

    # A second send reuses the tenant token instead of hitting auth again.
    again = send_feishu(
        {"app_id": "cli_test", "app_secret": "secret"}, "oc_chat", "再次完成"
    )
    assert again.ok
    assert sum(url.endswith("tenant_access_token/internal") for url, _ in calls) == 1


def test_member_list_uses_bounded_pagination(monkeypatch):
    _TOKEN_CACHE.clear()
    requested_pages = []

    def post(url, **kwargs):
        return _Response({"code": 0, "tenant_access_token": "t-token"})

    def get(url, **kwargs):
        requested_pages.append(kwargs["params"])
        if len(requested_pages) == 1:
            return _Response(
                {
                    "code": 0,
                    "data": {
                        "items": [{"name": "Alice", "member_id": "ou_a"}],
                        "member_total": 3,
                        "has_more": True,
                        "page_token": "next",
                    },
                }
            )
        return _Response(
            {
                "code": 0,
                "data": {
                    "items": [{"name": "Bob", "member_id": "ou_b"}],
                    "member_total": 3,
                    "has_more": True,
                    "page_token": "last",
                },
            }
        )

    monkeypatch.setattr("httpx.post", post)
    monkeypatch.setattr("httpx.get", get)
    members, total, truncated = list_feishu_chat_members(
        {"app_id": "cli_test", "app_secret": "secret"}, "oc_chat", limit=2
    )
    assert members == [
        {"name": "Alice", "open_id": "ou_a"},
        {"name": "Bob", "open_id": "ou_b"},
    ]
    assert total == 3 and truncated
    assert requested_pages[1]["page_token"] == "next"


def test_send_message_tool_uses_compound_feishu_credentials(tmp_path):
    secrets = SecretStore(tmp_path / "secrets.json")
    secrets.put(
        "feishu:default", {"app_id": "cli_test", "app_secret": "secret"}
    )
    seen = []

    def sender(credentials, chat_id, text, thread_id=None):
        seen.append((credentials, chat_id, text, thread_id))
        from coworker.connectors.base import SendResult

        return SendResult(True, message_id="om_ok")

    tool = make_send_message_tool(secrets, senders={"feishu": sender})
    out = tool(target="feishu:oc_chat:om_parent", text="hello")
    assert out["ok"]
    assert seen == [
        (
            {"app_id": "cli_test", "app_secret": "secret"},
            "oc_chat",
            "hello",
            "om_parent",
        )
    ]


async def test_adapter_starts_off_loop_and_dispatches_normalized_message(monkeypatch):
    class Events:
        MESSAGE = "message"

    class PolicyConfig:
        def __init__(self, **values):
            self.values = values

    class Channel:
        instance = None

        def __init__(self, **values):
            self.values = values
            self.handlers = {}
            self.connected = False
            Channel.instance = self

        def on(self, name, handler):
            self.handlers[name] = handler

        async def connect_until_ready(self, *, timeout):
            self.connected = True

        async def disconnect(self):
            self.connected = False

    monkeypatch.setattr(
        "coworker.connectors.feishu._load_channel_sdk",
        lambda: (Events, Channel, PolicyConfig),
    )
    adapter = FeishuAdapter("cli_test", "secret")
    received = []

    async def handler(event):
        received.append(event)

    adapter.set_message_handler(handler)
    assert await adapter.connect()
    message = SimpleNamespace(
        id="om_1",
        message_id="om_1",
        content_text="hello",
        conversation=SimpleNamespace(chat_id="oc_1", chat_type="p2p"),
        sender=SimpleNamespace(open_id="ou_1", display_name="Alice"),
        mentioned_bot=False,
        raw={},
    )
    await Channel.instance.handlers[Events.MESSAGE](message)
    assert received[0].source.target == "feishu:oc_1:om_1"
    await adapter.disconnect()
    assert not Channel.instance.connected


async def test_group_mention_adds_member_context(monkeypatch):
    class Events:
        MESSAGE = "message"

    class PolicyConfig:
        def __init__(self, **values):
            self.values = values

    class Channel:
        instance = None

        def __init__(self, **values):
            self.handlers = {}
            self._bot_open_id = "ou_bot"
            Channel.instance = self

        def on(self, name, handler):
            self.handlers[name] = handler

        async def connect_until_ready(self, *, timeout):
            pass

        async def disconnect(self):
            pass

    monkeypatch.setattr(
        "coworker.connectors.feishu._load_channel_sdk",
        lambda: (Events, Channel, PolicyConfig),
    )
    calls = []

    def members(credentials, chat_id, *, limit=200):
        calls.append(chat_id)
        return ([{"name": "Alice", "open_id": "ou_a"}], 1, False)

    monkeypatch.setattr(
        "coworker.connectors.feishu.list_feishu_chat_members", members
    )
    adapter = FeishuAdapter("cli_test", "secret")
    received = []

    async def handler(event):
        received.append(event)

    adapter.set_message_handler(handler)
    assert await adapter.connect()
    message = SimpleNamespace(
        id="om_mention",
        content_text="@智能助手",
        conversation=SimpleNamespace(chat_id="oc_group", chat_type="group"),
        sender=SimpleNamespace(open_id="ou_owner", display_name="Owner"),
        mentions=[SimpleNamespace(open_id="ou_bot")],
        mentioned_bot=False,
        raw={},
    )
    await Channel.instance.handlers[Events.MESSAGE](message)
    assert calls == ["oc_group"]
    assert '"Alice" (open_id: "ou_a")' in received[0].connector_context
    assert "Reply with the group member information" in received[0].tagged_text()


def test_group_message_without_bot_mention_has_no_member_context():
    message = SimpleNamespace(
        id="om_plain",
        content_text="大家好",
        conversation=SimpleNamespace(chat_id="oc_group", chat_type="group"),
        sender=SimpleNamespace(open_id="ou_owner", display_name="Owner"),
        mentions=[],
        mentioned_bot=False,
        raw={},
    )
    event = feishu_message_to_event(message, bot_open_id="ou_bot")
    assert event is not None
    assert not event.mentions_me
    assert event.connector_context is None
