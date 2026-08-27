"""WinUI 3 bubble-host protocol smoke test (Windows build output required)."""

import ctypes
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from ctypes import wintypes

import pytest


PLUGIN_DIR = Path(__file__).resolve().parents[1] / "plugins" / "floating-icon"
if not sys.platform.startswith("win"):
    pytest.skip("WinUI 3 bubble host is Windows-only", allow_module_level=True)
sys.path.insert(0, str(PLUGIN_DIR))

import floating_icon as fi  # noqa: E402


def test_python_controller_is_windowless():
    source = (PLUGIN_DIR / "floating_icon.py").read_text(encoding="utf-8")
    assert "tkinter" not in source
    assert "messagebox" not in source
    assert "root.after" not in source


def test_winui_bubble_host_ready_ping_and_shutdown():
    executable = fi._find_winui_bubble_host()
    if not executable:
        pytest.skip("WinUI 3 bubble host has not been built")

    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    process = subprocess.Popen(
        [executable],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        creationflags=flags,
    )
    commands = "\n".join(
        json.dumps(command)
        for command in (
            {"cmd": "ping", "text": "坐标 1920, 1080    选区 640 × 480"},
            {"cmd": "shutdown"},
        )
    ) + "\n"
    stdout, stderr = process.communicate(commands, timeout=15)
    events = [json.loads(line) for line in stdout.splitlines() if line.strip()]

    assert process.returncode == 0, stderr
    assert {"event": "ready"} in events
    assert {
        "event": "pong",
        "text": "坐标 1920, 1080    选区 640 × 480",
    } in events


def test_single_click_on_layered_icon_emits_one_open_event():
    executable = fi._find_winui_bubble_host()
    if not executable:
        pytest.skip("WinUI 3 bubble host has not been built")

    icon_path = (
        PLUGIN_DIR.parents[1]
        / "surfaces"
        / "gui"
        / "src-tauri"
        / "icons"
        / "Square44x44Logo.png"
    )
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    process = subprocess.Popen(
        [executable],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        creationflags=flags,
    )
    try:
        assert json.loads(process.stdout.readline()) == {"event": "ready"}
        process.stdin.write(
            json.dumps(
                {
                    "cmd": "initialize",
                    "icon_path": str(icon_path),
                    "version": "test",
                    "x": 500,
                    "y": 300,
                }
            )
            + "\n"
        )
        process.stdin.flush()
        time.sleep(0.4)

        user32 = ctypes.windll.user32
        icon_windows = []
        callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        @callback_type
        def find_icon(hwnd, _lparam):
            pid = wintypes.DWORD()
            class_name = ctypes.create_unicode_buffer(128)
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            user32.GetClassNameW(hwnd, class_name, len(class_name))
            if (
                pid.value == process.pid
                and user32.IsWindowVisible(hwnd)
                and class_name.value.startswith("OpenWorker.LayeredIcon.")
            ):
                icon_windows.append(hwnd)
            return True

        user32.EnumWindows(find_icon, 0)
        assert len(icon_windows) == 1
        user32.PostMessageW(icon_windows[0], 0x0201, 0x0001, 0)  # WM_LBUTTONDOWN
        user32.PostMessageW(icon_windows[0], 0x0202, 0, 0)  # WM_LBUTTONUP
        time.sleep(0.2)

        process.stdin.write(json.dumps({"cmd": "shutdown"}) + "\n")
        process.stdin.flush()
        process.stdin.close()
        events = [
            json.loads(line)
            for line in process.stdout.read().splitlines()
            if line.strip()
        ]
        process.wait(timeout=10)
        assert events.count({"event": "icon", "action": "open"}) == 1
        assert not any(
            event.get("event") == "icon" and event.get("action") == "moved"
            for event in events
        )
    finally:
        if process.poll() is None:
            process.kill()


