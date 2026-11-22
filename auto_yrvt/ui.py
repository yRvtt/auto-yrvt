"""
Interface do Auto yRvt.

Multi-instância: uma janela do jogo por instância, cada uma com seu perfil,
modo de combate, macros e câmaras. A comunicação com o jogo continua sendo
por PostMessage (não rouba o foco).
"""

import os
import queue
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog

import win32api
import win32gui

from . import memory, sniffer, ue4, updater
from .core import (
    APP_NAME, APP_VERSION, CFG_DIR, MACRO_TASKS, MODE_DEFAULT_INTERVAL,
    MODES, ROOMS, Manager, Recorder, VK_F12, client_point, client_size,
    find_launcher_window, format_duration, is_admin, launch_two, open_launcher,
    uia_available, window_sort_key, xp_rate,
)

BG = "#0a0d12"
SIDEBAR = "#0e131b"
SURFACE = "#121924"
SURFACE2 = "#17202c"
SURFACE3 = "#1e2937"
BORDER = "#223044"
FG = "#f2f6fc"
MUTED = "#8fa0b5"
ACCENT = "#f2b45c"
ACCENT2 = "#ffd08a"
GREEN = "#4ade80"
ORANGE = "#fbbf24"
RED = "#f87171"
FONT = "Segoe UI"
MONO = "Consolas"

MODE_LABELS = {key: label for key, label, _ in MODES}


def resource_path(name):
    if hasattr(sys, "_MEIPASS"):
        base = os.path.join(sys._MEIPASS, "auto_yrvt", "resources")
    else:
        base = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "resources")
    return os.path.join(base, name)


def _mix(c1, c2, t):
    t = max(0.0, min(1.0, t))
    a = tuple(int(c1[i:i + 2], 16) for i in (1, 3, 5))
    b = tuple(int(c2[i:i + 2], 16) for i in (1, 3, 5))
    return "#%02x%02x%02x" % tuple(
        round(a[i] + (b[i] - a[i]) * t) for i in range(3))


class HoverButton(tk.Button):
    def __init__(self, master, text="", command=None, bg=SURFACE2,
                 hover=SURFACE3, fg=FG, font=(FONT, 9), padx=12, pady=6, **kw):
        super().__init__(master, text=text, command=command, bg=bg, fg=fg,
                         activebackground=hover, activeforeground=fg,
                         relief="flat", bd=0, padx=padx, pady=pady,
                         cursor="hand2", font=font, **kw)
        self._base, self._hover = bg, hover
        self.bind("<Enter>", self._enter, add="+")
        self.bind("<Leave>", self._leave, add="+")

    def _enter(self, _event=None):
        self._fade(self._hover)

    def _leave(self, _event=None):
        self._fade(self._base)

    def _fade(self, target):
        try:
            if str(self["state"]) == "disabled":
                return
            start = self.cget("bg")
        except Exception:
            return

        def step(i):
            try:
                if not self.winfo_exists():
                    return
                self.config(bg=_mix(start, target, i / 6))
            except Exception:
                return
            if i < 6:
                self.after(12, lambda: step(i + 1))

        step(1)


class ToolTip:
    def __init__(self, widget, text):
        self.widget, self.text = widget, text
        self.tip = None
        self.job = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")

    def _schedule(self, _event=None):
        self.job = self.widget.after(450, self._show)

    def _hide(self, _event=None):
        if self.job:
            try:
                self.widget.after_cancel(self.job)
            except Exception:
                pass
            self.job = None
        if self.tip:
            try:
                self.tip.destroy()
            except Exception:
                pass
            self.tip = None

    def _show(self):
        try:
            x = self.widget.winfo_rootx() + self.widget.winfo_width() // 2
            y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6
            self.tip = tk.Toplevel(self.widget)
            self.tip.wm_overrideredirect(True)
            self.tip.attributes("-topmost", True)
            tk.Label(self.tip, text=self.text, bg=SURFACE3, fg=FG,
                     padx=8, pady=4, font=(FONT, 8)).pack()
            self.tip.wm_geometry(f"+{x - len(self.text) * 3}+{y}")
        except Exception:
            self.tip = None


class ToggleSwitch(tk.Canvas):
    def __init__(self, master, variable, command=None, bg=SURFACE2,
                 width=44, height=24):
        super().__init__(master, width=width, height=height, bg=bg,
                         highlightthickness=0, bd=0, cursor="hand2")
        self.var, self.command = variable, command
        self.w, self.h = width, height
        self.pos = 1.0 if variable.get() else 0.0
        self.bind("<Button-1>", self._toggle)
        variable.trace_add("write", lambda *_: self._sync())
        self._draw()

    def _draw(self):
        self.delete("all")
        track = _mix("#2b3748", ACCENT, self.pos)
        r = self.h // 2
        self.create_oval(1, 1, self.h - 1, self.h - 1, fill=track, outline="")
        self.create_oval(self.w - self.h + 1, 1, self.w - 1, self.h - 1,
                         fill=track, outline="")
        self.create_rectangle(r, 1, self.w - r, self.h - 1,
                              fill=track, outline="")
        kd = self.h - 6
        kx = 3 + (self.w - 6 - kd) * self.pos
        self.create_oval(kx, 3, kx + kd, self.h - 3, fill=FG, outline="")

    def _toggle(self, _event=None):
        self.var.set(not self.var.get())
        if self.command:
            self.command()

    def _sync(self):
        target = 1.0 if self.var.get() else 0.0
        start = self.pos

        def step(i):
            try:
                if not self.winfo_exists():
                    return
                self.pos = start + (target - start) * (i / 7)
                self._draw()
            except Exception:
                return
            if i < 7:
                self.after(14, lambda: step(i + 1))

        step(1)


class StatusDot(tk.Canvas):
    COLORS = {"rodando": GREEN, "pausado": ORANGE,
              "parado": MUTED, "gravando": RED}

    def __init__(self, master, bg=SURFACE2, size=14):
        super().__init__(master, width=size, height=size, bg=bg,
                         highlightthickness=0, bd=0)
        self.size = size
        self.state = "parado"
        self.phase = False
        self._draw()

    def set_state(self, state, phase=False):
        self.state = state
        self.phase = phase
        self._draw()

    def _draw(self):
        self.delete("all")
        color = self.COLORS.get(self.state, MUTED)
        if self.state == "rodando" and self.phase:
            color = _mix(color, self.cget("bg"), 0.45)
        pad = 2
        self.create_oval(pad, pad, self.size - pad, self.size - pad,
                         fill=color, outline="")


class SlimScrollbar(tk.Canvas):
    """Barra de rolagem fina e escura, desenhada no Canvas."""

    def __init__(self, master, command, bg=SURFACE, width=8):
        super().__init__(master, width=width, bg=bg,
                         highlightthickness=0, bd=0, cursor="hand2")
        self.command = command
        self.first, self.last = 0.0, 1.0
        self.bind("<Configure>", lambda _e: self._draw())
        self.bind("<Button-1>", self._jump)
        self.bind("<B1-Motion>", self._jump)

    def set(self, first, last):
        self.first, self.last = float(first), float(last)
        self._draw()

    def _draw(self):
        self.delete("all")
        height = self.winfo_height()
        width = self.winfo_width()
        if height <= 1:
            return
        if self.first <= 0 and self.last >= 1:
            return
        y1 = int(self.first * height)
        y2 = max(y1 + 22, int(self.last * height))
        self.create_rectangle(2, y1, max(3, width - 2), min(height, y2),
                              fill=SURFACE3, outline="")

    def _jump(self, event):
        height = max(1, self.winfo_height())
        self.command("moveto", max(0.0, min(1.0, event.y / height)))


