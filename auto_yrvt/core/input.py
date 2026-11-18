"""Entrada: teclas via PostMessage, cliques, pontos."""

import ctypes
import time

import win32api
import win32con
import win32gui

MAPVK_VSC_TO_VK_EX = 3
user32 = ctypes.windll.user32

SCAN = {
    "1": 0x02, "2": 0x03, "3": 0x04, "4": 0x05, "5": 0x06,
    "6": 0x07, "7": 0x08, "8": 0x09, "9": 0x0A, "0": 0x0B,
    "q": 0x10, "w": 0x11, "e": 0x12, "r": 0x13, "t": 0x14,
    "y": 0x15, "u": 0x16, "i": 0x17, "o": 0x18, "p": 0x19,
    "a": 0x1E, "s": 0x1F, "d": 0x20, "f": 0x21, "g": 0x22,
    "h": 0x23, "j": 0x24, "k": 0x25, "l": 0x26,
    "z": 0x2C, "x": 0x2D, "c": 0x2E, "v": 0x2F, "b": 0x30,
    "n": 0x31, "m": 0x32,
    "tab": 0x0F, "enter": 0x1C, "space": 0x39, "esc": 0x01,
    "pageup": 0x49, "pagedown": 0x51,
    "insert": 0x52, "delete": 0x53, "home": 0x47, "end": 0x4F,
    "up": 0x48, "down": 0x50, "left": 0x4B, "right": 0x4D,
    "f1": 0x3B, "f2": 0x3C, "f3": 0x3D, "f4": 0x3E, "f5": 0x3F,
}

EXT_KEYS = {"pageup", "pagedown", "insert", "delete", "home", "end",
            "up", "down", "left", "right"}

VK_BY_NAME = {
    name: (user32.MapVirtualKeyW(scan, MAPVK_VSC_TO_VK_EX) or scan)
    for name, scan in SCAN.items()
}

VK_F9 = 0x78
VK_F12 = 0x7B
VK_LBUTTON = 0x01


def key_code(name):
    """'pagedown' -> (0x51, True) | None"""
    name = (name or "").strip().lower()
    sc = SCAN.get(name)
    return None if sc is None else (sc, name in EXT_KEYS)


def post_key(hwnd, scan, up=False, ext=False):
    """WM_KEYDOWN/UP direto na janela — não muda o foco."""
    try:
        vk = VK_BY_NAME.get(
            next((k for k, v in SCAN.items() if v == scan), ""), 0
        ) or user32.MapVirtualKeyW(scan, MAPVK_VSC_TO_VK_EX) or scan
        lparam = 1 | (scan << 16)
        if ext:
            lparam |= 1 << 24
        if up:
            lparam |= (1 << 30) | (1 << 31)
        msg = win32con.WM_KEYUP if up else win32con.WM_KEYDOWN
        return bool(win32api.PostMessage(hwnd, msg, int(vk), int(lparam)))
    except Exception:
        return False


def click_at(hwnd, x, y):
    lp = win32api.MAKELONG(int(x), int(y))
    win32api.PostMessage(hwnd, win32con.WM_MOUSEMOVE, 0, lp)
    time.sleep(0.03)
    win32api.PostMessage(hwnd, win32con.WM_LBUTTONDOWN,
                         win32con.MK_LBUTTON, lp)
    time.sleep(0.05)
    win32api.PostMessage(hwnd, win32con.WM_LBUTTONUP, 0, lp)
    return True


def client_point(hwnd):
    x, y = win32api.GetCursorPos()
    return win32gui.ScreenToClient(hwnd, (x, y))


def client_size(hwnd):
    try:
        left, top, right, bottom = win32gui.GetClientRect(hwnd)
        return max(0, right - left), max(0, bottom - top)
    except Exception:
        return 0, 0


def work_area():
    info = win32api.GetMonitorInfo(win32api.MonitorFromPoint((0, 0)))
    left, top, right, bottom = info["Work"]
    return left, top, right - left, bottom - top
