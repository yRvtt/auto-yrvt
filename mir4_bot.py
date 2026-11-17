"""
MIR4 Bot — manda teclas pra janela do MIR4 sem tirar o foco (PostMessage).

Pode usar o PC normalmente enquanto o bot roda.
"""

import random
import sys
import threading
import time
import tkinter as tk
from tkinter import messagebox

import ctypes

import updater

try:
    import win32api
    import win32con
    import win32gui
except ImportError:
    print("pip install pywin32")
    sys.exit(1)

APP_VERSION = "1.0.0"

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


def force_foreground(hwnd):
    try:
        fg = user32.GetForegroundWindow()
        if fg == hwnd:
            return True
        fg_tid = user32.GetWindowThreadProcessId(fg, None)
        tid = user32.GetWindowThreadProcessId(hwnd, None)
        if fg_tid != tid:
            user32.AttachThreadInput(fg_tid, tid, True)
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        user32.SetForegroundWindow(hwnd)
        if fg_tid != tid:
            user32.AttachThreadInput(fg_tid, tid, False)
        return user32.GetForegroundWindow() == hwnd
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


# ── Bot ──────────────────────────────────────────────────────────
class Bot:
    def __init__(self, log_fn):
        self.log = log_fn
        self.stop = threading.Event()
        self.hwnd = None
        self.flags = {"acordar": False, "farm": False, "buff": False}
        self.buff_keys = ["1", "2", "3"]
        self.buff_interval = 180

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
            ("Focar MIR4", BLUE, self._focus),
        ]:
            tk.Button(ctrl, text=text, bg=color, fg=BG, relief="flat",
                      font=("Segoe UI", 9, "bold"), padx=10, pady=2,
                      activebackground=color, command=cmd).pack(side="left", padx=(0, 5))

        # teste de tecla
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
        tk.Frame(m, bg=BG3, height=1).pack(fill="x", pady=(4, 8))
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
        tk.Frame(m, bg=BG3, height=1).pack(fill="x", pady=8)
        self.log_box = tk.Text(m, height=6, width=42, bg=BG2, fg="#7982a9",
                               font=("Consolas", 8), relief="flat", state="disabled",
                               wrap="word", padx=6, pady=4)
        self.log_box.pack(fill="x")

    # ── ações ────────────────────────────────────────────────────
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

    def _focus(self):
        hwnd = find_mir4()
        if hwnd:
            force_foreground(hwnd)
            self._log("[foco] MIR4 focado")
        else:
            self._log("[!] MIR4 não encontrado")

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
        self.after(2000, self._tick)

    def _quit(self):
        self.bot.stop.set()
        self.destroy()


if __name__ == "__main__":
    App().mainloop()