def test_winui_menu_height_has_dpi_safe_layout_slack():
    source = (
        PLUGIN_DIR / "winui-bubbles" / "BubbleWindow.cs"
    ).read_text(encoding="utf-8")

    assert "Surface(panel, new Thickness(4), 12), 190, 94, 12" in source
    assert 'button.Resources["ButtonBackgroundPointerOver"] = Brush("#F5F5F5")' in source
    assert "DwmwaBorderColor" in source and "DwmColorNone" in source
    assert "DwmwaNcRenderingPolicy" in source and "DwmncrpDisabled" in source
    assert "GetDpiForWindow(_hwnd) / 96.0" in source
    surface = source.split("private static Border Surface", 1)[1].split(
        "private static Button ActionButton", 1
    )[0]
    assert "ThemeShadow" not in surface
    assert "Translation" not in surface
    assert 'BorderBrush = Brush("#D1D1D1")' in surface
    assert "BorderThickness = new Thickness(1)" in surface
    assert "WsCaption | WsSysMenu | WsThickFrame" in source
    assert "SwpFrameChanged" in source
    assert "AllowFocusOnInteraction = false" in source
    assert "UseSystemFocusVisuals = false" in source
    assert "CornerRadius = new CornerRadius(4)" in source


def test_selection_mask_matches_figma_overlay_token():
    source = (
        PLUGIN_DIR / "winui-bubbles" / "SelectionWindow.cs"
    ).read_text(encoding="utf-8")

    assert "Color.FromArgb(89, 0, 0, 0)" in source


def test_icon_uses_original_png_without_visual_adjustments():
    source = (
        PLUGIN_DIR / "winui-bubbles" / "IconWindow.cs"
    ).read_text(encoding="utf-8")

    assert "DrawImageUnscaled(bitmap, 0, 0)" in source
    assert "Stretch.UniformToFill" not in source
    assert "NativeWindow.ApplyRoundRegion" not in source
    assert "UpdateLayeredWindow" in source
    assert "WsExLayered" in source
    assert "PixelFormat.Format32bppPArgb" in source


def test_winui_toolbar_escape_cancels_selection():
    if os.environ.get("OW_ICON_GUI_TESTS") != "1":
        pytest.skip("set OW_ICON_GUI_TESTS=1 for foreground keyboard interaction")
    executable = fi._find_winui_bubble_host()
    if not executable:
        pytest.skip("WinUI 3 bubble host has not been built")

    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    process = subprocess.Popen(
        [executable],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        creationflags=flags,
    )
    try:
        assert json.loads(process.stdout.readline()) == {"event": "ready"}
        process.stdin.write(json.dumps({"cmd": "show_toolbar", "x": 700, "y": 400}) + "\n")
        process.stdin.flush()
        time.sleep(0.5)

        user32 = ctypes.windll.user32
        windows = []
        callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        @callback_type
        def collect_top(hwnd, _lparam):
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value == process.pid and user32.IsWindowVisible(hwnd):
                windows.append(hwnd)
            return True

        user32.EnumWindows(collect_top, 0)
        targets = list(windows)

        @callback_type
        def collect_child(hwnd, _lparam):
            targets.append(hwnd)
            return True

        for hwnd in windows:
            user32.EnumChildWindows(hwnd, collect_child, 0)
        assert targets
        for hwnd in targets:
            user32.PostMessageW(hwnd, 0x0100, 0x1B, 0)  # WM_KEYDOWN / VK_ESCAPE
            user32.PostMessageW(hwnd, 0x0101, 0x1B, 0)  # WM_KEYUP / VK_ESCAPE
        time.sleep(0.5)

        process.stdin.write(json.dumps({"cmd": "shutdown"}) + "\n")
        process.stdin.flush()
        process.stdin.close()
        events = [
            json.loads(line)
            for line in process.stdout.read().splitlines()
            if line.strip()
        ]
        process.wait(timeout=10)
        assert {"event": "selection", "action": "cancel"} in events
    finally:
        if process.poll() is None:
            process.kill()