class ScrollFrame(tk.Frame):
    def __init__(self, master, bg=BG):
        super().__init__(master, bg=bg)
        self.canvas = tk.Canvas(self, bg=bg, highlightthickness=0, bd=0)
        self.scroll = SlimScrollbar(self, command=self.canvas.yview,
                                    bg=bg)
        self.canvas.configure(yscrollcommand=self.scroll.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.scroll.pack(side="right", fill="y")
        self.inner = tk.Frame(self.canvas, bg=bg)
        self.window = self.canvas.create_window((0, 0), window=self.inner,
                                                anchor="nw")
        self.inner.bind(
            "<Configure>",
            lambda _e: self.canvas.configure(
                scrollregion=self.canvas.bbox("all")))
        self.canvas.bind(
            "<Configure>",
            lambda e: self.canvas.itemconfigure(self.window, width=e.width))

    def _wheel(self, event):
        try:
            self.canvas.yview_scroll(int(-event.delta / 120), "units")
        except Exception:
            pass


class NavItem(tk.Frame):
    def __init__(self, master, icon, text, command):
        super().__init__(master, bg=SIDEBAR, cursor="hand2")
        self.command = command
        self.bar = tk.Frame(self, bg=SIDEBAR, width=3)
        self.bar.pack(side="left", fill="y")
        self.icon = tk.Label(self, text=icon, bg=SIDEBAR, fg=MUTED,
                             font=(FONT, 11), width=3)
        self.icon.pack(side="left", pady=9)
        self.text = tk.Label(self, text=text, bg=SIDEBAR, fg=MUTED,
                             font=(FONT, 9), anchor="w")
        self.text.pack(side="left", fill="x", expand=True, pady=9)
        for w in (self, self.icon, self.text):
            w.bind("<Button-1>", lambda _e: self.command())
            w.bind("<Enter>", self._enter)
            w.bind("<Leave>", self._leave)

    def _paint(self, bg, fg):
        self.configure(bg=bg)
        self.icon.configure(bg=bg, fg=fg)
        self.text.configure(bg=bg, fg=fg)

    def _enter(self, _event=None):
        if self.bar.cget("bg") != ACCENT:
            self._paint(SURFACE, FG)

    def _leave(self, _event=None):
        if self.bar.cget("bg") != ACCENT:
            self._paint(SIDEBAR, MUTED)

    def set_active(self, active):
        if active:
            self.bar.configure(bg=ACCENT)
            self._paint(SURFACE2, FG)
        else:
            self.bar.configure(bg=SIDEBAR)
            self._paint(SIDEBAR, MUTED)


class DarkCombo(tk.Frame):
    def __init__(self, master, variable):
        super().__init__(master, bg=SURFACE2, highlightbackground=BORDER,
                         highlightthickness=1)
        self.var = variable
        self.btn = tk.Menubutton(
            self, textvariable=variable, bg=SURFACE2, fg=FG,
            activebackground=SURFACE3, activeforeground=FG, relief="flat",
            anchor="w", padx=8, pady=5, font=(FONT, 9), cursor="hand2",
            indicatoron=False)
        self.btn.pack(side="left", fill="both", expand=True)
        self.arrow = tk.Label(self, text="▾", bg=SURFACE2, fg=MUTED,
                              font=(FONT, 9), padx=6, cursor="hand2")
        self.arrow.pack(side="right", fill="y")
        self.menu = tk.Menu(self, tearoff=0, bg=SURFACE2, fg=FG,
                            activebackground=SURFACE3, activeforeground=FG,
                            bd=0, font=(FONT, 9))
        self.btn.configure(menu=self.menu)
        self.arrow.bind("<Button-1>", self._open)
        self._state = "normal"

    def _open(self, _event=None):
        if self._state == "normal":
            self.btn.event_generate("<Button-1>")

    def set_values(self, values):
        self.menu.delete(0, "end")
        for value in values:
            self.menu.add_command(label=value,
                                  command=lambda v=value: self.var.set(v))

    def set_state(self, state):
        self._state = state
        self.btn.configure(state="normal" if state == "normal" else "disabled")
        self.arrow.configure(fg=MUTED if state == "normal" else BORDER)


class AccountCard(tk.Frame):
    def __init__(self, master, app, hwnd):
        super().__init__(master, bg=SURFACE2, highlightbackground=BORDER,
                         highlightthickness=1, cursor="hand2")
        self.app, self.hwnd = app, hwnd
        self.selected = False
        self.bar = tk.Frame(self, bg=SURFACE2, width=3)
        self.bar.grid(row=0, column=0, rowspan=2, sticky="ns")
        self.dot = StatusDot(self, bg=SURFACE2)
        self.dot.grid(row=0, column=1, rowspan=2, padx=(10, 8))
        self.title = tk.Label(self, text="", bg=SURFACE2, fg=FG,
                              font=(FONT, 10, "bold"), anchor="w")
        self.title.grid(row=0, column=2, sticky="sw", pady=(8, 0))
        self.sub = tk.Label(self, text="", bg=SURFACE2, fg=MUTED,
                            font=(FONT, 8), anchor="w")
        self.sub.grid(row=1, column=2, sticky="nw", pady=(0, 8))
        self.status = tk.Label(self, text="", bg=SURFACE2, fg=MUTED,
                               font=(FONT, 7, "bold"), anchor="e")
        self.status.grid(row=0, column=3, rowspan=2, padx=8)
        self.btn_start = HoverButton(
            self, "▶", lambda: app._start_instance(hwnd),
            bg=SURFACE2, hover="#21402f", fg=GREEN, padx=0, pady=0,
            width=2, font=("Segoe UI Symbol", 9))
        self.btn_pause = HoverButton(
            self, "‖", lambda: app._toggle_pause_instance(hwnd),
            bg=SURFACE2, hover="#3b3320", fg=ORANGE, padx=0, pady=0,
            width=2, font=("Segoe UI Symbol", 9))
        self.btn_stop = HoverButton(
            self, "■", lambda: app._stop_instance(hwnd),
            bg=SURFACE2, hover="#3b2024", fg=RED, padx=0, pady=0,
            width=2, font=("Segoe UI Symbol", 9))
        self.btn_start.grid(row=0, column=4, rowspan=2, padx=1)
        self.btn_pause.grid(row=0, column=5, rowspan=2, padx=1)
        self.btn_stop.grid(row=0, column=6, rowspan=2, padx=(1, 8))
        ToolTip(self.btn_start, "Iniciar")
        ToolTip(self.btn_pause, "Pausar / retomar")
        ToolTip(self.btn_stop, "Parar")

        for w in (self, self.bar, self.dot, self.title, self.sub, self.status):
            w.bind("<Button-1>", lambda _e: app._select_account(hwnd))
        for w in (self, self.title, self.sub, self.status):
            w.bind("<Enter>", self._hover_in)
            w.bind("<Leave>", self._hover_out)

    def _hover_in(self, _event=None):
        if not self.selected:
            self.configure(highlightbackground=SURFACE3)

    def _hover_out(self, _event=None):
        if not self.selected:
            self.configure(highlightbackground=BORDER)

    def set_data(self, inst, selected, phase):
        self.selected = selected
        mode = MODE_LABELS.get(inst.profile.get("mode", "off"), "?")
        self.title.configure(text=inst.title)
        self.sub.configure(text=f"{inst.profile_name}  ·  {mode}")
        state = inst.status()
        self.status.configure(text=state.upper(),
                              fg={"rodando": GREEN, "pausado": ORANGE,
                                  "parado": MUTED}.get(state, MUTED))
        self.dot.set_state(state, phase)
        self.bar.configure(bg=ACCENT if selected else SURFACE2)
        self.configure(highlightbackground=ACCENT if selected else BORDER)
        running = inst.is_running()
        paused = state == "pausado"
        self.btn_start.configure(state="normal" if not running else "disabled")
        self.btn_pause.configure(
            state="normal" if running else "disabled",
            text="▶" if paused else "‖")
        self.btn_stop.configure(state="normal" if running else "disabled")


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"{APP_NAME} {APP_VERSION}")
        try:
            self.iconbitmap(resource_path("icon.ico"))
        except Exception:
            pass
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        w, h = min(1180, sw - 60), min(900, sh - 80)
        self.geometry(f"{w}x{h}+{(sw - w) // 2}+{max(0, (sh - h) // 2)}")
        self.minsize(980, 660)
        self.configure(bg=BG)
        try:
            self.attributes("-alpha", 0.0)
        except Exception:
            pass

        self._closing = False
        self._sel = None
        self._loading = False
        self._recorder = None
        self._record_task = None
        self._record_inst = None
        self._record_was_running = False
        self._cancel_recording = False
        self._launch_event = threading.Event()
        self._panic_flag = threading.Event()
        self._refresh_job = None

        self.log_queue = queue.Queue()
        self._ui_actions = queue.Queue()

        self.manager = Manager(self.log)
        self.cfg = self.manager.cfg

        self._build()
        self._refresh_profiles()
        self._refresh()
        self._drain_loop()
        self._hotkey_loop()
        self.protocol("WM_DELETE_WINDOW", self._quit)
        self.after(300, self._startup)

    # ── construção da janela ─────────────────────────────────────
    PAGE_INFO = {
        "contas": ("Contas", "Janelas do jogo, perfis e controle de execução."),
        "tarefas": ("Tarefas", "Modo de combate e extras por perfil."),
        "layout": ("Layout", "Organize várias janelas na mesma grade."),
        "xp": ("XP", "Meça o ganho de XP por hora e compare spots."),
        "camaras": ("Câmaras", "Busca por imagem nas salas de XP e Anti-Demônio."),
        "launcher": ("Launcher", "Abra o cliente PC e os dois jogos via UIAutomation."),
        "ajustes": ("Ajustes", "Teclas de teste, atualização e configuração."),
    }

    def _build(self):
        self._page = None
        self._pulse_phase = False
        self._toast_frame = None
        self._toast_job = None
        self.account_cards = {}
        self._accounts_sig = None

        self.grid_columnconfigure(0, weight=0)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self._sidebar()

        self.main = tk.Frame(self, bg=BG)
        self.main.grid(row=0, column=1, sticky="nsew")
        self.main.grid_columnconfigure(0, weight=1)
        self.main.grid_rowconfigure(1, weight=1)

        self._header()

        self.page_host = tk.Frame(self.main, bg=BG)
        self.page_host.grid(row=1, column=0, sticky="nsew",
                            padx=18, pady=(0, 8))
        self.pages = {}
        self._build_accounts()
        self._build_tasks()
        self._build_layout()
        self._build_xp()
        self._build_rooms()
        self._build_launcher()
        self._build_settings()
        self._build_log()

        self.bind_all("<MouseWheel>", self._global_wheel)
        self._show_page("contas", animate=False)
        self._fade_in()
        self.after(800, self._pulse_loop)

    # ── casca ────────────────────────────────────────────────────
    def _sidebar(self):
        side = tk.Frame(self, bg=SIDEBAR, width=224)
        side.grid(row=0, column=0, sticky="ns")
        side.grid_propagate(False)
        side.pack_propagate(False)

        brand = tk.Frame(side, bg=SIDEBAR)
        brand.pack(fill="x", padx=20, pady=(22, 2))
        tk.Label(brand, text="Auto", bg=SIDEBAR, fg=FG,
                 font=(FONT, 20, "bold")).pack(side="left")
        tk.Label(brand, text="yRvt", bg=SIDEBAR, fg=ACCENT,
                 font=(FONT, 20, "bold")).pack(side="left", padx=(5, 0))
        tk.Label(side, text="AUTOMAÇÃO MIR4", bg=SIDEBAR, fg=MUTED,
                 font=(FONT, 7, "bold")).pack(anchor="w", padx=22, pady=(0, 22))

        nav = tk.Frame(side, bg=SIDEBAR)
        nav.pack(fill="x")
        self.nav_items = {}
        for key, icon, label in (
                ("contas", "◈", "Contas"),
                ("tarefas", "✦", "Tarefas"),
                ("layout", "▦", "Layout"),
                ("xp", "↗", "XP"),
                ("camaras", "◎", "Câmaras"),
                ("launcher", "▷", "Launcher"),
                ("ajustes", "⚙", "Ajustes")):
            item = NavItem(nav, icon, label,
                           lambda k=key: self._show_page(k))
            item.pack(fill="x", padx=10, pady=2)
            self.nav_items[key] = item

        bottom = tk.Frame(side, bg=SIDEBAR)
        bottom.pack(side="bottom", fill="x", padx=14, pady=14)
        self.admin_label = tk.Label(bottom, text="● SEM ADMIN", bg=SIDEBAR,
                                    fg=ORANGE, font=(FONT, 7, "bold"),
                                    anchor="w")
        self.admin_label.pack(fill="x", pady=(0, 8))
        self.btn_panic = HoverButton(
            bottom, "PÂNICO  F12", self._panic, bg="#2a1418",
            hover="#3a1a20", fg=RED, font=(FONT, 9, "bold"), pady=7)
        self.btn_panic.pack(fill="x")
        self.version_btn = HoverButton(
            bottom, f"v{APP_VERSION}", self._check_update_manual,
            bg=SIDEBAR, hover=SURFACE, fg=MUTED, font=(FONT, 8),
            padx=4, pady=4)
        self.version_btn.pack(fill="x", pady=(8, 0))

    def _header(self):
        header = tk.Frame(self.main, bg=BG)
        header.grid(row=0, column=0, sticky="ew", padx=22, pady=(18, 10))
        header.grid_columnconfigure(0, weight=1)
        self.page_title = tk.Label(header, text="", bg=BG, fg=FG,
                                   font=(FONT, 17, "bold"), anchor="w")
        self.page_title.grid(row=0, column=0, sticky="w")
        self.page_sub = tk.Label(header, text="", bg=BG, fg=MUTED,
                                 font=(FONT, 9), anchor="w")
        self.page_sub.grid(row=1, column=0, sticky="w", pady=(2, 0))

        right = tk.Frame(header, bg=BG)
        right.grid(row=0, column=1, rowspan=2, sticky="e")
        self.chip_accounts = tk.Label(right, text="● 0 contas", bg=SURFACE2,
                                      fg=MUTED, font=(FONT, 8),
                                      padx=10, pady=4)
        self.chip_accounts.pack(side="left", padx=(0, 6))
        self.chip_running = tk.Label(right, text="● 0 rodando", bg=SURFACE2,
                                     fg=MUTED, font=(FONT, 8),
                                     padx=10, pady=4)
        self.chip_running.pack(side="left", padx=(0, 10))
        self.btn_refresh = HoverButton(
            right, "↻  Atualizar", self._refresh_now, bg=SURFACE2,
            hover=SURFACE3, font=(FONT, 9), padx=12, pady=5)
        self.btn_refresh.pack(side="left")

    def _build_accounts(self):
        page = tk.Frame(self.page_host, bg=BG)
        page.grid_columnconfigure(0, weight=1)
        page.grid_columnconfigure(1, weight=0, minsize=330)
        page.grid_rowconfigure(0, weight=1)
        self.pages["contas"] = page

        left = tk.Frame(page, bg=BG)
        left.grid(row=0, column=0, sticky="nsew")
        left.grid_columnconfigure(0, weight=1)
        left.grid_rowconfigure(1, weight=1)
        head = tk.Frame(left, bg=BG)
        head.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        head.grid_columnconfigure(0, weight=1)
        tk.Label(head, text="CONTAS CONECTADAS", bg=BG, fg=MUTED,
                 font=(FONT, 8, "bold")).grid(row=0, column=0, sticky="w")
        self.btn_start_all = HoverButton(
            head, "Iniciar todas", self._start_all, bg=SURFACE2,
            hover=SURFACE3, font=(FONT, 8), padx=10, pady=4)
        self.btn_start_all.grid(row=0, column=1, padx=(0, 6))
        self.btn_stop_all = HoverButton(
            head, "Parar tudo", self._stop_all, bg="#2a181b",
            hover="#3a2024", fg=RED, font=(FONT, 8), padx=10, pady=4)
        self.btn_stop_all.grid(row=0, column=2)

        list_card = tk.Frame(left, bg=SURFACE, highlightbackground=BORDER,
                             highlightthickness=1)
        list_card.grid(row=1, column=0, sticky="nsew")
        list_card.grid_columnconfigure(0, weight=1)
        list_card.grid_rowconfigure(0, weight=1)
        self.accounts_scroll = ScrollFrame(list_card, bg=SURFACE)
        self.accounts_scroll.grid(row=0, column=0, sticky="nsew",
                                  padx=6, pady=6)
        self.account_list = self.accounts_scroll.inner
        tk.Label(list_card,
                 text="▶ iniciar   ‖ pausar/retomar   ■ parar   ·   "
                      "a lista atualiza sozinha",
                 bg=SURFACE, fg=MUTED, font=(FONT, 7),
                 anchor="w").grid(row=1, column=0, sticky="ew",
                                  padx=10, pady=(0, 7))

        self.empty_state = tk.Frame(self.account_list, bg=SURFACE)
        tk.Label(self.empty_state, text="◇", bg=SURFACE, fg=SURFACE3,
                 font=(FONT, 34)).pack(pady=(18, 2))
        tk.Label(self.empty_state, text="Nenhuma janela encontrada",
                 bg=SURFACE, fg=FG,
                 font=(FONT, 11, "bold")).pack()
        tk.Label(self.empty_state,
                 text="Abra o MIR4 e clique em atualizar para começar.",
                 bg=SURFACE, fg=MUTED, font=(FONT, 8)).pack(pady=(4, 10))
        HoverButton(self.empty_state, "Procurar janelas", self._refresh_now,
                    bg=SURFACE2, hover=SURFACE3, font=(FONT, 9)).pack()
        self.empty_state.pack(fill="both", expand=True, pady=40)

        self._build_detail(page)

    def _build_detail(self, page):
        right = tk.Frame(page, bg=SURFACE, highlightbackground=BORDER,
                         highlightthickness=1)
        right.grid(row=0, column=1, sticky="nsew", padx=(16, 0))
        inner = tk.Frame(right, bg=SURFACE)
        inner.pack(fill="both", expand=True, padx=14, pady=14)

        tk.Label(inner, text="CONTA SELECIONADA", bg=SURFACE, fg=MUTED,
                 font=(FONT, 8, "bold")).pack(anchor="w")
        self.detail_title = tk.Label(inner, text="Nenhuma conta",
                                     bg=SURFACE, fg=FG,
                                     font=(FONT, 12, "bold"), anchor="w")
        self.detail_title.pack(fill="x", pady=(4, 0))
        self.detail_sub = tk.Label(inner, text="Selecione uma conta à esquerda.",
                                   bg=SURFACE, fg=MUTED,
                                   font=(FONT, 8), anchor="w")
        self.detail_sub.pack(fill="x", pady=(2, 0))
        self.detail_status = tk.Label(inner, text="", bg=SURFACE, fg=MUTED,
                                      font=(FONT, 7, "bold"), anchor="w")
        self.detail_status.pack(fill="x", pady=(4, 0))

        actions = tk.Frame(inner, bg=SURFACE)
        actions.pack(fill="x", pady=(12, 10))
        self.btn_start = HoverButton(
            actions, "Iniciar", self._start_selected, bg=ACCENT,
            hover=ACCENT2, fg=BG, font=(FONT, 9, "bold"), padx=10, pady=5)
        self.btn_start.pack(side="left")
        self.btn_pause = HoverButton(
            actions, "Pausar", self._toggle_pause, bg=SURFACE2,
            hover=SURFACE3, font=(FONT, 9), padx=10, pady=5)
        self.btn_pause.pack(side="left", padx=5)
        self.btn_stop = HoverButton(
            actions, "Parar", self._stop_selected, bg="#2a181b",
            hover="#3a2024", fg=RED, font=(FONT, 9), padx=10, pady=5)
        self.btn_stop.pack(side="left")

        tk.Frame(inner, bg=BORDER, height=1).pack(fill="x", pady=(4, 12))
        tk.Label(inner, text="PERFIL", bg=SURFACE, fg=MUTED,
                 font=(FONT, 8, "bold")).pack(anchor="w")
        self.profile_var = tk.StringVar()
        self.profile_combo = DarkCombo(inner, self.profile_var)
        self.profile_combo.pack(fill="x", pady=(5, 6))
        prof_actions = tk.Frame(inner, bg=SURFACE)
        prof_actions.pack(fill="x")
        for col in range(2):
            prof_actions.grid_columnconfigure(col, weight=1)
        self._button(prof_actions, "Aplicar", self._apply_profile,
                     bg="#1f3b4f", hover="#264a63").grid(
            row=0, column=0, sticky="ew", padx=(0, 3), pady=2)
        self._button(prof_actions, "Salvar", self._save_profile_btn,
                     bg="#1f4f3a", hover="#27634a").grid(
            row=0, column=1, sticky="ew", padx=(3, 0), pady=2)
        self._button(prof_actions, "Novo", self._new_profile).grid(
            row=1, column=0, sticky="ew", padx=(0, 3), pady=2)
        self._button(prof_actions, "Excluir", self._delete_profile,
                     bg="#3a2024", hover="#4a282e", fg=RED).grid(
            row=1, column=1, sticky="ew", padx=(3, 0), pady=2)
        self.profile_hint = tk.Label(
            inner, text="Cada perfil guarda modo, intervalos, macros e pontos.",
            bg=SURFACE, fg=MUTED, font=(FONT, 8), justify="left",
            wraplength=290)
        self.profile_hint.pack(fill="x", pady=(8, 0))
        tk.Label(inner,
                 text="Dica: vincule cada janela ao seu perfil para não "
                      "misturar as contas.",
                 bg=SURFACE, fg=MUTED, font=(FONT, 8), justify="left",
                 wraplength=290).pack(fill="x", pady=(8, 0))

        tk.Frame(inner, bg=BORDER, height=1).pack(fill="x", pady=(14, 12))
        tk.Label(inner, text="RESUMO DO PERFIL", bg=SURFACE, fg=MUTED,
                 font=(FONT, 8, "bold")).pack(anchor="w")
        self.summary_mode = tk.Label(
            inner, text="Modo: —", bg=SURFACE, fg=FG,
            font=(FONT, 9), anchor="w", justify="left", wraplength=290)
        self.summary_mode.pack(fill="x", pady=(6, 2))
        self.summary_extras = tk.Label(
            inner, text="Extras: —", bg=SURFACE, fg=MUTED,
            font=(FONT, 8), anchor="w", justify="left", wraplength=290)
        self.summary_extras.pack(fill="x", pady=1)
        self.summary_rooms = tk.Label(
            inner, text="Câmaras: —", bg=SURFACE, fg=MUTED,
            font=(FONT, 8), anchor="w", justify="left", wraplength=290)
        self.summary_rooms.pack(fill="x", pady=1)

        tk.Frame(inner, bg=BORDER, height=1).pack(fill="x", pady=(14, 12))
        tk.Label(inner, text="JANELA", bg=SURFACE, fg=MUTED,
                 font=(FONT, 8, "bold")).pack(anchor="w")
        self.detail_hwnd = tk.Label(
            inner, text="HWND —", bg=SURFACE, fg=MUTED,
            font=(FONT, 8), anchor="w")
        self.detail_hwnd.pack(fill="x", pady=(6, 1))
        self.detail_client = tk.Label(
            inner, text="Cliente —", bg=SURFACE, fg=MUTED,
            font=(FONT, 8), anchor="w")
        self.detail_client.pack(fill="x", pady=1)

    def _build_log(self):
        log = tk.Frame(self.main, bg=SURFACE, highlightbackground=BORDER,
                       highlightthickness=1)
        log.grid(row=2, column=0, sticky="ew", padx=18, pady=(0, 14))
        log.grid_columnconfigure(0, weight=1)
        head = tk.Frame(log, bg=SURFACE)
        head.grid(row=0, column=0, sticky="ew", padx=12, pady=(8, 0))
        head.grid_columnconfigure(0, weight=1)
        tk.Label(head, text="REGISTRO", bg=SURFACE, fg=MUTED,
                 font=(FONT, 8, "bold")).grid(row=0, column=0, sticky="w")
        HoverButton(head, "Limpar", self._clear_log, bg=SURFACE,
                    hover=SURFACE2, fg=MUTED, font=(FONT, 8),
                    padx=6, pady=1).grid(row=0, column=1, sticky="e")
        self.log_text = tk.Text(log, height=5, bg="#070a0f", fg="#c9d1d9",
                                insertbackground=FG, relief="flat", wrap="word",
                                font=(MONO, 8), state="disabled",
                                selectbackground=SURFACE3)
        self.log_text.grid(row=1, column=0, sticky="ew", padx=10, pady=(4, 10))
        scroll = SlimScrollbar(log, command=self.log_text.yview,
                               bg=SURFACE, width=8)
        scroll.grid(row=1, column=1, sticky="ns", pady=(4, 10), padx=(0, 4))
        self.log_text.configure(yscrollcommand=scroll.set)
        self.log_text.tag_configure("error", foreground=RED)
        self.log_text.tag_configure("success", foreground=GREEN)
        self.log_text.tag_configure("warn", foreground=ORANGE)
        self.log_text.tag_configure("muted", foreground=MUTED)

    def _card(self, parent, padx=14, pady=12):
        card = tk.Frame(parent, bg=SURFACE, highlightbackground=BORDER,
                        highlightthickness=1)
        inner = tk.Frame(card, bg=SURFACE)
        inner.pack(fill="both", expand=True, padx=padx, pady=pady)
        return card, inner

    def _button(self, parent, text, command, bg=SURFACE2, fg=FG,
                hover=SURFACE3):
        return HoverButton(parent, text, command, bg=bg, hover=hover, fg=fg,
                           font=(FONT, 9), padx=10, pady=4)

    def _label(self, parent, text, fg=FG, bg=SURFACE, **kwargs):
        return tk.Label(parent, text=text, bg=bg, fg=fg, **kwargs)

    def _entry(self, parent, var, width=8):
        return tk.Entry(parent, textvariable=var, width=width, bg=SURFACE2,
                        fg=FG, insertbackground=FG, relief="flat",
                        highlightthickness=1, highlightbackground=BORDER,
                        highlightcolor=ACCENT)

    # ── navegação e feedback ─────────────────────────────────────
    def _show_page(self, name, animate=True):
        if name == self._page:
            return
        self._save_ui()
        old = self.pages.get(self._page)
        if old is not None:
            old.place_forget()
        frame = self.pages[name]
        self._page = name
        for key, item in self.nav_items.items():
            item.set_active(key == name)
        title, sub = self.PAGE_INFO[name]
        self.page_title.configure(text=title)
        self.page_sub.configure(text=sub)
        frame.place(x=0, y=0, relwidth=1, relheight=1)
        frame.lift()
        if animate:
            frame.place_configure(x=16)

            def step(i):
                try:
                    if not frame.winfo_exists():
                        return
                    frame.place_configure(x=int(16 * (1 - i / 8)))
                except Exception:
                    return
                if i < 8:
                    self.after(16, lambda: step(i + 1))

            step(1)

    def _active_scroll(self):
        return {
            "contas": getattr(self, "accounts_scroll", None),
            "tarefas": getattr(self, "tasks_scroll", None),
            "layout": getattr(self, "layout_scroll", None),
            "xp": getattr(self, "xp_scroll", None),
            "camaras": getattr(self, "rooms_scroll", None),
            "launcher": getattr(self, "launcher_scroll", None),
            "ajustes": getattr(self, "settings_scroll", None),
        }.get(self._page)

    def _global_wheel(self, event):
        scroll = self._active_scroll()
        if scroll is None:
            return
        try:
            node = self.winfo_containing(event.x_root, event.y_root)
        except Exception:
            return
        while node is not None:
            if node is scroll:
                scroll._wheel(event)
                return
            node = getattr(node, "master", None)

    def _fade_in(self):
        try:
            self.attributes("-alpha", 0.0)
        except Exception:
            return

        def step(i):
            try:
                self.attributes("-alpha", i / 12)
            except Exception:
                try:
                    self.attributes("-alpha", 1.0)
                except Exception:
                    pass
                return
            if i < 12:
                self.after(18, lambda: step(i + 1))
            else:
                try:
                    self.attributes("-alpha", 1.0)
                except Exception:
                    pass

        step(1)

    def toast(self, message, kind="info"):
        color = {"info": ACCENT, "success": GREEN,
                 "error": RED, "warning": ORANGE}.get(kind, ACCENT)
        if self._toast_frame is not None:
            try:
                self._toast_frame.destroy()
            except Exception:
                pass
        if self._toast_job:
            try:
                self.after_cancel(self._toast_job)
            except Exception:
                pass
        frame = tk.Frame(self, bg=SURFACE3, highlightbackground=color,
                         highlightthickness=1)
        tk.Frame(frame, bg=color, width=3).pack(side="left", fill="y")
        tk.Label(frame, text=message, bg=SURFACE3, fg=FG, font=(FONT, 9),
                 padx=12, pady=8, wraplength=360,
                 justify="left").pack(side="left")
        frame.place(relx=1.0, rely=1.0, x=-20, y=-16, anchor="se")
        self._toast_frame = frame
        frame.place_configure(y=-66)

        def step(i):
            try:
                if not frame.winfo_exists():
                    return
                frame.place_configure(y=-66 + int(50 * (i / 8)))
            except Exception:
                return
            if i < 8:
                self.after(14, lambda: step(i + 1))

        step(1)
        self._toast_job = self.after(4000, self._hide_toast)

    def _hide_toast(self):
        frame = self._toast_frame
        self._toast_frame = None
        self._toast_job = None
        if not frame:
            return

        def step(i):
            try:
                if not frame.winfo_exists():
                    return
                frame.place_configure(y=-16 + int(40 * (i / 6)))
            except Exception:
                return
            if i < 6:
                self.after(12, lambda: step(i + 1))
            else:
                try:
                    frame.destroy()
                except Exception:
                    pass

        step(1)

    def _clear_log(self):
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")

    def _pulse_loop(self):
        if self._closing:
            return
        self._pulse_phase = not self._pulse_phase
        for hwnd, card in list(self.account_cards.items()):
            inst = self.manager.instances.get(hwnd)
            if inst:
                card.dot.set_state(inst.status(), self._pulse_phase)
        if self._record_task:
            color = "#6a2a2a" if self._pulse_phase else "#4a2a2a"
            buttons = self.macro_buttons.get(self._record_task)
            if buttons:
                buttons["record"].configure(bg=color)
                buttons["record"]._base = color
        self.after(700, self._pulse_loop)

    # ── páginas ──────────────────────────────────────────────────
    def _build_tasks(self):
        page = tk.Frame(self.page_host, bg=BG)
        self.pages["tarefas"] = page
        footer = tk.Frame(page, bg=BG)
        footer.pack(side="bottom", fill="x", pady=(8, 0))
        scroll = ScrollFrame(page, bg=BG)
        self.tasks_scroll = scroll
        scroll.pack(fill="both", expand=True)
        body = scroll.inner

        self.tasks_info = tk.Label(
            body, text="Selecione uma conta para editar o perfil.",
            bg=BG, fg=MUTED, font=(FONT, 9), anchor="w")
        self.tasks_info.pack(fill="x", pady=(0, 12))

        self.mode_var = tk.StringVar(value="off")
        self.mode_interval_vars = {
            key: tk.StringVar(value=self._fmt_num(MODE_DEFAULT_INTERVAL[key]))
            for key in MODE_DEFAULT_INTERVAL
        }
        self.ultimate_var = tk.BooleanVar(value=False)
        self.ultimate_key_var = tk.StringVar(value="r")
        self.mode_cards = {}

        tk.Label(body, text="MODO DE COMBATE", bg=BG, fg=MUTED,
                 font=(FONT, 8, "bold")).pack(anchor="w")
        tk.Label(body, text="Escolha uma rotina contínua. Os extras rodam junto.",
                 bg=BG, fg=MUTED, font=(FONT, 8)).pack(anchor="w", pady=(2, 8))
        modes = tk.Frame(body, bg=BG)
        modes.pack(fill="x", pady=(0, 14))
        for col in range(3):
            modes.grid_columnconfigure(col, weight=1)
        for i, (key, label, desc) in enumerate(MODES):
            card = tk.Frame(modes, bg=SURFACE, highlightbackground=BORDER,
                            highlightthickness=1)
            card.grid(row=i // 3, column=i % 3, sticky="nsew",
                      padx=4, pady=4)
            inner = tk.Frame(card, bg=SURFACE)
            inner.pack(fill="both", expand=True, padx=12, pady=10)
            top = tk.Frame(inner, bg=SURFACE)
            top.pack(fill="x")
            check = tk.Label(top, text="○", bg=SURFACE, fg=MUTED,
                             font=(FONT, 10, "bold"))
            check.pack(side="left")
            title = tk.Label(top, text=label, bg=SURFACE, fg=FG,
                             font=(FONT, 10, "bold"))
            title.pack(side="left", padx=(6, 0))
            desc_label = tk.Label(inner, text=desc, bg=SURFACE, fg=MUTED,
                                  font=(FONT, 8), justify="left",
                                  wraplength=210, anchor="w")
            desc_label.pack(fill="x", pady=(6, 0))
            if key != "off":
                row = tk.Frame(inner, bg=SURFACE)
                row.pack(fill="x", pady=(8, 0))
                tk.Label(row, text="a cada", bg=SURFACE, fg=MUTED,
                         font=(FONT, 8)).pack(side="left")
                self._entry(row, self.mode_interval_vars[key], 6).pack(
                    side="left", padx=4)
                tk.Label(row, text="s", bg=SURFACE, fg=MUTED,
                         font=(FONT, 8)).pack(side="left")
                if key == "farm":
                    ult_row = tk.Frame(inner, bg=SURFACE)
                    ult_row.pack(fill="x", pady=(5, 0))
                    ToggleSwitch(ult_row, self.ultimate_var,
                                 command=self._save_ui).pack(side="left")
                    tk.Label(ult_row, text="Ultimate", bg=SURFACE, fg=MUTED,
                             font=(FONT, 8)).pack(side="left", padx=(6, 3))
                    tk.Label(ult_row, text="tecla", bg=SURFACE, fg=MUTED,
                             font=(FONT, 8)).pack(side="left")
                    self._entry(ult_row, self.ultimate_key_var, 3).pack(
                        side="left", padx=3)
            for w in (card, inner, top, check, title, desc_label):
                w.bind("<Button-1>", lambda _e, k=key: self._select_mode(k))
            for w in (card, title, desc_label):
                w.bind("<Enter>", lambda _e, c=card, k=key:
                       c.configure(highlightbackground=ACCENT
                                   if self.mode_var.get() == k else SURFACE3))
                w.bind("<Leave>", lambda _e: self._sync_mode_cards())
            self.mode_cards[key] = (card, check)

        tip = tk.Frame(modes, bg=SURFACE2, highlightbackground=BORDER,
                       highlightthickness=1)
        tip.grid(row=1, column=2, sticky="nsew", padx=4, pady=4)
        tip_inner = tk.Frame(tip, bg=SURFACE2)
        tip_inner.pack(fill="both", expand=True, padx=12, pady=9)
        tk.Label(tip_inner, text="DICA", bg=SURFACE2, fg=ACCENT,
                 font=(FONT, 7, "bold")).pack(anchor="w")
        tk.Label(tip_inner,
                 text="Use um modo por conta. Buff e macros podem ficar "
                      "ligados: o bot intercala as ações e evita misturar "
                      "as telas.",
                 bg=SURFACE2, fg=MUTED, font=(FONT, 8), justify="left",
                 wraplength=210).pack(anchor="w", pady=(2, 0))

        self.ultimate_warn = tk.Frame(body, bg=BG)
        self.ultimate_warn.pack(fill="x", pady=(0, 8))
        self.ultimate_hint = tk.Label(
            self.ultimate_warn, text="", bg=BG, fg=MUTED,
            font=(FONT, 8), anchor="w", justify="left")
        self.ultimate_hint.pack(side="left", fill="x", expand=True)
        self.ultimate_fix_btn = HoverButton(
            self.ultimate_warn, "Ativar Farm + Ultimate",
            self._fix_ultimate_mode, bg="#3a2a18", hover="#4a3620",
            fg=ORANGE, font=(FONT, 8), padx=8, pady=3)
        self.ultimate_fix_btn.pack(side="right", padx=(8, 0))
        self.ultimate_fix_btn.pack_forget()

        tk.Label(body, text="EXTRAS", bg=BG, fg=MUTED,
                 font=(FONT, 8, "bold")).pack(anchor="w")
        tk.Label(body, text="Buff periódico e macros gravadas para o dia.",
                 bg=BG, fg=MUTED, font=(FONT, 8)).pack(anchor="w", pady=(2, 8))

        self.buff_on = tk.BooleanVar()
        self.buff_keys = tk.StringVar(value="1,2,3")
        self.buff_int = tk.StringVar(value="180")
        buff_card, buff = self._card(body)
        buff_card.pack(fill="x", pady=5)
        row = tk.Frame(buff, bg=SURFACE)
        row.pack(fill="x")
        ToggleSwitch(row, self.buff_on, command=self._save_ui,
                     bg=SURFACE).pack(side="left")
        tk.Label(row, text="Auto-Buff", bg=SURFACE, fg=FG,
                 font=(FONT, 10, "bold")).pack(side="left", padx=(8, 0))
        tk.Label(row, text="teclas", bg=SURFACE, fg=MUTED,
                 font=(FONT, 8)).pack(side="left", padx=(16, 3))
        self._entry(row, self.buff_keys, 10).pack(side="left")
        tk.Label(row, text="a cada", bg=SURFACE, fg=MUTED,
                 font=(FONT, 8)).pack(side="left", padx=(12, 3))
        self._entry(row, self.buff_int, 6).pack(side="left")
        tk.Label(row, text="s", bg=SURFACE, fg=MUTED,
                 font=(FONT, 8)).pack(side="left", padx=2)

        self.extra_on = {}
        self.extra_int = {}
        self.extra_rep = {}
        self.macro_labels = {}
        self.macro_buttons = {}
        for key, meta in MACRO_TASKS.items():
            card, inner = self._card(body)
            card.pack(fill="x", pady=5)
            top = tk.Frame(inner, bg=SURFACE)
            top.pack(fill="x")
            self.extra_on[key] = tk.BooleanVar()
            self.extra_int[key] = tk.StringVar(
                value=self._fmt_num(meta["interval"]))
            self.extra_rep[key] = tk.StringVar(value=str(meta["repeat"]))
            ToggleSwitch(top, self.extra_on[key], command=self._save_ui,
                         bg=SURFACE).pack(side="left")
            tk.Label(top, text=meta["label"], bg=SURFACE, fg=FG,
                     font=(FONT, 10, "bold")).pack(side="left", padx=(8, 0))
            tk.Label(top, text=meta["desc"], bg=SURFACE, fg=MUTED,
                     font=(FONT, 8)).pack(side="left", padx=(10, 0))

            row = tk.Frame(inner, bg=SURFACE)
            row.pack(fill="x", pady=(8, 0))
            tk.Label(row, text="a cada", bg=SURFACE, fg=MUTED,
                     font=(FONT, 8)).pack(side="left")
            self._entry(row, self.extra_int[key], 8).pack(side="left", padx=4)
            tk.Label(row, text="s", bg=SURFACE, fg=MUTED,
                     font=(FONT, 8)).pack(side="left")
            tk.Label(row, text="repetir", bg=SURFACE, fg=MUTED,
                     font=(FONT, 8)).pack(side="left", padx=(12, 3))
            self._entry(row, self.extra_rep[key], 4).pack(side="left")
            self.macro_labels[key] = tk.Label(
                row, text="macro: 0 passos", bg=SURFACE, fg=MUTED,
                font=(FONT, 8))
            self.macro_labels[key].pack(side="left", padx=(12, 8))
            buttons = {}
            buttons["record"] = HoverButton(
                row, "Gravar", lambda k=key: self._record_macro(k),
                bg="#3a2a18", hover="#4a3620", fg=ORANGE,
                font=(FONT, 8), padx=10, pady=3)
            buttons["test"] = HoverButton(
                row, "Testar", lambda k=key: self._test_macro(k),
                bg=SURFACE2, hover=SURFACE3, font=(FONT, 8),
                padx=10, pady=3)
            buttons["clear"] = HoverButton(
                row, "Limpar", lambda k=key: self._clear_macro(k),
                bg="#3a2024", hover="#4a282e", fg=RED,
                font=(FONT, 8), padx=10, pady=3)
            for b in buttons.values():
                b.pack(side="right", padx=2)
            self.macro_buttons[key] = buttons
            tk.Label(inner,
                     text="Grave uma vez com o jogo em foco. F9 para parar.",
                     bg=SURFACE, fg=MUTED, font=(FONT, 7)).pack(
                anchor="w", pady=(6, 0))

        actions = tk.Frame(footer, bg=BG)
        actions.pack(fill="x")
        self._button(actions, "Salvar perfil", self._save_profile_btn,
                     bg="#1f4f3a", hover="#27634a").pack(side="left")
        self._button(actions, "Executar modo agora",
                     self._run_mode_now, bg="#1f3b4f",
                     hover="#264a63").pack(side="left", padx=6)
        self._button(actions, "Parar tarefa", self._stop_selected,
                     bg="#3a2024", hover="#4a282e", fg=RED).pack(
            side="left")
        tk.Label(footer,
                 text="O modo escolhido é exclusivo; extras rodam entre os "
                      "ciclos. Intervalos em segundos (86400 = 1 dia).",
                 bg=BG, fg=MUTED, font=(FONT, 8), justify="left",
                 anchor="w", wraplength=680).pack(fill="x", pady=(6, 0))
        self._sync_mode_cards()

    def _select_mode(self, key):
        if self.mode_var.get() == key:
            return
        self.mode_var.set(key)
        self._on_mode_change()
        self._sync_mode_cards()

    def _sync_mode_cards(self):
        active = self.mode_var.get()
        for key, (card, check) in self.mode_cards.items():
            on = key == active
            card.configure(highlightbackground=ACCENT if on else BORDER)
            check.configure(text="✓" if on else "○",
                            fg=ACCENT if on else MUTED)
        self._update_ultimate_hint()

    def _update_ultimate_hint(self):
        if not hasattr(self, "ultimate_hint"):
            return
        active = self.ultimate_var.get()
        mode = self.mode_var.get()
        if active and mode not in ("farm", "acordar"):
            self.ultimate_hint.configure(
                text="Ultimate ligada, mas só roda no Farm EXP e no Acordar.",
                fg=ORANGE)
            if not self.ultimate_fix_btn.winfo_manager():
                self.ultimate_fix_btn.pack(side="right", padx=(8, 0))
        elif active:
            self.ultimate_hint.configure(
                text="Ultimate ativa: R é tentado a cada ~0,8 s "
                     "no Farm EXP e no Acordar.",
                fg=GREEN)
            self.ultimate_fix_btn.pack_forget()
        else:
            self.ultimate_hint.configure(text="Ultimate desligada.", fg=MUTED)
            self.ultimate_fix_btn.pack_forget()

    def _fix_ultimate_mode(self):
        self.mode_var.set("farm")
        self.ultimate_var.set(True)
        self._on_mode_change()
        self.toast("Farm EXP com Ultimate ativado.", "success")

    def _build_layout(self):
        page = tk.Frame(self.page_host, bg=BG)
        self.pages["layout"] = page
        scroll = ScrollFrame(page, bg=BG)
        self.layout_scroll = scroll
        scroll.pack(fill="both", expand=True)
        body = scroll.inner

        layout = self.cfg.layout
        self.layout_rows = tk.StringVar(value=str(layout.get("rows", 2)))
        self.layout_cols = tk.StringVar(value=str(layout.get("cols", 2)))
        card, inner = self._card(body)
        card.pack(fill="x", pady=4)
        tk.Label(inner, text="GRADE DE JANELAS", bg=SURFACE, fg=MUTED,
                 font=(FONT, 8, "bold")).pack(anchor="w")
        tk.Label(inner,
                 text="Divide a tela em linhas e colunas e move todas as "
                      "janelas do MIR4 para as células.",
                 bg=SURFACE, fg=MUTED, font=(FONT, 8),
                 justify="left", wraplength=680).pack(anchor="w", pady=(2, 10))
        row = tk.Frame(inner, bg=SURFACE)
        row.pack(fill="x")
        tk.Label(row, text="Linhas", bg=SURFACE, fg=FG,
                 font=(FONT, 9)).pack(side="left")
        self._entry(row, self.layout_rows, 5).pack(side="left", padx=4)
        tk.Label(row, text="Colunas", bg=SURFACE, fg=FG,
                 font=(FONT, 9)).pack(side="left", padx=(14, 0))
        self._entry(row, self.layout_cols, 5).pack(side="left", padx=4)
        self._button(row, "Aplicar grade", self._apply_grid,
                     bg=ACCENT, hover=ACCENT2, fg=BG).pack(
            side="left", padx=(16, 0))
        self.grid_preview = tk.Canvas(inner, height=190, bg=SURFACE2,
                                      highlightthickness=0, bd=0)
        self.grid_preview.pack(fill="x", pady=(12, 2))
        self.grid_preview.bind("<Configure>",
                               lambda _e: self._draw_grid_preview())
        for var in (self.layout_rows, self.layout_cols):
            var.trace_add("write", lambda *_: self._draw_grid_preview())
        self._draw_grid_preview()
        tk.Label(inner,
                 text="Depois de redimensionar, as referências das câmaras "
                      "podem precisar ser treinadas de novo.",
                 bg=SURFACE, fg=MUTED, font=(FONT, 8)).pack(anchor="w",
                                                            pady=(8, 0))

        card, inner = self._card(body)
        card.pack(fill="x", pady=(8, 4))
        tk.Label(inner, text="EXEMPLOS", bg=SURFACE, fg=ACCENT,
                 font=(FONT, 8, "bold")).pack(anchor="w")
        tk.Label(inner,
                 text="1x2 = duas contas lado a lado.  2x1 = duas empilhadas.  "
                      "2x2 = quatro contas.  A grade usa a área útil da tela.",
                 bg=SURFACE, fg=MUTED, font=(FONT, 8), justify="left",
                 wraplength=680).pack(anchor="w", pady=(4, 0))

    def _draw_grid_preview(self):
        canvas = getattr(self, "grid_preview", None)
        if canvas is None:
            return
        try:
            rows = max(1, self._int(self.layout_rows, 2, 1))
            cols = max(1, self._int(self.layout_cols, 2, 1))
            width = max(200, canvas.winfo_width())
            height = max(120, canvas.winfo_height())
        except Exception:
            return
        canvas.delete("all")
        pad = 12
        gap = 4
        cell_w = (width - 2 * pad - gap * (cols - 1)) / cols
        cell_h = (height - 2 * pad - gap * (rows - 1)) / rows
        n = 0
        for r in range(rows):
            for c in range(cols):
                x1 = pad + c * (cell_w + gap)
                y1 = pad + r * (cell_h + gap)
                x2, y2 = x1 + cell_w, y1 + cell_h
                canvas.create_rectangle(x1, y1, x2, y2,
                                        fill=SURFACE3, outline=BORDER)
                n += 1
                canvas.create_text((x1 + x2) / 2, (y1 + y2) / 2,
                                   text=str(n), fill=MUTED,
                                   font=(FONT, 9, "bold"))

    # ── laboratório de XP ────────────────────────────────────────
    def _build_xp(self):
        page = tk.Frame(self.page_host, bg=BG)
        self.pages["xp"] = page
        scroll = ScrollFrame(page, bg=BG)
        self.xp_scroll = scroll
        scroll.pack(fill="both", expand=True)
        body = scroll.inner

        self._xp_active = None
        self._xp_sig = None
        self._sniffer = None
        self._sniff_timer = None
        self._sniff_spot_start = None
        self._mem_handle = None
        self._mem_pid = None
        self._mem_candidates = None
        self._mem_addr = None
        self._ue4_result = None
        self.xp_mem_value_var = tk.StringVar()
        self.xp_spot_var = tk.StringVar(value="Spot 1")
        self.xp_start_var = tk.StringVar()
        self.xp_end_var = tk.StringVar()


        # ── CARD 1: SNIFFER (principal) ──
        card, inner = self._card(body)
        card.pack(fill="x", pady=4)
        head = tk.Frame(inner, bg=SURFACE)
        head.pack(fill="x")
        tk.Label(head, text="SNIFFER DE XP", bg=SURFACE, fg=ACCENT,
                 font=(FONT, 10, "bold")).pack(side="left")
        self.xp_sniff_indicator = tk.Label(
            head, text="", bg=SURFACE, fg=MUTED, font=(FONT, 8))
        self.xp_sniff_indicator.pack(side="right")

        tk.Label(inner,
                 text="Informe o spot e sua % de XP atual, inicie e "
                      "deixe o macro rodando. Ao parar, informe a % final.",
                 bg=SURFACE, fg=MUTED, font=(FONT, 8), justify="left",
                 wraplength=680).pack(anchor="w", pady=(4, 8))

        row = tk.Frame(inner, bg=SURFACE)
        row.pack(fill="x")
        tk.Label(row, text="Spot", bg=SURFACE, fg=MUTED,
                 font=(FONT, 9)).pack(side="left")
        self._entry(row, self.xp_spot_var, 20).pack(side="left", padx=(6, 14))
        tk.Label(row, text="XP início %", bg=SURFACE, fg=MUTED,
                 font=(FONT, 9)).pack(side="left")
        self.xp_sniff_start_var = tk.StringVar()
        self._entry(row, self.xp_sniff_start_var, 8).pack(
            side="left", padx=(6, 14))
        tk.Label(row, text="XP fim %", bg=SURFACE, fg=MUTED,
                 font=(FONT, 9)).pack(side="left")
        self.xp_sniff_end_var = tk.StringVar()
        self._entry(row, self.xp_sniff_end_var, 8).pack(
            side="left", padx=(6, 0))

        actions = tk.Frame(inner, bg=SURFACE)
        actions.pack(fill="x", pady=(10, 0))
        self.xp_sniff_btn = HoverButton(
            actions, "Iniciar Sniffer", self._sniff_toggle, bg=ACCENT,
            hover=ACCENT2, fg=BG, font=(FONT, 9, "bold"), padx=14, pady=5)
        self.xp_sniff_btn.pack(side="left")
        self.xp_sniff_save_btn = HoverButton(
            actions, "Salvar sessao", self._sniff_save, bg="#1f4f3a",
            hover="#27634a", font=(FONT, 9), padx=10, pady=5,
            state="disabled")
        self.xp_sniff_save_btn.pack(side="left", padx=6)

        self.xp_sniff_status = tk.Label(
            inner, text="Parado. Clique Iniciar para capturar.",
            bg=SURFACE, fg=MUTED, font=(FONT, 8), anchor="w")
        self.xp_sniff_status.pack(fill="x", pady=(8, 0))

        # Live stats grid
        stats_frame = tk.Frame(inner, bg=SURFACE2, padx=10, pady=8)
        stats_frame.pack(fill="x", pady=(8, 0))
        self._sniff_labels = {}
        for i, (key, label) in enumerate([
            ("xp_kill", "XP / Kill"),
            ("kills", "Mobs mortos"),
            ("kills_min", "Kills / Min"),
            ("elapsed", "Tempo"),
        ]):
            col = i % 4
            cell = tk.Frame(stats_frame, bg=SURFACE2)
            cell.grid(row=0, column=col, sticky="w", padx=(0, 30), pady=2)
            tk.Label(cell, text=label, bg=SURFACE2, fg=MUTED,
                     font=(FONT, 7, "bold")).pack(anchor="w")
            lbl = tk.Label(cell, text="—", bg=SURFACE2, fg=FG,
                           font=(MONO, 11, "bold"), anchor="w")
            lbl.pack(anchor="w")
            self._sniff_labels[key] = lbl

        # ── CARD 2: COMPARAÇÃO ──
        card, inner = self._card(body)
        card.pack(fill="x", pady=4)
        head = tk.Frame(inner, bg=SURFACE)
        head.pack(fill="x")
        tk.Label(head, text="COMPARAÇÃO DE SPOTS", bg=SURFACE, fg=MUTED,
                 font=(FONT, 8, "bold")).pack(side="left")
        self.xp_best_label = tk.Label(head, text="", bg=SURFACE, fg=ACCENT,
                                      font=(FONT, 8))
        self.xp_best_label.pack(side="right")
        self.xp_chart = tk.Canvas(inner, height=200, bg=SURFACE2,
                                  highlightthickness=0, bd=0)
        self.xp_chart.pack(fill="x", pady=(10, 0))
        self.xp_chart.bind("<Configure>", lambda _e: self._draw_xp_chart())

        # ── CARD 3: HISTÓRICO ──
        card, inner = self._card(body)
        card.pack(fill="x", pady=4)
        head = tk.Frame(inner, bg=SURFACE)
        head.pack(fill="x")
        tk.Label(head, text="HISTÓRICO", bg=SURFACE, fg=MUTED,
                 font=(FONT, 8, "bold")).pack(side="left")
        HoverButton(head, "Limpar", self._xp_clear, bg=SURFACE,
                    hover=SURFACE2, fg=MUTED, font=(FONT, 8),
                    padx=6, pady=1).pack(side="right")
        self.xp_sessions_box = tk.Frame(inner, bg=SURFACE)
        self.xp_sessions_box.pack(fill="x", pady=(6, 0))

        # ── CARD 4: SESSÃO MANUAL (colapsável) ──
        card, inner = self._card(body)
        card.pack(fill="x", pady=4)
        self._manual_xp_inner = inner
        self._manual_xp_visible = False
        self._manual_xp_toggle_btn = HoverButton(
            inner, "Sessão manual (digitado)  ▸",
            self._toggle_manual_xp, bg=SURFACE, hover=SURFACE2,
            fg=MUTED, font=(FONT, 8), padx=0, pady=2)
        self._manual_xp_toggle_btn.pack(anchor="w")
        self._manual_xp_body = tk.Frame(inner, bg=SURFACE)

        self._build_manual_xp(self._manual_xp_body)

        # ── CARD 5: MEMÓRIA / SDK (colapsável) ──
        card, inner = self._card(body)
        card.pack(fill="x", pady=4)
        self._adv_xp_visible = False
        self._adv_xp_toggle_btn = HoverButton(
            inner, "Métodos avançados (memória / SDK)  ▸",
            self._toggle_adv_xp, bg=SURFACE, hover=SURFACE2,
            fg=MUTED, font=(FONT, 8), padx=0, pady=2)
        self._adv_xp_toggle_btn.pack(anchor="w")
        self._adv_xp_body = tk.Frame(inner, bg=SURFACE)

        self._build_adv_xp(self._adv_xp_body)

        self._update_xp_ui()

    def _toggle_manual_xp(self):
        self._manual_xp_visible = not self._manual_xp_visible
        arrow = "▾" if self._manual_xp_visible else "▸"
        self._manual_xp_toggle_btn.configure(
            text=f"Sessão manual (digitado)  {arrow}")
        if self._manual_xp_visible:
            self._manual_xp_body.pack(fill="x", pady=(6, 0))
        else:
            self._manual_xp_body.pack_forget()

    def _toggle_adv_xp(self):
        self._adv_xp_visible = not self._adv_xp_visible
        arrow = "▾" if self._adv_xp_visible else "▸"
        self._adv_xp_toggle_btn.configure(
            text=f"Métodos avançados (memória / SDK)  {arrow}")
        if self._adv_xp_visible:
            self._adv_xp_body.pack(fill="x", pady=(6, 0))
        else:
            self._adv_xp_body.pack_forget()

    def _build_manual_xp(self, body):
        row = tk.Frame(body, bg=SURFACE)
        row.pack(fill="x", pady=(4, 0))
        tk.Label(row, text="XP inicial (%)", bg=SURFACE, fg=MUTED,
                 font=(FONT, 8)).pack(side="left")
        self._entry(row, self.xp_start_var, 8).pack(side="left", padx=(4, 14))
        tk.Label(row, text="XP final (%)", bg=SURFACE, fg=MUTED,
                 font=(FONT, 8)).pack(side="left")
        self._entry(row, self.xp_end_var, 8).pack(side="left", padx=(4, 14))

        actions = tk.Frame(body, bg=SURFACE)
        actions.pack(fill="x", pady=(8, 0))
        self.xp_start_btn = HoverButton(
            actions, "Iniciar sessão", self._xp_start, bg=ACCENT,
            hover=ACCENT2, fg=BG, font=(FONT, 9, "bold"), padx=12, pady=5)
        self.xp_start_btn.pack(side="left")
        self.xp_stop_btn = HoverButton(
            actions, "Finalizar e calcular", self._xp_stop, bg="#1f4f3a",
            hover="#27634a", font=(FONT, 9), padx=12, pady=5,
            state="disabled")
        self.xp_stop_btn.pack(side="left", padx=6)
        self.xp_cancel_btn = HoverButton(
            actions, "Cancelar", self._xp_cancel, bg="#3a2024",
            hover="#4a282e", fg=RED, font=(FONT, 9), padx=10, pady=5,
            state="disabled")
        self.xp_cancel_btn.pack(side="left")
        self.xp_active_label = tk.Label(
            body, text="Nenhuma sessão ativa.", bg=SURFACE, fg=MUTED,
            font=(FONT, 9), anchor="w")
        self.xp_active_label.pack(fill="x", pady=(8, 0))

        tk.Label(body,
                 text="Informe manualmente a % de XP no início e no fim.",
                 bg=SURFACE, fg=MUTED, font=(FONT, 7),
                 justify="left").pack(anchor="w", pady=(6, 0))

    def _build_adv_xp(self, body):
        tk.Label(body, text="LEITURA POR MEMÓRIA",
                 bg=SURFACE, fg=MUTED, font=(FONT, 8, "bold")).pack(
            anchor="w", pady=(4, 0))
        self.xp_mem_status = tk.Label(
            body, text="Nenhum endereço encontrado.",
            bg=SURFACE, fg=MUTED, font=(FONT, 8), anchor="w", justify="left")
        self.xp_mem_status.pack(fill="x", pady=(4, 6))
        row = tk.Frame(body, bg=SURFACE)
        row.pack(fill="x")
        tk.Label(row, text="XP atual (%)", bg=SURFACE, fg=MUTED,
                 font=(FONT, 8)).pack(side="left")
        self._entry(row, self.xp_mem_value_var, 8).pack(
            side="left", padx=(3, 10))
        self.xp_mem_scan_btn = HoverButton(
            row, "1° Scan", self._mem_scan_xp, bg="#1f3b4f",
            hover="#264a63", font=(FONT, 8), padx=8, pady=3)
        self.xp_mem_scan_btn.pack(side="left")
        self.xp_mem_rescan_btn = HoverButton(
            row, "Re-scan", self._mem_rescan_xp, bg="#1f3b4f",
            hover="#264a63", font=(FONT, 8), padx=8, pady=3,
            state="disabled")
        self.xp_mem_rescan_btn.pack(side="left", padx=6)
        self.xp_mem_read_btn = HoverButton(
            row, "Ler agora", self._mem_read_xp, bg="#1f4f3a",
            hover="#27634a", font=(FONT, 8), padx=8, pady=3,
            state="disabled")
        self.xp_mem_read_btn.pack(side="left")
        tk.Label(body,
                 text="Digite seu XP% atual no jogo, clique 1° Scan. "
                      "Ganhe XP, atualize o valor e clique Re-scan. "
                      "Repita até achar o endereço.",
                 bg=SURFACE, fg=MUTED, font=(FONT, 7),
                 justify="left").pack(anchor="w", pady=(6, 0))

        sep = tk.Frame(body, bg=BORDER, height=1)
        sep.pack(fill="x", pady=(10, 6))
        tk.Label(body, text="AUTO-DETECT (UE4 SDK DUMP)",
                 bg=SURFACE, fg=MUTED, font=(FONT, 8, "bold")).pack(anchor="w")
        self.xp_sdk_status = tk.Label(
            body, text="Varre GNames/GObjects do UE4 pra achar offsets.",
            bg=SURFACE, fg=MUTED, font=(FONT, 8), anchor="w",
            justify="left", wraplength=600)
        self.xp_sdk_status.pack(fill="x", pady=(4, 6))
        sdk_row = tk.Frame(body, bg=SURFACE)
        sdk_row.pack(fill="x")
        self.xp_sdk_btn = HoverButton(
            sdk_row, "Dump SDK", self._sdk_dump, bg="#3a1f4f",
            hover="#4a2863", font=(FONT, 8), padx=8, pady=3)
        self.xp_sdk_btn.pack(side="left")
        self.xp_sdk_props_box = tk.Frame(body, bg=SURFACE)
        self.xp_sdk_props_box.pack(fill="x", pady=(6, 0))

    @staticmethod
    def _parse_pct(text):
        try:
            value = str(text).replace("%", "").replace(",", ".").strip()
            return float(value)
        except Exception:
            return None

    def _read_xp_value(self):
        if self._mem_addr and self._mem_handle:
            val = memory.read_float(self._mem_handle, self._mem_addr)
            if val is not None:
                if abs(val) < 1.1:
                    val = val * 100
                return val, f"mem:0x{self._mem_addr:X}"
            ival = memory.read_int32(self._mem_handle, self._mem_addr)
            if ival is not None and ival > 0:
                val = ival / 10000.0 if ival > 10000 else ival / 100.0
                return val, f"mem:0x{self._mem_addr:X}"
        return None, "sem método de leitura disponível"

    def _sniff_toggle(self):
        if self._sniffer and self._sniffer.running:
            self._sniffer.stop()
            self._sniffer = None
            if self._sniff_timer:
                self.after_cancel(self._sniff_timer)
                self._sniff_timer = None
            self.xp_sniff_btn.configure(text="Iniciar Sniffer", bg=ACCENT)
            self.xp_sniff_status.configure(
                text="Parado. Clique Iniciar para capturar.", fg=MUTED)
            self.xp_sniff_indicator.configure(text="")
            self.xp_sniff_save_btn.configure(state="normal")
            for lbl in self._sniff_labels.values():
                lbl.configure(text="—")
            self.toast("Sniffer parado.", "info")
            return
        self._sniffer = sniffer.Sniffer()
        self._sniffer.start()
        self._sniff_spot_start = time.time()
        self.xp_sniff_save_btn.configure(state="disabled")
        self.after(500, self._sniff_check_started)

    def _sniff_check_started(self):
        if not self._sniffer:
            return
        if self._sniffer.running:
            self.xp_sniff_btn.configure(text="Parar Sniffer", bg="#3a2024")
            self.xp_sniff_status.configure(text="Capturando...", fg=GREEN)
            self.xp_sniff_indicator.configure(text="● AO VIVO", fg=GREEN)
            self.toast("Sniffer iniciado.", "success")
            self._sniff_update()
        else:
            self.xp_sniff_status.configure(
                text="Falha ao iniciar. Rode como administrador.", fg=RED)
            self._sniffer = None

    def _sniff_update(self):
        if not self._sniffer or not self._sniffer.running:
            return
        snap = self._sniffer.data.snapshot()
        elapsed = time.time() - (self._sniff_spot_start or time.time())
        hist = self._sniffer.data.xp_history
        kills_min = 0.0
        if len(hist) >= 2:
            dt = hist[-1][0] - hist[0][0]
            if dt > 5:
                kills_min = len(hist) / (dt / 60)

        sl = self._sniff_labels
        if snap['xp_base'] > 0:
            sl["xp_kill"].configure(
                text=f"{snap['xp_base']:,}", fg=ACCENT)
        sl["kills"].configure(text=f"{snap['xp_count']}", fg=FG)
        if kills_min > 0:
            sl["kills_min"].configure(text=f"{kills_min:.1f}", fg=FG)

        mins = int(elapsed) // 60
        secs = int(elapsed) % 60
        sl["elapsed"].configure(text=f"{mins:02d}:{secs:02d}", fg=FG)

        self.xp_sniff_status.configure(
            text=f"Capturando...  |  "
                 f"Pacotes: {self._sniffer.stats['packets']:,}",
            fg=GREEN)

        self._sniff_timer = self.after(2000, self._sniff_update)

    def _sniff_save(self):
        if not self._sniffer:
            self.toast("Nenhuma captura ativa.", "warning")
            return
        hist = self._sniffer.data.xp_history
        if not hist:
            self.toast("Nenhum mob morto ainda.", "warning")
            return
        snap = self._sniffer.data.snapshot()
        spot = self.xp_spot_var.get().strip() or "Spot"
        total_secs = int(time.time() - (self._sniff_spot_start or time.time()))

        start_pct = self._parse_pct(self.xp_sniff_start_var.get())
        end_pct = self._parse_pct(self.xp_sniff_end_var.get())
        if start_pct is None or end_pct is None:
            self.toast("Informe a % de XP inicial e final.", "warning")
            return
        delta = end_pct - start_pct
        if delta < 0:
            levels = simpledialog.askinteger(
                "Upou?", "Quantos níveis subiu?",
                initialvalue=1, minvalue=1, maxvalue=50)
            if levels:
                delta += levels * 100

        kills_min = 0.0
        if len(hist) >= 2:
            dt = hist[-1][0] - hist[0][0]
            if dt > 5:
                kills_min = len(hist) / (dt / 60)

        rate = delta / (total_secs / 3600) if total_secs > 0 else 0

        session = {
            "spot": spot,
            "start_ts": self._sniff_spot_start,
            "end_ts": time.time(),
            "seconds": total_secs,
            "start_pct": start_pct,
            "end_pct": end_pct,
            "delta": round(delta, 4),
            "rate": round(rate, 2),
            "xp_per_kill": snap['xp_base'],
            "kills_min": round(kills_min, 1),
            "kills": len(hist),
            "source": "sniffer",
        }
        self.cfg.xp_sessions.append(session)
        self.cfg.save()
        self._rebuild_xp_sessions()
        self._draw_xp_chart()
        self.xp_sniff_save_btn.configure(state="disabled")
        self.toast(
            f"'{spot}': {start_pct:.2f}% → {end_pct:.2f}%  "
            f"(+{delta:.2f}%, {len(hist)} kills)", "success")

    def _mem_ensure_handle(self):
        inst = self._sel_inst()
        if not inst:
            self.toast("Selecione uma conta primeiro.", "warning")
            return None
        pid = inst.win.pid
        if self._mem_pid != pid:
            if self._mem_handle:
                memory.close_process(self._mem_handle)
            self._mem_handle = memory.open_process(pid)
            self._mem_pid = pid
            self._mem_candidates = None
            self._mem_addr = None
            if not self._mem_handle:
                self.toast("Não consegui abrir o processo. Rode como admin.",
                           "error")
                return None
        return self._mem_handle

    def _mem_scan_xp(self):
        handle = self._mem_ensure_handle()
        if not handle:
            return
        val = self._parse_pct(self.xp_mem_value_var.get())
        if val is None:
            self.toast("Digite o XP% atual (ex.: 45.23).", "warning")
            return
        self.xp_mem_status.configure(text="Escaneando memória (multi-formato)...", fg=ORANGE)
        self.update_idletasks()

        def run():
            results = []
            # float como digitado (ex: 50.79)
            results += memory.scan_float(handle, val, tolerance=0.01)
            # float 0-1 (ex: 0.5079)
            results += memory.scan_float(handle, val / 100.0, tolerance=0.0001)
            # int ×100 (ex: 5079)
            results += memory.scan_int(handle, int(round(val * 100)))
            # int ×10000 (ex: 507900)
            results += memory.scan_int(handle, int(round(val * 10000)))
            # deduplica
            results = list(dict.fromkeys(results))
            self.after(0, lambda: self._mem_scan_done(results))

        threading.Thread(target=run, daemon=True).start()

    def _mem_scan_done(self, results):
        self._mem_candidates = results
        n = len(results)
        if n == 0:
            self.xp_mem_status.configure(
                text="Nenhum endereço encontrado. Verifique o valor.", fg=RED)
            self.xp_mem_rescan_btn.configure(state="disabled")
            self.xp_mem_read_btn.configure(state="disabled")
        elif n <= 10:
            self._mem_addr = results[0]
            self.xp_mem_status.configure(
                text=f"{n} endereço(s) encontrado(s). Pronto pra ler!",
                fg=GREEN)
            self.xp_mem_rescan_btn.configure(state="normal")
            self.xp_mem_read_btn.configure(state="normal")
        else:
            self.xp_mem_status.configure(
                text=f"{n} candidatos. Ganhe XP, atualize o valor e Re-scan.",
                fg=ACCENT)
            self.xp_mem_rescan_btn.configure(state="normal")
            self.xp_mem_read_btn.configure(state="disabled")

    def _mem_rescan_xp(self):
        handle = self._mem_ensure_handle()
        if not handle or not self._mem_candidates:
            return
        val = self._parse_pct(self.xp_mem_value_var.get())
        if val is None:
            self.toast("Atualize o XP% atual e clique Re-scan.", "warning")
            return
        results = []
        for target, tol in [(val, 0.01), (val / 100.0, 0.0001)]:
            results += memory.scan_float(
                handle, target, tolerance=tol, candidates=self._mem_candidates)
        for target_i in [int(round(val * 100)), int(round(val * 10000))]:
            results += memory.scan_int(
                handle, target_i, candidates=self._mem_candidates)
        results = list(dict.fromkeys(results))
        self._mem_scan_done(results)

    def _mem_read_xp(self):
        handle = self._mem_ensure_handle()
        if not handle or not self._mem_addr:
            return
        # tenta float, int/100, int/10000
        val = memory.read_float(handle, self._mem_addr)
        if val is not None:
            if abs(val) < 1.1:
                val = val * 100  # era 0-1 range
        else:
            ival = memory.read_int32(handle, self._mem_addr)
            if ival is not None:
                if ival > 10000:
                    val = ival / 10000.0
                elif ival > 0:
                    val = ival / 100.0
        if val is None:
            self.xp_mem_status.configure(
                text="Falha na leitura. Endereço pode ter mudado.", fg=RED)
            return
        field = self.xp_end_var if self._xp_active else self.xp_start_var
        field.set(f"{val:.2f}")
        self.xp_mem_status.configure(
            text=f"Leitura: {val:.2f}%  (addr: 0x{self._mem_addr:X})",
            fg=GREEN)
        self.toast(f"XP (memória): {val:.2f}%", "success")

    def _sdk_dump(self):
        handle = self._mem_ensure_handle()
        if not handle:
            return
        self.xp_sdk_status.configure(text="Escaneando GNames...", fg=ORANGE)
        self.xp_sdk_btn.configure(state="disabled")
        self.update_idletasks()

        def run():
            def progress(phase, pct):
                self.after(0, lambda p=phase, v=pct:
                           self.xp_sdk_status.configure(
                               text=f"{p}: {v}%", fg=ORANGE))
            result = ue4.full_scan(handle, progress)
            self.after(0, lambda: self._sdk_dump_done(result))

        threading.Thread(target=run, daemon=True).start()

    def _sdk_dump_done(self, result):
        self.xp_sdk_btn.configure(state="normal")
        for w in self.xp_sdk_props_box.winfo_children():
            w.destroy()

        if not result:
            self.xp_sdk_status.configure(
                text="Não encontrou GNames/GObjects. O jogo está aberto?",
                fg=RED)
            return

        if result.get("error") == "gnames_not_found":
            probe = result.get("probe_count", 0)
            if probe > 0:
                self.xp_sdk_status.configure(
                    text=f"GNames não encontrado mas 'ByteProperty' existe "
                         f"({probe}x). Header diferente do padrão.",
                    fg=ORANGE)
            else:
                self.xp_sdk_status.configure(
                    text="Nomes UE4 não encontrados em plaintext. "
                         "O jogo pode encriptar FNames.",
                    fg=RED)
            return

        player_data = result.get("player_data", {})
        if not player_data:
            gn = result["gnames"]
            go = result["gobjects"]
            self.xp_sdk_status.configure(
                text=f"GNames OK ({sum(1 for b in gn['blocks'] if b)} blocos), "
                     f"GObjects OK ({go['num_elements']} objetos). "
                     f"Mas nenhuma classe do jogador encontrada.",
                fg=ORANGE)
            self._ue4_result = result
            return

        total_props = sum(len(v) for v in player_data.values())
        self.xp_sdk_status.configure(
            text=f"Encontrado! {len(player_data)} classes, "
                 f"{total_props} propriedades.",
            fg=GREEN)
        self._ue4_result = result

        for cls_name, props in player_data.items():
            tk.Label(self.xp_sdk_props_box, text=cls_name,
                     bg=SURFACE, fg=ACCENT, font=(FONT, 8, "bold")).pack(
                anchor="w", pady=(4, 0))
            for pname, offset in sorted(props.items(), key=lambda x: x[1]):
                row = tk.Frame(self.xp_sdk_props_box, bg=SURFACE)
                row.pack(fill="x")
                tk.Label(row, text=f"  0x{offset:04X}  {pname}",
                         bg=SURFACE, fg=MUTED, font=(MONO, 8)).pack(
                    side="left")

    def _xp_start(self):
        if self._xp_active:
            self.toast("Já existe uma sessão ativa.", "warning")
            return
        spot = self.xp_spot_var.get().strip() or "Spot 1"
        start = self._parse_pct(self.xp_start_var.get())
        if start is None:
            self.toast("Informe o XP inicial (ex.: 12.34).", "warning")
            return
        self._xp_active = {
            "spot": spot,
            "start_ts": time.time(),
            "start_pct": start,
        }
        self.xp_end_var.set("")
        self._update_xp_ui()
        self.toast(f"Sessão iniciada: {spot}.", "success")

    def _xp_stop(self):
        active = self._xp_active
        if not active:
            self.toast("Nenhuma sessão ativa.", "warning")
            return
        end = self._parse_pct(self.xp_end_var.get())
        if end is None:
            self.toast("Informe o XP final (ex.: 15.67).", "warning")
            return
        seconds = time.time() - active["start_ts"]
        if seconds < 1:
            self.toast("Espere pelo menos alguns segundos.", "warning")
            return
        delta = end - active["start_pct"]
        if delta < 0:
            levels = simpledialog.askinteger(
                "Nível subiu?",
                "O XP final é menor que o inicial.\nQuantos níveis subiram?",
                parent=self, initialvalue=1, minvalue=1)
            if levels is None:
                return
            delta += 100.0 * levels
        if delta <= 0:
            self.toast("O XP final precisa ser maior que o inicial.", "warning")
            return
        session = {
            "spot": active["spot"],
            "start_ts": active["start_ts"],
            "end_ts": time.time(),
            "start_pct": active["start_pct"],
            "end_pct": end,
            "seconds": seconds,
            "delta": delta,
            "rate": xp_rate(delta, seconds),
            "per10": delta * 600.0 / seconds,
        }
        self.cfg.xp_sessions.append(session)
        self.cfg.xp_sessions = self.cfg.xp_sessions[-50:]
        self.cfg.save()
        self._xp_active = None
        self.xp_end_var.set("")
        self._update_xp_ui()
        self.toast(
            f"{session['spot']}: +{delta:.2f}% em "
            f"{format_duration(seconds)} ({session['rate']:.1f}%/h).",
            "success")

    def _xp_cancel(self):
        if not self._xp_active:
            return
        if not messagebox.askyesno("Cancelar", "Cancelar a sessão de XP atual?"):
            return
        self._xp_active = None
        self._update_xp_ui()
        self.toast("Sessão cancelada.", "info")

    def _xp_clear(self):
        if not self.cfg.xp_sessions:
            return
        if not messagebox.askyesno("Limpar histórico",
                                   "Apagar todas as medições de XP?"):
            return
        self.cfg.xp_sessions = []
        self.cfg.save()
        self._xp_sig = None
        self._update_xp_ui()
        self.toast("Histórico de XP apagado.", "success")

    def _xp_remove(self, index):
        try:
            session = self.cfg.xp_sessions[index]
        except IndexError:
            return
        if not messagebox.askyesno(
                "Remover", f"Remover a medição de {session.get('spot', '?')}?"):
            return
        self.cfg.xp_sessions.pop(index)
        self.cfg.save()
        self._xp_sig = None
        self._update_xp_ui()

    def _update_xp_ui(self):
        if not hasattr(self, "xp_active_label"):
            return
        active = self._xp_active
        if active:
            elapsed = time.time() - active["start_ts"]
            self.xp_active_label.configure(
                text=f"Sessão ativa: {active['spot']}  ·  "
                     f"{format_duration(elapsed)}  ·  "
                     f"início {active['start_pct']:.2f}%",
                fg=GREEN)
            self.xp_start_btn.configure(state="disabled")
            self.xp_stop_btn.configure(state="normal")
            self.xp_cancel_btn.configure(state="normal")
        else:
            self.xp_active_label.configure(text="Nenhuma sessão ativa.",
                                           fg=MUTED)
            self.xp_start_btn.configure(state="normal")
            self.xp_stop_btn.configure(state="disabled")
            self.xp_cancel_btn.configure(state="disabled")

        sessions = self.cfg.xp_sessions
        sig = tuple((s.get("spot"), s.get("end_ts"), s.get("delta"),
                     s.get("rate"), s.get("xp_abs_hour")) for s in sessions)
        if sig == self._xp_sig:
            return
        self._xp_sig = sig
        self._rebuild_xp_sessions()
        self._draw_xp_chart()

    def _rebuild_xp_sessions(self):
        for child in self.xp_sessions_box.winfo_children():
            child.destroy()
        sessions = self.cfg.xp_sessions
        if not sessions:
            tk.Label(self.xp_sessions_box,
                     text="Nenhuma medição ainda. Inicie o sniffer e rode o macro.",
                     bg=SURFACE, fg=MUTED, font=(FONT, 8)).pack(anchor="w",
                                                                pady=6)
            self.xp_best_label.configure(text="")
            return

        best = max(s.get("rate", 0) for s in sessions)
        best_session = max(sessions, key=lambda s: s.get("rate", 0))
        self.xp_best_label.configure(
            text=f"Melhor: {best_session.get('spot', '?')}  ·  "
                 f"{best:.2f}%/h")

        header = tk.Frame(self.xp_sessions_box, bg=SURFACE)
        header.pack(fill="x", pady=(0, 2))
        for text, width in (("SPOT", 16), ("DURAÇÃO", 8), ("INÍCIO", 8),
                            ("FIM", 8), ("+XP%", 8), ("%/H", 8),
                            ("KILLS", 7), ("XP/KILL", 10)):
            tk.Label(header, text=text, bg=SURFACE, fg=MUTED,
                     font=(FONT, 7, "bold"), width=width, anchor="w").pack(
                side="left")
        for index, session in enumerate(reversed(sessions)):
            real_index = len(sessions) - 1 - index
            row = tk.Frame(self.xp_sessions_box, bg=SURFACE)
            row.pack(fill="x", pady=2)
            tk.Label(row, text=str(session.get("spot", "?"))[:16], bg=SURFACE,
                     fg=FG, font=(FONT, 9, "bold"), width=16,
                     anchor="w").pack(side="left")
            tk.Label(row, text=format_duration(session.get("seconds", 0)),
                     bg=SURFACE, fg=MUTED, font=(FONT, 8), width=8,
                     anchor="w").pack(side="left")
            sp = session.get("start_pct")
            ep = session.get("end_pct")
            tk.Label(row, text=f"{sp:.2f}%" if sp is not None else "—",
                     bg=SURFACE, fg=MUTED, font=(FONT, 8), width=8,
                     anchor="w").pack(side="left")
            tk.Label(row, text=f"{ep:.2f}%" if ep is not None else "—",
                     bg=SURFACE, fg=MUTED, font=(FONT, 8), width=8,
                     anchor="w").pack(side="left")
            delta = session.get("delta", 0)
            tk.Label(row, text=f"+{delta:.2f}%", bg=SURFACE, fg=GREEN,
                     font=(FONT, 8), width=8, anchor="w").pack(side="left")
            rate = session.get("rate", 0)
            tk.Label(row, text=f"{rate:.2f}", bg=SURFACE,
                     fg=ACCENT if rate == best else FG,
                     font=(FONT, 8, "bold"), width=8,
                     anchor="w").pack(side="left")
            kills = session.get("kills", 0)
            tk.Label(row, text=str(kills) if kills else "—",
                     bg=SURFACE, fg=MUTED, font=(FONT, 8), width=7,
                     anchor="w").pack(side="left")
            xpk = session.get("xp_per_kill", 0)
            tk.Label(row, text=f"{xpk:,}" if xpk else "—",
                     bg=SURFACE, fg=MUTED, font=(FONT, 8), width=10,
                     anchor="w").pack(side="left")
            HoverButton(row, "×", lambda i=real_index: self._xp_remove(i),
                        bg=SURFACE, hover="#3a2024", fg=RED,
                        font=(FONT, 9), padx=4, pady=1).pack(side="right")

    def _draw_xp_chart(self):
        canvas = getattr(self, "xp_chart", None)
        if canvas is None:
            return
        canvas.delete("all")
        sessions = self.cfg.xp_sessions
        if not sessions:
            canvas.create_text(12, 20, anchor="w", fill=MUTED,
                               font=(FONT, 8),
                               text="Sem dados: inicie o sniffer e rode o macro.")
            return

        sorted_s = sorted(sessions, key=lambda s: s.get("rate", 0),
                          reverse=True)[:8]

        width = max(200, canvas.winfo_width())
        height = max(120, canvas.winfo_height())
        row_h = min(24, max(16, (height - 18) // max(1, len(sorted_s))))
        label_w = min(150, int(width * 0.25))
        bar_x = label_w + 8
        bar_max = max(30, width - bar_x - 80)

        best_val = max(s.get("rate", 0) for s in sorted_s) or 1

        for i, session in enumerate(sorted_s):
            y = 8 + i * row_h
            spot = str(session.get("spot", "?"))[:18]
            canvas.create_text(4, y + row_h / 2 - 2, anchor="w",
                               text=spot, fill=FG, font=(FONT, 8))
            val = session.get("rate", 0)
            bar_w = int(bar_max * min(1.0, val / best_val)) if best_val else 0
            label = f"{val:.2f}%/h"
            color = ACCENT if i == 0 else SURFACE3
            canvas.create_rectangle(bar_x, y + 3, bar_x + max(2, bar_w),
                                    y + row_h - 3, fill=color, outline="")
            canvas.create_text(bar_x + bar_w + 6, y + row_h / 2 - 2,
                               anchor="w", text=label,
                               fill=ACCENT if i == 0 else MUTED,
                               font=(FONT, 8, "bold" if i == 0 else "normal"))

    def _build_rooms(self):
        page = tk.Frame(self.page_host, bg=BG)
        self.pages["camaras"] = page
        scroll = ScrollFrame(page, bg=BG)
        self.rooms_scroll = scroll
        scroll.pack(fill="both", expand=True)
        body = scroll.inner

        self.rooms_info = tk.Label(
            body, text="Selecione uma conta na aba Contas.",
            bg=BG, fg=MUTED, font=(FONT, 9), anchor="w")
        self.rooms_info.pack(fill="x", pady=(0, 10))

        tk.Label(body, text="SALAS", bg=BG, fg=MUTED,
                 font=(FONT, 8, "bold")).pack(anchor="w")
        grid = tk.Frame(body, bg=BG)
        grid.pack(fill="x", pady=(6, 10))
        for col in range(2):
            grid.grid_columnconfigure(col, weight=1)
        self.room_search_btns = {}
        self.room_save_btns = {}
        for i, (key, label) in enumerate(ROOMS):
            card, inner = self._card(grid)
            card.grid(row=i // 2, column=i % 2, sticky="nsew",
                      padx=4, pady=4)
            tk.Label(inner, text=label, bg=SURFACE, fg=FG,
                     font=(FONT, 10, "bold")).pack(anchor="w")
            tk.Label(inner, text="Compara o nome da sala com a referência.",
                     bg=SURFACE, fg=MUTED, font=(FONT, 8)).pack(
                anchor="w", pady=(2, 8))
            row = tk.Frame(inner, bg=SURFACE)
            row.pack(fill="x")
            save = HoverButton(row, "Salvar referência",
                               lambda k=key: self._save_room(k),
                               bg=SURFACE2, hover=SURFACE3,
                               font=(FONT, 8), padx=8, pady=3)
            search = HoverButton(row, "Procurar",
                                 lambda k=key: self._toggle_search(k),
                                 bg="#1f3b4f", hover="#264a63",
                                 font=(FONT, 8), padx=8, pady=3)
            save.pack(side="left")
            search.pack(side="left", padx=6)
            self.room_save_btns[key] = save
            self.room_search_btns[key] = search

        tk.Label(body, text="PONTOS", bg=BG, fg=MUTED,
                 font=(FONT, 8, "bold")).pack(anchor="w")
        card, inner = self._card(body)
        card.pack(fill="x", pady=(6, 4))
        point_defs = [
            ("amovivel", "Ponto Amovível", "botão azul que troca de sala"),
            ("nome", "Ponto Nome", "começo do texto do nome da sala"),
            ("entrar", "Ponto Entrar", "botão de entrar na sala"),
        ]
        for key, label, desc in point_defs:
            row = tk.Frame(inner, bg=SURFACE)
            row.pack(fill="x", pady=3)
            tk.Label(row, text=label, bg=SURFACE, fg=FG,
                     font=(FONT, 9)).pack(side="left")
            tk.Label(row, text=desc, bg=SURFACE, fg=MUTED,
                     font=(FONT, 8)).pack(side="left", padx=10)
            HoverButton(row, "Capturar (3s)",
                        lambda k=key: self._capture_point(k),
                        bg=SURFACE2, hover=SURFACE3,
                        font=(FONT, 8), padx=8, pady=3).pack(side="right")
        tk.Label(body,
                 text="A busca pausa a instância para não misturar cliques. "
                      "A captura usa PrintWindow; deixe o jogo visível, "
                      "não minimizado.",
                 bg=BG, fg=MUTED, font=(FONT, 8), justify="left",
                 wraplength=680).pack(fill="x", pady=(8, 0))

        card, inner = self._card(body)
        card.pack(fill="x", pady=(8, 4))
        tk.Label(inner, text="COMO TREINAR", bg=SURFACE, fg=ACCENT,
                 font=(FONT, 8, "bold")).pack(anchor="w")
        tk.Label(inner,
                 text="1. Abra a tela das câmaras no jogo.  "
                      "2. Capture os três pontos com o mouse parado.  "
                      "3. Salve a referência de cada sala enquanto ela "
                      "aparece.  4. Use Procurar; a busca clica no botão "
                      "azul até o nome bater.",
                 bg=SURFACE, fg=MUTED, font=(FONT, 8), justify="left",
                 wraplength=680).pack(anchor="w", pady=(4, 0))

    def _build_launcher(self):
        page = tk.Frame(self.page_host, bg=BG)
        self.pages["launcher"] = page
        scroll = ScrollFrame(page, bg=BG)
        self.launcher_scroll = scroll
        scroll.pack(fill="both", expand=True)
        body = scroll.inner

        cfg = self.cfg.launcher
        self.launcher_path = tk.StringVar(value=cfg.get("path", ""))
        self.launcher_title = tk.StringVar(value=cfg.get("title", "MIR4"))
        self.g1x = tk.StringVar(value=str(cfg.get("g1", [0, 0])[0]))
        self.g1y = tk.StringVar(value=str(cfg.get("g1", [0, 0])[1]))
        self.g2x = tk.StringVar(value=str(cfg.get("g2", [0, 0])[0]))
        self.g2y = tk.StringVar(value=str(cfg.get("g2", [0, 0])[1]))
        self.launcher_pre = tk.StringVar(value=str(cfg.get("pre", 3.0)))
        self.launcher_between = tk.StringVar(value=str(cfg.get("between", 6.0)))

        card, inner = self._card(body)
        card.pack(fill="x", pady=4)
        tk.Label(inner, text="LAUNCHER", bg=SURFACE, fg=MUTED,
                 font=(FONT, 8, "bold")).pack(anchor="w")
        row = tk.Frame(inner, bg=SURFACE)
        row.pack(fill="x", pady=(8, 3))
        tk.Label(row, text="Caminho", bg=SURFACE, fg=MUTED,
                 font=(FONT, 8), width=10, anchor="w").pack(side="left")
        self._entry(row, self.launcher_path, 62).pack(
            side="left", fill="x", expand=True, padx=4)
        self._button(row, "Procurar", self._browse_launcher).pack(
            side="left", padx=2)
        self._button(row, "Abrir", self._launch_one,
                     bg="#1f3b4f", hover="#264a63").pack(side="left", padx=2)
        row = tk.Frame(inner, bg=SURFACE)
        row.pack(fill="x", pady=3)
        tk.Label(row, text="Título", bg=SURFACE, fg=MUTED,
                 font=(FONT, 8), width=10, anchor="w").pack(side="left")
        self._entry(row, self.launcher_title, 30).pack(side="left", padx=4)
        tk.Label(row, text="parte do título da janela do launcher",
                 bg=SURFACE, fg=MUTED, font=(FONT, 8)).pack(side="left",
                                                            padx=6)

        coords = tk.Frame(body, bg=BG)
        coords.pack(fill="x", pady=4)
        for col in range(2):
            coords.grid_columnconfigure(col, weight=1)
        for col, (name, xv, yv, which) in enumerate((
                ("GAME 1", self.g1x, self.g1y, "g1"),
                ("GAME 2", self.g2x, self.g2y, "g2"))):
            card, inner = self._card(coords)
            card.grid(row=0, column=col, sticky="nsew", padx=4)
            tk.Label(inner, text=name, bg=SURFACE, fg=MUTED,
                     font=(FONT, 8, "bold")).pack(anchor="w")
            tk.Label(inner, text="Posição do botão na janela do launcher.",
                     bg=SURFACE, fg=MUTED, font=(FONT, 8)).pack(anchor="w",
                                                                pady=(2, 8))
            row = tk.Frame(inner, bg=SURFACE)
            row.pack(fill="x")
            tk.Label(row, text="X", bg=SURFACE, fg=MUTED,
                     font=(FONT, 8)).pack(side="left")
            self._entry(row, xv, 6).pack(side="left", padx=(3, 10))
            tk.Label(row, text="Y", bg=SURFACE, fg=MUTED,
                     font=(FONT, 8)).pack(side="left")
            self._entry(row, yv, 6).pack(side="left", padx=(3, 10))
            self._button(row, "Capturar",
                         lambda w=which: self._capture_launcher(w)).pack(
                side="left")

        card, inner = self._card(body)
        card.pack(fill="x", pady=4)
        row = tk.Frame(inner, bg=SURFACE)
        row.pack(fill="x")
        tk.Label(row, text="Espera após abrir", bg=SURFACE, fg=MUTED,
                 font=(FONT, 8)).pack(side="left")
        self._entry(row, self.launcher_pre, 6).pack(side="left", padx=4)
        tk.Label(row, text="s", bg=SURFACE, fg=MUTED,
                 font=(FONT, 8)).pack(side="left")
        tk.Label(row, text="entre Game 1 e Game 2", bg=SURFACE, fg=MUTED,
                 font=(FONT, 8)).pack(side="left", padx=(16, 3))
        self._entry(row, self.launcher_between, 6).pack(side="left", padx=4)
        tk.Label(row, text="s", bg=SURFACE, fg=MUTED,
                 font=(FONT, 8)).pack(side="left")

        actions = tk.Frame(body, bg=BG)
        actions.pack(fill="x", pady=(10, 4))
        self._button(actions, "Abrir 2 clientes (UIA)", self._launch_two,
                     bg=ACCENT, hover=ACCENT2, fg=BG).pack(side="left")
        self._button(actions, "Parar abertura", self._stop_launch,
                     bg="#3a2024", hover="#4a282e", fg=RED).pack(
            side="left", padx=6)
        self._button(actions, "Salvar configuração",
                     self._save_launcher).pack(side="left")

        self.uia_label = tk.Label(
            body,
            text="UIAutomation: " + ("pywinauto instalado" if uia_available()
                                    else "pywinauto NÃO instalado (usa PostMessage)"),
            bg=BG, fg=GREEN if uia_available() else ORANGE,
            font=(FONT, 8), anchor="w")
        self.uia_label.pack(fill="x", pady=(8, 2))
        tk.Label(body,
                 text="Capture os botões Game 1/Game 2 com a janela do "
                      "launcher visível. Depois use Farm EXP na instância.",
                 bg=BG, fg=MUTED, font=(FONT, 8), justify="left",
                 wraplength=680).pack(fill="x")

        card, inner = self._card(body)
        card.pack(fill="x", pady=(8, 4))
        tk.Label(inner, text="COMO ABRIR", bg=SURFACE, fg=ACCENT,
                 font=(FONT, 8, "bold")).pack(anchor="w")
        tk.Label(inner,
                 text="1. Informe o launcher e capture os dois botões.  "
                      "2. Clique em Abrir 2 clientes (UIA).  "
                      "3. Volte em Contas e inicie o perfil de cada janela.  "
                      "Se o launcher não expor botões, o clique cai para "
                      "PostMessage.",
                 bg=SURFACE, fg=MUTED, font=(FONT, 8), justify="left",
                 wraplength=680).pack(anchor="w", pady=(4, 0))

    def _build_settings(self):
        page = tk.Frame(self.page_host, bg=BG)
        self.pages["ajustes"] = page
        scroll = ScrollFrame(page, bg=BG)
        self.settings_scroll = scroll
        scroll.pack(fill="both", expand=True)
        body = scroll.inner

        card, inner = self._card(body)
        card.pack(fill="x", pady=4)
        tk.Label(inner, text="TESTAR TECLA", bg=SURFACE, fg=MUTED,
                 font=(FONT, 8, "bold")).pack(anchor="w")
        self.test_key_var = tk.StringVar(value="1")
        row = tk.Frame(inner, bg=SURFACE)
        row.pack(fill="x", pady=(8, 2))
        self._entry(row, self.test_key_var, 20).pack(side="left")
        self._button(row, "Enviar para a conta",
                     self._test_key).pack(side="left", padx=8)
        tk.Label(inner, text="ex.: q  ou  q,e,t  ou  pagedown",
                 bg=SURFACE, fg=MUTED, font=(FONT, 8)).pack(anchor="w")

        card, inner = self._card(body)
        card.pack(fill="x", pady=4)
        tk.Label(inner, text="ATUALIZAÇÕES", bg=SURFACE, fg=MUTED,
                 font=(FONT, 8, "bold")).pack(anchor="w")
        row = tk.Frame(inner, bg=SURFACE)
        row.pack(fill="x", pady=(8, 0))
        tk.Label(row, text=f"Versão atual: {APP_VERSION}", bg=SURFACE,
                 fg=FG, font=(FONT, 9)).pack(side="left")
        self._button(row, "Verificar atualização",
                     self._check_update_manual).pack(side="left", padx=10)

        card, inner = self._card(body)
        card.pack(fill="x", pady=4)
        tk.Label(inner, text="CONFIGURAÇÃO E ATALHOS", bg=SURFACE,
                 fg=MUTED, font=(FONT, 8, "bold")).pack(anchor="w")
        self._button(inner, "Abrir pasta de configuração",
                     self._open_cfg_folder).pack(anchor="w", pady=(8, 4))
        tk.Label(inner, text=f"{CFG_DIR}", bg=SURFACE, fg=MUTED,
                 font=(MONO, 8), justify="left").pack(anchor="w")
        tk.Label(inner, text="F12 = pânico  •  F9 = parar gravação",
                 bg=SURFACE, fg=MUTED, font=(FONT, 8)).pack(anchor="w",
                                                            pady=(6, 0))

        tk.Label(body,
                 text="O bot envia teclas por PostMessage. Se o jogo ignorar, "
                      "rode como administrador. UIAutomation é usado apenas "
                      "no launcher; o jogo continua por PostMessage.",
                 bg=BG, fg=MUTED, font=(FONT, 8), justify="left",
                 wraplength=680).pack(fill="x", pady=(10, 0))

    def _refresh_profiles(self):
        names = sorted(self.cfg.profiles)
        self.profile_combo.set_values(names)
        if self.profile_var.get() not in names:
            self.profile_var.set(names[0] if names else "default")

    def _select_account(self, hwnd):
        if hwnd == self._sel:
            return
        self._save_ui()
        self._sel = hwnd
        inst = self._sel_inst()
        if not inst:
            return
        self.profile_var.set(inst.profile_name)
        self._load_ui(inst.profile)
        self._update_buttons()

    def _start_instance(self, hwnd):
        self._select_account(hwnd)
        self._start_selected()

    def _toggle_pause_instance(self, hwnd):
        self._select_account(hwnd)
        self._toggle_pause()

    def _stop_instance(self, hwnd):
        self._select_account(hwnd)
        self._stop_selected()

    def _apply_profile(self):
        inst = self._sel_inst()
        if not inst:
            self.toast("Selecione uma conta primeiro.", "warning")
            return
        name = self.profile_var.get().strip()
        if not name:
            return
        self._save_ui()
        self.manager.bind(inst.hwnd, name)
        self._load_ui(inst.profile)
        self._log(f"Perfil '{name}' aplicado em {inst.title}.")
        self.toast(f"Perfil '{name}' aplicado.", "success")
        self._refresh_now()

    def _new_profile(self):
        name = simpledialog.askstring("Novo perfil", "Nome do perfil:", parent=self)
        if not name:
            return
        name = name.strip()
        if not name:
            return
        if name in self.cfg.profiles:
            self.toast("Já existe um perfil com esse nome.", "warning")
            return
        profile = self.cfg.ensure_profile(name)
        inst = self._sel_inst()
        if inst:
            self._save_ui()
            self.cfg.bind(name, inst.win.title)
            inst.profile = profile
            inst.profile_name = name
            inst.acordar_started = False
        self.cfg.save()
        self._refresh_profiles()
        self.profile_var.set(name)
        self._load_ui(profile)
        self._refresh_now()
        self._log(f"Perfil '{name}' criado.")

    def _delete_profile(self):
        name = self.profile_var.get().strip()
        if not name:
            return
        if name == "default":
            self.toast("O perfil 'default' não pode ser excluído.", "warning")
            return
        if not messagebox.askyesno("Excluir perfil",
                                   f"Excluir o perfil '{name}'?"):
            return
        for inst in list(self.manager.instances.values()):
            if inst.profile_name == name:
                fallback = self.cfg.ensure_profile("default")
                inst.profile = fallback
                inst.profile_name = "default"
                inst.acordar_started = False
        self.cfg.delete_profile(name)
        self._refresh_profiles()
        inst = self._sel_inst()
        if inst:
            self._load_ui(inst.profile)
            self.profile_var.set(inst.profile_name)
        self._refresh_now()
        self._log(f"Perfil '{name}' excluído.")
        self.toast("Perfil excluído.", "success")

    def _save_profile_btn(self):
        if not self._sel_inst():
            self.toast("Selecione uma conta primeiro.", "warning")
            return
        self._save_ui()
        self._refresh_profile_labels()
        self._log("Perfil salvo.")
        self.toast("Perfil salvo.", "success")
        self._refresh_now()

    def _save_ui(self):
        if self._loading:
            return
        inst = self._sel_inst()
        if not inst:
            return
        profile = inst.profile
        mode = self.mode_var.get()
        if mode not in MODE_DEFAULT_INTERVAL and mode != "off":
            mode = "off"
        profile["mode"] = mode
        for key, var in self.mode_interval_vars.items():
            profile.setdefault("mode_cfg", {}).setdefault(key, {})
            profile["mode_cfg"][key]["interval"] = self._num(
                var, MODE_DEFAULT_INTERVAL.get(key, 5.0), 0.5)
        farm = profile["mode_cfg"].setdefault("farm", {})
        farm["ultimate"] = bool(self.ultimate_var.get())
        farm["ultimate_key"] = (
            self.ultimate_key_var.get().strip().lower() or "r")
        farm.setdefault("ultimate_interval", 0.8)

        extras = profile.setdefault("extras", {})
        buff = extras.setdefault("buff", {})
        buff["on"] = bool(self.buff_on.get())
        buff["interval"] = self._num(self.buff_int, 180.0, 1.0)
        buff["keys"] = [part.strip().lower()
                        for part in self.buff_keys.get().split(",")
                        if part.strip()]
        for key, meta in MACRO_TASKS.items():
            cfg = extras.setdefault(key, {})
            cfg["on"] = bool(self.extra_on[key].get())
            cfg["interval"] = self._num(self.extra_int[key],
                                        meta["interval"], 1.0)
            cfg["repeat"] = self._int(self.extra_rep[key],
                                      meta["repeat"], 1)
        self.cfg.save()
        self._update_buttons()

    def _load_ui(self, profile):
        self._loading = True
        try:
            mode = profile.get("mode", "off")
            self.mode_var.set(mode if mode in MODE_LABELS else "off")
            for key, var in self.mode_interval_vars.items():
                value = (profile.get("mode_cfg", {}).get(key, {})
                         .get("interval", MODE_DEFAULT_INTERVAL[key]))
                var.set(self._fmt_num(value))
            farm_cfg = profile.get("mode_cfg", {}).get("farm", {})
            self.ultimate_var.set(bool(farm_cfg.get("ultimate", False)))
            self.ultimate_key_var.set(
                farm_cfg.get("ultimate_key", "r") or "r")
            buff = profile.get("extras", {}).get("buff", {})
            self.buff_on.set(bool(buff.get("on")))
            self.buff_int.set(self._fmt_num(buff.get("interval", 180)))
            self.buff_keys.set(",".join(buff.get("keys", ["1", "2", "3"])))
            for key, meta in MACRO_TASKS.items():
                cfg = profile.get("extras", {}).get(key, {})
                self.extra_on[key].set(bool(cfg.get("on")))
                self.extra_int[key].set(
                    self._fmt_num(cfg.get("interval", meta["interval"])))
                self.extra_rep[key].set(str(cfg.get("repeat", meta["repeat"])))
            self._refresh_profile_labels()
            self._sync_mode_cards()
        finally:
            self._loading = False

    def _refresh_profile_labels(self):
        inst = self._sel_inst()
        for key in MACRO_TASKS:
            steps = []
            if inst:
                steps = (inst.profile.get("extras", {}).get(key, {})
                         .get("macro") or [])
            label = self.macro_labels.get(key)
            if label:
                label.config(text=f"macro: {len(steps)} passos")

    def _on_mode_change(self):
        inst = self._sel_inst()
        if inst:
            inst.acordar_started = False
        self._save_ui()
        self._sync_mode_cards()

    # ── início / parada ──────────────────────────────────────────
    def _sel_inst(self):
        if not self._sel:
            return None
        return self.manager.instances.get(self._sel)

    def _start_selected(self):
        inst = self._sel_inst()
        if not inst:
            self.toast("Selecione uma conta primeiro.", "warning")
            return
        self._save_ui()
        active = (inst.profile.get("mode", "off") != "off"
                  or any(cfg.get("on") for cfg in
                         inst.profile.get("extras", {}).values()))
        if not active:
            self.toast("Nenhuma tarefa ativa nesse perfil.", "warning")
        if inst.start():
            self._log(f"[{inst.profile_name}] iniciado em {inst.title}.")
            self.toast(f"{inst.title} iniciada.", "success")
        self._update_buttons()

    def _start_all(self):
        self._save_ui()
        count = self.manager.start_all()
        self._log(f"Iniciadas {count} instância(s).")
        self.toast(f"{count} conta(s) iniciada(s).", "success")
        self._update_buttons()

    def _toggle_pause(self):
        inst = self._sel_inst()
        if not inst:
            return
        if inst.status() == "pausado":
            inst.resume()
        else:
            inst.pause()
        self._update_buttons()

    def _stop_selected(self):
        inst = self._sel_inst()
        if inst:
            inst.stop()
            self.toast(f"{inst.title} parada.", "info")
        self._update_buttons()

    def _stop_all(self):
        self.manager.stop_all()
        self._log("Todas as instâncias paradas.")
        self.toast("Todas as contas paradas.", "info")
        self._update_buttons()

    def _panic(self):
        if self._recorder is not None:
            self._cancel_recording = True
            self._recorder.stop()
        else:
            self._record_task = None
            self._record_inst = None
            self._record_was_running = False
        self.manager.stop_all()
        self._log("PÂNICO: todas as instâncias paradas.")
        self.toast("Pânico: tudo parado.", "error")
        try:
            self.deiconify()
            self.lift()
        except Exception:
            pass

    def _run_mode_now(self):
        inst = self._sel_inst()
        if not inst:
            self.toast("Selecione uma conta primeiro.", "warning")
            return
        self._save_ui()
        mode = inst.profile.get("mode", "off")
        if mode == "off":
            self.toast("Escolha um modo de combate antes.", "warning")
            return
        if inst.is_running():
            self.toast("Pare a conta antes de executar manualmente.", "warning")
            return
        inst.stop_event.clear()
        inst.pause_event.clear()
        threading.Thread(target=self._run_mode_thread,
                         args=(inst, mode), daemon=True).start()

    @staticmethod
    def _run_mode_thread(inst, mode):
        try:
            inst.run_mode(mode)
        except Exception as exc:
            inst.log(f"execução manual: {exc}")

    # ── macros ───────────────────────────────────────────────────
    def _record_macro(self, key):
        if self._recorder and self._recorder.is_alive():
            self._recorder.stop()
            return
        if self._record_task:
            # contagem de 3s em andamento: cancela
            self._record_task = None
            self._record_inst = None
            self._record_was_running = False
            self._log("Gravação cancelada.")
            self._update_buttons()
            return
        inst = self._sel_inst()
        if not inst:
            messagebox.showinfo("Gravar macro", "Selecione uma instância primeiro.")
            return
        meta = MACRO_TASKS[key]
        if not messagebox.askokcancel(
                "Gravar macro",
                f"{meta['label']}:\n\n"
                "A instância será pausada. Abra a tela no jogo, faça a ação "
                "uma vez e pressione F9 para parar.\n\n"
                "O tempo e os cliques dentro da janela do jogo serão gravados."):
            return
        self._save_ui()
        self._record_task = key
        self._record_inst = inst
        self._record_was_running = inst.is_running()
        self._cancel_recording = False
        if self._record_was_running:
            inst.pause()
        self._log(f"Gravação de '{meta['label']}' em 3s...")
        self.after(1000, lambda: self._countdown(2, key))
        self._update_buttons()

    def _countdown(self, remaining, key):
        if self._record_task != key:
            return
        if remaining > 0:
            self._log(f"{remaining}...")
            self.after(1000, lambda: self._countdown(remaining - 1, key))
        else:
            inst = self._record_inst
            if not inst:
                return
            self._recorder = Recorder(inst.hwnd, self._macro_done, self.log)
            self._recorder.start()
            self._log("GRAVANDO — F9 para parar.")
            self._update_buttons()

    def _macro_done(self, steps):
        key = self._record_task
        inst = self._record_inst
        was_running = self._record_was_running
        cancelled = self._cancel_recording
        self._cancel_recording = False
        self._ui_actions.put(
            lambda: self._finish_macro(key, inst, steps, was_running, cancelled))

    def _finish_macro(self, key, inst, steps, was_running, cancelled=False):
        self._recorder = None
        self._record_task = None
        self._record_inst = None
        self._record_was_running = False
        if cancelled:
            self._log("Gravação cancelada.")
        elif inst and steps and key in MACRO_TASKS:
            inst.profile.setdefault("extras", {}).setdefault(key, {})["macro"] = steps
            self.cfg.save()
            self._log(f"Macro '{MACRO_TASKS[key]['label']}' salva: "
                      f"{len(steps)} passos.")
            self.toast(f"Macro salva: {len(steps)} passos.", "success")
        elif inst:
            self._log("Nada foi gravado.")
            self.toast("Nada foi gravado.", "warning")
        if inst and was_running and inst.is_running():
            inst.resume()
        self._refresh_profile_labels()
        self._update_buttons()
        self._refresh_now()

    def _test_macro(self, key):
        inst = self._sel_inst()
        if not inst:
            self.toast("Selecione uma conta primeiro.", "warning")
            return
        steps = (inst.profile.get("extras", {}).get(key, {})
                 .get("macro") or [])
        if not steps:
            self.toast("Grave a macro primeiro.", "warning")
            return
        if inst.is_running():
            self.toast("Pare a conta antes de testar a macro.", "warning")
            return
        repeat = self._int(self.extra_rep[key], MACRO_TASKS[key]["repeat"], 1)
        self._log(f"Testando '{MACRO_TASKS[key]['label']}' ({len(steps)} passos x{repeat})...")
        threading.Thread(target=self._test_macro_thread,
                         args=(inst, steps, repeat), daemon=True).start()

    @staticmethod
    def _test_macro_thread(inst, steps, repeat):
        try:
            if hasattr(inst, "test_macro"):
                inst.test_macro(steps, repeat)
            else:
                inst.stop_event.clear()
                inst.run_macro(steps, repeat)
            inst.log("teste de macro concluído")
        except Exception as exc:
            inst.log(f"teste de macro: {exc}")

    def _clear_macro(self, key):
        inst = self._sel_inst()
        if not inst:
            return
        if not messagebox.askyesno("Limpar macro",
                                   f"Apagar a macro de {MACRO_TASKS[key]['label']}?"):
            return
        inst.profile.setdefault("extras", {}).setdefault(key, {})["macro"] = []
        self.cfg.save()
        self._refresh_profile_labels()
        self._log("Macro apagada.")
        self.toast("Macro apagada.", "info")

    # ── layout ───────────────────────────────────────────────────
    def _apply_grid(self):
        rows = self._int(self.layout_rows, 1, 1)
        cols = self._int(self.layout_cols, 1, 1)
        count = self.manager.apply_grid(rows, cols)
        self._log(f"Grade {rows}x{cols} aplicada em {count} janela(s).")
        self.toast(f"Grade {rows}x{cols} aplicada.", "success")

    # ── câmaras ──────────────────────────────────────────────────
    def _capture_point(self, key):
        inst = self._sel_inst()
        if not inst:
            self.toast("Selecione uma conta primeiro.", "warning")
            return
        self._log(f"Capture o ponto '{key}': mova o mouse e espere 3s...")
        self.toast("Mova o mouse para o ponto; capturando em 3s.", "info")
        self.after(3000, lambda: self._do_capture_point(inst, key))

    def _do_capture_point(self, inst, key):
        point = inst.capture_point(key)
        if point:
            self._log(f"Ponto '{key}' capturado em {point}.")
            self.toast(f"Ponto '{key}' capturado.", "success")
        else:
            self._log(f"Falha ao capturar o ponto '{key}'.")
            self.toast(f"Falha ao capturar '{key}'.", "error")

    def _save_room(self, room):
        inst = self._sel_inst()
        if not inst:
            self.toast("Selecione uma conta primeiro.", "warning")
            return
        if inst.salvar_sala(room):
            self.toast("Referência salva.", "success")
        self._update_buttons()

    def _toggle_search(self, room):
        inst = self._sel_inst()
        if not inst:
            self.toast("Selecione uma conta primeiro.", "warning")
            return
        inst.buscar_sala(room)
        self.after(300, self._tick_rooms)

    def _tick_rooms(self):
        inst = self._sel_inst()
        for key, btn in self.room_search_btns.items():
            active = bool(inst and inst.searching == key)
            color = "#4a2028" if active else "#1f3b4f"
            btn.configure(text="Parar" if active else "Procurar", bg=color)
            btn._base = color
        if not inst:
            self.rooms_info.configure(text="Selecione uma conta na aba Contas.")
        elif inst.searching:
            label = dict(ROOMS).get(inst.searching, inst.searching)
            self.rooms_info.configure(text=f"Procurando: {label}")
        else:
            self.rooms_info.configure(
                text=f"Conta: {inst.title}  •  perfil: {inst.profile_name}")

    # ── launcher ─────────────────────────────────────────────────
    def _save_launcher(self, silent=False):
        path = self.launcher_path.get().strip()
        title = self.launcher_title.get().strip() or "MIR4"
        g1 = [self._int(self.g1x, 0, 0), self._int(self.g1y, 0, 0)]
        g2 = [self._int(self.g2x, 0, 0), self._int(self.g2y, 0, 0)]
        self.cfg.launcher.update(
            path=path, title=title, g1=g1, g2=g2,
            pre=self._num(self.launcher_pre, 3.0, 0.0),
            between=self._num(self.launcher_between, 6.0, 0.0))
        self.cfg.save()
        if not silent:
            self._log("Configuração do launcher salva.")

    def _browse_launcher(self):
        path = filedialog.askopenfilename(
            title="Escolher launcher do MIR4",
            filetypes=[("Executável", "*.exe"), ("Todos", "*.*")])
        if path:
            self.launcher_path.set(path)
            self._save_launcher(silent=True)

    def _launch_one(self):
        self._save_launcher(silent=True)
        path = self.launcher_path.get().strip()
        if not path:
            self.toast("Escolha o caminho do launcher.", "warning")
            return
        try:
            open_launcher(path)
            self._log("Launcher aberto.")
            self.toast("Launcher aberto.", "success")
        except Exception as exc:
            self.toast(f"Erro no launcher: {exc}", "error")

    def _launch_two(self):
        self._save_launcher(silent=True)
        path = self.launcher_path.get().strip()
        if not path:
            self.toast("Escolha o caminho do launcher.", "warning")
            return
        if not path.lower().startswith(("steam://", "http://", "https://")):
            if not os.path.exists(path):
                self.toast("O arquivo do launcher não existe.", "warning")
                return
        g1 = [self._int(self.g1x, 0, 0), self._int(self.g1y, 0, 0)]
        g2 = [self._int(self.g2x, 0, 0), self._int(self.g2y, 0, 0)]
        if g1 == [0, 0] or g2 == [0, 0]:
            self.toast("Capture os botões Game 1 e Game 2 antes.", "warning")
            return
        self._launch_event.set()
        event = threading.Event()
        self._launch_event = event
        args = (
            path, self.launcher_title.get().strip() or "MIR4",
            g1, g2,
            self._num(self.launcher_pre, 3.0, 0.0),
            self._num(self.launcher_between, 6.0, 0.0),
            self.log, event,
        )
        threading.Thread(target=self._launch_two_thread, args=args,
                         daemon=True).start()
        self._log("Abrindo launcher e clicando em Game 1/Game 2...")
        self.toast("Abrindo launcher e os dois jogos...", "info")

    def _launch_two_thread(self, *args):
        try:
            launch_two(*args)
        except Exception as exc:
            self.log(f"Launcher: {exc}")

    def _stop_launch(self):
        self._launch_event.set()
        self._log("Abertura do launcher interrompida.")

    def _capture_launcher(self, which):
        self._save_launcher(silent=True)
        title = self.launcher_title.get().strip() or "MIR4"
        hwnd = find_launcher_window(title, timeout=0.1)
        if not hwnd:
            self.toast("Abra o launcher e deixe a janela visível.", "warning")
            return
        self._log(f"Capture o botão {which.upper()}: mouse sobre ele, 3s...")
        self.toast("Mova o mouse para o botão; capturando em 3s.", "info")
        self.after(3000, lambda: self._do_capture_launcher(which, hwnd))

    def _do_capture_launcher(self, which, hwnd):
        if not win32gui.IsWindow(hwnd):
            self._log("Launcher fechou antes da captura.")
            return
        x, y = client_point(hwnd)
        w, h = client_size(hwnd)
        if not (0 <= x < w and 0 <= y < h):
            self.toast("O mouse não estava sobre o launcher.", "warning")
            return
        if which == "g1":
            self.g1x.set(str(x))
            self.g1y.set(str(y))
        else:
            self.g2x.set(str(x))
            self.g2y.set(str(y))
        self._save_launcher(silent=True)
        self._log(f"Botão {which.upper()} capturado em ({x}, {y}).")
        self.toast(f"Botão {which.upper()} capturado.", "success")

    # ── ajustes / atualização ────────────────────────────────────
    def _test_key(self):
        inst = self._sel_inst()
        if not inst:
            self.toast("Selecione uma conta primeiro.", "warning")
            return
        keys = [part.strip().lower()
                for part in self.test_key_var.get().replace(";", ",").split(",")
                if part.strip()]
        if not keys:
            self.toast("Digite pelo menos uma tecla.", "warning")
            return
        ok = inst.burst(keys)
        self._log(f"Teclas {keys} enviadas: {'ok' if ok else 'falhou'}.")
        self.toast("Teclas enviadas." if ok else "As teclas falharam.",
                   "success" if ok else "error")

    def _check_update_manual(self):
        self._log("Verificando atualização...")
        threading.Thread(target=self._check_update_bg,
                         args=(True,), daemon=True).start()

    def _check_update_bg(self, manual):
        try:
            info = updater.check(APP_VERSION)
        except Exception as exc:
            if manual:
                self.log(f"Atualização: {exc}")
            return
        if not info:
            if manual:
                self.log("Você já está na última versão.")
            return
        self.log(f"Nova versão disponível: {info['tag']}")
        self._ui_actions.put(lambda: self._ask_update(info))

    def _ask_update(self, info):
        notes = (info.get("notes") or "").strip()
        if len(notes) > 700:
            notes = notes[:700] + "..."
        text = f"Nova versão {info['tag']} disponível."
        if notes:
            text += f"\n\n{notes}"
        text += "\n\nBaixar, substituir o exe e reiniciar?"
        if not messagebox.askyesno("Atualização", text):
            return
        threading.Thread(target=self._do_update, args=(info,),
                         daemon=True).start()

    def _do_update(self, info):
        try:
            updater.download_and_restart(info["url"], self.log)
        except Exception as exc:
            self.log(f"Falha na atualização: {exc}")

    def _open_cfg_folder(self):
        try:
            os.makedirs(CFG_DIR, exist_ok=True)
            os.startfile(CFG_DIR)
        except Exception as exc:
            self._log(f"Erro ao abrir a pasta: {exc}")

    # ── atualização da tela ──────────────────────────────────────
    def _refresh_now(self):
        if self._refresh_job:
            try:
                self.after_cancel(self._refresh_job)
            except Exception:
                pass
            self._refresh_job = None
        self._refresh()

    def _refresh(self):
        if self._closing:
            return
        try:
            self.manager.refresh()
        except Exception as exc:
            self.log(f"Erro ao atualizar janelas: {exc}")

        rows = sorted(self.manager.instances.values(),
                      key=lambda inst: window_sort_key(inst.title))
        signature = tuple(
            (inst.hwnd, inst.title, inst.profile_name)
            for inst in rows)
        if signature != self._accounts_sig:
            self._accounts_sig = signature
            self._rebuild_accounts(rows)

        if self._sel not in self.manager.instances:
            self._sel = rows[0].hwnd if rows else None
            if self._sel:
                inst = self.manager.instances[self._sel]
                self.profile_var.set(inst.profile_name)
                self._load_ui(inst.profile)

        if rows:
            self.empty_state.pack_forget()
        else:
            self.empty_state.pack(fill="both", expand=True, pady=40)

        for inst in rows:
            card = self.account_cards.get(inst.hwnd)
            if card:
                card.set_data(inst, inst.hwnd == self._sel,
                              self._pulse_phase)

        if self._panic_flag.is_set():
            self._panic_flag.clear()
            self._panic()
        self._tick_rooms()
        self._update_buttons()
        self._update_xp_ui()
        if self._refresh_job:
            try:
                self.after_cancel(self._refresh_job)
            except Exception:
                pass
        self._refresh_job = self.after(1200, self._refresh)

    def _rebuild_accounts(self, rows):
        for card in self.account_cards.values():
            try:
                card.destroy()
            except Exception:
                pass
        self.account_cards.clear()
        for inst in rows:
            card = AccountCard(self.account_list, self, inst.hwnd)
            card.pack(fill="x", padx=4, pady=4)
            self.account_cards[inst.hwnd] = card

    def _update_buttons(self):
        inst = self._sel_inst()
        if inst:
            mode = MODE_LABELS.get(inst.profile.get("mode", "off"), "?")
            self.tasks_info.configure(
                text=f"Editando '{inst.profile_name}' — {inst.title}")
            self.profile_hint.configure(
                text=f"Vínculo: {inst.profile.get('window') or 'sem vínculo'}  •  "
                     "cada perfil guarda modo, macros e pontos.")
            self.detail_title.configure(text=inst.title)
            self.detail_sub.configure(
                text=f"Perfil {inst.profile_name}  ·  {mode}")
            state = inst.status()
            self.detail_status.configure(
                text=state.upper(),
                fg={"rodando": GREEN, "pausado": ORANGE}.get(state, MUTED))
            self.profile_combo.set_state("normal")

            mode_key = inst.profile.get("mode", "off")
            interval = (inst.profile.get("mode_cfg", {}).get(mode_key, {})
                        .get("interval"))
            try:
                interval = float(interval or 0)
            except Exception:
                interval = 0
            if mode_key == "off":
                self.summary_mode.configure(text="Modo: desligado")
            else:
                self.summary_mode.configure(
                    text=f"Modo: {mode}  ·  a cada {interval:g}s")
            active = []
            extras = inst.profile.get("extras", {})
            if extras.get("buff", {}).get("on"):
                active.append("Buff")
            steps = 0
            for key, meta in MACRO_TASKS.items():
                cfg = extras.get(key, {})
                if cfg.get("on"):
                    active.append(meta["label"])
                steps += len(cfg.get("macro") or [])
            self.summary_extras.configure(
                text="Extras: " + (", ".join(active) if active else "nenhum")
                     + f"  ·  {steps} passos de macro")
            refs = len(inst.profile.get("refs") or {})
            self.summary_rooms.configure(
                text=f"Câmaras: {refs} referência"
                     + ("" if refs == 1 else "s"))
            width, height = client_size(inst.hwnd)
            self.detail_hwnd.configure(
                text=f"HWND {inst.hwnd}  ·  PID {inst.win.pid}")
            self.detail_client.configure(
                text=f"Cliente {width}×{height}  ·  "
                     f"{inst.win.exe or 'processo protegido'}")
        else:
            self.tasks_info.configure(
                text="Selecione uma conta para editar o perfil.")
            self.profile_hint.configure(
                text="Cada perfil guarda modo, intervalos, macros e pontos.")
            self.detail_title.configure(text="Nenhuma conta")
            self.detail_sub.configure(text="Selecione uma conta à esquerda.")
            self.detail_status.configure(text="SEM CONEXÃO", fg=MUTED)
            self.profile_combo.set_state("disabled")
            self.summary_mode.configure(text="Modo: —")
            self.summary_extras.configure(text="Extras: —")
            self.summary_rooms.configure(text="Câmaras: —")
            self.detail_hwnd.configure(text="HWND —")
            self.detail_client.configure(text="Cliente —")

        running = bool(inst and inst.is_running())
        paused = bool(inst and inst.status() == "pausado")
        self.btn_start.configure(
            state="normal" if inst and not running else "disabled")
        self.btn_start_all.configure(
            state="normal" if self.manager.instances else "disabled")
        self.btn_pause.configure(
            state="normal" if running else "disabled",
            text="Retomar" if paused else "Pausar")
        self.btn_stop.configure(state="normal" if running else "disabled")
        self.btn_stop_all.configure(
            state="normal" if self.manager.instances else "disabled")
        for btn in self.room_search_btns.values():
            btn.configure(state="normal" if inst else "disabled")
        for btn in self.room_save_btns.values():
            btn.configure(state="normal" if inst else "disabled")
        recording = bool(self._recorder and self._recorder.is_alive())
        for key, buttons in self.macro_buttons.items():
            is_rec = recording and self._record_task == key
            pending = bool(self._record_task == key and not recording)
            color = "#4a2a2a" if (is_rec or pending) else "#3a2a18"
            buttons["record"].configure(
                state="normal" if inst else "disabled",
                text="Parar (F9)" if is_rec else ("Cancelar" if pending else "Gravar"),
                bg=color)
            buttons["record"]._base = color
            steps = []
            if inst:
                steps = (inst.profile.get("extras", {}).get(key, {})
                         .get("macro") or [])
            buttons["test"].configure(
                state="normal" if inst and steps and not running else "disabled")
            buttons["clear"].configure(
                state="normal" if inst and steps else "disabled")
        self.admin_label.configure(
            text="● ADMIN" if is_admin() else "● SEM ADMIN",
            fg=GREEN if is_admin() else ORANGE)
        total = len(self.manager.instances)
        running_count = sum(1 for item in self.manager.instances.values()
                            if item.is_running())
        self.chip_accounts.configure(
            text=f"● {total} conta" + ("" if total == 1 else "s"))
        self.chip_running.configure(
            text=f"● {running_count} rodando",
            fg=GREEN if running_count else MUTED)
        for hwnd, card in self.account_cards.items():
            item = self.manager.instances.get(hwnd)
            if item:
                card.set_data(item, hwnd == self._sel, self._pulse_phase)
        self._refresh_profile_labels()
        self._update_ultimate_hint()

    # ── log e threads ────────────────────────────────────────────
    def log(self, message):
        self.log_queue.put(str(message))

    def _log(self, message):
        self.log(message)

    def _drain_loop(self):
        while True:
            try:
                message = self.log_queue.get_nowait()
            except queue.Empty:
                break
            self._append_log(message)
        while True:
            try:
                action = self._ui_actions.get_nowait()
            except queue.Empty:
                break
            try:
                action()
            except Exception as exc:
                self._append_log(f"Erro na interface: {exc}")
        if not self._closing:
            self.after(120, self._drain_loop)

    def _append_log(self, message):
        stamp = time.strftime("%H:%M:%S")
        low = message.lower()
        if any(w in low for w in ("erro", "falha", "exception", "corromp")):
            tag = "error"
        elif any(w in low for w in ("iniciado", "salva", "conclu", "aberto", "ok")):
            tag = "success"
        elif any(w in low for w in ("aviso", "pausado", "cancel", "vazia", "sem ")):
            tag = "warn"
        else:
            tag = "muted"
        self.log_text.config(state="normal")
        self.log_text.insert("end", f"[{stamp}] {message}\n", tag)
        lines = int(self.log_text.index("end-1c").split(".")[0])
        if lines > 800:
            self.log_text.delete("1.0", f"{lines - 799}.0")
        self.log_text.see("end")
        self.log_text.config(state="disabled")

    def _hotkey_loop(self):
        def run():
            last = False
            while not self._closing:
                try:
                    pressed = bool(win32api.GetAsyncKeyState(VK_F12) & 0x8000)
                except Exception:
                    pressed = False
                if pressed and not last:
                    self._panic_flag.set()
                last = pressed
                time.sleep(0.08)

        threading.Thread(target=run, daemon=True).start()

    def _startup(self):
        if not is_admin():
            self.log("AVISO: rode como administrador para o jogo receber as teclas.")
        self.log(f"{APP_NAME} {APP_VERSION} pronto. F12 = pânico, F9 = parar gravação.")
        self.log(f"Configuração: {CFG_DIR}")
        self.toast("Pronto. F12 para tudo, F9 para a gravação.", "info")
        threading.Thread(target=self._check_update_bg,
                         args=(False,), daemon=True).start()

    def _quit(self):
        if self._closing:
            return
        self._closing = True
        try:
            if self._recorder:
                self._recorder.stop()
            if getattr(self, '_sniffer', None):
                self._sniffer.stop()
            self.manager.stop_all()
        finally:
            self.destroy()

    # ── util ─────────────────────────────────────────────────────
    @staticmethod
    def _num(var, default, minimum=0.0):
        try:
            value = float(str(var.get()).replace(",", "."))
        except Exception:
            return default
        return max(minimum, value)

    @staticmethod
    def _fmt_num(value):
        try:
            number = float(value)
            return str(int(number)) if number.is_integer() else f"{number:g}"
        except Exception:
            return str(value)

    @staticmethod
    def _int(var, default, minimum=0):
        try:
            value = int(float(str(var.get()).replace(",", ".")))
        except Exception:
            return default
        return max(minimum, value)


def main():
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
