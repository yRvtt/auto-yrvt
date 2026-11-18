"""
Núcleo do Auto yRvt: janelas, entrada, perfis, macros e instâncias.

Tudo aqui é independente da interface Tkinter, para poder ser testado
e para permitir várias instâncias do jogo ao mesmo tempo.
"""

import base64
import ctypes
import ctypes.wintypes
import json
import os
import random
import re
import shutil
import subprocess
import threading
import time

import win32api
import win32con
import win32gui
import win32process

from . import __app_name__ as APP_NAME, __version__ as APP_VERSION

MAPVK_VSC_TO_VK_EX = 3
user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32

# ── teclas ───────────────────────────────────────────────────────
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

MIR4_PROCESS_NAMES = {
    "mir4.exe", "mir4g.exe", "mir4gclient.exe", "mir4s.exe",
    "mir4-win64-shipping.exe", "mir4g-win64-shipping.exe",
    "mir4client.exe",
}

ROOM_W, ROOM_H = 340, 26
ROOM_MATCH = 15
REFRESH_WAIT = 1.0
ROOMS = [("xp1", "XP 1"), ("xp2", "XP 2"), ("xp3", "XP 3"), ("anti", "Anti-Demônio")]

# ── modos e tarefas ──────────────────────────────────────────────
MODES = [
    ("off", "Desligado", "Nenhuma automação de combate"),
    ("farm", "Farm EXP", "Tab → Down → F (+ R ultimate)"),
    ("missao_q", "Missão Q", "Q/E → T x3 → cliques no centro"),
    ("boss", "Summon Boss", "E periódico"),
    ("acordar", "Acordar", "R → Tab → PageDown x3 → F (ultimate opcional)"),
]

MODE_DEFAULT_INTERVAL = {
    "farm": 4.0,
    "missao_q": 8.0,
    "boss": 10.0,
    "acordar": 3.8,
}

MACRO_TASKS = {
    "doar_cla": {
        "label": "Doar clã",
        "desc": "Grave uma doação completa (F9 para parar).",
        "interval": 86400.0,
        "repeat": 1,
    },
    "comprar_intensify": {
        "label": "Comprar Intensify",
        "desc": "Grave a compra uma vez; repita 4x se quiser.",
        "interval": 86400.0,
        "repeat": 4,
    },
}


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


# ── janelas ──────────────────────────────────────────────────────
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


def client_size(hwnd):
    try:
        left, top, right, bottom = win32gui.GetClientRect(hwnd)
        return max(0, right - left), max(0, bottom - top)
    except Exception:
        return 0, 0


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


# ── captura / clique ─────────────────────────────────────────────
class BMI(ctypes.Structure):
    _fields_ = [("biSize", ctypes.wintypes.DWORD),
                ("biWidth", ctypes.wintypes.LONG),
                ("biHeight", ctypes.wintypes.LONG),
                ("biPlanes", ctypes.wintypes.WORD),
                ("biBitCount", ctypes.wintypes.WORD),
                ("biCompression", ctypes.wintypes.DWORD),
                ("biSizeImage", ctypes.wintypes.DWORD),
                ("biXPelsPerMeter", ctypes.wintypes.LONG),
                ("biYPelsPerMeter", ctypes.wintypes.LONG),
                ("biClrUsed", ctypes.wintypes.DWORD),
                ("biClrImportant", ctypes.wintypes.DWORD)]


def grab(hwnd):
    """(w, h, pixels BGRA bottom-up) da área cliente, mesmo coberta."""
    rect = ctypes.wintypes.RECT()
    if not user32.GetClientRect(hwnd, ctypes.byref(rect)):
        return None
    w, h = rect.right, rect.bottom
    if w <= 0 or h <= 0:
        return None
    hdc = user32.GetDC(hwnd)
    mem = gdi32.CreateCompatibleDC(hdc)
    bmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
    if not mem or not bmp:
        if bmp:
            gdi32.DeleteObject(bmp)
        if mem:
            gdi32.DeleteDC(mem)
        user32.ReleaseDC(hwnd, hdc)
        return None
    gdi32.SelectObject(mem, bmp)
    ok = user32.PrintWindow(hwnd, mem, 2)  # PW_RENDERFULLCONTENT
    bmi = BMI()
    bmi.biSize = ctypes.sizeof(BMI)
    bmi.biWidth, bmi.biHeight = w, h
    bmi.biPlanes, bmi.biBitCount = 1, 32
    buf = ctypes.create_string_buffer(w * h * 4)
    gdi32.GetDIBits(mem, bmp, 0, h, buf, ctypes.byref(bmi), 0)
    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(mem)
    user32.ReleaseDC(hwnd, hdc)
    return (w, h, buf.raw) if ok else None


