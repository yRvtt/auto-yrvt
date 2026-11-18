"""Configuração, perfis e constantes do jogo."""

import json
import os
import shutil
import threading

from .. import __app_name__ as APP_NAME, __version__ as APP_VERSION

ROOMS = [("xp1", "XP 1"), ("xp2", "XP 2"), ("xp3", "XP 3"), ("anti", "Anti-Demônio")]
REFRESH_WAIT = 1.0

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

MIR4_PROCESS_NAMES = {
    "mir4.exe", "mir4g.exe", "mir4gclient.exe", "mir4s.exe",
    "mir4-win64-shipping.exe", "mir4g-win64-shipping.exe",
    "mir4client.exe",
}

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
                if match and not match.endswith("]") and match in low:
                    return name
            if len(self.profiles) == 1:
                return next(iter(self.profiles))
            return "default"
