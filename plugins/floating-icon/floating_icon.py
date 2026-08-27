#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import ctypes
import http.client
import json
import os
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from ctypes import wintypes
from urllib.parse import urlparse

from vision_backend import (
    OPENWORKER_HOST,
    OPENWORKER_PORT,
    VISION_ACTION_PROMPTS,
    VISION_MODEL,
    VISION_SESSION_ID,
    _image_to_base64_png,
    _load_api_token,
    _server_reachable,
    _ws_connect,
    _ws_recv_frame,
    _ws_send_frame,
    capture_screen_region,
    send_screenshot_to_openworker,
)

APP_NAME = "OpenWorker"
PLUGIN_VERSION = "v6.0"
GUI_URL = "http://localhost:1420"
ICON_SIZE = 44
MARGIN = 16
STATE_PATH = os.path.join(
    os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"),
    APP_NAME,
    "floating-icon-state.json",
)
LOCK_PATH = os.path.join(
    os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"),
    APP_NAME,
    "floating-icon.lock",
)

_user32 = ctypes.windll.user32
_kernel32 = ctypes.windll.kernel32
_kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
_kernel32.CreateMutexW.restype = wintypes.HANDLE
_kernel32.OpenProcess.restype = wintypes.HANDLE


def _find_icon():
    relative = os.path.join(
        "surfaces", "gui", "src-tauri", "icons", "Square44x44Logo.png"
    )
    directory = os.path.dirname(os.path.abspath(__file__))
    for _ in range(7):
        candidate = os.path.join(directory, relative)
        if os.path.isfile(candidate):
            return candidate
        parent = os.path.dirname(directory)
        if parent == directory:
            break
        directory = parent
    return None


def _find_winui_bubble_host():
    override = os.environ.get("OW_ICON_WINUI_BUBBLES")
    if override and os.path.isfile(override):
        return override
    project = os.path.join(os.path.dirname(os.path.abspath(__file__)), "winui-bubbles")
    name = "OpenWorker.WinUIBubbles.exe"
    candidates = [
        os.path.join(project, name),
        os.path.join(
            project, "bin", "Release", "net8.0-windows10.0.19041.0", "win-x64", name
        ),
        os.path.join(project, "publish", name),
    ]
    return next((path for path in candidates if os.path.isfile(path)), None)


def load_state():
    try:
        with open(STATE_PATH, encoding="utf-8") as file:
            value = json.load(file)
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def save_state(state):
    try:
        os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
        with open(STATE_PATH, "w", encoding="utf-8") as file:
            json.dump(state, file)
    except Exception:
        pass


class _WinUIBubbleHost:
    """WinUI 3 常驻 UI 进程的 JSON Lines 客户端。"""

    def __init__(self):
        executable = _find_winui_bubble_host()
        if not executable:
            raise FileNotFoundError("未找到 WinUI 3 宿主，请先运行 winui-bubbles\\build.ps1")
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        self.handlers = {}
        self.ready = threading.Event()
        self.ready_ok = False
        self.write_lock = threading.Lock()
        self.closed = False
        self.stderr_tail = []
        self.process = subprocess.Popen(
            [executable],
            cwd=os.path.dirname(executable),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            creationflags=flags,
        )
        threading.Thread(target=self._read_stdout, daemon=True).start()
        threading.Thread(target=self._read_stderr, daemon=True).start()
        if not self.ready.wait(10) or not self.ready_ok:
            detail = "\n".join(self.stderr_tail[-4:])
            self.close(force=True)
            raise RuntimeError(f"WinUI 3 宿主启动失败{': ' + detail if detail else ''}")

    def _read_stdout(self):
        try:
            for line in self.process.stdout:
                try:
                    event = json.loads(line)
                except Exception:
                    continue
                if event.get("event") == "ready":
                    self.ready_ok = True
                    self.ready.set()
                    continue
                handler = self.handlers.get(event.get("event"))
                if handler:
                    try:
                        handler(event)
                    except Exception as error:
                        self.stderr_tail.append(f"event handler failed: {error}")
                        del self.stderr_tail[:-20]
        finally:
            self.ready.set()

    def _read_stderr(self):
        try:
            for line in self.process.stderr:
                self.stderr_tail.append(line.rstrip())
                del self.stderr_tail[:-20]
        except Exception:
            pass

    def set_handler(self, event, handler):
        if handler is None:
            self.handlers.pop(event, None)
        else:
            self.handlers[event] = handler

    def send(self, command, **payload):
        if self.closed or self.process.poll() is not None:
            return
        body = json.dumps({"cmd": command, **payload}, ensure_ascii=False)
        try:
            with self.write_lock:
                self.process.stdin.write(body + "\n")
                self.process.stdin.flush()
        except Exception:
            pass

    def close(self, force=False):
        if self.closed:
            return
        if not force:
            self.send("shutdown")
            try:
                self.process.wait(timeout=3)
            except Exception:
                force = True
        self.closed = True
        if force and self.process.poll() is None:
            self.process.kill()

    def wait(self, timeout=None):
        return self.process.wait(timeout=timeout)


def gui_is_reachable(url):
    try:
        parsed = urlparse(url)
        with socket.create_connection((parsed.hostname, parsed.port), timeout=1):
            return True
    except OSError:
        return False


def _browser_gui_is_open():
    headers = {}
    token = _load_api_token()
    if token:
        headers["X-OpenWorker-Token"] = token
    connection = http.client.HTTPConnection(OPENWORKER_HOST, OPENWORKER_PORT, timeout=1)
    try:
        connection.request("GET", "/v1/gui/browser-presence", headers=headers)
        response = connection.getresponse()
        if response.status != 200:
            return False
        return json.loads(response.read().decode("utf-8")).get("open") is True
    except (OSError, ValueError, http.client.HTTPException):
        return False
    finally:
        connection.close()


