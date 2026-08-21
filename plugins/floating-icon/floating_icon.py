#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
OpenWorker 悬浮窗
=================

用法:
    直接双击 start-floating-icon.bat 启动，或运行:
        pythonw floating_icon.py

交互:
    - 按住左键拖动      : 移动位置
    - 左键双击          : 在浏览器打开 OpenWorker 界面
    - 右键菜单          : 打开界面 / 置顶开关 / 识图(截屏提问) / 退出
    - 鼠标悬停          : 显示提示
    - 位置自动记忆      : 退出时保存，下次启动恢复

识图（截屏提问）:
    右键菜单选择「识图 · 截屏提问」，进入全屏遮罩截图层：整屏变暗，拖动框选区域时
    选区处挖洞露出真实画面，并实时显示鼠标屏幕坐标与选区宽高；
    松开后在屏幕底部弹出无边框悬浮工具栏：提取文字 / 翻译 / 解答 / 退出。
    选择后先用 qwen-vl-max（DashScope）把截图读成文字，再把识别内容
    作为新对话发送给 OpenWorker 后端（agent=chat），由 Qwen3 Max
    （qwen:qwen3-max）在对话中作答；退出则关闭截图。qwen3-max 为纯
    文本模型，对话中携带的是识别文字（配置视觉模型时才会附带截图）。
    需要 OpenWorker 后端已启动（start-dev.bat）且已配置 Qwen/DashScope
    API Key。模型可用环境变量或 floating-icon-config.json 覆盖
    （vision_model 为对话模型，vision_extract_model 为视觉预读模型）。


命令行参数:
    --smoke   自检模式: 启动 1.5 秒后自动退出（用于验证脚本可正常运行）
