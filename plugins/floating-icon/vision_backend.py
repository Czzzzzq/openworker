"""OpenWorker 悬浮识图的无窗口截图、编码与 WebSocket 客户端。"""

import base64
import binascii
import ctypes
import hashlib
import json
import os
import socket
import struct
import threading
import time
import zlib
from ctypes import wintypes

VISION_MODEL = "openai-codex:gpt-5.6-terra"
VISION_SESSION_ID = "floating-vision"
OPENWORKER_HOST = "127.0.0.1"
OPENWORKER_PORT = 8765
TURN_TIMEOUT = 240
MAX_IMAGE_SIDE = 1280

VISION_ACTION_PROMPTS = {
    "extract": (
        "请提取这张截图中的全部文字内容，按原布局顺序完整输出，"
        "保留原语言，不要翻译、不要总结、不要添加任何解释。"
    ),
    "translate": (
        "请识别并翻译这张截图中的文字：若原文不是中文请翻译成中文，"
        "若原文已是中文请翻译成英文。只输出翻译结果。"
    ),
    "answer": (
        "请仔细观察这张截图，给出完整、有条理的回答：先概括画面核心内容，"
        "再解释界面、图表、数据或代码；有歧义或缺失时明确说明，不要编造。"
    ),
}


def _state_dirs():
    directories = []
    configured = os.environ.get("COWORKER_STATE_DIR")
    if configured:
        directories.append(configured)
    directory = os.path.dirname(os.path.abspath(__file__))
    for _ in range(7):
        candidate = os.path.join(directory, ".dev-state")
        if os.path.isdir(candidate):
            directories.append(candidate)
        parent = os.path.dirname(directory)
        if parent == directory:
            break
        directory = parent
    appdata = os.environ.get("APPDATA")
    if appdata:
        directories.append(os.path.join(appdata, "coworker"))
    directories.append(os.path.join(os.path.expanduser("~"), ".config", "coworker"))
    return directories


def _load_api_token():
    configured = os.environ.get("COWORKER_API_TOKEN")
    if configured and configured.strip():
        return configured.strip()
    for directory in _state_dirs():
        try:
            with open(
                os.path.join(directory, f"sidecar-{OPENWORKER_PORT}.token"),
                encoding="utf-8",
            ) as file:
                token = file.read().strip()
            if token:
                return token
        except Exception:
            continue
    return ""


def _recv_exact(sock, size):
    data = b""
    while len(data) < size:
        chunk = sock.recv(size - len(data))
        if not chunk:
            raise ConnectionError("OpenWorker 连接被关闭")
        data += chunk
    return data


def _ws_send_frame(sock, opcode, payload):
    length = len(payload)
    if length < 126:
        head = bytes([0x80 | opcode, 0x80 | length])
    elif length < 65536:
        head = bytes([0x80 | opcode, 0x80 | 126]) + struct.pack(">H", length)
    else:
        head = bytes([0x80 | opcode, 0x80 | 127]) + struct.pack(">Q", length)
    mask = os.urandom(4)
    sock.sendall(head + mask + bytes(value ^ mask[i % 4] for i, value in enumerate(payload)))


def _ws_recv_frame(sock):
    while True:
        header = _recv_exact(sock, 2)
        opcode = header[0] & 0x0F
        length = header[1] & 0x7F
        if length == 126:
            length = struct.unpack(">H", _recv_exact(sock, 2))[0]
        elif length == 127:
            length = struct.unpack(">Q", _recv_exact(sock, 8))[0]
        if header[1] & 0x80:
            mask = _recv_exact(sock, 4)
            payload = bytes(
                value ^ mask[i % 4]
                for i, value in enumerate(_recv_exact(sock, length))
            )
        else:
            payload = _recv_exact(sock, length)
        if opcode == 0x9:
            _ws_send_frame(sock, 0xA, payload)
            continue
        if opcode == 0xA:
            continue
        if opcode == 0x8:
            raise ConnectionError("OpenWorker 关闭了连接")
        return opcode, payload


