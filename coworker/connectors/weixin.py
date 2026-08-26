"""Native Weixin (WeChat ClawBot) connector over Tencent's iLink protocol.

The connector intentionally owns the transport instead of running a Node/ACP
sidecar.  Long-lived credentials stay in ``SecretStore``; the opaque update
cursor and short-lived reply contexts live in a separate user-private runtime
file next to it.  Reply context never enters message text or a model-visible
target -- targets carry only the public inbound message id.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import secrets as random_secrets
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional
from urllib.parse import urlparse

import httpx

from .. import __version__
from ..secrets import write_private_text
from .base import BasePlatformAdapter, MessageEvent, SendResult, SessionSource

logger = logging.getLogger("coworker.connectors")

ILINK_BASE_URL = "https://ilinkai.weixin.qq.com"
# This is the iLink channel protocol version implemented here, not OpenWorker's
# release number.  The encoded header is major<<16 | minor<<8 | patch.
ILINK_CHANNEL_VERSION = "2.4.6"
ILINK_APP_ID = "bot"
ILINK_APP_CLIENT_VERSION = str((2 << 16) | (4 << 8) | 6)
_API_TIMEOUT = 15.0
_QR_POLL_TIMEOUT = 35.0
_QR_TTL_SECONDS = 5 * 60
_DEFAULT_LONG_POLL_MS = 35_000
_MAX_SEEN = 512
_MAX_CONTEXTS = 512
_STATE_LOCK = threading.RLock()


class WeixinProtocolError(RuntimeError):
    """An HTTP-successful iLink response that carries a business error."""

    def __init__(self, message: str, *, code: Optional[int] = None) -> None:
        super().__init__(message)
        self.code = code


def _base_info() -> dict[str, str]:
    return {
        "channel_version": ILINK_CHANNEL_VERSION,
        "bot_agent": f"OpenWorker/{__version__}",
    }


def _common_headers() -> dict[str, str]:
    return {
        "iLink-App-Id": ILINK_APP_ID,
        "iLink-App-ClientVersion": ILINK_APP_CLIENT_VERSION,
    }


def _authenticated_headers(token: str = "") -> dict[str, str]:
    raw_uin = str(random_secrets.randbits(32)).encode("ascii")
    headers = {
        "Content-Type": "application/json",
        "AuthorizationType": "ilink_bot_token",
        "X-WECHAT-UIN": base64.b64encode(raw_uin).decode("ascii"),
        **_common_headers(),
    }
    if token.strip():
        headers["Authorization"] = f"Bearer {token.strip()}"
    return headers


def _normalize_base_url(value: Any, *, fallback: str = ILINK_BASE_URL) -> str:
    raw = str(value or fallback).strip()
    if "://" not in raw:
        raw = "https://" + raw
    parsed = urlparse(raw)
    if not parsed.hostname:
        raise ValueError("Weixin returned an invalid API host")
    # Credentials must never be sent over clear text.  Loopback is admitted so
    # integration tests can use a local fake without weakening production URLs.
    if parsed.scheme != "https" and parsed.hostname not in {
        "127.0.0.1",
        "localhost",
        "::1",
    }:
        raise ValueError("Weixin API URL must use HTTPS")
    return raw.rstrip("/")


def _endpoint(base_url: str, path: str) -> str:
    return f"{base_url.rstrip('/')}/{path.lstrip('/')}"


def _business_error(data: dict[str, Any], label: str) -> None:
    raw_code = data.get("errcode")
    if raw_code in (None, 0, "0"):
        raw_code = data.get("ret")
    if raw_code in (None, 0, "0"):
        return
    try:
        code = int(raw_code)
    except (TypeError, ValueError):
        code = None
    detail = str(data.get("errmsg") or data.get("message") or "request failed")
    raise WeixinProtocolError(f"{label} failed ({raw_code}): {detail}", code=code)


class WeixinClient:
    """Small async iLink JSON client with an injectable HTTP transport."""

    def __init__(
        self,
        base_url: str,
        token: str = "",
        *,
        http: Optional[httpx.AsyncClient] = None,
    ) -> None:
        self.base_url = _normalize_base_url(base_url)
        self.token = token
        self._http = http or httpx.AsyncClient()
        self._owns_http = http is None

    async def close(self) -> None:
        if self._owns_http:
            await self._http.aclose()

    async def _post(
        self,
        path: str,
        body: dict[str, Any],
        *,
        timeout: float = _API_TIMEOUT,
        label: str,
        token: Optional[str] = None,
    ) -> dict[str, Any]:
        response = await self._http.post(
            _endpoint(self.base_url, path),
            headers=_authenticated_headers(self.token if token is None else token),
            json=body,
            timeout=timeout,
        )
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            raise WeixinProtocolError(f"{label} returned invalid JSON")
        _business_error(data, label)
        return data

    async def fetch_qr(self, local_tokens: list[str]) -> dict[str, Any]:
        return await self._post(
            "ilink/bot/get_bot_qrcode?bot_type=3",
            {"local_token_list": local_tokens[:10]},
            label="get_bot_qrcode",
            token="",
        )

    async def poll_qr_status(
        self, qrcode: str, verify_code: Optional[str] = None
    ) -> dict[str, Any]:
        params = {"qrcode": qrcode}
        if verify_code:
            params["verify_code"] = verify_code
        try:
            response = await self._http.get(
                _endpoint(self.base_url, "ilink/bot/get_qrcode_status"),
                headers=_common_headers(),
                params=params,
                timeout=_QR_POLL_TIMEOUT,
            )
            response.raise_for_status()
            data = response.json()
            if not isinstance(data, dict):
                raise WeixinProtocolError("get_qrcode_status returned invalid JSON")
            _business_error(data, "get_qrcode_status")
            return data
        except (httpx.TimeoutException, httpx.RequestError):
            # The endpoint is a long poll.  A timeout or transient transport
            # failure means "still waiting", not a failed login.
            return {"status": "wait"}

    async def get_updates(
        self, cursor: str, *, timeout_ms: int = _DEFAULT_LONG_POLL_MS
    ) -> dict[str, Any]:
        try:
            return await self._post(
                "ilink/bot/getupdates",
                {"get_updates_buf": cursor, "base_info": _base_info()},
                timeout=max(1.0, timeout_ms / 1000),
                label="getupdates",
            )
        except httpx.TimeoutException:
            return {"ret": 0, "msgs": [], "get_updates_buf": cursor}

    async def notify_start(self) -> None:
        await self._post(
            "ilink/bot/msg/notifystart",
            {"base_info": _base_info()},
            timeout=10.0,
            label="notifystart",
        )

    async def notify_stop(self) -> None:
        await self._post(
            "ilink/bot/msg/notifystop",
            {"base_info": _base_info()},
            timeout=10.0,
            label="notifystop",
        )


def weixin_state_path(secrets_store: Any) -> Path:
    return Path(secrets_store.path).parent / "weixin-runtime.json"


class WeixinStateStore:
    """Private, bounded transport state shared by listener and stateless sends."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser()

    def _read_unlocked(self) -> dict[str, Any]:
        if not self.path.is_file():
            return {"accounts": {}}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"accounts": {}}
        if not isinstance(data, dict) or not isinstance(data.get("accounts"), dict):
            return {"accounts": {}}
        return data

    def _write_unlocked(self, data: dict[str, Any]) -> None:
        write_private_text(
            self.path, json.dumps(data, ensure_ascii=False, separators=(",", ":"))
        )

    @staticmethod
    def _account(data: dict[str, Any], account_id: str) -> dict[str, Any]:
        accounts = data.setdefault("accounts", {})
        account = accounts.setdefault(account_id, {})
        if not isinstance(account, dict):
            account = {}
            accounts[account_id] = account
        return account

    def cursor(self, account_id: str) -> str:
        with _STATE_LOCK:
            account = self._read_unlocked().get("accounts", {}).get(account_id, {})
            return str(account.get("cursor") or "") if isinstance(account, dict) else ""

    def set_cursor(self, account_id: str, cursor: str) -> None:
        if not cursor:
            return
        with _STATE_LOCK:
            data = self._read_unlocked()
            self._account(data, account_id)["cursor"] = cursor
            self._write_unlocked(data)

    def remember_context(
        self,
        account_id: str,
        user_id: str,
        context_token: str,
        message_id: Optional[str],
    ) -> None:
        if not user_id or not context_token:
            return
        with _STATE_LOCK:
            data = self._read_unlocked()
            account = self._account(data, account_id)
            latest = account.setdefault("latest_context", {})
            latest[user_id] = context_token
            if message_id:
                contexts = account.setdefault("message_contexts", {})
                order = account.setdefault("context_order", [])
                contexts[message_id] = {
                    "user_id": user_id,
                    "token": context_token,
                }
                if message_id in order:
                    order.remove(message_id)
                order.append(message_id)
                while len(order) > _MAX_CONTEXTS:
                    contexts.pop(order.pop(0), None)
            self._write_unlocked(data)

    def context(
        self, account_id: str, user_id: str, message_id: Optional[str] = None
    ) -> Optional[str]:
        with _STATE_LOCK:
            account = self._read_unlocked().get("accounts", {}).get(account_id, {})
            if not isinstance(account, dict):
                return None
            if message_id:
                item = (account.get("message_contexts") or {}).get(message_id)
                if isinstance(item, dict) and item.get("user_id") == user_id:
                    return str(item.get("token") or "") or None
            return str((account.get("latest_context") or {}).get(user_id) or "") or None

    def seen(self, account_id: str, message_id: str) -> bool:
        with _STATE_LOCK:
            account = self._read_unlocked().get("accounts", {}).get(account_id, {})
            return bool(
                isinstance(account, dict)
                and message_id in (account.get("seen_ids") or [])
            )

    def mark_seen(self, account_id: str, message_id: str) -> None:
        if not message_id:
            return
        with _STATE_LOCK:
            data = self._read_unlocked()
            account = self._account(data, account_id)
            seen = account.setdefault("seen_ids", [])
            if message_id in seen:
                return
            seen.append(message_id)
            del seen[:-_MAX_SEEN]
            self._write_unlocked(data)

    def clear_account(self, account_id: Optional[str] = None) -> None:
        with _STATE_LOCK:
            data = self._read_unlocked()
            if account_id:
                data.get("accounts", {}).pop(account_id, None)
            else:
                data = {"accounts": {}}
            self._write_unlocked(data)


