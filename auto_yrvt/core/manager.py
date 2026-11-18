"""Manager (multi-instância) e launcher (UIAutomation)."""

import os
import subprocess
import threading
import time

import win32con
import win32gui

from .config import Config
from .input import click_at, client_size, work_area
from .instance import Instance
from .window import (
    WindowInfo, find_launcher_window, list_mir4_windows, wait_game_window,
)


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


def launch_two(path, title, g1, g2, pre=3.0, between=6.0,
               log=None, stop_event=None):
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
        launcher = find_launcher_window(title, timeout=20,
                                        stop_event=stop_event)
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
