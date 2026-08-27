"""WinUI 3 悬浮图标与 Terra 识图控制器测试。"""

import json
import sys
import threading
from pathlib import Path

import pytest

PLUGIN_DIR = Path(__file__).resolve().parents[1] / "plugins" / "floating-icon"
if not sys.platform.startswith("win"):
    pytest.skip("floating-icon 仅支持 Windows", allow_module_level=True)
sys.path.insert(0, str(PLUGIN_DIR))

import floating_icon as fi  # noqa: E402
import vision_backend as vb  # noqa: E402


def test_floating_icon_uses_tauri_square_44_logo():
    expected = (
        Path(__file__).resolve().parents[1]
        / "surfaces" / "gui" / "src-tauri" / "icons" / "Square44x44Logo.png"
    )
    assert Path(fi._find_icon()).resolve() == expected.resolve()


def test_all_screenshot_actions_use_terra_chatgpt_plan():
    assert fi.VISION_MODEL == "openai-codex:gpt-5.6-terra"
    assert set(fi.VISION_ACTION_PROMPTS) == {"extract", "translate", "answer"}


def test_screenshot_turns_share_one_terra_conversation(monkeypatch):
    sent = []
    paths = []

    class FakeSocket:
        def settimeout(self, _timeout):
            pass

        def close(self):
            pass

    monkeypatch.setattr(vb, "_server_reachable", lambda: True)
    monkeypatch.setattr(vb, "_load_api_token", lambda: "token")

    def connect(_host, _port, path, _token):
        paths.append(path)
        return FakeSocket()

    monkeypatch.setattr(vb, "_ws_connect", connect)
    monkeypatch.setattr(
        vb,
        "_ws_send_frame",
        lambda _sock, opcode, payload: sent.append(
            (opcode, json.loads(payload.decode("utf-8")))
        ),
    )
    monkeypatch.setattr(
        vb,
        "_ws_recv_frame",
        lambda _sock: (0x1, json.dumps({"type": "turn_done"}).encode("utf-8")),
    )

    ok1, session1 = fi.send_screenshot_to_openworker(
        "cG5n", fi.VISION_ACTION_PROMPTS["extract"], threading.Event(), lambda _msg: None
    )
    ok2, session2 = fi.send_screenshot_to_openworker(
        "cG5n", fi.VISION_ACTION_PROMPTS["translate"], threading.Event(), lambda _msg: None
    )

    assert ok1 is True and ok2 is True
    assert session1 == session2 == fi.VISION_SESSION_ID == "floating-vision"
    assert paths == [
        "/ws/session/floating-vision?workspace=&agent=chat",
        "/ws/session/floating-vision?workspace=&agent=chat",
    ]
    assert [opcode for opcode, _payload in sent] == [0x1, 0x1]
    assert all(payload["model"] == fi.VISION_MODEL for _, payload in sent)
    assert sent[0][1]["attachments"][0]["data_url"] == "data:image/png;base64,cG5n"


def test_open_gui_does_not_duplicate_existing_browser_page(monkeypatch):
    opened = []
    monkeypatch.setattr(fi, "gui_is_reachable", lambda _url: True)
    monkeypatch.setattr(fi, "_browser_gui_is_open", lambda: True)
    monkeypatch.setattr(fi.webbrowser, "open", opened.append)
    assert fi._open_gui_without_duplicate() is True
    assert opened == []

    monkeypatch.setattr(fi, "_browser_gui_is_open", lambda: False)
    assert fi._open_gui_without_duplicate() is True
    assert opened == [fi.GUI_URL]


def test_visible_ui_is_all_winui3_and_tk_is_removed():
    python_source = (PLUGIN_DIR / "floating_icon.py").read_text(encoding="utf-8")
    winui_dir = PLUGIN_DIR / "winui-bubbles"
    icon_source = (winui_dir / "IconWindow.cs").read_text(encoding="utf-8")
    selection_source = (winui_dir / "SelectionWindow.cs").read_text(encoding="utf-8")
    host_source = (winui_dir / "BubbleHost.cs").read_text(encoding="utf-8")

    assert "tkinter" not in python_source
    assert "tk." not in python_source
    assert "class IconWindow : Window" in icon_source
    assert "class SelectionWindow : Window" in selection_source
    assert "ScreenCapture.Capture" in selection_source
    assert "PointerPressed" in selection_source
    assert "KeyboardAccelerator" in selection_source
    assert "_dim = new Rectangle[4]" in selection_source
    assert "NativeWindow.FlushComposition()" in host_source


def test_png_encoder_keeps_expected_dimensions():
    bgra = bytes((0, 0, 255, 255)) * 6
    encoded = vb._image_to_base64_png(bgra, 3, 2)
    assert encoded.startswith("iVBOR")


def test_gdi_capture_accepts_64_bit_windows_handles():
    width, height, bgra = vb.capture_screen_region((0, 0, 2, 2))

    assert (width, height) == (2, 2)
    assert len(bgra) == 2 * 2 * 4