def _ws_connect(host, port, path, token, timeout=15):
    sock = socket.create_connection((host, port), timeout=timeout)
    sock.settimeout(timeout)
    key = base64.b64encode(os.urandom(16)).decode("ascii")
    protocols = "openworker" + (f", {token}" if token else "")
    request = (
        f"GET {path} HTTP/1.1\r\nHost: {host}:{port}\r\n"
        "Upgrade: websocket\r\nConnection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n"
        f"Sec-WebSocket-Protocol: {protocols}\r\n\r\n"
    )
    try:
        sock.sendall(request.encode("latin-1"))
        response = b""
        while b"\r\n\r\n" not in response:
            chunk = sock.recv(4096)
            if not chunk:
                raise ConnectionError("OpenWorker 连接被关闭（握手失败）")
            response += chunk
        head = response.split(b"\r\n\r\n", 1)[0]
        status = head.split(b"\r\n", 1)[0].decode("latin-1", "replace")
        if b" 101 " not in head:
            raise RuntimeError(f"OpenWorker WebSocket 握手失败：{status}")
        expected = base64.b64encode(
            hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode("ascii")).digest()
        )
        accept = None
        for line in head.split(b"\r\n")[1:]:
            name, _, value = line.partition(b":")
            if name.strip().lower() == b"sec-websocket-accept":
                accept = value.strip()
                break
        if accept != expected:
            raise RuntimeError("OpenWorker WebSocket 握手校验失败")
        return sock
    except Exception:
        sock.close()
        raise


def _server_reachable():
    try:
        with socket.create_connection((OPENWORKER_HOST, OPENWORKER_PORT), timeout=1):
            return True
    except OSError:
        return False


def send_screenshot_to_openworker(image_b64, text, cancel, on_status):
    if not _server_reachable():
        raise RuntimeError("OpenWorker 后端未运行，请先启动 start-dev.bat。")
    payload = {
        "type": "user_message",
        "text": text,
        "model": VISION_MODEL,
        "attachments": [{
            "kind": "image",
            "name": "screenshot.png",
            "mime": "image/png",
            "data_url": "data:image/png;base64," + image_b64,
        }],
    }
    sock = _ws_connect(
        OPENWORKER_HOST,
        OPENWORKER_PORT,
        f"/ws/session/{VISION_SESSION_ID}?workspace=&agent=chat",
        _load_api_token(),
    )
    try:
        _ws_send_frame(sock, 0x1, json.dumps(payload).encode("utf-8"))
        deadline = time.time() + TURN_TIMEOUT
        while True:
            if cancel.is_set():
                return False, "已取消等待（识别仍在 OpenWorker 对话中继续）"
            if time.time() > deadline:
                return False, "等待超时，结果仍会输出到 OpenWorker 识图对话"
            sock.settimeout(30)
            try:
                opcode, data = _ws_recv_frame(sock)
            except socket.timeout:
                continue
            if opcode != 0x1:
                continue
            try:
                event = json.loads(data.decode("utf-8"))
            except Exception:
                continue
            kind = event.get("type", "")
            if kind == "turn_done":
                return True, VISION_SESSION_ID
            if kind in ("input_rejected", "error"):
                detail = event.get("data") or {}
                return False, str(detail.get("error") or detail.get("message") or kind)
            if kind == "ready":
                on_status("已连接 OpenWorker，正在发送截图…")
            elif kind == "turn_start":
                on_status("GPT-5.6 Terra · ChatGPT plan 正在处理截图…")
    except ConnectionError as error:
        return False, str(error)
    finally:
        sock.close()


class _BitmapInfoHeader(ctypes.Structure):
    _fields_ = [
        ("size", wintypes.DWORD), ("width", ctypes.c_long), ("height", ctypes.c_long),
        ("planes", wintypes.WORD), ("bit_count", wintypes.WORD),
        ("compression", wintypes.DWORD), ("size_image", wintypes.DWORD),
        ("x_pixels", ctypes.c_long), ("y_pixels", ctypes.c_long),
        ("colors_used", wintypes.DWORD), ("colors_important", wintypes.DWORD),
    ]


class _BitmapInfo(ctypes.Structure):
    _fields_ = [("header", _BitmapInfoHeader), ("colors", wintypes.DWORD * 3)]


