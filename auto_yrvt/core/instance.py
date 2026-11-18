"""Instance (automação por janela) e Recorder (gravação de macro)."""

import base64
import random
import threading
import time

import win32api
import win32gui

from .capture import (
    ROOM_H, ROOM_MATCH, ROOM_W,
    crop, diff, grab, is_black,
)
from .config import MACRO_TASKS, REFRESH_WAIT
from .input import (
    VK_BY_NAME, VK_F9, VK_LBUTTON,
    click_at, client_point, client_size, key_code, post_key,
)


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

    def _loop(self):
        while not self.stop_event.is_set():
            if self.pause_event.is_set():
                self.stop_event.wait(0.2)
                continue
            now = time.time()
            mode = self.profile.get("mode", "off")

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

    def salvar_sala(self, room):
        point = self.point_px("nome")
        if not point:
            self.log("[sala] capture o ponto do nome primeiro")
            return False
        frame = grab(self.hwnd)
        if not frame or is_black(frame[2]):
            self.log("[sala] captura preta — jogo minimizado?")
            return False
        fw, fh = frame[0], frame[1]
        x, y = point
        cw = min(ROOM_W, fw - x)
        ch = min(ROOM_H, fh - y)
        if cw < 50 or ch < 10:
            self.log("[sala] ponto muito perto da borda; recorte incompleto")
            return False
        ref = crop(fw, fh, frame[2], x, y, cw, ch)
        self.profile.setdefault("refs", {})[room] = {
            "data": base64.b64encode(ref).decode(),
            "w": cw, "h": ch,
            "ref_w": fw, "ref_h": fh,
        }
        self.manager.cfg.save()
        self.log(f"[sala] referência salva: {room} ({cw}x{ch} em {fw}x{fh})")
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
        self.search_thread = threading.Thread(
            target=self._buscar_sala, args=(room,), daemon=True)
        self.search_thread.start()

    def _buscar_sala(self, room):
        try:
            point = self.point_px("amovivel")
            name_point = self.point_px("nome")
            if not point or not name_point:
                self.log("[sala] faltam pontos (Amovível / Nome)")
                return
            ref_entry = (self.profile.get("refs") or {}).get(room)
            if not ref_entry:
                self.log(f"[sala] salve a referência de {room} primeiro")
                return
            if isinstance(ref_entry, str):
                try:
                    ref = base64.b64decode(ref_entry)
                except Exception:
                    self.log("[sala] referência corrompida; salve de novo")
                    return
                ref_cw, ref_ch = ROOM_W, ROOM_H
                ref_fw, ref_fh = 0, 0
            else:
                try:
                    ref = base64.b64decode(ref_entry["data"])
                    ref_cw = ref_entry.get("w", ROOM_W)
                    ref_ch = ref_entry.get("h", ROOM_H)
                    ref_fw = ref_entry.get("ref_w", 0)
                    ref_fh = ref_entry.get("ref_h", 0)
                except Exception:
                    self.log("[sala] referência corrompida; salve de novo")
                    return
            best_score = 999.0
            attempts = 0
            while (not self.search_stop.is_set()
                   and self.searching == room):
                if not self.ensure():
                    self.log("[sala] janela fechou; busca encerrada")
                    return
                attempts += 1
                if attempts > 500:
                    self.log(f"[sala] limite de 500 tentativas "
                             f"(melhor score: {best_score:.0f})")
                    return
                click_at(self.hwnd, *point)
                time.sleep(REFRESH_WAIT)
                frame = grab(self.hwnd)
                if not frame or is_black(frame[2]):
                    self.log("[sala] captura preta — jogo minimizado?")
                    return
                fw, fh = frame[0], frame[1]
                if ref_fw > 0 and ref_fh > 0:
                    cw = max(50, int(ref_cw * fw / ref_fw))
                    ch = max(10, int(ref_ch * fh / ref_fh))
                else:
                    cw = min(ROOM_W, fw - name_point[0])
                    ch = min(ROOM_H, fh - name_point[1])
                current = crop(fw, fh, frame[2],
                               name_point[0], name_point[1], cw, ch)
                score = diff(current, ref)
                best_score = min(best_score, score)
                if score <= ROOM_MATCH:
                    enter = self.point_px("entrar")
                    if enter:
                        click_at(self.hwnd, *enter)
                    self.log(f"[sala] {room} achada em {attempts} "
                             f"tentativas (score {score:.0f})")
                    return
                if attempts <= 3 or attempts % 10 == 0:
                    self.log(f"[sala] tentativa {attempts}: "
                             f"score {score:.0f} (melhor: {best_score:.0f})")
                time.sleep(0.3)
        finally:
            if self.searching == room:
                self.searching = None
            if self.search_paused:
                self.search_paused = False
                if self.is_running():
                    self.pause_event.clear()
                    self.log("[sala] busca encerrada; instância retomada")