def _open_gui_without_duplicate():
    if not gui_is_reachable(GUI_URL):
        return False
    if not _browser_gui_is_open():
        webbrowser.open(GUI_URL)
    return True


def _virtual_screen():
    return tuple(_user32.GetSystemMetrics(index) for index in (76, 77, 78, 79))


class _ProgressWin:
    def __init__(self, host):
        self.host = host
        self.cancel_event = threading.Event()
        host.set_handler("progress", lambda _event: self.cancel())
        x, y, width, height = _virtual_screen()
        host.send(
            "show_progress",
            x=x + (width - 320) // 2,
            y=y + (height - 58) // 2,
            text="正在处理截图…",
        )

    def set_msg(self, message):
        self.host.send("update_progress", text=message)

    def cancel(self):
        self.cancel_event.set()
        self.close()

    def close(self):
        self.host.send("hide_progress")
        self.host.set_handler("progress", None)


def run_vision_flow(event, host):
    action = event.get("action")
    if action not in VISION_ACTION_PROMPTS:
        return
    bbox = tuple(int(event[name]) for name in ("left", "top", "right", "bottom"))
    try:
        width, height, bgra = capture_screen_region(bbox)
    except Exception as error:
        host.send("show_icon")
        host.send("show_notice", text=f"截取屏幕失败：\n{error}")
        return
    host.send("show_icon")
    if not _server_reachable():
        host.send("show_notice", text="OpenWorker 后端未运行。\n请先启动 start-dev.bat。")
        return
    progress = _ProgressWin(host)
    names = {"extract": "提取文字", "translate": "翻译", "answer": "解答"}
    try:
        image = _image_to_base64_png(bgra, width, height)
        ok, info = send_screenshot_to_openworker(
            image,
            VISION_ACTION_PROMPTS[action],
            progress.cancel_event,
            progress.set_msg,
        )
        if progress.cancel_event.is_set():
            return
        progress.close()
        if ok:
            _open_gui_without_duplicate()
            host.send("show_notice", text=f"“{names[action]}”结果已追加到 OpenWorker 识图对话。")
        else:
            host.send("show_notice", text=info)
    except Exception as error:
        progress.close()
        host.send("show_notice", text=f"识图失败：\n{error}")


def _kill_old_instance():
    try:
        with open(LOCK_PATH, encoding="utf-8") as file:
            old_pid = int(file.read().strip())
    except Exception:
        return
    handle = _kernel32.OpenProcess(0x0001, False, old_pid)
    if handle:
        _kernel32.TerminateProcess(handle, 0)
        _kernel32.CloseHandle(handle)
        time.sleep(0.6)


def _acquire_singleton(replace):
    if replace:
        _kill_old_instance()
    for _ in range(30):
        mutex = _kernel32.CreateMutexW(None, False, "Local\\OpenWorkerFloatingIcon")
        if _kernel32.GetLastError() != 183:
            return mutex
        if not replace:
            return None
        time.sleep(0.25)
    return None


def _write_lock():
    os.makedirs(os.path.dirname(LOCK_PATH), exist_ok=True)
    with open(LOCK_PATH, "w", encoding="utf-8") as file:
        file.write(str(os.getpid()))


def _release_lock():
    try:
        with open(LOCK_PATH, encoding="utf-8") as file:
            owned = file.read().strip() == str(os.getpid())
        if owned:
            os.remove(LOCK_PATH)
    except Exception:
        pass


def main():
    replace = "--replace" in sys.argv
    smoke = "--smoke" in sys.argv
    try:
        _user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except Exception:
        _user32.SetProcessDPIAware()
    instance_mutex = _acquire_singleton(replace)
    if not instance_mutex:
        return 0
    _write_lock()
    host = None
    try:
        icon_path = _find_icon()
        if not icon_path:
            raise FileNotFoundError("找不到 Square44x44Logo.png")
        host = _WinUIBubbleHost()
        screen_x, screen_y, screen_width, screen_height = _virtual_screen()
        state = load_state()
        x = int(state.get("x", screen_x + screen_width - ICON_SIZE - MARGIN))
        y = int(state.get("y", screen_y + MARGIN))
        x = max(screen_x, min(x, screen_x + screen_width - ICON_SIZE))
        y = max(screen_y, min(y, screen_y + screen_height - ICON_SIZE))

        def on_icon(event):
            action = event.get("action")
            if action == "moved":
                save_state({"x": int(event["x"]), "y": int(event["y"])})
            elif action == "open":
                if not _open_gui_without_duplicate():
                    host.send("show_notice", text=f"OpenWorker 界面未运行。\n请先启动服务：{GUI_URL}")
            elif action == "close":
                save_state({"x": int(event["x"]), "y": int(event["y"])})
                host.close()

        def on_selection(event):
            if event.get("action") in VISION_ACTION_PROMPTS:
                threading.Thread(target=run_vision_flow, args=(event, host), daemon=True).start()

        host.set_handler("icon", on_icon)
        host.set_handler("selection", on_selection)
        host.send(
            "initialize",
            icon_path=os.path.abspath(icon_path),
            version=PLUGIN_VERSION,
            x=x,
            y=y,
        )
        if smoke:
            threading.Timer(1.5, host.close).start()
            print("[smoke] WinUI 3 floating icon launched OK", flush=True)
        host.wait()
        return 0
    except Exception as error:
        print(f"{APP_NAME}: {error}", file=sys.stderr, flush=True)
        return 1
    finally:
        if host is not None:
            host.close(force=host.process.poll() is None)
        _release_lock()
        _kernel32.CloseHandle(instance_mutex)


if __name__ == "__main__":
    raise SystemExit(main())
