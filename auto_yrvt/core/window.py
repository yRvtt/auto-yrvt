"""Enumeração e gerenciamento de janelas do MIR4."""

import ctypes
import os
import re
import time

import win32api
import win32con
import win32gui
import win32process

from .config import MIR4_PROCESS_NAMES
from .input import client_size


class WindowInfo:
    __slots__ = ("hwnd", "title", "pid", "exe", "width", "height")

    def __init__(self, hwnd, title, pid, exe, width, height):
        self.hwnd = hwnd
        self.title = title
        self.pid = pid
        self.exe = exe
        self.width = width
        self.height = height

    def __repr__(self):
        return f"WindowInfo({self.hwnd}, {self.title!r})"


def process_name(pid):
    handle = None
    try:
        handle = win32api.OpenProcess(
            win32con.PROCESS_QUERY_INFORMATION | win32con.PROCESS_VM_READ,
            False, pid)
        return os.path.basename(
            win32process.GetModuleFileNameEx(handle, 0)).lower()
    except Exception:
        return ""
    finally:
        if handle:
            win32api.CloseHandle(handle)


def window_sort_key(title):
    match = re.search(r"\[(\d+)\]", title or "")
    if match:
        return (0, int(match.group(1)), (title or "").lower())
    return (1, 0, (title or "").lower())


def list_mir4_windows():
    """Todas as janelas visíveis do jogo, validadas pelo processo."""
    found = []

    def cb(hwnd, _):
        try:
            if not win32gui.IsWindowVisible(hwnd):
                return True
            title = win32gui.GetWindowText(hwnd)
            if "mir4" not in title.lower():
                return True
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            exe = process_name(pid)
            class_name = win32gui.GetClassName(hwnd)
            if exe and exe not in MIR4_PROCESS_NAMES:
                return True
            if (not exe and "mir4g" not in title.lower()
                    and class_name != "UnrealWindow"):
                return True
            w, h = client_size(hwnd)
            if w < 200 or h < 150:
                return True
            found.append(WindowInfo(hwnd, title, pid, exe, w, h))
        except Exception:
            pass
        return True

    try:
        win32gui.EnumWindows(cb, None)
    except Exception:
        pass
    found.sort(key=lambda w: window_sort_key(w.title))
    return found


def find_launcher_window(part, timeout=40.0, stop_event=None):
    """Janela do launcher (não confunde com uma janela do jogo)."""
    deadline = time.time() + timeout
    part = (part or "").lower()
    while True:
        if stop_event is not None and stop_event.is_set():
            return None
        matches = []

        def cb(hwnd, _):
            if win32gui.IsWindowVisible(hwnd):
                if part in win32gui.GetWindowText(hwnd).lower():
                    matches.append(hwnd)
            return True

        try:
            win32gui.EnumWindows(cb, None)
        except Exception:
            pass
        for hwnd in matches:
            try:
                _, pid = win32process.GetWindowThreadProcessId(hwnd)
                exe = process_name(pid)
            except Exception:
                exe = ""
            if exe not in MIR4_PROCESS_NAMES:
                return hwnd
        if time.time() >= deadline:
            return matches[0] if matches else None
        time.sleep(0.4)


def wait_game_window(exclude, timeout=90, stop_event=None):
    """Espera uma janela nova do processo do jogo."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if stop_event is not None and stop_event.is_set():
            return None
        for win in list_mir4_windows():
            if win.hwnd not in exclude:
                return win.hwnd
        time.sleep(0.5)
    return None


def is_admin():
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False
