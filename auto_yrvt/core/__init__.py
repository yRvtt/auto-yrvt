"""Núcleo do Auto yRvt — pacote dividido em submódulos."""

from .capture import (
    ROOM_H, ROOM_MATCH, ROOM_W,
    crop, diff, grab, is_black,
)
from .config import (
    APP_NAME, APP_VERSION, CFG_DIR, LEGACY_DIR, LEGACY_FILE, LEGACY_STATE,
    MACRO_TASKS, MIR4_PROCESS_NAMES, MODE_DEFAULT_INTERVAL, MODES,
    REFRESH_WAIT, ROOMS, STATE_FILE,
    Config, default_extras, default_mode_cfg, default_profile,
    format_duration, normalize_profile, xp_rate,
)
from .input import (
    EXT_KEYS, SCAN, VK_BY_NAME, VK_F9, VK_F12, VK_LBUTTON,
    click_at, client_point, client_size, key_code, post_key, work_area,
)
from .instance import Instance, Recorder
from .manager import (
    Manager, launch_two, open_launcher, uia_available, uia_click,
)
from .window import (
    WindowInfo, find_launcher_window, is_admin, list_mir4_windows,
    process_name, wait_game_window, window_sort_key,
)

__all__ = [
    "APP_NAME", "APP_VERSION", "CFG_DIR", "Config", "EXT_KEYS",
    "Instance", "LEGACY_DIR", "LEGACY_FILE", "LEGACY_STATE",
    "MACRO_TASKS", "MIR4_PROCESS_NAMES", "MODE_DEFAULT_INTERVAL",
    "MODES", "Manager", "REFRESH_WAIT", "ROOM_H", "ROOM_MATCH",
    "ROOM_W", "ROOMS", "Recorder", "SCAN", "STATE_FILE",
    "VK_BY_NAME", "VK_F12", "VK_F9", "VK_LBUTTON", "WindowInfo",
    "click_at", "client_point", "client_size", "crop", "default_extras",
    "default_mode_cfg", "default_profile", "diff", "find_launcher_window",
    "format_duration", "grab", "is_admin", "is_black", "key_code",
    "launch_two", "list_mir4_windows", "normalize_profile", "open_launcher",
    "post_key", "process_name", "uia_available", "uia_click",
    "wait_game_window", "window_sort_key", "work_area", "xp_rate",
]