def crop(w, h, data, x, y, cw=ROOM_W, ch=ROOM_H):
    x = max(0, min(int(x), w - 1))
    y = max(0, min(int(y), h - 1))
    cw, ch = min(cw, w - x), min(ch, h - y)
    if cw <= 0 or ch <= 0:
        return b""
    out = bytearray(cw * ch * 4)
    row_bytes = cw * 4
    for row in range(ch):
        src = (h - 1 - (y + row)) * w * 4 + x * 4
        out[row * row_bytes:(row + 1) * row_bytes] = data[src:src + row_bytes]
    return bytes(out)


def diff(a, b):
    """Diferença média de luminância (0-255), ignorando alpha."""
    if not a or len(a) != len(b):
        return 999.0
    total = 0.0
    n = 0
    for i in range(0, len(a) - 3, 16):
        la = 0.114 * a[i] + 0.587 * a[i + 1] + 0.299 * a[i + 2]
        lb = 0.114 * b[i] + 0.587 * b[i + 1] + 0.299 * b[i + 2]
        total += abs(la - lb)
        n += 1
    return total / n if n else 999.0


def is_black(data):
    if not data:
        return True
    for i in range(0, len(data) - 3, 400):
        if data[i] or data[i + 1] or data[i + 2]:
            return False
    return True


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


def work_area():
    info = win32api.GetMonitorInfo(win32api.MonitorFromPoint((0, 0)))
    left, top, right, bottom = info["Work"]
    return left, top, right - left, bottom - top


# ── configuração / perfis ────────────────────────────────────────
CFG_DIR = (
    os.environ.get("AUTO_YRVT_CONFIG_DIR")
    or os.environ.get("MIR4BOT_CONFIG_DIR")
    or os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"),
                    "AutoYrvt")
)
STATE_FILE = os.path.join(CFG_DIR, "state.json")
LEGACY_DIR = os.path.join(
    os.environ.get("APPDATA") or os.path.expanduser("~"), "MIR4Bot")
LEGACY_STATE = os.path.join(LEGACY_DIR, "state.json")
LEGACY_FILE = os.path.join(LEGACY_DIR, "salas.json")


def default_mode_cfg():
    return {
        key: {"interval": value}
        for key, value in MODE_DEFAULT_INTERVAL.items()
    } | {"farm": {"interval": MODE_DEFAULT_INTERVAL["farm"],
                  "ultimate": False, "ultimate_key": "r",
                  "ultimate_interval": 0.8}}


def default_extras():
    return {
        "buff": {"on": False, "interval": 180.0, "keys": ["1", "2", "3"]},
        "doar_cla": {"on": False, "interval": 86400.0,
                     "macro": [], "repeat": 1},
        "comprar_intensify": {"on": False, "interval": 86400.0,
                              "macro": [], "repeat": 4},
    }


def xp_rate(delta_pct, seconds):
    """Retorna % de XP por hora (0 se a duração for inválida)."""
    if seconds <= 0:
        return 0.0
    return delta_pct * 3600.0 / seconds


def format_duration(seconds):
    seconds = max(0, int(seconds))
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def default_profile(name="default"):
    return {
        "name": name,
        "window": "",
        "mode": "off",
        "mode_cfg": default_mode_cfg(),
        "extras": default_extras(),
        "points": {"amovivel": None, "nome": None, "entrar": None},
        "refs": {},
        "xp_region": None,
    }


def _merge(base, incoming):
    if not isinstance(incoming, dict):
        return base
    for key, value in incoming.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _merge(base[key], value)
        else:
            base[key] = value
    return base