def _message_id(message: dict[str, Any]) -> str:
    for key in ("message_id", "client_id"):
        value = message.get(key)
        if value not in (None, ""):
            return str(value)
    stable = {
        "from_user_id": message.get("from_user_id"),
        "create_time_ms": message.get("create_time_ms"),
        "item_list": message.get("item_list"),
    }
    encoded = json.dumps(stable, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return "sha256-" + hashlib.sha256(encoded).hexdigest()


def weixin_message_to_event(message: Any) -> Optional[MessageEvent]:
    """Map one finished user text/voice-transcript message to OpenWorker."""
    if not isinstance(message, dict):
        return None
    if message.get("message_type") != 1:
        return None
    if message.get("message_state") not in (None, 2):
        return None
    user_id = str(message.get("from_user_id") or "").strip()
    if not user_id:
        return None
    parts: list[str] = []
    for item in message.get("item_list") or []:
        if not isinstance(item, dict):
            continue
        text = ""
        if item.get("type") == 1:
            text = str((item.get("text_item") or {}).get("text") or "")
        elif item.get("type") == 3:
            text = str((item.get("voice_item") or {}).get("text") or "")
        if text.strip():
            parts.append(text.strip())
    if not parts:
        return None
    message_id = _message_id(message)
    return MessageEvent(
        text="\n".join(parts),
        source=SessionSource(
            platform="weixin",
            chat_id=user_id,
            user_id=user_id,
            chat_type="dm",
            # This public id selects the exact short-lived reply context without
            # exposing that context itself to the model.
            thread_id=message_id,
        ),
        message_id=message_id,
    )


def send_weixin(
    credentials: Any,
    chat_id: str,
    text: str,
    thread_id: Optional[str] = None,
) -> SendResult:
    """Stateless text sender used by ``send_message`` and automations."""
    if not isinstance(credentials, dict):
        return SendResult(False, error="invalid Weixin credentials")
    token = str(credentials.get("bot_token") or "")
    account_id = str(credentials.get("ilink_bot_id") or "")
    base_url = str(credentials.get("base_url") or "")
    state_path = credentials.get("state_path") or credentials.get("_state_path")
    if not token or not account_id or not base_url or not state_path:
        return SendResult(False, error="incomplete Weixin credentials")
    context = WeixinStateStore(state_path).context(account_id, chat_id, thread_id)
    if not context:
        return SendResult(
            False,
            error=(
                "Weixin reply context is unavailable; ask the user to message "
                "the ClawBot again before replying."
            ),
        )
    client_id = "openworker-weixin-" + uuid.uuid4().hex
    payload = {
        "msg": {
            "from_user_id": "",
            "to_user_id": chat_id,
            "client_id": client_id,
            "message_type": 2,
            "message_state": 2,
            "context_token": context,
            "item_list": [{"type": 1, "text_item": {"text": text}}],
        },
        "base_info": _base_info(),
    }
    try:
        response = httpx.post(
            _endpoint(_normalize_base_url(base_url), "ilink/bot/sendmessage"),
            headers=_authenticated_headers(token),
            json=payload,
            timeout=_API_TIMEOUT,
        )
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            return SendResult(False, error="Weixin send returned invalid JSON")
        _business_error(data, "sendmessage")
    except Exception as exc:
        return SendResult(False, error=str(exc))
    return SendResult(True, message_id=client_id)


class WeixinAdapter(BasePlatformAdapter):
    platform = "weixin"

    def __init__(
        self,
        profile: dict[str, Any],
        *,
        state_path: str | Path,
        client_factory: Callable[[str, str], WeixinClient] = WeixinClient,
    ) -> None:
        super().__init__()
        self.profile = dict(profile)
        self.bot_token = str(profile.get("bot_token") or "")
        self.account_id = str(profile.get("ilink_bot_id") or "")
        self.base_url = _normalize_base_url(profile.get("base_url"))
        self.state = WeixinStateStore(state_path)
        self.state_path = Path(state_path)
        self._client_factory = client_factory
        self._client: Optional[WeixinClient] = None
        self._task: Optional[asyncio.Task[None]] = None
        self._stopping = asyncio.Event()
        self.needs_reconnect = False
        self.last_error: Optional[str] = None

    async def connect(self) -> bool:
        if not self.bot_token or not self.account_id:
            return False
        self._stopping.clear()
        self.needs_reconnect = False
        self.last_error = None
        self._client = self._client_factory(self.base_url, self.bot_token)
        try:
            await self._client.notify_start()
        except Exception:
            # Presence reconciliation is useful but must not make a transient
            # notify failure prevent the actual long-poll listener from starting.
            logger.warning("Weixin notifystart failed", exc_info=True)
        self._task = asyncio.create_task(
            self._poll_loop(), name=f"weixin-poll-{self.account_id}"
        )
        logger.info("weixin adapter polling for account %s", self.account_id)
        return True

    async def _poll_loop(self) -> None:
        assert self._client is not None
        cursor = self.state.cursor(self.account_id)
        timeout_ms = _DEFAULT_LONG_POLL_MS
        backoff = 1.0
        while not self._stopping.is_set():
            try:
                response = await self._client.get_updates(
                    cursor, timeout_ms=timeout_ms
                )
                messages = response.get("msgs") or []
                if not isinstance(messages, list):
                    raise WeixinProtocolError("getupdates returned invalid msgs")
                for message in messages:
                    if not isinstance(message, dict):
                        continue
                    message_id = _message_id(message)
                    if self.state.seen(self.account_id, message_id):
                        continue
                    user_id = str(message.get("from_user_id") or "")
                    context = str(message.get("context_token") or "")
                    if user_id and context:
                        self.state.remember_context(
                            self.account_id,
                            user_id,
                            context,
                            message_id,
                        )
                    event = weixin_message_to_event(message)
                    if event is not None:
                        await self.handle_message(event)
                    self.state.mark_seen(self.account_id, message_id)
                next_cursor = str(response.get("get_updates_buf") or "")
                if next_cursor and next_cursor != cursor:
                    self.state.set_cursor(self.account_id, next_cursor)
                    cursor = next_cursor
                suggested = response.get("longpolling_timeout_ms")
                if suggested is not None:
                    try:
                        # A small transport cushion lets the server finish its own
                        # long poll before the client timeout fires.
                        timeout_ms = min(120_000, max(5_000, int(suggested) + 5_000))
                    except (TypeError, ValueError):
                        pass
                backoff = 1.0
            except asyncio.CancelledError:
                break
            except WeixinProtocolError as exc:
                self.last_error = str(exc)
                if exc.code == -14:
                    self.needs_reconnect = True
                    logger.error(
                        "Weixin session expired for account %s; scan again",
                        self.account_id,
                    )
                    break
                logger.warning("Weixin getupdates failed: %s", exc)
                await self._backoff(backoff)
                backoff = min(30.0, backoff * 2)
            except Exception as exc:
                self.last_error = str(exc)
                logger.warning("Weixin long poll failed; retrying", exc_info=True)
                await self._backoff(backoff)
                backoff = min(30.0, backoff * 2)

    async def _backoff(self, seconds: float) -> None:
        try:
            await asyncio.wait_for(self._stopping.wait(), timeout=seconds)
        except asyncio.TimeoutError:
            pass

    async def disconnect(self) -> None:
        self._stopping.set()
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        client, self._client = self._client, None
        if client is not None:
            try:
                await client.notify_stop()
            except Exception:
                logger.warning("Weixin notifystop failed", exc_info=True)
            await client.close()

    async def send(
        self, chat_id: str, text: str, *, thread_id: Optional[str] = None
    ) -> SendResult:
        credentials = {
            "bot_token": self.bot_token,
            "ilink_bot_id": self.account_id,
            "base_url": self.base_url,
            "state_path": str(self.state_path),
        }
        return await asyncio.to_thread(
            send_weixin, credentials, chat_id, text, thread_id
        )


@dataclass
class WeixinLoginFlow:
    session_id: str
    qrcode: str
    qr_content: str
    created_at: float
    expires_at: float
    state: str = "waiting_scan"
    current_base_url: str = ILINK_BASE_URL
    error: Optional[str] = None
    account: Optional[str] = None
    user_id: Optional[str] = None
    pending_verify_code: Optional[str] = None
    verify_event: asyncio.Event = field(default_factory=asyncio.Event, repr=False)
    task: Optional[asyncio.Task[None]] = field(default=None, repr=False)

    def public(self) -> dict[str, Any]:
        active = self.state in {
            "waiting_scan",
            "waiting_confirm",
            "need_verify_code",
        }
        return {
            "ok": True,
            "session_id": self.session_id,
            "state": self.state,
            "qr_content": self.qr_content if active else None,
            "expires_at": self.expires_at,
            "account": self.account,
            "needs_verify_code": self.state == "need_verify_code",
            "error": self.error,
        }


ConnectedCallback = Callable[[dict[str, Any]], Awaitable[dict[str, Any] | None]]


class WeixinLoginCoordinator:
    """Own in-memory QR sessions; only confirmed credentials reach the callback."""

    _TERMINAL = {"connected", "expired", "cancelled", "error"}

    def __init__(
        self,
        *,
        client_factory: Callable[[str, str], WeixinClient] = WeixinClient,
    ) -> None:
        self._client_factory = client_factory
        self._flows: dict[str, WeixinLoginFlow] = {}

    async def start(
        self,
        *,
        on_connected: ConnectedCallback,
        existing_profile: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        await self.cancel_active()
        self._purge()
        existing = dict(existing_profile or {})
        local_tokens = [str(existing.get("bot_token") or "")]
        local_tokens = [token for token in local_tokens if token]
        client = self._client_factory(ILINK_BASE_URL, "")
        try:
            response = await client.fetch_qr(local_tokens)
        except Exception:
            logger.warning("could not start Weixin QR login", exc_info=True)
            return {
                "ok": False,
                "error": "Could not create a Weixin QR code. Check the network and try again.",
            }
        finally:
            await client.close()
        qrcode = str(response.get("qrcode") or "")
        qr_content = str(response.get("qrcode_img_content") or "")
        if not qrcode or not qr_content:
            return {"ok": False, "error": "Weixin did not return a QR code"}
        now = time.time()
        flow = WeixinLoginFlow(
            session_id=uuid.uuid4().hex,
            qrcode=qrcode,
            qr_content=qr_content,
            created_at=now,
            expires_at=now + _QR_TTL_SECONDS,
        )
        self._flows[flow.session_id] = flow
        flow.task = asyncio.create_task(
            self._monitor(flow, on_connected, existing),
            name=f"weixin-login-{flow.session_id}",
        )
        return flow.public()

    def status(self, session_id: str) -> dict[str, Any]:
        flow = self._flows.get(session_id)
        if flow is None:
            return {"ok": False, "error": "QR session not found"}
        return flow.public()

    def submit_verify_code(self, session_id: str, code: str) -> dict[str, Any]:
        flow = self._flows.get(session_id)
        if flow is None:
            return {"ok": False, "error": "QR session not found"}
        code = str(code or "").strip()
        if flow.state != "need_verify_code":
            return {"ok": False, "error": "This QR session is not asking for a code"}
        if not code.isdigit() or not (4 <= len(code) <= 12):
            return {"ok": False, "error": "Enter the numeric code shown in WeChat"}
        flow.pending_verify_code = code
        flow.state = "waiting_confirm"
        flow.error = None
        flow.verify_event.set()
        return flow.public()

    async def cancel(self, session_id: str) -> dict[str, Any]:
        flow = self._flows.get(session_id)
        if flow is None:
            return {"ok": False, "error": "QR session not found"}
        if flow.state in self._TERMINAL:
            return {"ok": True, "session_id": session_id, "state": flow.state}
        flow.state = "cancelled"
        if flow.task is not None:
            flow.task.cancel()
            await asyncio.gather(flow.task, return_exceptions=True)
            flow.task = None
        return flow.public()

    async def cancel_active(self) -> None:
        for flow in list(self._flows.values()):
            if flow.state not in self._TERMINAL:
                await self.cancel(flow.session_id)

    async def close(self) -> None:
        await self.cancel_active()

    def _purge(self) -> None:
        cutoff = time.time() - 15 * 60
        for session_id, flow in list(self._flows.items()):
            if flow.created_at < cutoff and flow.state in self._TERMINAL:
                self._flows.pop(session_id, None)

    async def _monitor(
        self,
        flow: WeixinLoginFlow,
        on_connected: ConnectedCallback,
        existing_profile: dict[str, Any],
    ) -> None:
        client = self._client_factory(flow.current_base_url, "")
        try:
            while time.time() < flow.expires_at:
                if flow.state == "need_verify_code":
                    remaining = max(0.1, flow.expires_at - time.time())
                    flow.verify_event.clear()
                    try:
                        await asyncio.wait_for(flow.verify_event.wait(), remaining)
                    except asyncio.TimeoutError:
                        flow.state = "expired"
                        return
                client.base_url = _normalize_base_url(flow.current_base_url)
                response = await client.poll_qr_status(
                    flow.qrcode, flow.pending_verify_code
                )
                status = str(response.get("status") or "wait")
                if status == "wait":
                    if flow.state != "waiting_confirm":
                        flow.state = "waiting_scan"
                elif status == "scaned":
                    flow.pending_verify_code = None
                    flow.state = "waiting_confirm"
                elif status == "need_verifycode":
                    flow.state = "need_verify_code"
                    continue
                elif status == "verify_code_blocked":
                    flow.state = "error"
                    flow.error = "Too many incorrect pairing codes. Create a new QR code."
                    return
                elif status == "expired":
                    flow.state = "expired"
                    return
                elif status == "scaned_but_redirect":
                    redirect_host = str(response.get("redirect_host") or "")
                    if not redirect_host:
                        raise WeixinProtocolError(
                            "Weixin redirect response did not include a host"
                        )
                    flow.current_base_url = _normalize_base_url(redirect_host)
                    flow.state = "waiting_confirm"
                elif status == "binded_redirect":
                    required = ("bot_token", "ilink_bot_id", "ilink_user_id")
                    if all(existing_profile.get(key) for key in required):
                        flow.state = "connected"
                        flow.account = str(
                            existing_profile.get("account")
                            or existing_profile.get("ilink_bot_id")
                        )
                        flow.user_id = str(existing_profile.get("ilink_user_id") or "")
                    else:
                        flow.state = "error"
                        flow.error = (
                            "This ClawBot is already bound, but this OpenWorker has no "
                            "saved credentials for it. Disconnect it at the previous "
                            "installation before scanning again."
                        )
                    return
                elif status == "confirmed":
                    credentials = {
                        "bot_token": str(response.get("bot_token") or ""),
                        "ilink_bot_id": str(response.get("ilink_bot_id") or ""),
                        "ilink_user_id": str(response.get("ilink_user_id") or ""),
                        "base_url": _normalize_base_url(
                            response.get("baseurl") or flow.current_base_url
                        ),
                    }
                    missing = [key for key, value in credentials.items() if not value]
                    if missing:
                        raise WeixinProtocolError(
                            "Weixin confirmation omitted " + ", ".join(missing)
                        )
                    result = await on_connected(credentials) or {}
                    flow.account = str(
                        result.get("account") or credentials["ilink_bot_id"]
                    )
                    flow.user_id = credentials["ilink_user_id"]
                    flow.state = "connected"
                    return
                else:
                    raise WeixinProtocolError(
                        f"Weixin returned unknown QR status: {status}"
                    )
                await asyncio.sleep(1)
            flow.state = "expired"
        except asyncio.CancelledError:
            if flow.state not in self._TERMINAL:
                flow.state = "cancelled"
            raise
        except Exception as exc:
            logger.warning("Weixin QR login failed", exc_info=True)
            flow.state = "error"
            if isinstance(exc, (WeixinProtocolError, ValueError)):
                flow.error = str(exc)
            else:
                # HTTP exceptions include the full request URL. QR status URLs
                # carry an opaque login id, so never echo those exceptions to GUI.
                flow.error = "Weixin sign-in failed. Check the network and try again."
        finally:
            await client.close()
            flow.task = None


def clear_weixin_runtime(path: str | Path, account_id: Optional[str] = None) -> None:
    WeixinStateStore(path).clear_account(account_id)