"""
import base64
import ctypes
import hashlib
import json
import os
import socket
import struct
import sys
import threading
import time
import tkinter as tk
import urllib.error
import urllib.request
import webbrowser
from ctypes import wintypes
from tkinter import messagebox
from urllib.parse import urlparse

APP_NAME = "OpenWorker"
PLUGIN_VERSION = "v4.3"  # 显示在悬停提示中，便于确认运行的是最新版本
GUI_URL = "http://localhost:1420"  # OpenWorker GUI 地址（vite dev, strictPort=1420）
def _find_icon():
    """定位 OpenWorker 图标 (surfaces/gui/src-tauri/icons/Square44x44Logo.png)。

    优先按仓库标准布局: <repo>/plugins/floating-icon -> <repo>/surfaces/...；
    插件被复制到别处时向上逐级搜索；找不到返回 None。
    """
    rel = os.path.join("surfaces", "gui", "src-tauri", "icons", "Square44x44Logo.png")
    here = os.path.dirname(os.path.abspath(__file__))
    candidate = os.path.join(os.path.dirname(os.path.dirname(here)), rel)
    if os.path.isfile(candidate):
        return candidate
    d = here
    for _ in range(6):
        d = os.path.dirname(d)
        candidate = os.path.join(d, rel)
        if os.path.isfile(candidate):
            return candidate
    return None
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

# ---------------------------------------------------------------------------
# 识图（截屏提问）配置
# ---------------------------------------------------------------------------
# qwen-vl-max 预读截图时的动作提示词（qwen3-max 本身不能看图，先由视觉模型读成文字）
VL_ACTION_PROMPTS = {
    "extract": (
        "请提取这张截图中的全部文字内容，按原布局顺序完整输出，"
        "保留原语言，不要翻译、不要总结、不要添加任何解释。"
    ),
    "translate": (
        "请识别并翻译这张截图中的文字：若原文不是中文请翻译成中文，"
        "若原文已是中文请翻译成英文。只输出翻译结果。"
    ),
    "answer": (
        "请仔细观察这张截图，给出完整、有条理的回答："
        "先概括画面核心内容（可见文字原文照抄），再解释界面/图表/数据/代码等说明了什么，"
        "指出值得注意的细节；有歧义或缺失时明确说明，不要编造。"
    ),
}
# 发送到 OpenWorker 新对话（qwen3-max 作答）时的引导语
CONVERSATION_LEADS = {
    "extract": (
        "（悬浮窗识图 · 提取文字）请根据下面的截图内容，"
        "输出完整清晰的文字（保留原语言，不要翻译、不要总结）：\n\n"
    ),
    "translate": (
        "（悬浮窗识图 · 翻译）请根据下面的截图内容，"
        "输出最终翻译（原文非中文→中文，中文→英文）：\n\n"
    ),
    "answer": (
        "（悬浮窗识图 · 解答）请根据下面的截图内容，"
        "给出完整、有条理的回答：\n\n"
    ),
}

SIZE = 44          # 悬浮窗直径（像素，圆形）
MARGIN = 16        # 默认位置距屏幕边缘的间距

# ---------------------------------------------------------------------------
# Win32: 分层窗口（每像素 alpha，不需要透明色）
# ---------------------------------------------------------------------------
_user32 = ctypes.windll.user32
_gdi32 = ctypes.windll.gdi32

GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x00080000
GWLP_WNDPROC = -4
WM_NCHITTEST = 0x0084
HTTRANSPARENT = -1
AC_SRC_OVER = 0x00
AC_SRC_ALPHA = 0x01
ULW_ALPHA = 0x00000002


class _BLENDFUNCTION(ctypes.Structure):
    _fields_ = [
        ("BlendOp", ctypes.c_byte),
        ("BlendFlags", ctypes.c_byte),
        ("SourceConstantAlpha", ctypes.c_byte),
        ("AlphaFormat", ctypes.c_byte),
    ]


class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", ctypes.c_long),
        ("biHeight", ctypes.c_long),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", ctypes.c_long),
        ("biYPelsPerMeter", ctypes.c_long),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class _BITMAPINFO(ctypes.Structure):
    _fields_ = [
        ("bmiHeader", _BITMAPINFOHEADER),
        ("bmiColors", wintypes.DWORD * 3),
    ]


class _POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class _SIZE(ctypes.Structure):
    _fields_ = [("cx", ctypes.c_long), ("cy", ctypes.c_long)]


# 显式声明 64 位指针/句柄参数，避免 ctypes 默认 32 位转换导致句柄截断/溢出
_user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
_user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
_user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
_user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
_user32.CallWindowProcW.argtypes = [ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
_user32.CallWindowProcW.restype = ctypes.c_ssize_t
_user32.GetDC.argtypes = [wintypes.HWND]
_user32.GetDC.restype = wintypes.HDC
_user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
_user32.ReleaseDC.restype = ctypes.c_int
_user32.UpdateLayeredWindow.argtypes = [
    wintypes.HWND, wintypes.HDC, ctypes.POINTER(_POINT), ctypes.POINTER(_SIZE),
    wintypes.HDC, ctypes.POINTER(_POINT), wintypes.COLORREF, ctypes.POINTER(_BLENDFUNCTION), wintypes.DWORD,
]
_user32.UpdateLayeredWindow.restype = wintypes.BOOL
_user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
_user32.GetWindowRect.restype = wintypes.BOOL

_gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
_gdi32.CreateCompatibleDC.restype = wintypes.HDC
_gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
_gdi32.DeleteObject.restype = wintypes.BOOL
_gdi32.DeleteDC.argtypes = [wintypes.HDC]
_gdi32.DeleteDC.restype = wintypes.BOOL
_gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
_gdi32.SelectObject.restype = wintypes.HGDIOBJ
_gdi32.CreateDIBSection.argtypes = [
    wintypes.HDC, ctypes.POINTER(_BITMAPINFO), wintypes.UINT,
    ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE, wintypes.DWORD,
]
_gdi32.CreateDIBSection.restype = wintypes.HBITMAP
_gdi32.BitBlt.argtypes = [
    wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    wintypes.HDC, ctypes.c_int, ctypes.c_int, wintypes.DWORD,
]
_gdi32.BitBlt.restype = wintypes.BOOL

# GetAncestor / 单实例互斥体 / 无控制台错误弹窗
GA_ROOT = 2
ERROR_ALREADY_EXISTS = 183
MB_ICONERROR = 0x10
_user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
_user32.GetAncestor.restype = wintypes.HWND
_user32.MessageBoxW.argtypes = [wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.UINT]
_user32.MessageBoxW.restype = ctypes.c_int
_kernel32 = ctypes.windll.kernel32
_kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
_kernel32.CreateMutexW.restype = wintypes.HANDLE
_kernel32.GetLastError.argtypes = []
_kernel32.GetLastError.restype = wintypes.DWORD
_kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
_kernel32.OpenProcess.restype = wintypes.HANDLE
_kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
_kernel32.TerminateProcess.restype = wintypes.BOOL
_kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
_kernel32.CloseHandle.restype = wintypes.BOOL


# 子类化窗口过程所需的回调（必须保持引用，避免被 GC 回收导致崩溃）
_WNDPROC = ctypes.WINFUNCTYPE(
    ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
)
_wndproc_holder = []
_subclass_originals = {}  # hwnd -> 原窗口过程指针（SetWindowLongPtrW 返回值），按窗口分开保存


def _subclass_window(hwnd, handler):
    """子类化窗口：handler(hwnd, msg, wparam, lparam, orig) -> LRESULT。

    多个窗口各自保存原窗口过程，互不覆盖（悬浮窗圆外穿透、识图 HUD 穿透共用）。
    """
    def proc(hwnd, msg, wparam, lparam):
        return handler(hwnd, msg, wparam, lparam, _subclass_originals.get(hwnd, 0))

    cb = _WNDPROC(proc)
    _wndproc_holder.append(cb)
    _subclass_originals[hwnd] = _user32.SetWindowLongPtrW(
        hwnd, GWLP_WNDPROC, ctypes.cast(cb, ctypes.c_void_p).value
    )
    return cb


# ---------------------------------------------------------------------------
# GDI+（图标 PNG 解码/缩放，仅依赖 gdiplus.dll，无需 Pillow）
# ---------------------------------------------------------------------------
PIXEL_FORMAT_32ARGB = 0x0026200A  # PixelFormat32bppARGB
INTERP_HQ_BICUBIC = 7  # InterpolationModeHighQualityBicubic
GDI_OK = 0


class _GdiplusStartupInput(ctypes.Structure):
    _fields_ = [
        ("GdiplusVersion", wintypes.DWORD),
        ("DebugEventCallback", ctypes.c_void_p),
        ("SuppressBackgroundThread", wintypes.BOOL),
        ("SuppressExternalCodecs", wintypes.BOOL),
    ]


class _GdiplusStartupOutput(ctypes.Structure):
    _fields_ = [
        ("NotificationHook", ctypes.c_void_p),
        ("NotificationUnhook", ctypes.c_void_p),
    ]


class _GpRect(ctypes.Structure):
    _fields_ = [
        ("X", ctypes.c_int), ("Y", ctypes.c_int),
        ("Width", ctypes.c_int), ("Height", ctypes.c_int),
    ]


class _BitmapData(ctypes.Structure):
    _fields_ = [
        ("Width", wintypes.DWORD), ("Height", wintypes.DWORD),
        ("Stride", ctypes.c_int), ("PixelFormat", ctypes.c_int),
        ("Scan0", ctypes.c_void_p), ("Reserved", ctypes.c_void_p),
    ]


_gdiplus = ctypes.windll.gdiplus
_gdiplus.GdiplusStartup.argtypes = [
    ctypes.POINTER(ctypes.c_size_t),
    ctypes.POINTER(_GdiplusStartupInput),
    ctypes.POINTER(_GdiplusStartupOutput),
]
_gdiplus.GdiplusStartup.restype = ctypes.c_int
_gdiplus.GdipCreateBitmapFromFile.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_void_p)]
_gdiplus.GdipCreateBitmapFromFile.restype = ctypes.c_int
_gdiplus.GdipCreateBitmapFromScan0.argtypes = [
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p),
]
_gdiplus.GdipCreateBitmapFromScan0.restype = ctypes.c_int
_gdiplus.GdipGetImageGraphicsContext.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
_gdiplus.GdipGetImageGraphicsContext.restype = ctypes.c_int
_gdiplus.GdipGraphicsClear.argtypes = [ctypes.c_void_p, wintypes.DWORD]
_gdiplus.GdipGraphicsClear.restype = ctypes.c_int
_gdiplus.GdipSetInterpolationMode.argtypes = [ctypes.c_void_p, ctypes.c_int]
_gdiplus.GdipSetInterpolationMode.restype = ctypes.c_int
_gdiplus.GdipDrawImageRectI.argtypes = [
    ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
]
_gdiplus.GdipDrawImageRectI.restype = ctypes.c_int
_gdiplus.GdipBitmapLockBits.argtypes = [
    ctypes.c_void_p, ctypes.POINTER(_GpRect), wintypes.DWORD, ctypes.c_int, ctypes.POINTER(_BitmapData),
]
_gdiplus.GdipBitmapLockBits.restype = ctypes.c_int
_gdiplus.GdipBitmapUnlockBits.argtypes = [ctypes.c_void_p, ctypes.POINTER(_BitmapData)]
_gdiplus.GdipBitmapUnlockBits.restype = ctypes.c_int
_gdiplus.GdipDisposeImage.argtypes = [ctypes.c_void_p]
_gdiplus.GdipDisposeImage.restype = ctypes.c_int
_gdiplus.GdipDeleteGraphics.argtypes = [ctypes.c_void_p]
_gdiplus.GdipDeleteGraphics.restype = ctypes.c_int

_gdiplus_token = [0]


def _gdiplus_ensure():
    """进程内一次性初始化 GDI+（失败时抛出 RuntimeError）。"""
    if _gdiplus_token[0]:
        return
    inp = _GdiplusStartupInput()
    inp.GdiplusVersion = 1
    out = _GdiplusStartupOutput()
    token = ctypes.c_size_t()
    status = _gdiplus.GdiplusStartup(ctypes.byref(token), ctypes.byref(inp), ctypes.byref(out))
    if status != GDI_OK:
        raise RuntimeError(f"GDI+ 初始化失败 (status={status})")
    _gdiplus_token[0] = token.value


def load_icon_bgra(size: int):
    """读取 OpenWorker 图标：GDI+ 解码并按高质量缩放到 size×size，圆形裁剪后返回 BGRA 字节。

    圆外像素 alpha 直接置 0（真透明），无抗锯齿、无描边。
    仅依赖 Windows GDI+（gdiplus.dll），无需 Pillow。
    """
    icon_path = _find_icon()
    if icon_path is None:
        raise FileNotFoundError("未找到 OpenWorker 图标 (surfaces/gui/src-tauri/icons/Square44x44Logo.png)")
    _gdiplus_ensure()

    src = ctypes.c_void_p()
    status = _gdiplus.GdipCreateBitmapFromFile(icon_path, ctypes.byref(src))
    if status != GDI_OK or not src.value:
        raise RuntimeError(f"GDI+ 无法解码图标 (status={status})")
    target = ctypes.c_void_p()
    try:
        status = _gdiplus.GdipCreateBitmapFromScan0(size, size, 0, PIXEL_FORMAT_32ARGB, None, ctypes.byref(target))
        if status != GDI_OK or not target.value:
            raise RuntimeError(f"GDI+ 创建位图失败 (status={status})")
        try:
            g = ctypes.c_void_p()
            status = _gdiplus.GdipGetImageGraphicsContext(target, ctypes.byref(g))
            if status != GDI_OK or not g.value:
                raise RuntimeError(f"GDI+ 获取绘图上下文失败 (status={status})")
            try:
                _gdiplus.GdipGraphicsClear(g, 0x00000000)  # 透明黑底，避免与未初始化像素混合
                _gdiplus.GdipSetInterpolationMode(g, INTERP_HQ_BICUBIC)
                _gdiplus.GdipDrawImageRectI(g, src, 0, 0, size, size)
            finally:
                _gdiplus.GdipDeleteGraphics(g)
            rect = _GpRect(0, 0, size, size)
            bd = _BitmapData()
            status = _gdiplus.GdipBitmapLockBits(
                target, ctypes.byref(rect), 1, PIXEL_FORMAT_32ARGB, ctypes.byref(bd)
            )
            if status != GDI_OK or not bd.Scan0:
                raise RuntimeError(f"GDI+ 读取图标像素失败 (status={status})")
            try:
                data = ctypes.string_at(bd.Scan0, size * size * 4)
            finally:
                _gdiplus.GdipBitmapUnlockBits(target, ctypes.byref(bd))
        finally:
            _gdiplus.GdipDisposeImage(target)
    finally:
        _gdiplus.GdipDisposeImage(src)

    # 圆形裁剪（BGRA：每像素 4 字节，索引 3 为 alpha）
    out = bytearray(data)
    cx = cy = (size - 1) / 2.0
    r = size / 2.0
    for y in range(size):
        row = y * size * 4
        for x in range(size):
            dx, dy = x - cx, y - cy
            if dx * dx + dy * dy > r * r:
                out[row + x * 4 + 3] = 0  # 圆外 alpha=0 -> 完全透明
    return bytes(out)


def _apply_layered_icon(root, bgra, size, hwnd=None):
    """把 BGRA 图像按像素真透明绘制到 Tk 窗口（UpdateLayeredWindow）。

    圆外 alpha=0 的像素在屏幕上完全透明（露出桌面），无需透明色。
    hwnd 默认为 root.winfo_id()；传入 GetAncestor(GA_ROOT) 的顶层窗口
    可避免 Tk 子窗口结构导致的坐标双重偏移。
    """
    w = h = size
    data = bgra
    hwnd = hwnd or root.winfo_id()

    # 1) 加 WS_EX_LAYERED 扩展样式
    ex = _user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE) or 0
    _user32.SetWindowLongPtrW(hwnd, GWL_EXSTYLE, ex | WS_EX_LAYERED)

    # 2) 创建 32bpp top-down DIB，把像素拷进去
    bmi = _BITMAPINFO()
    bmi.bmiHeader.biSize = ctypes.sizeof(_BITMAPINFOHEADER)
    bmi.bmiHeader.biWidth = w
    bmi.bmiHeader.biHeight = -h  # 负值 = top-down
    bmi.bmiHeader.biPlanes = 1
    bmi.bmiHeader.biBitCount = 32
    bmi.bmiHeader.biCompression = 0
    hdc_mem = _gdi32.CreateCompatibleDC(None)
    bits = ctypes.c_void_p()
    hbmp = _gdi32.CreateDIBSection(hdc_mem, ctypes.byref(bmi), 0, ctypes.byref(bits), None, 0)
    if not hbmp:
        return
    _gdi32.SelectObject(hdc_mem, hbmp)
    ctypes.memmove(bits, data, len(data))

    # 3) UpdateLayeredWindow：真 alpha 合成
    pt_dst = _POINT(root.winfo_rootx(), root.winfo_rooty())
    sz = _SIZE(w, h)
    pt_src = _POINT(0, 0)
    blend = _BLENDFUNCTION(AC_SRC_OVER, 0, 255, AC_SRC_ALPHA)
    hdc_screen = _user32.GetDC(None)
    _user32.UpdateLayeredWindow(
        hwnd, hdc_screen, ctypes.byref(pt_dst), ctypes.byref(sz),
        hdc_mem, ctypes.byref(pt_src), 0, ctypes.byref(blend), ULW_ALPHA,
    )
    _user32.ReleaseDC(None, hdc_screen)

    # 位图/DC 在进程生命周期内保持（分层窗口由 DWM 持续引用内容）
    _apply_layered_icon._keep = (hdc_mem, hbmp)


def _install_click_through(root, size, hwnd=None):
    """圆外点击穿透（WM_NCHITTEST 返回 HTTRANSPARENT），圆内正常接收事件。"""
    hwnd = hwnd or root.winfo_id()
    radius = size / 2.0
    center = (size - 1) / 2.0

    def handler(hwnd, msg, wparam, lparam, orig):
        if msg == WM_NCHITTEST:
            # lParam: 低16位 x, 高16位 y（屏幕坐标，有符号）
            x = ctypes.c_short(lparam & 0xFFFF).value
            y = ctypes.c_short((lparam >> 16) & 0xFFFF).value
            rect = wintypes.RECT()
            _user32.GetWindowRect(hwnd, ctypes.byref(rect))
            dx, dy = (x - rect.left) - center, (y - rect.top) - center
            if dx * dx + dy * dy > radius * radius:
                return HTTRANSPARENT
        return _user32.CallWindowProcW(orig, hwnd, msg, wparam, lparam)

    _subclass_window(hwnd, handler)


def _install_pass_through(hwnd):
    """整窗点击穿透（识图 HUD 用，避免悬浮提示挡住遮罩层的鼠标事件）。"""
    def handler(hwnd, msg, wparam, lparam, orig):
        if msg == WM_NCHITTEST:
            return HTTRANSPARENT
        return _user32.CallWindowProcW(orig, hwnd, msg, wparam, lparam)

    _subclass_window(hwnd, handler)


def _fatal(msg):
    """pythonw 无控制台窗口，用弹窗报告致命错误并退出。"""
    try:
        _user32.MessageBoxW(None, msg, APP_NAME, MB_ICONERROR)
    except Exception:
        pass
    sys.exit(1)


def load_state():
    try:
        with open(STATE_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(state):
    try:
        os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
        with open(STATE_PATH, "w", encoding="utf-8") as f:
            json.dump(state, f)
    except Exception:
        pass


def gui_is_reachable(url: str) -> bool:
    try:
        p = urlparse(url)
        with socket.create_connection((p.hostname, p.port), timeout=1):
            return True
    except OSError:
        return False


# ---------------------------------------------------------------------------
# 识图（截屏提问）：qwen-vl-max 预读 + OpenWorker 新对话（qwen3-max 作答）
# ---------------------------------------------------------------------------
def _load_plugin_config():
    """读取本插件同目录的 floating-icon-config.json（可选），用于覆盖模型等配置。

    字段: vision_model / reason_model / deepseek_base_url。
    优先级: 环境变量 OW_ICON_* > 配置文件 > 默认值。
    """
    cfg = {}
    try:
        path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "floating-icon-config.json"
        )
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            cfg = data
    except Exception:
        pass
    return cfg


_PLUGIN_CFG = _load_plugin_config()
# 对话模型：OpenWorker 新对话中作答的模型（接受完整路由 id，裸名默认加 qwen: 前缀）
VISION_MODEL = (
    os.environ.get("OW_ICON_VISION_MODEL")
    or _PLUGIN_CFG.get("vision_model")
    or "qwen:qwen3-max"
).strip()
# 视觉预读模型：把截图读成文字（qwen3-max 不能看图，用同一 DashScope Key 的 qwen-vl-max）
VISION_EXTRACT_MODEL = (
    os.environ.get("OW_ICON_VL_MODEL")
    or _PLUGIN_CFG.get("vision_extract_model")
    or "qwen-vl-max"
).strip()
OPENWORKER_HOST = "127.0.0.1"
OPENWORKER_PORT = 8765  # openworker-server 端口（GUI 前端在 1420，二者不同）
TURN_TIMEOUT = 240  # 等待 OpenWorker 回合完成的最长秒数
MAX_IMAGE_SIDE = 1280  # 截图长边超过该值先缩小，控制请求体积


def _state_dirs():
    """OpenWorker 可能的状态目录（优先级从高到低）。

    1) $COWORKER_STATE_DIR   —— 显式指定（start-dev.bat 指向 .dev-state）
    2) <仓库>/.dev-state     —— 本插件在仓库内时的开发状态目录（向上搜索）
    3) %APPDATA%\\coworker    —— 生产桌面应用状态目录
    4) ~/.config/coworker
    """
    dirs = []
    sd = os.environ.get("COWORKER_STATE_DIR")
    if sd:
        dirs.append(sd)
    d = os.path.dirname(os.path.abspath(__file__))
    for _ in range(6):
        cand = os.path.join(d, ".dev-state")
        if os.path.isdir(cand):
            dirs.append(cand)
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    appdata = os.environ.get("APPDATA")
    if appdata:
        dirs.append(os.path.join(appdata, "coworker"))
    dirs.append(os.path.join(os.path.expanduser("~"), ".config", "coworker"))
    return dirs


def _load_api_token():
    """读取 OpenWorker 后端启动令牌（sidecar-<port>.token），WS 子协议鉴权用。"""
    tok = os.environ.get("COWORKER_API_TOKEN")
    if tok and tok.strip():
        return tok.strip()
    for d in _state_dirs():
        try:
            with open(os.path.join(d, f"sidecar-{OPENWORKER_PORT}.token"), encoding="utf-8") as f:
                tok = f.read().strip()
            if tok:
                return tok
        except Exception:
            continue
    return ""


def _load_secrets():
    """读取 OpenWorker 密钥库（与 SecretStore 语义一致，支持 ${ENV_VAR} 引用）。"""
    for d in _state_dirs():
        try:
            with open(os.path.join(d, "secrets.json"), encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
        except Exception:
            continue
    return {}


def _qwen_credentials(secrets):
    """Qwen/DashScope (api_key, base_url)：环境变量优先，其次密钥库 provider:qwen。"""
    prof = secrets.get("provider:qwen") or {}
    key = os.environ.get("DASHSCOPE_API_KEY") or str(prof.get("api_key") or "").strip()
    base = (
        str(prof.get("base_url") or "").strip().rstrip("/")
        or "https://dashscope.aliyuncs.com/compatible-mode/v1"
    )
    return (key.strip() or None), base


def _http_json(url, payload, headers=None, timeout=180):
    """POST JSON 并解析响应；网络/HTTP 错误转成带可读信息的 RuntimeError。"""
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:500]
        raise RuntimeError(f"HTTP {e.code}: {detail}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"网络错误: {e.reason}") from e
    except OSError as e:
        raise RuntimeError(f"网络错误: {e}") from e
    return json.loads(raw.decode("utf-8"))


def _openai_error_text(data):
    try:
        err = data.get("error", {}) or {}
        msg = err.get("message")
        if msg:
            return f"Qwen API 错误: {msg}"
    except Exception:
        pass
    return "Qwen 未返回可用的内容"


def _route_model(model):
    """裸模型名补全为 qwen 路由 id；已含 provider 前缀的原样返回。"""
    model = (model or "").strip()
    if ":" in model:
        return model
    return "qwen:" + model


def _model_vision_likely(model):
    """对话模型是否可能支持看图（决定是否在对话中附带截图附件）。

    qwen3-max 等纯文本模型附件会被 OpenWorker 换成占位符，反而干扰作答；
    只有视觉模型（qwen-vl-*、gemini、claude、gpt 等）才附带真实截图。
    """
    m = _route_model(model).lower()
    if "vl" in m or "vision" in m:
        return True
    return m.startswith(("gemini:", "anthropic:", "openai:", "gpt-", "meta:", "zai:"))


def qwen_vision_extract(image_b64, action, api_key, base_url, model=VISION_EXTRACT_MODEL, timeout=180):
    """用 qwen-vl-max 把截图读成文字（按动作：提取文字/翻译/解答）。"""
    prompt = VL_ACTION_PROMPTS.get(action) or VL_ACTION_PROMPTS["answer"]
    payload = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64," + image_b64}},
                ],
            }
        ],
        "temperature": 0.3,
    }
    url = base_url + "/chat/completions"
    data = _http_json(
        url, payload,
        headers={"Authorization": "Bearer " + api_key},
        timeout=timeout,
    )
    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        raise RuntimeError(_openai_error_text(data))
    if not content or not str(content).strip():
        raise RuntimeError(_openai_error_text(data))
    return str(content).strip()


# ---------------------------------------------------------------------------
# 极简 WebSocket 客户端（RFC 6455，纯标准库）—— 连接 OpenWorker 后端
# ---------------------------------------------------------------------------
_WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


def _recv_exact(sock, n):
    data = b""
    while len(data) < n:
        chunk = sock.recv(n - len(data))
        if not chunk:
            raise ConnectionError("OpenWorker 连接被关闭")
        data += chunk
    return data


def _ws_send_frame(sock, opcode, payload):
    """发送一个掩码帧（客户端帧必须掩码）。"""
    n = len(payload)
    if n < 126:
        head = bytes([0x80 | opcode, 0x80 | n])
    elif n < 65536:
        head = bytes([0x80 | opcode, 0x80 | 126]) + struct.pack(">H", n)
    else:
        head = bytes([0x80 | opcode, 0x80 | 127]) + struct.pack(">Q", n)
    mask = os.urandom(4)
    sock.sendall(head + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(payload)))


def _ws_recv_frame(sock):
    """读一帧；返回 (opcode, payload)。自动回 pong、跳过 ping/pong。"""
    while True:
        h = _recv_exact(sock, 2)
        opcode = h[0] & 0x0F
        length = h[1] & 0x7F
        if length == 126:
            length = struct.unpack(">H", _recv_exact(sock, 2))[0]
        elif length == 127:
            length = struct.unpack(">Q", _recv_exact(sock, 8))[0]
        if h[1] & 0x80:
            mask = _recv_exact(sock, 4)
            payload = bytes(b ^ mask[i % 4] for i, b in enumerate(_recv_exact(sock, length)))
        else:
            payload = _recv_exact(sock, length)
        if opcode == 0x9:  # ping -> pong
            _ws_send_frame(sock, 0xA, payload)
            continue
        if opcode == 0xA:  # pong
            continue
        if opcode == 0x8:  # close
            raise ConnectionError("OpenWorker 关闭了连接")
        return opcode, payload


def _ws_connect(host, port, path, token, timeout=15):
    """RFC 6455 客户端握手。返回已连接的 socket。"""
    sock = socket.create_connection((host, port), timeout=timeout)
    sock.settimeout(timeout)
    key = base64.b64encode(os.urandom(16)).decode("ascii")
    protocols = "openworker" + (f", {token}" if token else "")
    req = (
        f"GET {path} HTTP/1.1\r\n"
        f"Host: {host}:{port}\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\n"
        "Sec-WebSocket-Version: 13\r\n"
        f"Sec-WebSocket-Protocol: {protocols}\r\n"
        "\r\n"
    )
    try:
        sock.sendall(req.encode("latin-1"))
        resp = b""
        while b"\r\n\r\n" not in resp:
            chunk = sock.recv(4096)
            if not chunk:
                raise ConnectionError("OpenWorker 连接被关闭（握手失败）")
            resp += chunk
        head = resp.split(b"\r\n\r\n", 1)[0]
        status = head.split(b"\r\n", 1)[0].decode("latin-1", "replace")
        if b" 101 " not in head:
            sock.close()
            raise RuntimeError(f"OpenWorker WebSocket 握手失败：{status}")
        # 校验 Sec-WebSocket-Accept（base64 大小写敏感，按行取真实值比较）
        expected = base64.b64encode(
            hashlib.sha1((key + _WS_GUID).encode("ascii")).digest()
        ).decode("ascii")
        accept_value = None
        for line in head.split(b"\r\n")[1:]:
            name, _, value = line.partition(b":")
            if name.strip().lower() == b"sec-websocket-accept":
                accept_value = value.strip()
                break
        if accept_value != expected.encode("ascii"):
            sock.close()
            raise RuntimeError("OpenWorker WebSocket 握手校验失败（Accept 头不匹配）")
    except Exception:
        try:
            sock.close()
        except Exception:
            pass
        raise
    return sock


def _server_reachable():
    try:
        with socket.create_connection((OPENWORKER_HOST, OPENWORKER_PORT), timeout=1):
            return True
    except OSError:
        return False


def send_screenshot_to_openworker(image_b64, text, cancel, on_status, attach_image=True):
    """把提问（含预读内容）作为新对话发送给 OpenWorker（agent=chat，模型 VISION_MODEL）。

    image_b64: PNG base64；text: 发送给对话的提问文本（含预读内容）；
    attach_image: 是否附带截图附件（纯文本模型如 qwen3-max 传 False，避免占位符干扰作答）；
    cancel: threading.Event（取消等待，回合仍在服务端继续）；
    on_status(msg): 跨线程安全的状态回调。
    返回 (ok, message)：ok=True 时 message 为 session_id；否则为错误/提示文本。
    """
    if not _server_reachable():
        raise RuntimeError(
            f"OpenWorker 后端未运行（{OPENWORKER_HOST}:{OPENWORKER_PORT}）。\n"
            "请先启动 OpenWorker（start-dev.bat）。"
        )
    token = _load_api_token()
    session_id = os.urandom(6).hex()  # 12 位 hex，与 GUI 的 newId() 同格式
    payload = {
        "type": "user_message",
        "text": text,
        "model": _route_model(VISION_MODEL),
    }
    if attach_image:
        payload["attachments"] = [
            {
                "kind": "image",
                "name": "screenshot.png",
                "mime": "image/png",
                "data_url": "data:image/png;base64," + image_b64,
            }
        ]
    sock = _ws_connect(
        OPENWORKER_HOST, OPENWORKER_PORT,
        f"/ws/session/{session_id}?workspace=&agent=chat",
        token,
    )
    try:
        _ws_send_frame(sock, 0x1, json.dumps(payload).encode("utf-8"))
    except Exception:
        try:
            sock.close()
        except Exception:
            pass
        raise

    deadline = time.time() + TURN_TIMEOUT
    try:
        while True:
            if cancel.is_set():
                return False, "已取消等待（识别仍在 OpenWorker 的新对话中继续）"
            if time.time() > deadline:
                return False, "等待超时，结果仍会输出到 OpenWorker 的新对话"
            sock.settimeout(30)
            try:
                opcode, data = _ws_recv_frame(sock)
            except socket.timeout:
                continue
            if opcode != 0x1:
                continue
            try:
                evt = json.loads(data.decode("utf-8"))
            except Exception:
                continue
            kind = evt.get("type", "")
            if kind == "turn_done":
                return True, session_id
            if kind in ("input_rejected", "error"):
                d = evt.get("data") or {}
                err = d.get("error") or d.get("message") or kind
                return False, str(err)
            if kind == "ready":
                on_status("已连接 OpenWorker，正在新建对话并发送内容…")
            elif kind == "turn_start":
                on_status(f"{_route_model(VISION_MODEL)} 正在作答…")
    except ConnectionError as e:
        return False, str(e)
    finally:
        try:
            sock.close()
        except Exception:
            pass


def _bgra_to_rgb(bgra, w, h):
    """BGRA（B,G,R,A 每像素 4 字节）→ RGB 字节流（C 速度的切片重排）。

    注意通道对应：RGB 第 0 字节取源的 R（偏移 2），第 2 字节取源的 B（偏移 0）。
    """
    del w, h  # 长度由 bgra 决定
    out = bytearray(len(bgra) // 4 * 3)
    out[0::3] = bgra[2::4]  # R
    out[1::3] = bgra[1::4]  # G
    out[2::3] = bgra[0::4]  # B
    return bytes(out)


def _png_encode_rgb(w, h, rgb):
    """把 RGB 字节流编码为 PNG（纯标准库：zlib + struct + binascii）。"""
    import binascii
    import struct
    import zlib

    def _chunk(tag, payload):
        body = tag + payload
        return (
            struct.pack(">I", len(payload))
            + body
            + struct.pack(">I", binascii.crc32(body) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)  # 8-bit, color type 2 (RGB)
    raw = bytearray(w * h * 3 + h)  # 每行 1 个 filter 字节 + w*3 个像素字节
    pos = 0
    for y in range(h):
        raw[pos] = 0  # filter: None
        pos += 1
        raw[pos:pos + w * 3] = rgb[y * w * 3:(y + 1) * w * 3]
        pos += w * 3
    out = b"\x89PNG\r\n\x1a\n"
    out += _chunk(b"IHDR", ihdr)
    out += _chunk(b"IDAT", zlib.compress(bytes(raw), 6))
    out += _chunk(b"IEND", b"")
    return out


def _resize_bgra(bgra, w, h, nw, nh):
    """双线性缩放到 nw×nh，返回 (nw, nh, BGRA 字节)。"""
    out = bytearray(nw * nh * 4)
    xs = w / float(nw)
    ys = h / float(nh)
    for y in range(nh):
        sy = min(y * ys, h - 1.0)
        y0 = int(sy)
        fy = sy - y0
        y1 = min(y0 + 1, h - 1)
        r0 = y0 * w * 4
        r1 = y1 * w * 4
        ob = y * nw * 4
        for x in range(nw):
            sx = min(x * xs, w - 1.0)
            x0 = int(sx)
            fx = sx - x0
            x1 = min(x0 + 1, w - 1)
            p00 = r0 + x0 * 4
            p01 = r0 + x1 * 4
            p10 = r1 + x0 * 4
            p11 = r1 + x1 * 4
            for c in range(4):
                v = (
                    bgra[p00 + c] * (1 - fx) * (1 - fy)
                    + bgra[p01 + c] * fx * (1 - fy)
                    + bgra[p10 + c] * (1 - fx) * fy
                    + bgra[p11 + c] * fx * fy
                )
                out[ob + x * 4 + c] = int(v + 0.5)
    return bytes(out), nw, nh


def _image_to_base64_png(bgra, w, h):
    """缩小长边并编码为 PNG base64（纯标准库），控制请求体积。"""
    if max(w, h) > MAX_IMAGE_SIDE:
        scale = MAX_IMAGE_SIDE / float(max(w, h))
        bgra, w, h = _resize_bgra(bgra, w, h, max(1, int(w * scale)), max(1, int(h * scale)))
    rgb = _bgra_to_rgb(bgra, w, h)
    return base64.b64encode(_png_encode_rgb(w, h, rgb)).decode("ascii")


def capture_screen_region(bbox):
    """用 GDI BitBlt 截取屏幕区域 (left, top, right, bottom)，返回 (w, h, BGRA 字节)。

    CAPTUREBLT 使分层窗口（含悬浮窗自身）也参与截图。
    """
    x1, y1, x2, y2 = (int(v) for v in bbox)
    w, h = x2 - x1, y2 - y1
    if w <= 0 or h <= 0:
        raise RuntimeError("无效的截图区域")
    hdc_screen = _user32.GetDC(None)
    hdc_mem = _gdi32.CreateCompatibleDC(hdc_screen)
    try:
        bmi = _BITMAPINFO()
        bmi.bmiHeader.biSize = ctypes.sizeof(_BITMAPINFOHEADER)
        bmi.bmiHeader.biWidth = w
        bmi.bmiHeader.biHeight = -h  # 负值 = top-down
        bmi.bmiHeader.biPlanes = 1
        bmi.bmiHeader.biBitCount = 32
        bmi.bmiHeader.biCompression = 0
        bits = ctypes.c_void_p()
        hbmp = _gdi32.CreateDIBSection(hdc_mem, ctypes.byref(bmi), 0, ctypes.byref(bits), None, 0)
        if not hbmp:
            raise RuntimeError("创建截图位图失败")
        try:
            old = _gdi32.SelectObject(hdc_mem, hbmp)
            # SRCCOPY | CAPTUREBLT
            _gdi32.BitBlt(hdc_mem, 0, 0, w, h, hdc_screen, x1, y1, 0x40CC0020)
            _gdi32.SelectObject(hdc_mem, old)
            data = ctypes.string_at(bits, w * h * 4)
        finally:
            _gdi32.DeleteObject(hbmp)
    finally:
        _gdi32.DeleteDC(hdc_mem)
        _user32.ReleaseDC(None, hdc_screen)
    return w, h, data


def _virtual_screen():
    """虚拟桌面范围（含所有显示器）: (x, y, w, h)。"""
    u = ctypes.windll.user32
    return (
        u.GetSystemMetrics(76),  # SM_XVIRTUALSCREEN
        u.GetSystemMetrics(77),  # SM_YVIRTUALSCREEN
        u.GetSystemMetrics(78),  # SM_CXVIRTUALSCREEN
        u.GetSystemMetrics(79),  # SM_CYVIRTUALSCREEN
    )


# ---------------------------------------------------------------------------
# 识图遮罩：全屏半透明黑色遮罩（选区挖洞露出真实画面）+ 实时坐标/选区 HUD + 底部工具栏
# ---------------------------------------------------------------------------
MASK_DIM = 0.35       # 遮罩变暗系数（遮罩为黑色、alpha=MASK_DIM*255，35% 黑叠加）
MASK_BORDER = 2       # 选区边框宽度（像素）
FRAME_INTERVAL = 0.016  # 遮罩重绘节流（≈60fps）


def _mask_base(w, h):
    """遮罩底图：整屏黑色半透明（alpha = MASK_DIM*255），挖洞（alpha=0）露出真实屏幕。

    变暗由 DWM 合成时完成（黑色半透明叠加），无需预先把截图逐像素变暗，
    底图只需生成一次，每次重绘整体拷贝后再挖洞/画边框。
    """
    return bytes((0, 0, 0, int(255 * MASK_DIM))) * (w * h)


def _poke_hole(buf, w, h, hole):
    """在遮罩底图上挖出选区（alpha=0 露出真实屏幕）并画青蓝边框。"""
    x0, y0, x1, y1 = (int(v) for v in hole)
    x0 = max(0, min(x0, w)); y0 = max(0, min(y0, h))
    x1 = max(0, min(x1, w)); y1 = max(0, min(y1, h))
    if x1 - x0 < 1 or y1 - y0 < 1:
        return
    cyan = b"\xff\xe5\x00\xff"  # BGRA: #00e5ff
    hw_ = x1 - x0
    for y in range(y0, y1):
        s = (y * w + x0) * 4
        buf[s:s + hw_ * 4] = b"\x00" * (hw_ * 4)
        if x0 >= MASK_BORDER:
            ls = (y * w + x0 - MASK_BORDER) * 4
            buf[ls:ls + MASK_BORDER * 4] = cyan * MASK_BORDER
        if x1 <= w - MASK_BORDER:
            rs = (y * w + x1) * 4
            buf[rs:rs + MASK_BORDER * 4] = cyan * MASK_BORDER
    xl = max(0, x0 - MASK_BORDER)
    xr = min(w, x1 + MASK_BORDER)
    for y in (y0 - MASK_BORDER, y0 - 1, y1, y1 + 1):
        if 0 <= y < h:
            s = (y * w + xl) * 4
            buf[s:s + (xr - xl) * 4] = cyan * (xr - xl)


class _LayeredSurface:
    """把整块 BGRA 按真 alpha 渲染到窗口（UpdateLayeredWindow），供遮罩反复重绘。"""

    def __init__(self, hwnd, vx, vy, w, h):
        self.hwnd = hwnd
        self.pt_dst = _POINT(int(vx), int(vy))
        self.sz = _SIZE(int(w), int(h))
        self.pt_src = _POINT(0, 0)
        self.blend = _BLENDFUNCTION(AC_SRC_OVER, 0, 255, AC_SRC_ALPHA)
        self.hdc_mem = _gdi32.CreateCompatibleDC(None)
        bmi = _BITMAPINFO()
        bmi.bmiHeader.biSize = ctypes.sizeof(_BITMAPINFOHEADER)
        bmi.bmiHeader.biWidth = w
        bmi.bmiHeader.biHeight = -h
        bmi.bmiHeader.biPlanes = 1
        bmi.bmiHeader.biBitCount = 32
        bmi.bmiHeader.biCompression = 0
        self.bits = ctypes.c_void_p()
        self.hbmp = _gdi32.CreateDIBSection(
            self.hdc_mem, ctypes.byref(bmi), 0, ctypes.byref(self.bits), None, 0
        )
        if not self.hbmp:
            raise RuntimeError("创建遮罩位图失败")
        _gdi32.SelectObject(self.hdc_mem, self.hbmp)
        self._size = w * h * 4
        ex = _user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE) or 0
        _user32.SetWindowLongPtrW(hwnd, GWL_EXSTYLE, ex | WS_EX_LAYERED)

    def render(self, bgra):
        ctypes.memmove(self.bits, bgra, self._size)
        hdc_screen = _user32.GetDC(None)
        try:
            _user32.UpdateLayeredWindow(
                self.hwnd, hdc_screen,
                ctypes.byref(self.pt_dst), ctypes.byref(self.sz),
                self.hdc_mem, ctypes.byref(self.pt_src),
                0, ctypes.byref(self.blend), ULW_ALPHA,
            )
        finally:
            _user32.ReleaseDC(None, hdc_screen)


def select_region_with_toolbar(root):
    """识图框选：全屏遮罩（选区挖洞露真实画面）+ 实时坐标/选区 HUD + 底部工具栏。

    返回 (bbox, action)：bbox 为虚拟屏幕坐标 (left, top, right, bottom)，
    action 为 "extract"/"translate"/"answer"；取消（Esc / 退出 / 点击无拖动）返回 None。
    """
    vx, vy, vw, vh = _virtual_screen()
    base = _mask_base(vw, vh)

    done = {"out": None}
    finished = {"yes": False}
    st = {"x1": 0, "y1": 0, "hole": None, "bbox": None, "locked": False}
    last_render = [0.0]

    def _close(out):
        if finished["yes"]:
            return
        finished["yes"] = True
        done["out"] = out
        for w in (hud, toolbar, mask):
            try:
                if w.winfo_exists():
                    w.destroy()
            except Exception:
                pass

    # ---------- 遮罩窗口（事件面 + 真 alpha 全屏渲染） ----------
    mask = tk.Toplevel(root)
    mask.overrideredirect(True)
    mask.configure(bg="#000000")
    mask.geometry(f"{vw}x{vh}+{int(vx)}+{int(vy)}")
    mask.attributes("-topmost", True)
    cv = tk.Canvas(mask, bg="#000000", highlightthickness=0, cursor="crosshair")
    cv.pack(fill="both", expand=True)
    mask.update_idletasks()
    top_hwnd = _user32.GetAncestor(mask.winfo_id(), GA_ROOT)
    surface = _LayeredSurface(top_hwnd, vx, vy, vw, vh)
    surface.render(base)  # 初始：全屏变暗，无选区

    # ---------- HUD：实时鼠标坐标 + 选区宽高（点击穿透，不挡遮罩鼠标事件） ----------
    hud = tk.Toplevel(root)
    hud.overrideredirect(True)
    hud.attributes("-topmost", True)
    hud.configure(bg="#1f1f1f")
    hud_label = tk.Label(
        hud, text="", bg="#1f1f1f", fg="#e8e8e8",
        font=("Microsoft YaHei UI", 9), padx=8, pady=4,
    )
    hud_label.pack()
    hud.withdraw()
    try:
        _install_pass_through(_user32.GetAncestor(hud.winfo_id(), GA_ROOT))
    except Exception:
        pass

    # ---------- 底部工具栏（无边框悬浮窗）：提取文字 / 翻译 / 解答 / 退出 ----------
    toolbar = tk.Toplevel(root)
    toolbar.overrideredirect(True)
    toolbar.attributes("-topmost", True)
    bar = tk.Frame(toolbar, bg="#1f1f1f", highlightbackground="#555555", highlightthickness=1)
    bar.pack(fill="both", expand=True)

    def _btn(label, action, color="#3a3a3a"):
        if action is None:
            command = lambda: _close(None)
        else:
            command = lambda a=action: _close((st["bbox"], a))
        tk.Button(
            bar, text=label, command=command,
            bg=color, fg="#ffffff", activebackground="#4a6ea9", relief="flat",
            font=("Microsoft YaHei UI", 10), padx=14, pady=4,
        ).pack(side="left", padx=3, pady=4)

    _btn("提取文字", "extract")
    _btn("翻译", "translate")
    _btn("解答", "answer")
    _btn("退出", None, color="#5a2a2a")
    toolbar.withdraw()
    toolbar.bind("<Escape>", lambda e: _close(None))

    # ---------- 遮罩重绘（节流） ----------
    def _render(force=False):
        now = time.time()
        if not force and now - last_render[0] < FRAME_INTERVAL:
            return
        last_render[0] = now
        buf = bytearray(base)
        if st["hole"] is not None:
            _poke_hole(buf, vw, vh, st["hole"])
        surface.render(bytes(buf))

    # ---------- HUD 显示 ----------
    def _hud_place(x_root, y_root, size):
        text = f"坐标 {x_root}, {y_root}"
        if size:
            text += f"    选区 {size[0]} × {size[1]}"
        hud_label.config(text=text)
        hud.update_idletasks()
        hw_, hh_ = hud.winfo_reqwidth(), hud.winfo_reqheight()
        x, y = x_root + 16, y_root + 18
        if x + hw_ > vx + vw - 4:
            x = x_root - hw_ - 16
        if y + hh_ > vy + vh - 4:
            y = y_root - hh_ - 18
        hud.geometry(f"+{int(x)}+{int(y)}")
        hud.deiconify()
        hud.lift()

    # ---------- 鼠标交互 ----------
    def on_press(e):
        if st["locked"]:
            return
        st["x1"], st["y1"] = e.x, e.y
        st["hole"] = (e.x, e.y, e.x, e.y)
        _hud_place(e.x_root, e.y_root, (0, 0))
        _render()

    def on_motion(e):
        if st["locked"]:
            return
        if st["hole"] is None:
            _hud_place(e.x_root, e.y_root, None)
            return
        x0, y0 = st["x1"], st["y1"]
        st["hole"] = (min(x0, e.x), min(y0, e.y), max(x0, e.x), max(y0, e.y))
        _hud_place(e.x_root, e.y_root, (st["hole"][2] - st["hole"][0], st["hole"][3] - st["hole"][1]))
        _render()

    def on_release(e):
        if st["locked"]:
            return
        st["locked"] = True
        hud.withdraw()
        x0, y0 = st["x1"], st["y1"]
        if abs(e.x - x0) < 4 or abs(e.y - y0) < 4:
            _close(None)  # 点击无拖动 -> 取消
            return
        l, r = sorted((x0, e.x))
        t, b = sorted((y0, e.y))
        l, r = max(0, l), min(vw, r)
        t, b = max(0, t), min(vh, b)
        if r - l < 4 or b - t < 4:
            _close(None)
            return
        st["bbox"] = (vx + l, vy + t, vx + r, vy + b)
        st["hole"] = (l, t, r, b)
        _render(force=True)
        _show_toolbar()

    # ---------- 底部工具栏定位（默认屏幕底部居中，与选区重叠时移到选区下方/上方） ----------
    def _show_toolbar():
        toolbar.update_idletasks()
        tw_, th_ = toolbar.winfo_reqwidth(), toolbar.winfo_reqheight()
        x = vx + (vw - tw_) // 2
        y = vy + vh - th_ - 16
        l, t, r, b = st["bbox"]
        if not (x + tw_ < l or x > r or y + th_ < t or y > b):
            if b + 8 + th_ <= vy + vh:
                y = b + 8
            else:
                y = max(vy + 8, t - th_ - 8)
        toolbar.geometry(f"+{int(x)}+{int(y)}")
        toolbar.deiconify()
        toolbar.lift()
        toolbar.focus_force()

    cv.bind("<ButtonPress-1>", on_press)
    cv.bind("<B1-Motion>", on_motion)
    cv.bind("<Motion>", on_motion)
    cv.bind("<ButtonRelease-1>", on_release)
    cv.bind("<Escape>", lambda e: _close(None))
    mask.bind("<Escape>", lambda e: _close(None))
    mask.focus_force()
    mask.lift()  # 确保遮罩盖在悬浮窗之上
    mask.wait_window()  # 模态：阻塞到用户选择（内部仍处理事件）
    return done["out"]


class _ProgressWin:
    """识图进行中的小型提示窗（含取消）。"""

    def __init__(self, root):
        self.win = tk.Toplevel(root)
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.win.configure(bg="#1f1f1f")
        self._msg = ""
        self.label = tk.Label(
            self.win, text="", bg="#1f1f1f", fg="#e8e8e8",
            font=("Microsoft YaHei UI", 10), padx=18, pady=12,
        )
        self.label.pack()
        tk.Button(
            self.win, text="取消", command=self.cancel,
            bg="#333333", fg="#ffffff", activebackground="#555555", relief="flat",
            font=("Microsoft YaHei UI", 9), padx=12,
        ).pack(pady=(0, 10))
        self.cancel_event = threading.Event()
        self.win.update_idletasks()
        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        x = sw // 2 - self.win.winfo_width() // 2
        y = sh // 2 - self.win.winfo_height() // 2
        self.win.geometry(f"+{x}+{y}")
        self._tick(0)

    def _tick(self, i):
        try:
            if not self.win.winfo_exists():
                return
        except Exception:
            return
        dots = "·" * (i % 4) + " " * (3 - (i % 4))
        try:
            self.label.config(text=self._msg + dots)
        except Exception:
            return
        self.win.after(150, lambda: self._tick(i + 1))

    def set_msg(self, msg):
        """跨线程安全：widget.after 是 Tkinter 唯一线程安全的方法。"""
        self._msg = msg

        def _apply():
            try:
                self.label.config(text=msg)
            except Exception:
                pass

        try:
            self.win.after(0, _apply)
        except Exception:
            pass

    def cancel(self):
        self.cancel_event.set()

    def close(self):
        try:
            self.win.destroy()
        except Exception:
            pass


def run_vision_flow(root):
    """识图主流程：全屏遮罩框选（实时坐标/选区 HUD + 底部工具栏）-> 截图 -> qwen-vl-max 预读 -> OpenWorker 新对话（qwen3-max 作答）。"""
    try:
        result = select_region_with_toolbar(root)
    except Exception as e:
        messagebox.showerror(APP_NAME, f"识图启动失败：\n{e}", parent=root)
        return
    if result is None:
        return
    bbox, action = result
    try:
        w, h, bgra = capture_screen_region(bbox)
    except Exception as e:
        messagebox.showerror(APP_NAME, f"截取屏幕失败：\n{e}", parent=root)
        return

    # 预检：Qwen/DashScope Key 与 OpenWorker 后端
    secrets = _load_secrets()
    qwen_key, qwen_base = _qwen_credentials(secrets)
    if not qwen_key:
        messagebox.showerror(
            APP_NAME,
            "未找到 Qwen/DashScope API Key。\n请在 OpenWorker「设置 → 模型」中配置 Qwen，"
            "或设置环境变量 DASHSCOPE_API_KEY。",
            parent=root,
        )
        return
    if not _server_reachable():
        messagebox.showerror(
            APP_NAME,
            "OpenWorker 后端未运行。\n请先启动 OpenWorker（start-dev.bat）。",
            parent=root,
        )
        return

    image_b64 = _image_to_base64_png(bgra, w, h)
    progress = _ProgressWin(root)
    cancel = progress.cancel_event

    def _on_main(callback):
        """把结果回调调度回主线程；应用已退出时静默丢弃。"""
        try:
            root.after(0, callback)
        except Exception:
            pass

    action_names = {"extract": "提取文字", "translate": "翻译", "answer": "解答"}

    def worker():
        try:
            if cancel.is_set():
                return
            progress.set_msg(f"正在用 {VISION_EXTRACT_MODEL} 读取截图…")
            vision_text = qwen_vision_extract(image_b64, action, qwen_key, qwen_base)
            if cancel.is_set():
                return
            conversation_text = (
                (CONVERSATION_LEADS.get(action) or "") + vision_text
            )
            progress.set_msg("正在发送到 OpenWorker 新对话…")
            ok, info = send_screenshot_to_openworker(
                image_b64, conversation_text, cancel,
                lambda msg: progress.set_msg(msg),
                attach_image=_model_vision_likely(VISION_MODEL),
            )
            if cancel.is_set():
                return
            if ok:
                # 打开 GUI 让用户看到新对话（GUI 每 5 秒刷新会话列表）
                if gui_is_reachable(GUI_URL):
                    webbrowser.open(GUI_URL)
                _on_main(
                    lambda: (
                        progress.close(),
                        messagebox.showinfo(
                            APP_NAME,
                            f"“{action_names[action]}”结果已输出到 OpenWorker 的新对话。\n"
                            "请在 OpenWorker 左侧会话列表点击最新会话查看。",
                            parent=root,
                        ),
                    )
                )
            else:
                _on_main(
                    lambda: (
                        progress.close(),
                        messagebox.showwarning(APP_NAME, info, parent=root),
                    )
                )
        except Exception as e:
            _on_main(
                lambda: (
                    progress.close(),
                    messagebox.showerror(APP_NAME, f"识图失败：\n{e}", parent=root),
                )
            )

    threading.Thread(target=worker, daemon=True).start()


def _kill_old_instance():
    """按 PID 锁文件终止旧实例（--replace 模式下用，避免一直跑旧代码）。"""
    try:
        with open(LOCK_PATH, encoding="utf-8") as f:
            old_pid = int(f.read().strip())
    except Exception:
        return
    try:
        PROCESS_TERMINATE = 0x0001
        h = _kernel32.OpenProcess(PROCESS_TERMINATE, False, old_pid)
        if h:
            _kernel32.TerminateProcess(h, 0)
            _kernel32.CloseHandle(h)
            time.sleep(0.6)
    except Exception:
        pass


def _acquire_singleton(replace):
    """单实例互斥体；replace=True 时先终止旧实例再获取（供 bat 重启用）。"""
    if replace:
        _kill_old_instance()
    for _ in range(30):
        mutex = _kernel32.CreateMutexW(None, False, "Local\\OpenWorkerFloatingIcon")
        if _kernel32.GetLastError() != ERROR_ALREADY_EXISTS:
            return mutex
        if not replace:
            break
        time.sleep(0.25)
    _fatal("OpenWorker 悬浮窗已在运行。\n如看不到窗口，请检查任务栏或任务管理器。")
    return None  # 不可达


def _write_lock():
    try:
        os.makedirs(os.path.dirname(LOCK_PATH), exist_ok=True)
        with open(LOCK_PATH, "w", encoding="utf-8") as f:
            f.write(str(os.getpid()))
    except Exception:
        pass


def _release_lock():
    try:
        with open(LOCK_PATH, encoding="utf-8") as f:
            if f.read().strip() == str(os.getpid()):
                os.remove(LOCK_PATH)
    except Exception:
        pass


def main():
    smoke = "--smoke" in sys.argv
    replace = "--replace" in sys.argv

    # DPI-aware: 必须在创建任何窗口之前调用，否则坐标/尺寸错乱。
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

    # 单实例保护：防止 bat 连点两次出现两个重叠悬浮窗；--replace 自动替换旧实例
    _instance_mutex = _acquire_singleton(replace)
    _write_lock()

    root = tk.Tk()
    root.title(APP_NAME)
    root.overrideredirect(True)                 # 无边框
    root.attributes("-topmost", True)           # 永远置顶
    root.configure(cursor="hand2")

    # ---------- 初始位置: 记忆位置(夹到屏幕内) 或 默认右上角 ----------
    state = load_state()
    sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
    x = state.get("x", sw - SIZE - MARGIN)
    y = state.get("y", MARGIN)
    x = max(MARGIN - SIZE, min(x, sw - SIZE - MARGIN))
    y = max(MARGIN - SIZE, min(y, sh - SIZE - MARGIN))
    root.geometry(f"{SIZE}x{SIZE}+{int(x)}+{int(y)}")
    root.update_idletasks()  # 确保 HWND/尺寸/位置已生效

    # ---------- 真 alpha 圆形显示（无需透明色，无需 Pillow） ----------
    try:
        icon_bgra = load_icon_bgra(SIZE)
    except Exception as e:
        _fatal(f"无法加载 OpenWorker 图标：\n{e}")
    # 取真正的顶层窗口（winfo_id 返回 Tk 子窗口，ULW 用子窗口会导致坐标双重偏移）
    top_hwnd = _user32.GetAncestor(root.winfo_id(), GA_ROOT)
    _apply_layered_icon(root, icon_bgra, SIZE, hwnd=top_hwnd)
    _install_click_through(root, SIZE, hwnd=top_hwnd)

    # ---------- 拖动 ----------
    drag = {"dx": 0, "dy": 0}

    def on_press(e):
        drag["dx"], drag["dy"] = e.x, e.y

    def on_motion(e):
        root.geometry(f"+{root.winfo_x() + e.x - drag['dx']}+{root.winfo_y() + e.y - drag['dy']}")

    def on_release(e):
        save_state({"x": root.winfo_x(), "y": root.winfo_y()})

    # ---------- 打开 GUI ----------
    def open_gui():
        if gui_is_reachable(GUI_URL):
            webbrowser.open(GUI_URL)
        else:
            messagebox.showinfo(
                APP_NAME,
                f"OpenWorker 界面未运行。\n请先启动服务，然后访问：\n{GUI_URL}",
                parent=root,
            )

    # ---------- 右键菜单 ----------
    def on_close():
        save_state({"x": root.winfo_x(), "y": root.winfo_y()})
        root.destroy()

    menu = tk.Menu(root, tearoff=0)
    menu.add_command(label="打开 OpenWorker 界面", command=open_gui)
    topmost_var = tk.BooleanVar(value=True)
    menu.add_checkbutton(
        label="置顶", variable=topmost_var,
        command=lambda: root.attributes("-topmost", topmost_var.get()),
    )
    menu.add_separator()
    menu.add_command(label="识图", command=lambda: run_vision_flow(root))
    menu.add_separator()
    menu.add_command(label="退出", command=on_close)

    # ---------- 悬停提示 ----------
    tip = tk.Toplevel(root)
    tip.overrideredirect(True)
    tip.attributes("-topmost", True)
    tip.configure(bg="#1f1f1f")
    tk.Label(
        tip, text=f"{APP_NAME} {PLUGIN_VERSION}", bg="#1e1e1e", fg="#ffffff",
        font=("Microsoft YaHei UI", 9), padx=8, pady=4,
    ).pack()
    tip.withdraw()
    tip_job = [None]

    def show_tip():
        tip.geometry(f"+{root.winfo_x() + SIZE // 2}+{root.winfo_y() - 28}")
        tip.deiconify()
        tip.lift()

    def hide_tip():
        if tip_job[0]:
            root.after_cancel(tip_job[0])
            tip_job[0] = None
        tip.withdraw()

    def on_enter(e):
        tip_job[0] = root.after(500, show_tip)

    def on_leave(e):
        hide_tip()

    def on_motion_anywhere(e):
        # 窗口移动时收起提示
        tip.withdraw()

    # ---------- 绑定 ----------
    root.bind("<Button-1>", on_press)
    root.bind("<B1-Motion>", on_motion)
    root.bind("<ButtonRelease-1>", on_release)
    root.bind("<Double-Button-1>", lambda e: open_gui())
    root.bind("<Button-3>", lambda e: menu.tk_popup(e.x_root, e.y_root))
    root.bind("<Enter>", on_enter)
    root.bind("<Leave>", on_leave)
    root.bind("<Configure>", lambda e: on_motion_anywhere(e))

    root.protocol("WM_DELETE_WINDOW", on_close)

    if smoke:
        # 自检不写状态，避免覆盖用户记忆的悬浮窗位置
        root.after(1500, root.destroy)
        print("[smoke] floating icon launched OK", flush=True)

    root.mainloop()
    _release_lock()
    return 0


if __name__ == "__main__":
    sys.exit(main())