def normalize_profile(name, data):
    profile = default_profile(name)
    _merge(profile, data or {})
    profile["name"] = name
    if not isinstance(profile.get("mode_cfg"), dict):
        profile["mode_cfg"] = default_mode_cfg()
    if not isinstance(profile.get("extras"), dict):
        profile["extras"] = default_extras()
    if not isinstance(profile.get("points"), dict):
        profile["points"] = {"amovivel": None, "nome": None, "entrar": None}
    # compatibilidade com a versão antiga (tasks/flags)
    for mode in MODE_DEFAULT_INTERVAL:
        if not isinstance(profile["mode_cfg"].get(mode), dict):
            profile["mode_cfg"][mode] = {"interval": MODE_DEFAULT_INTERVAL[mode]}
        profile["mode_cfg"][mode].setdefault("interval", MODE_DEFAULT_INTERVAL[mode])
    if not isinstance(profile["mode_cfg"].get("farm"), dict):
        profile["mode_cfg"]["farm"] = {"interval": MODE_DEFAULT_INTERVAL["farm"]}
    profile["mode_cfg"]["farm"].setdefault("ultimate", False)
    profile["mode_cfg"]["farm"].setdefault("ultimate_key", "r")
    profile["mode_cfg"]["farm"].setdefault("ultimate_interval", 0.8)
    for key, cfg in default_extras().items():
        if not isinstance(profile["extras"].get(key), dict):
            profile["extras"][key] = dict(cfg)
        for field, value in cfg.items():
            profile["extras"][key].setdefault(field, value)
    if not isinstance(profile["extras"]["buff"].get("keys"), list):
        profile["extras"]["buff"]["keys"] = ["1", "2", "3"]
    for key in MACRO_TASKS:
        if not isinstance(profile["extras"][key].get("macro"), list):
            profile["extras"][key]["macro"] = []
        profile["extras"][key].pop("last_run", None)
    for key in ("amovivel", "nome", "entrar"):
        profile["points"].setdefault(key, None)
    profile.setdefault("xp_region", None)
    if not isinstance(profile.get("refs"), dict):
        profile["refs"] = {}
    return profile


class Config:
    def __init__(self, path=STATE_FILE, legacy=LEGACY_FILE,
                 old_state=LEGACY_STATE):
        self.path = path
        self.legacy = legacy
        self.old_state = old_state
        self.profiles = {}
        self.layout = {"rows": 2, "cols": 2}
        self.launcher = {"path": "", "title": "MIR4",
                         "g1": [0, 0], "g2": [0, 0],
                         "pre": 3.0, "between": 6.0}
        self.xp_sessions = []
        self._lock = threading.RLock()
        self.load()

    def load(self):
        # Migra o estado da versão antiga (MIR4 Bot) sem apagar o original.
        if (not os.path.exists(self.path) and self.old_state
                and os.path.exists(self.old_state)):
            try:
                os.makedirs(os.path.dirname(self.path), exist_ok=True)
                shutil.copy2(self.old_state, self.path)
            except Exception:
                pass
        data = {}
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            data = {}
        if not isinstance(data, dict):
            data = {}

        profiles = data.get("profiles") or {}
        self.profiles = {
            name: normalize_profile(name, value)
            for name, value in profiles.items()
            if isinstance(value, dict)
        }
        if isinstance(data.get("layout"), dict):
            _merge(self.layout, data["layout"])
        if isinstance(data.get("launcher"), dict):
            _merge(self.launcher, data["launcher"])
        sessions = data.get("xp_sessions")
        if isinstance(sessions, list):
            self.xp_sessions = [s for s in sessions
                                if isinstance(s, dict)][-50:]

        if not self.profiles:
            self._migrate_legacy()
        if "default" not in self.profiles:
            self.profiles["default"] = default_profile("default")
        self.save()

    def _migrate_legacy(self):
        try:
            with open(self.legacy, encoding="utf-8") as f:
                old = json.load(f)
        except Exception:
            return
        profile = default_profile("default")
        for key, value in (old.get("pts") or {}).items():
            if key in profile["points"] and value:
                profile["points"][key] = list(value)
        refs = old.get("refs") or {}
        if isinstance(refs, dict):
            profile["refs"] = refs
        self.profiles["default"] = profile

    def save(self):
        with self._lock:
            directory = os.path.dirname(self.path)
            if directory:
                os.makedirs(directory, exist_ok=True)
            data = {
                "version": 3,
                "profiles": self.profiles,
                "layout": self.layout,
                "launcher": self.launcher,
                "xp_sessions": self.xp_sessions,
            }
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            os.replace(tmp, self.path)

    def ensure_profile(self, name):
        with self._lock:
            name = (name or "default").strip() or "default"
            if name not in self.profiles:
                self.profiles[name] = default_profile(name)
            return self.profiles[name]

    def delete_profile(self, name):
        with self._lock:
            if name == "default":
                return False
            if self.profiles.pop(name, None) is None:
                return False
            self.save()
            return True

    def bind(self, profile_name, title):
        with self._lock:
            profile = self.ensure_profile(profile_name)
            low = (title or "").strip().lower()
            for other_name, other in self.profiles.items():
                if other_name != profile["name"]:
                    if (other.get("window") or "").strip().lower() == low:
                        other["window"] = ""
            profile["window"] = title
            self.save()
            return profile

    def profile_for_title(self, title):
        with self._lock:
            low = (title or "").strip().lower()
            exact = [name for name, profile in self.profiles.items()
                     if (profile.get("window") or "").strip().lower() == low]
            if exact:
                return exact[0]
            for name, profile in self.profiles.items():
                match = (profile.get("window") or "").strip().lower()
                # títulos do jogo terminam em "[0]", "[1]"...; substring
                # faria "[1]" casar com "[10]".
                if match and not match.endswith("]") and match in low:
                    return name
            if len(self.profiles) == 1:
                return next(iter(self.profiles))
            return "default"


