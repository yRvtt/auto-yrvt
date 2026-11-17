"""Testes rápidos do núcleo do Auto yRvt.

Rodam sem abrir o jogo e sem tocar na configuração real do usuário.
"""

import json
import os
import tempfile
import threading
import unittest

from auto_yrvt import core


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="auto_yrvt_test_")

    def _paths(self, name="state.json"):
        return {
            "path": os.path.join(self.tmp, name),
            "legacy": os.path.join(self.tmp, "salas.json"),
            "old_state": os.path.join(self.tmp, "old_state.json"),
        }

    def test_profile_roundtrip_and_binding(self):
        paths = self._paths()
        cfg = core.Config(**paths)
        profile = cfg.ensure_profile("conta1")
        profile["mode"] = "farm"
        profile["mode_cfg"]["farm"]["ultimate"] = True
        profile["extras"]["doar_cla"]["macro"] = [
            {"t": "key", "k": "q", "delay": 0.1, "hold": 0.06}
        ]
        cfg.bind("conta1", "Mir4G[1]")
        cfg.save()

        reloaded = core.Config(**paths)
        self.assertEqual(reloaded.profile_for_title("Mir4G[1]"), "conta1")
        self.assertEqual(reloaded.profiles["conta1"]["mode"], "farm")
        self.assertTrue(
            reloaded.profiles["conta1"]["extras"]["doar_cla"]["macro"])

    def test_old_state_is_copied(self):
        paths = self._paths()
        old = {
            "version": 2,
            "profiles": {"antigo": core.default_profile("antigo")},
            "layout": {"rows": 1, "cols": 2},
            "launcher": {},
        }
        with open(paths["old_state"], "w", encoding="utf-8") as f:
            json.dump(old, f)

        cfg = core.Config(**paths)
        self.assertIn("antigo", cfg.profiles)
        self.assertTrue(os.path.exists(paths["path"]))
        # o arquivo antigo não é apagado
        self.assertTrue(os.path.exists(paths["old_state"]))

    def test_legacy_salas_import(self):
        paths = self._paths()
        with open(paths["legacy"], "w", encoding="utf-8") as f:
            json.dump({"pts": {"nome": [10, 20]}, "refs": {"xp1": "abc"}}, f)

        cfg = core.Config(**paths)
        profile = cfg.profiles["default"]
        self.assertEqual(profile["points"]["nome"], [10, 20])
        self.assertEqual(profile["refs"]["xp1"], "abc")


class ImageTests(unittest.TestCase):
    def test_crop_and_diff(self):
        width, height = 8, 4
        data = bytearray(width * height * 4)
        for y in range(height):
            for x in range(width):
                index = ((height - 1 - y) * width + x) * 4
                data[index:index + 4] = bytes([x * 10, y * 10, 50, 255])
        frame = bytes(data)
        cropped = core.crop(width, height, frame, 2, 1, 3, 2)
        self.assertEqual(len(cropped), 3 * 2 * 4)
        self.assertEqual(core.diff(cropped, cropped), 0.0)
        self.assertEqual(core.diff(cropped, cropped[:10]), 999.0)


class MiscTests(unittest.TestCase):
    def test_window_sort_is_natural(self):
        titles = ["Mir4G[10]", "Mir4G[2]", "Mir4G[1]"]
        self.assertEqual(
            sorted(titles, key=core.window_sort_key),
            ["Mir4G[1]", "Mir4G[2]", "Mir4G[10]"])

    def test_macro_replay_order(self):
        instance = object.__new__(core.Instance)
        instance.stop_event = threading.Event()
        instance.pause_event = threading.Event()
        calls = []
        instance.tap = lambda key, hold=0.06: calls.append(("key", key)) or True
        instance.click_rel = lambda x, y: calls.append(("click", x, y)) or True
        instance.run_macro([
            {"t": "click", "x": 0.5, "y": 0.5, "delay": 0.01},
            {"t": "key", "k": "e", "delay": 0.01},
        ], 2)
        self.assertEqual(
            calls,
            [("click", 0.5, 0.5), ("key", "e")] * 2)

    def test_ultimate_is_retried_between_farm_cycles(self):
        class Manager:
            cfg = type("Cfg", (), {"save": lambda _self: None})()

            def log(self, _message):
                pass

        class Win:
            hwnd = 1
            title = "fake"

        profile = core.default_profile("teste")
        profile["mode"] = "farm"
        profile["mode_cfg"]["farm"]["interval"] = 100.0
        profile["mode_cfg"]["farm"]["ultimate"] = True
        profile["mode_cfg"]["farm"]["ultimate_key"] = "r"
        profile["mode_cfg"]["farm"]["ultimate_interval"] = 0.3
        instance = core.Instance(Manager(), Win(), profile, "teste")
        calls = []
        instance.tap = lambda key, hold=0.06: calls.append(key) or True
        instance.click_rel = lambda _x, _y: True
        instance.start()
        try:
            import time
            time.sleep(2.0)
        finally:
            instance.stop()
            if instance.thread:
                instance.thread.join(timeout=1.0)
        # deve tentar R várias vezes, não só uma por ciclo de farm
        self.assertGreaterEqual(calls.count("r"), 2)


if __name__ == "__main__":
    unittest.main()
