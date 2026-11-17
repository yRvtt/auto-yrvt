"""
MIR4 Bot — manda teclas pra janela do MIR4 sem tirar o foco (PostMessage).

Pode usar o PC normalmente enquanto o bot roda.
"""

import base64
import ctypes
import ctypes.wintypes
import json
import os
import random
import sys
import threading
import time
import tkinter as tk
from tkinter import messagebox

import updater

try:
    import win32api
    import win32con
    import win32gui
except ImportError:
    print("pip install pywin32")
    sys.exit(1)

APP_VERSION = "1.1.0"

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

# teclas com prefixo E0 — sem isso o jogo recebe tecla de numpad
EXT_KEYS = {"pageup", "pagedown", "insert", "delete", "home", "end",
            "up", "down", "left", "right"}


def key_code(name):
    """'pagedown' -> (0x51, True) | None"""
    name = (name or "").strip().lower()
    sc = SCAN.get(name)
    return None if sc is None else (sc, name in EXT_KEYS)


def post_key(hwnd, scan, up=False, ext=False):
    """WM_KEYDOWN/UP direto na janela — não muda o foco."""
    try:
        vk = user32.MapVirtualKeyW(scan, MAPVK_VSC_TO_VK_EX) or scan
        lparam = 1 | (scan << 16)
        if ext:
            lparam |= 1 << 24
        if up:
            lparam |= (1 << 30) | (1 << 31)
        msg = win32con.WM_KEYUP if up else win32con.WM_KEYDOWN
        return bool(win32api.PostMessage(hwnd, msg, int(vk), lparam))
    except Exception:
        return False


MIR4_TITLE = "Mir4G"


def find_mir4():
    found = []

    def cb(hwnd, _):
        if win32gui.IsWindowVisible(hwnd) and MIR4_TITLE.lower() in win32gui.GetWindowText(hwnd).lower():
            found.append(hwnd)
            return False
        return True

    try:
        win32gui.EnumWindows(cb, None)
    except Exception:
        pass
    return found[0] if found else None


def is_admin():
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


# ── Captura da janela / clique ───────────────────────────────────
gdi32 = ctypes.windll.gdi32

ROOM_W, ROOM_H = 340, 26          # recorte do nome da sala (px)
ROOM_MATCH = 15                   # score máx. pra considerar "é a sala"
REFRESH_WAIT = 1.0                # tempo pro jogo trocar de sala

ROOMS = [("xp1", "XP 1"), ("xp2", "XP 2"), ("xp3", "XP 3"), ("anti", "Anti-Demônio")]

CFG_DIR = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), "MIR4Bot")
CFG_FILE = os.path.join(CFG_DIR, "salas.json")


class BMI(ctypes.Structure):
    _fields_ = [("biSize", ctypes.wintypes.DWORD), ("biWidth", ctypes.wintypes.LONG),
                ("biHeight", ctypes.wintypes.LONG), ("biPlanes", ctypes.wintypes.WORD),
                ("biBitCount", ctypes.wintypes.WORD), ("biCompression", ctypes.wintypes.DWORD),
                ("biSizeImage", ctypes.wintypes.DWORD), ("biXPelsPerMeter", ctypes.wintypes.LONG),
                ("biYPelsPerMeter", ctypes.wintypes.LONG), ("biClrUsed", ctypes.wintypes.DWORD),
                ("biClrImportant", ctypes.wintypes.DWORD)]


def grab(hwnd):
    """(w, h, pixels BGRA bottom-up) da área cliente, mesmo coberta por outras janelas."""
    r = ctypes.wintypes.RECT()
    if not user32.GetClientRect(hwnd, ctypes.byref(r)):
        return None
    w, h = r.right, r.bottom
    if w <= 0 or h <= 0:
        return None
    hdc = user32.GetDC(hwnd)
    mem = gdi32.CreateCompatibleDC(hdc)
    bmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
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
    x = max(0, min(x, w - 1))
    y = max(0, min(y, h - 1))
    cw, ch = min(cw, w - x), min(ch, h - y)
    out = b"".join(data[(h - 1 - row) * w * 4 + x * 4:][:cw * 4] for row in range(y, y + ch))
    return out