# ── gravação de macro ────────────────────────────────────────────
class Recorder(threading.Thread):
    """Grava teclas e cliques dentro da janela do jogo, com tempos."""

    def __init__(self, hwnd, on_done, log):
        super().__init__(daemon=True)
        self.hwnd = hwnd
        self.on_done = on_done
        self.log = log
        self.steps = []
        self.stop_event = threading.Event()

    def stop(self):
        self.stop_event.set()

    def run(self):
        start = time.time()
        last = start
        keys_down = set()
        key_down_at = {}
        lbutton_down = False
        self.log("Gravando macro — F9 para parar.")
        try:
            while not self.stop_event.is_set():
                if time.time() - start > 300:
                    self.log("Gravação encerrada (limite de 5 min).")
                    break
                if win32api.GetAsyncKeyState(VK_F9) & 0x8000:
                    break
                if not win32gui.IsWindow(self.hwnd):
                    self.log("Janela do jogo fechou; gravação encerrada.")
                    break

                now = time.time()
                # teclas
                for name, vk in VK_BY_NAME.items():
                    pressed = bool(win32api.GetAsyncKeyState(vk) & 0x8000)
                    if pressed and name not in keys_down:
                        keys_down.add(name)
                        key_down_at[name] = now
                        delay = min(now - last, 10.0)
                        self.steps.append({
                            "t": "key", "k": name,
                            "delay": round(delay, 2),
                            "hold": 0.06,
                        })
                        last = now
                    elif not pressed and name in keys_down:
                        keys_down.discard(name)
                        hold = now - key_down_at.pop(name, now)
                        if (self.steps and self.steps[-1].get("t") == "key"
                                and self.steps[-1].get("k") == name):
                            self.steps[-1]["hold"] = round(
                                min(max(hold, 0.05), 1.5), 2)

                # clique esquerdo
                pressed = bool(win32api.GetAsyncKeyState(VK_LBUTTON) & 0x8000)
                if pressed and not lbutton_down:
                    lbutton_down = True
                    if now - start >= 0.25:
                        x, y = win32api.GetCursorPos()
                        cx, cy = win32gui.ScreenToClient(self.hwnd, (x, y))
                        w, h = client_size(self.hwnd)
                        if w > 0 and h > 0 and 0 <= cx < w and 0 <= cy < h:
                            delay = min(now - last, 10.0)
                            self.steps.append({
                                "t": "click",
                                "x": round(cx / w, 4),
                                "y": round(cy / h, 4),
                                "delay": round(delay, 2),
                            })
                            last = now
                elif not pressed:
                    lbutton_down = False

                time.sleep(0.01)
        except Exception as exc:
            self.log(f"Erro na gravação: {exc}")
        finally:
            self.log(f"Macro gravada: {len(self.steps)} passos.")
            try:
                self.on_done(self.steps)
            except Exception as exc:
                self.log(f"Erro ao salvar macro: {exc}")