_user32 = ctypes.windll.user32
_gdi32 = ctypes.windll.gdi32
_user32.GetDC.argtypes = [wintypes.HWND]
_user32.GetDC.restype = wintypes.HDC
_user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
_user32.ReleaseDC.restype = ctypes.c_int
_gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
_gdi32.CreateCompatibleDC.restype = wintypes.HDC
_gdi32.CreateDIBSection.argtypes = [
    wintypes.HDC,
    ctypes.POINTER(_BitmapInfo),
    wintypes.UINT,
    ctypes.POINTER(ctypes.c_void_p),
    wintypes.HANDLE,
    wintypes.DWORD,
]
_gdi32.CreateDIBSection.restype = wintypes.HBITMAP
_gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
_gdi32.SelectObject.restype = wintypes.HGDIOBJ
_gdi32.BitBlt.argtypes = [
    wintypes.HDC,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    wintypes.HDC,
    ctypes.c_int,
    ctypes.c_int,
    wintypes.DWORD,
]
_gdi32.BitBlt.restype = wintypes.BOOL
_gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
_gdi32.DeleteObject.restype = wintypes.BOOL
_gdi32.DeleteDC.argtypes = [wintypes.HDC]
_gdi32.DeleteDC.restype = wintypes.BOOL


def capture_screen_region(bbox):
    left, top, right, bottom = (int(value) for value in bbox)
    width, height = right - left, bottom - top
    if width <= 0 or height <= 0:
        raise RuntimeError("无效的截图区域")
    screen = _user32.GetDC(None)
    memory = _gdi32.CreateCompatibleDC(screen)
    try:
        info = _BitmapInfo()
        info.header.size = ctypes.sizeof(_BitmapInfoHeader)
        info.header.width = width
        info.header.height = -height
        info.header.planes = 1
        info.header.bit_count = 32
        bits = ctypes.c_void_p()
        bitmap = _gdi32.CreateDIBSection(memory, ctypes.byref(info), 0, ctypes.byref(bits), None, 0)
        if not bitmap:
            raise RuntimeError("创建截图位图失败")
        try:
            previous = _gdi32.SelectObject(memory, bitmap)
            if not _gdi32.BitBlt(memory, 0, 0, width, height, screen, left, top, 0x40CC0020):
                raise RuntimeError("截取屏幕失败")
            _gdi32.SelectObject(memory, previous)
            return width, height, ctypes.string_at(bits, width * height * 4)
        finally:
            _gdi32.DeleteObject(bitmap)
    finally:
        _gdi32.DeleteDC(memory)
        _user32.ReleaseDC(None, screen)


def _png_encode_rgb(width, height, rgb):
    def chunk(tag, payload):
        body = tag + payload
        return struct.pack(">I", len(payload)) + body + struct.pack(">I", binascii.crc32(body) & 0xFFFFFFFF)

    raw = bytearray(width * height * 3 + height)
    position = 0
    for row in range(height):
        raw[position] = 0
        position += 1
        length = width * 3
        raw[position:position + length] = rgb[row * length:(row + 1) * length]
        position += length
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(bytes(raw), 6)) + chunk(b"IEND", b"")
    )


def _resize_bgra(bgra, width, height, new_width, new_height):
    output = bytearray(new_width * new_height * 4)
    x_scale = width / float(new_width)
    y_scale = height / float(new_height)
    for y in range(new_height):
        source_y = min(int(y * y_scale), height - 1)
        for x in range(new_width):
            source_x = min(int(x * x_scale), width - 1)
            source = (source_y * width + source_x) * 4
            target = (y * new_width + x) * 4
            output[target:target + 4] = bgra[source:source + 4]
    return bytes(output), new_width, new_height


def _image_to_base64_png(bgra, width, height):
    if max(width, height) > MAX_IMAGE_SIDE:
        scale = MAX_IMAGE_SIDE / float(max(width, height))
        bgra, width, height = _resize_bgra(
            bgra, width, height, max(1, int(width * scale)), max(1, int(height * scale))
        )
    rgb = bytearray(width * height * 3)
    rgb[0::3] = bgra[2::4]
    rgb[1::3] = bgra[1::4]
    rgb[2::3] = bgra[0::4]
    return base64.b64encode(_png_encode_rgb(width, height, bytes(rgb))).decode("ascii")
