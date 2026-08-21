"""floating-icon 插件识图功能（plugins/floating-icon/floating_icon.py）测试。

覆盖：
- 纯函数 _mask_base / _poke_hole（遮罩底图、选区挖洞 + 边框、越界钳制）——
  无 GUI，始终运行；
- select_region_with_toolbar 完整交互流（提取文字 / 退出 / Esc 取消 / 点击无拖动取消）——
  会在真实桌面上短暂弹出全屏遮罩，仅在 OW_ICON_GUI_TESTS=1 且存在可用桌面时运行。
"""

import os
import sys
import tkinter as tk
from pathlib import Path

import pytest

PLUGIN_DIR = Path(__file__).resolve().parents[1] / "plugins" / "floating-icon"
if not sys.platform.startswith("win"):
    pytest.skip("floating-icon 仅支持 Windows（ctypes.windll）", allow_module_level=True)
sys.path.insert(0, str(PLUGIN_DIR))

import floating_icon as fi  # noqa: E402


# ---------------------------------------------------------------------------
# 纯函数：遮罩底图 / 挖洞 / 边框
# ---------------------------------------------------------------------------

def test_mask_base_is_black_semi_transparent():
    w, h = 4, 3
    base = fi._mask_base(w, h)
    assert len(base) == w * h * 4
    assert base[:4] == bytes((0, 0, 0, int(255 * fi.MASK_DIM)))


def test_poke_hole_zeros_hole_and_draws_border():
    w, h = 16, 16
    base = bytearray(b"\x11\x22\x33\x44") * (w * h)
    fi._poke_hole(base, w, h, (4, 4, 12, 12))
    # 洞内 alpha 归零（露出真实屏幕）
    assert base[(6 * w + 6) * 4 + 3] == 0
    # 远离洞的角落不受影响
    assert base[(15 * w + 0) * 4 + 3] == 0x44
    # 洞上边框（y=3，x=6）为青蓝 #00e5ff -> BGRA (255, 229, 0, 255)
    s = (3 * w + 6) * 4
    assert base[s:s + 4] == b"\xff\xe5\x00\xff"


def test_poke_hole_clamps_at_edges_and_ignores_degenerate():
    w, h = 6, 6
    base = bytearray(b"\xff" * (w * h * 4))
    # 越界洞：被钳制到画布内，不抛异常
    fi._poke_hole(base, w, h, (-5, -5, 100, 100))
    assert base[(0 * w + 0) * 4 + 3] == 0
    assert base[(5 * w + 5) * 4 + 3] == 0
    # 零尺寸洞：不修改任何像素
    buf2 = bytearray(b"\xff" * (w * h * 4))
    fi._poke_hole(buf2, w, h, (3, 3, 3, 3))
    assert buf2 == bytearray(b"\xff" * (w * h * 4))


# ---------------------------------------------------------------------------
# 交互流（GUI）：需要真实桌面，仅 OW_ICON_GUI_TESTS=1 时运行
# ---------------------------------------------------------------------------

def _gui_available():
    if os.environ.get("OW_ICON_GUI_TESTS") != "1":
        return False
    try:
        root = tk.Tk()
        root.destroy()
        return True
    except Exception:
        return False


def _walk(widget, pred):
    found = []
    for child in widget.winfo_children():
        if pred(child):
            found.append(child)
        found.extend(_walk(child, pred))
    return found


def _canvas(root):
    return _walk(root, lambda w: isinstance(w, tk.Canvas))[0]


def _button(root, text):
    return _walk(root, lambda w: isinstance(w, tk.Button) and w.cget("text") == text)[0]


def _run_flow(driver):
    """root.after 驱动 + select_region_with_toolbar（阻塞到选择完成），返回其结果。"""
    root = tk.Tk()
    root.withdraw()
    root.after(250, lambda: driver(root))
    try:
        return fi.select_region_with_toolbar(root)
    finally:
        root.destroy()


def _drag(cv, root, x1, y1, x2, y2, after_release=None):
    def step_press():
        cv.event_generate("<ButtonPress-1>", x=x1, y=y1)
        root.after(60, step_motion)

    def step_motion():
        cv.event_generate("<B1-Motion>", x=x2, y=y2)
        root.after(60, step_release)

    def step_release():
        cv.event_generate("<ButtonRelease-1>", x=x2, y=y2)
        if after_release:
            root.after(80, after_release)

    root.after(0, step_press)


def _drive_extract(root):
    _drag(_canvas(root), root, 120, 120, 500, 360,
          after_release=lambda: _button(root, "提取文字").invoke())


def _drive_exit(root):
    _drag(_canvas(root), root, 120, 120, 500, 360,
          after_release=lambda: _button(root, "退出").invoke())


def _drive_esc(root):
    _drag(_canvas(root), root, 120, 120, 500, 360,
          after_release=lambda: _canvas(root).event_generate("<Escape>"))


def _drive_tiny_drag(root):
    cv = _canvas(root)
    _drag(cv, root, 10, 10, 11, 11)


@pytest.mark.skipif(not _gui_available(), reason="需 OW_ICON_GUI_TESTS=1 且存在可用桌面")
def test_flow_extract_returns_bbox_and_action():
    vx, vy, _, _ = fi._virtual_screen()
    out = _run_flow(_drive_extract)
    assert out == ((vx + 120, vy + 120, vx + 500, vy + 360), "extract")


@pytest.mark.skipif(not _gui_available(), reason="需 OW_ICON_GUI_TESTS=1 且存在可用桌面")
def test_flow_exit_returns_none():
    assert _run_flow(_drive_exit) is None


@pytest.mark.skipif(not _gui_available(), reason="需 OW_ICON_GUI_TESTS=1 且存在可用桌面")
def test_flow_esc_cancels():
    assert _run_flow(_drive_esc) is None


@pytest.mark.skipif(not _gui_available(), reason="需 OW_ICON_GUI_TESTS=1 且存在可用桌面")
def test_flow_click_without_drag_cancels():
    assert _run_flow(_drive_tiny_drag) is None