# ── instância ────────────────────────────────────────────────────
class Instance:
    def __init__(self, manager, win, profile, profile_name):
        self.manager = manager
        self.win = win
        self.hwnd = win.hwnd
        self.profile = profile
        self.profile_name = profile_name
        self.stop_event = threading.Event()
        self.pause_event = threading.Event()
        self.thread = None
        self.next_mode = 0.0
        self.next_extra = {}
        self.next_ultimate = 0.0
        self.acordar_started = False
        self.keys_down = set()
        self.task_count = 0
        self.next_action = 0.0
        self.prefer_extra = False
        self.searching = None
        self.search_paused = False
        self.search_stop = threading.Event()
        self.search_thread = None

    # ── util ────────────────────────────────────────────────────
    @property
    def title(self):
        return self.win.title

    def log(self, msg):
        self.manager.log(f"[{self.profile_name}] {msg}")

    def log_task(self, msg, every=10):
        self.task_count += 1
        if self.task_count <= 3 or self.task_count % every == 0:
            self.log(msg)

    def ensure(self):
        return bool(self.hwnd and win32gui.IsWindow(self.hwnd))

    def status(self):
        if self.thread and self.thread.is_alive():
            return "pausado" if self.pause_event.is_set() else "rodando"
        return "parado"

    def is_running(self):
        return bool(self.thread and self.thread.is_alive())

    def start(self):
        if self.is_running():
            return False
        self.stop_event.clear()
        self.pause_event.clear()
        self.acordar_started = False
        self.task_count = 0
        self.next_mode = time.time()
        self.next_action = time.time() + 0.5
        self.next_ultimate = 0.0
        self.prefer_extra = False
        now = time.time()
        self.next_extra = {
            key: now + (random.uniform(5, 15)
                        if float(cfg.get("interval", 60.0) or 60.0) > 3600
                        else random.uniform(0, 1.0))
            for key, cfg in self.profile.get("extras", {}).items()
        }
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()
        self.log("iniciado")
        return True

    def pause(self):
        self.pause_event.set()
        self.log("pausado")

    def resume(self):
        self.pause_event.clear()
        self.next_action = time.time() + 0.5
        self.log("retomado")

    def stop(self):
        was_running = self.is_running()
        self.stop_event.set()
        self.search_stop.set()
        self.release_all()
        if was_running:
            self.log("parado")

    def release_all(self):
        for name in list(self.keys_down):
            code = key_code(name)
            if code:
                post_key(self.hwnd, code[0], True, code[1])
        self.keys_down.clear()

    def _sleep(self, seconds):
        end = time.time() + max(0.0, seconds)
        while time.time() < end:
            if self.stop_event.is_set():
                return False
            while self.pause_event.is_set():
                if self.stop_event.wait(0.2):
                    return False
            time.sleep(min(0.1, max(0.0, end - time.time())))
        return True

    # ── entrada ─────────────────────────────────────────────────
    def tap(self, name, hold=0.06):
        code = key_code(name)
        if not code or not self.ensure():
            return False
        scan, ext = code
        self.keys_down.add(name)
        down = post_key(self.hwnd, scan, False, ext)
        time.sleep(max(0.01, hold))
        up = post_key(self.hwnd, scan, True, ext)
        self.keys_down.discard(name)
        return down and up

    def burst(self, keys, hold=0.06):
        ok = True
        for key in keys:
            if self.stop_event.is_set():
                break
            ok = self.tap(key, hold) and ok
            time.sleep(0.05)
        return ok

    def click_rel(self, rx, ry):
        if not self.ensure():
            return False
        w, h = client_size(self.hwnd)
        if w <= 0 or h <= 0:
            return False
        return click_at(self.hwnd, int(rx * w), int(ry * h))

    def click_center(self):
        return self.click_rel(0.5, 0.5)

    def point_px(self, key):
        point = (self.profile.get("points") or {}).get(key)
        if not point:
            return None
        try:
            if len(point) >= 4 and point[2] and point[3]:
                w, h = client_size(self.hwnd)
                return (int(point[0] * w / point[2]),
                        int(point[1] * h / point[3]))
            return int(point[0]), int(point[1])
        except Exception:
            return None

    def capture_point(self, key):
        if not self.ensure():
            return None
        x, y = win32api.GetCursorPos()
        cx, cy = win32gui.ScreenToClient(self.hwnd, (x, y))
        w, h = client_size(self.hwnd)
        if w <= 0 or h <= 0:
            return None
        self.profile.setdefault("points", {})[key] = [cx, cy, w, h]
        self.manager.cfg.save()
        return cx, cy

    def xp_region_px(self):
        region = self.profile.get("xp_region")
        if not isinstance(region, dict):
            return None
        try:
            w, h = client_size(self.hwnd)
            ref_w = region.get("ref_w") or w
            ref_h = region.get("ref_h") or h
            return (int(region["x"] * w / ref_w),
                    int(region["y"] * h / ref_h),
                    max(1, int(region["w"] * w / ref_w)),
                    max(1, int(region["h"] * h / ref_h)))
        except Exception:
            return None

    def set_xp_region(self, x, y, width, height):
        w, h = client_size(self.hwnd)
        if w <= 0 or h <= 0:
            return False
        self.profile["xp_region"] = {
            "x": int(x), "y": int(y),
            "w": max(1, int(width)), "h": max(1, int(height)),
            "ref_w": w, "ref_h": h,
        }
        self.manager.cfg.save()
        return True

    # ── agendador ───────────────────────────────────────────────
    def _loop(self):
        while not self.stop_event.is_set():
            if self.pause_event.is_set():
                self.stop_event.wait(0.2)
                continue
            now = time.time()
            mode = self.profile.get("mode", "off")

            # Ultimate é independente do ciclo: Farm e Acordar tentam de novo
            # a cada ~0.8 s para sair assim que a recarga liberar.
            if mode in ("farm", "acordar"):
                cfg = self.profile.get("mode_cfg", {}).get("farm", {})
                if cfg.get("ultimate"):
                    if now >= self.next_ultimate:
                        key = (cfg.get("ultimate_key") or "r").strip().lower()
                        try:
                            interval = float(
                                cfg.get("ultimate_interval", 0.8) or 0.8)
                        except Exception:
                            interval = 0.8
                        self.tap(key, hold=0.08)
                        self.next_ultimate = time.time() + max(0.3, interval)
                        self.log_task(f"ulti: {key.upper()}", every=20)

            if time.time() < self.next_action:
                self.stop_event.wait(0.2)
                continue
            now = time.time()
            due_extras = [
                (key, cfg)
                for key, cfg in list(self.profile.get("extras", {}).items())
                if cfg.get("on") and now >= self.next_extra.get(key, 0)
            ]
            mode_due = mode != "off" and now >= self.next_mode

            if due_extras and (not mode_due or self.prefer_extra):
                key, cfg = min(
                    due_extras,
                    key=lambda item: self.next_extra.get(item[0], 0))
                try:
                    self.run_extra(key, cfg)
                except Exception as exc:
                    self.log(f"{key}: {exc}")
                interval = float(cfg.get("interval", 60.0))
                self.next_extra[key] = time.time() + max(1.0, interval)
                self.next_action = time.time() + 0.8
                self.prefer_extra = False
            elif mode_due:
                try:
                    self.run_mode(mode)
                    cfg = self.profile["mode_cfg"].get(mode, {})
                    interval = float(cfg.get("interval", 5.0))
                    self.next_mode = time.time() + max(0.5, interval)
                except Exception as exc:
                    self.log(f"modo {mode}: {exc}")
                    self.next_mode = time.time() + 5
                self.next_action = time.time() + 0.8
                self.prefer_extra = True

            self.stop_event.wait(0.2)

    def run_mode(self, mode):
        if mode == "acordar":
            ultimate_on = self.profile["mode_cfg"].get("farm", {}).get(
                "ultimate")
            if not ultimate_on and not self.acordar_started:
                self.tap("r")
                self.acordar_started = True
                self.log_task("acordar: R → menu")
                if not self._sleep(0.5):
                    return
            self.burst(["tab"] + ["pagedown"] * 3 + ["f"])
            self.log_task("acordar: ciclo")
        elif mode == "farm":
            self.tap("tab")
            if not self._sleep(0.5):
                return
            self.tap("down")
            if not self._sleep(0.4):
                return
            self.tap("f")
            self.log_task("farm: Tab ↓ F")
        elif mode == "missao_q":
            keys = ["q", "e"]
            random.shuffle(keys)
            for key in keys:
                self.tap(key)
                if not self._sleep(random.uniform(0.3, 0.5)):
                    return
            for _ in range(3):
                self.tap("t")
                if not self._sleep(0.25):
                    return
            for _ in range(5):
                self.click_center()
                if not self._sleep(0.25):
                    return
            self.log_task("missão Q: Q/E T x3 + 5 cliques")
        elif mode == "boss":
            self.tap("e")
            self.log_task("boss: E")

    def run_extra(self, key, cfg):
        if key == "buff":
            keys = cfg.get("keys") or ["1", "2", "3"]
            self.burst(keys)
            self.log("buff: " + ",".join(keys))
            return True
        macro = cfg.get("macro") or []
        if not macro:
            self.log(f"{MACRO_TASKS.get(key, {}).get('label', key)}: "
                     "macro vazia — grave uma na aba Tarefas.")
            return False
        repeat = max(1, int(cfg.get("repeat", 1)))
        self.log(f"{MACRO_TASKS.get(key, {}).get('label', key)}: "
                 f"{len(macro)} passos x{repeat}")
        return self.run_macro(macro, repeat)

    def run_macro(self, steps, repeat=1):
        for _ in range(max(1, int(repeat))):
            for step in steps:
                if self.stop_event.is_set():
                    return False
                delay = float(step.get("delay", 0) or 0)
                if delay > 0 and not self._sleep(min(delay, 10.0)):
                    return False
                kind = step.get("t")
                if kind == "key":
                    self.tap(step.get("k", ""), float(step.get("hold", 0.06)))
                elif kind == "click":
                    self.click_rel(float(step.get("x", 0.5)),
                                   float(step.get("y", 0.5)))
        return True

    def test_macro(self, steps, repeat=1):
        """Roda uma macro sem depender do agendador (instância parada)."""
        old_stop, old_pause = self.stop_event, self.pause_event
        self.stop_event, self.pause_event = threading.Event(), threading.Event()
        try:
            return self.run_macro(steps, repeat)
        finally:
            self.stop_event, self.pause_event = old_stop, old_pause

    # ── câmaras ─────────────────────────────────────────────────
    def salvar_sala(self, room):
        point = self.point_px("nome")
        if not point:
            self.log("[sala] capture o ponto do nome primeiro")
            return False
        frame = grab(self.hwnd)
        if not frame or is_black(frame[2]):
            self.log("[sala] captura preta — jogo minimizado?")
            return False
        x, y = point
        if x + ROOM_W > frame[0] or y + ROOM_H > frame[1]:
            self.log("[sala] ponto muito perto da borda; recorte incompleto")
            return False
        ref = crop(frame[0], frame[1], frame[2], x, y)
        self.profile.setdefault("refs", {})[room] = base64.b64encode(ref).decode()
        self.manager.cfg.save()
        self.log(f"[sala] referência salva: {room}")
        return True

    def buscar_sala(self, room):
        if self.searching:
            if self.searching == room:
                self.search_stop.set()
            else:
                self.log("[sala] já existe uma busca em andamento; pare primeiro")
            return
        self.search_stop.clear()
        self.searching = room
        self.search_paused = False
        if self.is_running() and not self.pause_event.is_set():
            self.pause_event.set()
            self.search_paused = True
            self.log("[sala] instância pausada durante a busca")
        self.search_thread = threading.Thread(target=self._buscar_sala, args=(room,), daemon=True)
        self.search_thread.start()

    def _buscar_sala(self, room):
        try:
            point = self.point_px("amovivel")
            name_point = self.point_px("nome")
            if not point or not name_point:
                self.log("[sala] faltam pontos (Amovível / Nome)")
                return
            ref_b64 = (self.profile.get("refs") or {}).get(room)
            if not ref_b64:
                self.log(f"[sala] salve a referência de {room} primeiro")
                return
            try:
                ref = base64.b64decode(ref_b64)
            except Exception:
                self.log("[sala] referência corrompida; salve de novo")
                return
            attempts = 0
            while (not self.search_stop.is_set()
                   and self.searching == room):
                if not self.ensure():
                    self.log("[sala] janela fechou; busca encerrada")
                    return
                attempts += 1
                if attempts > 500:
                    self.log("[sala] limite de 500 tentativas; parando")
                    return
                click_at(self.hwnd, *point)
                time.sleep(REFRESH_WAIT)
                frame = grab(self.hwnd)
                if not frame or is_black(frame[2]):
                    self.log("[sala] captura preta — jogo minimizado?")
                    return
                score = diff(crop(frame[0], frame[1], frame[2], *name_point), ref)
                if score <= ROOM_MATCH:
                    enter = self.point_px("entrar")
                    if enter:
                        click_at(self.hwnd, *enter)
                    self.log(f"[sala] {room} achada em {attempts} tentativas (score {score:.0f})")
                    return
                if attempts <= 3 or attempts % 10 == 0:
                    self.log(f"[sala] tentativa {attempts}: score {score:.0f}")
                time.sleep(0.3)
        finally:
            if self.searching == room:
                self.searching = None
            if self.search_paused:
                self.search_paused = False
                if self.is_running():
                    self.pause_event.clear()
                    self.log("[sala] busca encerrada; instância retomada")