def diff(a, b):
    """diferença média (0-255) amostrando 1 a cada 13 bytes"""
    if not a or len(a) != len(b):
        return 999.0
    idx = range(0, len(a), 13)
    return sum(abs(a[i] - b[i]) for i in idx) / len(idx)


def is_black(data):
    return not any(data[0:len(data):997])


def click_at(hwnd, x, y):
    lp = (int(y) << 16) | int(x)
    win32api.PostMessage(hwnd, win32con.WM_MOUSEMOVE, 0, lp)
    time.sleep(0.03)
    win32api.PostMessage(hwnd, win32con.WM_LBUTTONDOWN, win32con.MK_LBUTTON, lp)
    time.sleep(0.05)
    win32api.PostMessage(hwnd, win32con.WM_LBUTTONUP, 0, lp)
    return True


def client_point(hwnd):
    x, y = win32api.GetCursorPos()
    return win32gui.ScreenToClient(hwnd, (x, y))


# ── Bot ──────────────────────────────────────────────────────────
class Bot:
    def __init__(self, log_fn):
        self.log = log_fn
        self.stop = threading.Event()
        self.hwnd = None
        self.flags = {"acordar": False, "farm": False, "buff": False}
        self.buff_keys = ["1", "2", "3"]
        self.buff_interval = 180
        self.pts = {"amovivel": None, "nome": None, "entrar": None}
        self.refs = {}
        self.search_stop = threading.Event()
        self.searching = None
        self.load_cfg()

    def _ensure_hwnd(self):
        if not self.hwnd or not win32gui.IsWindow(self.hwnd):
            self.hwnd = find_mir4()
        return bool(self.hwnd)

    def tap(self, key_name, hold=0.06):
        code = key_code(key_name)
        if not code or not self._ensure_hwnd():
            return False
        scan, ext = code
        ok = post_key(self.hwnd, scan, False, ext)
        time.sleep(hold)
        return post_key(self.hwnd, scan, True, ext) and ok

    def burst(self, keys, hold=0.06):
        codes = [key_code(k) for k in keys]
        if not all(codes) or not self._ensure_hwnd():
            return False
        ok = True
        for scan, ext in codes:
            ok = post_key(self.hwnd, scan, False, ext) and ok
            time.sleep(hold)
            ok = post_key(self.hwnd, scan, True, ext) and ok
            time.sleep(0.05)
        if not ok:
            self.log("[!] PostMessage falhou — abra o bot como admin")
        return ok

    def start(self):
        for fn in (self._acordar_loop, self._farm_loop, self._buff_loop):
            threading.Thread(target=fn, daemon=True).start()

    # ── Câmaras: config salva em %APPDATA%\MIR4Bot ───────────────
    def load_cfg(self):
        try:
            with open(CFG_FILE, encoding="utf-8") as f:
                d = json.load(f)
        except Exception:
            return
        for k, v in (d.get("pts") or {}).items():
            if k in self.pts and v:
                self.pts[k] = tuple(v)
        self.refs = {k: base64.b64decode(v) for k, v in (d.get("refs") or {}).items()}

    def save_cfg(self):
        os.makedirs(CFG_DIR, exist_ok=True)
        d = {"pts": self.pts,
             "refs": {k: base64.b64encode(v).decode() for k, v in self.refs.items()}}
        with open(CFG_FILE, "w", encoding="utf-8") as f:
            json.dump(d, f)

    def salvar_sala(self, room):
        """Guarda o recorte atual como referência da sala (treino)."""
        if not self._ensure_hwnd() or not self.pts["nome"]:
            self.log("[sala] capture o ponto do nome primeiro")
            return False
        f = grab(self.hwnd)
        if not f or is_black(f[2]):
            self.log("[sala] captura preta — jogo minimizado?")
            return False
        self.refs[room] = crop(f[0], f[1], f[2], *self.pts["nome"])
        self.save_cfg()
        self.log(f"[sala] referência salva: {room}")
        return True

    def buscar_sala(self, room):
        """Clica no Amovível até o nome bater com a referência da sala."""
        self.searching = room
        self.search_stop.clear()
        try:
            if not self.pts["amovivel"] or not self.pts["nome"]:
                self.log("[sala] faltam pontos (Amovível / Nome)")
                return
            if room not in self.refs:
                self.log(f"[sala] salve a referência de {room} primeiro")
                return
            n = 0
            while (not self.search_stop.is_set() and self.searching == room
                   and not self.stop.is_set()):
                if not self._ensure_hwnd():
                    time.sleep(1)
                    continue
                n += 1
                click_at(self.hwnd, *self.pts["amovivel"])
                time.sleep(REFRESH_WAIT)
                f = grab(self.hwnd)
                if not f or is_black(f[2]):
                    self.log("[sala] captura preta — jogo minimizado?")
                    return
                score = diff(crop(f[0], f[1], f[2], *self.pts["nome"]), self.refs[room])
                if score <= ROOM_MATCH:
                    if self.pts["entrar"]:
                        click_at(self.hwnd, *self.pts["entrar"])
                    self.log(f"[sala] {room} achada em {n} tentativas (score {score:.0f})")
                    return
                if n <= 3 or n % 10 == 0:
                    self.log(f"[sala] tentativa {n}: score {score:.0f}")
                time.sleep(0.3)
        finally:
            if self.searching == room:
                self.searching = None


    # ── Acordar (Tributação) ─────────────────────────────────────
    def _acordar_loop(self):
        while not self.stop.is_set():
            if not self.flags["acordar"]:
                self.stop.wait(0.5)
                continue
            if not self._ensure_hwnd():
                self.stop.wait(3)
                continue

            self.tap("r")
            self.log("[acordar] R → menu")
            time.sleep(0.5)

            while self.flags["acordar"] and not self.stop.is_set():
                if not self._ensure_hwnd():
                    break
                self.burst(["tab"] + ["pagedown"] * 3 + ["f"])
                self.log("[acordar] Ciclo OK")
                time.sleep(3.8 + random.uniform(0, 0.1))

    # ── Auto-Farm ────────────────────────────────────────────────
    def _farm_loop(self):
        while not self.stop.is_set():
            if not self.flags["farm"]:
                self.stop.wait(0.5)
                continue
            if not self._ensure_hwnd():
                self.stop.wait(3)
                continue
            self.tap("f")
            time.sleep(4.0)
            self.tap("r")
            time.sleep(1.0)
            self.log("[farm] F + R")

    # ── Auto-Buff ────────────────────────────────────────────────
    def _buff_loop(self):
        while not self.stop.is_set():
            if not self.flags["buff"]:
                self.stop.wait(0.5)
                continue
            if not self._ensure_hwnd():
                self.stop.wait(3)
                continue
            self.burst(self.buff_keys)
            self.log(f"[buff] {','.join(self.buff_keys)}")
            self.stop.wait(self.buff_interval)