# ── gerenciador ──────────────────────────────────────────────────
class Manager:
    def __init__(self, log, cfg=None):
        self.log = log
        self.cfg = cfg or Config()
        self.instances = {}
        self.lock = threading.RLock()

    def refresh(self):
        with self.lock:
            wins = list_mir4_windows()
            alive = {win.hwnd for win in wins}
            for hwnd in list(self.instances):
                if hwnd not in alive:
                    self.instances[hwnd].stop()
                    del self.instances[hwnd]
            for win in wins:
                inst = self.instances.get(win.hwnd)
                if inst is None:
                    name = self.cfg.profile_for_title(win.title)
                    inst = Instance(self, win,
                                    self.cfg.ensure_profile(name), name)
                    self.instances[win.hwnd] = inst
                else:
                    inst.win = win
            return list(self.instances.values())

    def bind(self, hwnd, profile_name):
        inst = self.instances.get(hwnd)
        if not inst:
            return None
        profile = self.cfg.bind(profile_name, inst.win.title)
        inst.profile = profile
        inst.profile_name = profile["name"]
        inst.acordar_started = False
        self.log(f"[{profile['name']}] vinculado a {inst.win.title}")
        return profile

    def start_all(self):
        count = 0
        for inst in self.instances.values():
            if inst.start():
                count += 1
        return count

    def stop_all(self):
        for inst in list(self.instances.values()):
            inst.stop()

    def apply_grid(self, rows, cols):
        wins = list_mir4_windows()
        if not wins:
            return 0
        x, y, w, h = work_area()
        rows = max(1, int(rows))
        cols = max(1, int(cols))
        cell_w, cell_h = w // cols, h // rows
        for i, win in enumerate(wins):
            row, col = divmod(i, cols)
            try:
                win32gui.ShowWindow(win.hwnd, win32con.SW_RESTORE)
                win32gui.MoveWindow(
                    win.hwnd,
                    x + col * cell_w,
                    y + row * cell_h,
                    cell_w,
                    cell_h,
                    True)
            except Exception:
                pass
        self.cfg.layout.update(rows=rows, cols=cols)
        self.cfg.save()
        return len(wins)


# ── launcher / UIAutomation ─────────────────────────────────────
def uia_available():
    try:
        import pywinauto  # noqa: F401
        return True
    except Exception:
        return False


def uia_click(hwnd, x, y, log=None):
    """Clique via UIAutomation; cai para PostMessage se falhar."""
    if uia_available():
        try:
            from pywinauto import Desktop
            abs_x, abs_y = win32gui.ClientToScreen(hwnd, (int(x), int(y)))
            window = Desktop(backend="uia").window(handle=hwnd)
            for button in window.descendants(control_type="Button"):
                rect = button.rectangle()
                if (rect.left <= abs_x <= rect.right
                        and rect.top <= abs_y <= rect.bottom
                        and button.is_enabled()):
                    button.invoke()
                    if log:
                        log("UIA: botão invocado")
                    return True
            for ctrl in window.descendants():
                try:
                    rect = ctrl.rectangle()
                    if (rect.left <= abs_x <= rect.right
                            and rect.top <= abs_y <= rect.bottom
                            and ctrl.is_enabled()):
                        ctrl.invoke()
                        if log:
                            log("UIA: controle invocado")
                        return True
                except Exception:
                    continue
        except Exception as exc:
            if log:
                log(f"UIA falhou ({exc}); usando PostMessage")
    click_at(hwnd, x, y)
    if log:
        log("PostMessage: clique enviado")
    return True