# ── GUI ──────────────────────────────────────────────────────────
BG = "#1a1b26"
BG2 = "#24283b"
BG3 = "#414868"
FG = "#c0caf5"
GREEN = "#9ece6a"
RED = "#f7768e"
BLUE = "#7aa2f7"
ORANGE = "#ff9e64"
PURPLE = "#bb9af7"
FONTS = ("Segoe UI", 9)
FONTB = ("Segoe UI", 11, "bold")


class ToggleButton(tk.Canvas):
    def __init__(self, parent, callback, **kw):
        super().__init__(parent, width=50, height=26, bg=BG2, highlightthickness=0, **kw)
        self.on = False
        self.cb = callback
        self._draw()
        self.bind("<Button-1>", self._click)

    def _draw(self):
        self.delete("all")
        if self.on:
            self.create_rectangle(0, 0, 50, 26, fill=GREEN, outline="")
            self.create_oval(26, 2, 48, 24, fill="white", outline="")
        else:
            self.create_rectangle(0, 0, 50, 26, fill=BG3, outline="")
            self.create_oval(2, 2, 24, 24, fill="#565f89", outline="")

    def _click(self, e=None):
        self.set(not self.on)
        self.cb(self.on)

    def set(self, val):
        self.on = val
        self._draw()


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("MIR4 Bot")
        self.resizable(False, False)
        self.configure(bg=BG)
        self.bot = Bot(self._log)
        self._build()
        self.bot.start()
        self._tick()
        self.protocol("WM_DELETE_WINDOW", self._quit)
        if not is_admin():
            self._log("[!] Sem admin — rode como admin se o MIR4 estiver elevado")
        threading.Thread(target=lambda: self._check_update(silent=True), daemon=True).start()

    def _build(self):
        m = tk.Frame(self, bg=BG, padx=16, pady=12)
        m.pack()

        hdr = tk.Frame(m, bg=BG)
        hdr.pack(fill="x")
        tk.Label(hdr, text="MIR4 Bot", bg=BG, fg=BLUE,
                 font=("Segoe UI", 18, "bold")).pack(side="left")
        tk.Label(hdr, text=" background ", bg=GREEN, fg=BG,
                 font=("Segoe UI", 7, "bold")).pack(side="left", padx=(8, 0))
        self.dot = tk.Label(hdr, text="●", bg=BG, fg=RED, font=("Segoe UI", 14))
        self.dot.pack(side="right")
        self.lbl_ver = tk.Label(hdr, text=f"v{APP_VERSION}", bg=BG, fg="#565f89",
                                font=("Segoe UI", 8), cursor="hand2")
        self.lbl_ver.pack(side="right", padx=6)
        self.lbl_ver.bind("<Button-1>", lambda e: self._check_update(silent=False))

        self.lbl_status = tk.Label(m, text="Procurando MIR4...", bg=BG, fg="#565f89", font=FONTS)
        self.lbl_status.pack(anchor="w", pady=(0, 4))

        tk.Label(m, text="Envia tecla direto pra janela — não tira seu foco.",
                 bg=BG, fg=ORANGE, font=("Segoe UI", 8)).pack(anchor="w", pady=(0, 8))

        self.toggles = {}
        self._section(m, "AUTOMAÇÕES")
        for key, label, desc in [
            ("acordar", "Acordar", "Tributação: R → Tab → PageDown x3 → F"),
            ("farm", "Auto-Farm", "Loop: ataque (F) + coleta (R)"),
            ("buff", "Auto-Buff", "Teclas de buff periodicamente"),
        ]:
            card = tk.Frame(m, bg=BG2, padx=12, pady=8)
            card.pack(fill="x", pady=3)
            left = tk.Frame(card, bg=BG2)
            left.pack(side="left", fill="x", expand=True)
            tk.Label(left, text=label, bg=BG2, fg=FG, font=FONTB).pack(anchor="w")
            tk.Label(left, text=desc, bg=BG2, fg="#565f89",
                     font=("Segoe UI", 8)).pack(anchor="w")
            toggle = ToggleButton(card, lambda on, k=key: self._on_toggle(k, on))
            toggle.pack(side="right", padx=(8, 0))
            self.toggles[key] = toggle

        ctrl = tk.Frame(m, bg=BG, pady=8)
        ctrl.pack(fill="x")
        for text, color, cmd in [
            ("Tudo ON", GREEN, self._all_on),
            ("Tudo OFF", RED, self._all_off),
        ]:
            tk.Button(ctrl, text=text, bg=color, fg=BG, relief="flat",
                      font=("Segoe UI", 9, "bold"), padx=10, pady=2,
                      activebackground=color, command=cmd).pack(side="left", padx=(0, 5))

        # ── Câmaras ──────────────────────────────────────────────
        self._section(m, "CÂMARAS — clica no Amovível até achar")
        self.room_btns = {}
        for key, label in ROOMS:
            row = tk.Frame(m, bg=BG2, padx=10, pady=4)
            row.pack(fill="x", pady=2)
            tk.Label(row, text=label, bg=BG2, fg=FG, font=FONTS, width=12,
                     anchor="w").pack(side="left")
            self.room_btns[key] = tk.Button(
                row, text="Procurar", bg=BLUE, fg=BG, relief="flat", bd=0,
                font=("Segoe UI", 8, "bold"), padx=10, pady=2,
                command=lambda k=key: self._search(k))
            self.room_btns[key].pack(side="right", padx=(4, 0))
            tk.Button(row, text="Salvar", bg=BG3, fg=FG, relief="flat", bd=0,
                      font=("Segoe UI", 8), padx=6, pady=2,
                      command=lambda k=key: self.bot.salvar_sala(k)).pack(side="right")

        pts = tk.Frame(m, bg=BG)
        pts.pack(fill="x", pady=(6, 0))
        for text, key in [("Ponto Amovível", "amovivel"),
                          ("Ponto Nome", "nome"),
                          ("Ponto Entrar", "entrar")]:
            tk.Button(pts, text=text, bg=BG3, fg=FG, relief="flat", bd=0,
                      font=("Segoe UI", 8), padx=6, pady=2,
                      command=lambda k=key: self._capture_point(k)).pack(side="left", padx=(0, 4))

        # teste de tecla
        self._section(m, "AJUSTES")
        test_row = tk.Frame(m, bg=BG)
        test_row.pack(fill="x", pady=(0, 6))
        tk.Label(test_row, text="Testar tecla", bg=BG, fg="#565f89", font=FONTS).pack(side="left", padx=(0, 6))
        self.ent_test = tk.Entry(test_row, width=8, bg=BG2, fg=FG, font=FONTS,
                                 insertbackground=FG, relief="flat", bd=4)
        self.ent_test.insert(0, "1")
        self.ent_test.pack(side="left", padx=(0, 6))
        tk.Button(test_row, text="Enviar", bg=BLUE, fg=BG, relief="flat",
                  font=("Segoe UI", 8, "bold"), padx=8, pady=1,
                  command=self._test_key).pack(side="left")

        # config
        cfg = tk.Frame(m, bg=BG)
        cfg.pack(fill="x")
        tk.Label(cfg, text="Buff teclas", bg=BG, fg="#565f89", font=FONTS).pack(side="left")
        self.ent_keys = tk.Entry(cfg, width=8, bg=BG2, fg=FG, font=FONTS,
                                 insertbackground=FG, relief="flat", bd=4)
        self.ent_keys.insert(0, "1,2,3")
        self.ent_keys.pack(side="left", padx=4)
        tk.Label(cfg, text="a cada", bg=BG, fg="#565f89", font=FONTS).pack(side="left")
        self.ent_int = tk.Entry(cfg, width=4, bg=BG2, fg=FG, font=FONTS,
                                insertbackground=FG, relief="flat", bd=4)
        self.ent_int.insert(0, "180")
        self.ent_int.pack(side="left", padx=4)
        tk.Label(cfg, text="s", bg=BG, fg="#565f89", font=FONTS).pack(side="left")
        tk.Button(cfg, text="Salvar", bg=PURPLE, fg=BG, relief="flat",
                  font=("Segoe UI", 8, "bold"), padx=6,
                  command=self._save_cfg).pack(side="right")

        # log
        tk.Frame(m, bg=BG3, height=1).pack(fill="x", pady=(10, 6))
        self.log_box = tk.Text(m, height=6, width=42, bg=BG2, fg="#7982a9",
                               font=("Consolas", 8), relief="flat", state="disabled",
                               wrap="word", padx=6, pady=4)
        self.log_box.pack(fill="x")

    # ── ações ────────────────────────────────────────────────────
    # ── câmaras ──────────────────────────────────────────────────
    def _section(self, parent, text):
        tk.Label(parent, text=text, bg=BG, fg="#565f89",
                 font=("Segoe UI", 8, "bold")).pack(anchor="w", pady=(10, 3))

    def _capture_point(self, key):
        hwnd = find_mir4()
        if not hwnd:
            self._log("[pts] MIR4 não encontrado")
            return
        self._log(f"[pts] 3s — ponha o mouse no ponto de {key}")

        def snap():
            x, y = client_point(hwnd)
            self.bot.pts[key] = (x, y)
            self.bot.save_cfg()
            self._log(f"[pts] {key} = {x},{y}")

        self.after(3000, snap)

    def _search(self, key):
        if self.bot.searching == key:
            self.bot.search_stop.set()
            return
        threading.Thread(target=self.bot.buscar_sala, args=(key,), daemon=True).start()

    def _test_key(self):
        key = self.ent_test.get().strip() or "1"
        if key_code(key) is None:
            self._log(f"[teste] tecla '{key}' desconhecida")
            return
        ok = self.bot.tap(key)
        self._log(f"[teste] {key} → {'ok' if ok else 'FALHOU'}")

    def _on_toggle(self, key, on):
        self.bot.flags[key] = on
        self._log(f"[{key}] {'ON' if on else 'OFF'}")

    def _all_on(self):
        for k, t in self.toggles.items():
            self.bot.flags[k] = True
            t.set(True)
        self._log("[*] Tudo ON")

    def _all_off(self):
        for k, t in self.toggles.items():
            self.bot.flags[k] = False
            t.set(False)
        self._log("[*] Tudo OFF")

    def _save_cfg(self):
        keys = [k.strip() for k in self.ent_keys.get().split(",") if k.strip()]
        if keys:
            self.bot.buff_keys = keys
        try:
            v = int(self.ent_int.get())
            if v > 0:
                self.bot.buff_interval = v
        except ValueError:
            pass
        self._log(f"[cfg] Buff: {self.bot.buff_keys} / {self.bot.buff_interval}s")

    # ── update ───────────────────────────────────────────────────
    def _check_update(self, silent=False):
        if not updater.is_frozen():
            if not silent:
                self._log("[update] só funciona no .exe")
            return
        try:
            info = updater.check(APP_VERSION)
        except Exception as e:
            self._log(f"[update] falha: {e}")
            return
        if not info:
            if not silent:
                self._log(f"[update] v{APP_VERSION} é a última")
            return
        self._log(f"[update] nova versão {info['tag']}")
        self.after(0, lambda: self._ask_update(info))

    def _ask_update(self, info):
        if messagebox.askyesno("Atualização",
                               f"Nova versão {info['tag']} disponível.\n\nBaixar e reiniciar?"):
            threading.Thread(target=self._install_update, args=(info,), daemon=True).start()

    def _install_update(self, info):
        try:
            updater.download_and_restart(info["url"], log=self._log)
        except Exception as e:
            self._log(f"[update] erro: {e}")
            return
        self.after(0, self._quit)

    # ── infra ────────────────────────────────────────────────────
    def _log(self, msg):
        def _w():
            self.log_box.config(state="normal")
            self.log_box.insert("end", f"{time.strftime('%H:%M:%S')}  {msg}\n")
            self.log_box.see("end")
            n = int(self.log_box.index("end-1c").split(".")[0])
            if n > 200:
                self.log_box.delete("1.0", f"{n - 200}.0")
            self.log_box.config(state="disabled")
        self.after(0, _w)

    def _tick(self):
        hwnd = find_mir4()
        if hwnd:
            fg = user32.GetForegroundWindow() == hwnd
            self.lbl_status.config(text=f"MIR4 conectado — {'em foco' if fg else 'em background'}")
            self.dot.config(fg=GREEN)
        else:
            self.lbl_status.config(text="MIR4 não encontrado")
            self.dot.config(fg=RED)

        for k, b in getattr(self, "room_btns", {}).items():
            run = self.bot.searching == k
            b.config(text="Parar" if run else "Procurar",
                     bg=RED if run else BLUE, fg=BG)
        self.after(1000, self._tick)

    def _quit(self):
        self.bot.stop.set()
        self.destroy()


if __name__ == "__main__":
    App().mainloop()