def open_launcher(path):
    if not path:
        raise ValueError("caminho do launcher vazio")
    if path.lower().startswith(("steam://", "http://", "https://")):
        os.startfile(path)
    elif path.lower().endswith((".lnk", ".url")):
        os.startfile(path)
    else:
        subprocess.Popen(path)


def launch_two(path, title, g1, g2, pre=3.0, between=6.0, log=None, stop_event=None):
    """Abre o launcher e clica em Game 1 e Game 2 via UIAutomation."""
    log = log or (lambda _m: None)
    stop_event = stop_event or threading.Event()

    log("Abrindo launcher...")
    before = {win.hwnd for win in list_mir4_windows()}
    open_launcher(path)
    launcher = find_launcher_window(title, timeout=40, stop_event=stop_event)
    if not launcher:
        log(f"Launcher '{title}' não encontrado ou abertura cancelada.")
        return False
    log(f"Launcher encontrado (HWND {launcher}).")
    if stop_event.wait(pre):
        return False

    log("Clicando em Game 1...")
    uia_click(launcher, g1[0], g1[1], log)
    game1 = wait_game_window(before, timeout=120, stop_event=stop_event)
    if stop_event.is_set():
        log("Abertura cancelada.")
        return False
    if game1:
        before.add(game1)
        log("Game 1 aberto.")
    else:
        log("Game 1 não apareceu a tempo; seguindo para o Game 2.")

    if stop_event.wait(between):
        log("Abertura cancelada.")
        return False
    if not win32gui.IsWindow(launcher):
        launcher = find_launcher_window(title, timeout=20, stop_event=stop_event)
    if not launcher:
        log("Launcher fechou; não deu para abrir o Game 2.")
        return False

    log("Clicando em Game 2...")
    uia_click(launcher, g2[0], g2[1], log)
    game2 = wait_game_window(before, timeout=120, stop_event=stop_event)
    if stop_event.is_set():
        log("Abertura cancelada.")
        return False
    if game2:
        log("Game 2 aberto.")
    else:
        log("Game 2 não apareceu a tempo.")
    return True
