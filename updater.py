"""
Auto-update via GitHub Releases.

O app só checa/download no exe (PyInstaller). Em dev (python mir4_bot.py)
as funções retornam sem fazer nada.
"""

import json
import os
import subprocess
import sys
import tempfile
import urllib.request

# ── configure aqui o repo ────────────────────────────────────────
OWNER = "yRvtt"
REPO = "mir4-bot"
API = f"https://api.github.com/repos/{OWNER}/{REPO}/releases/latest"
UA = "mir4-bot-updater"
TIMEOUT = 15


def is_frozen():
    return bool(getattr(sys, "frozen", False))


def _ver(v):
    """'v1.2.3' -> (1, 2, 3)"""
    out = []
    for part in (v or "").strip().lstrip("vV").split("."):
        try:
            out.append(int(part))
        except ValueError:
            out.append(0)
    while len(out) < 3:
        out.append(0)
    return tuple(out[:3])


def check(current_version):
    """Retorna {'tag', 'url', 'notes'} se houver versão nova, senão None."""
    req = urllib.request.Request(
        API, headers={"User-Agent": UA, "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        data = json.load(r)

    tag = data.get("tag_name") or ""
    if _ver(tag) <= _ver(current_version):
        return None

    asset = next(
        (a for a in data.get("assets", [])
         if (a.get("name") or "").lower().endswith(".exe")), None)
    if not asset:
        return None

    return {
        "tag": tag,
        "url": asset["browser_download_url"],
        "notes": data.get("body") or "",
    }


def download_and_restart(url, log=None):
    """Baixa o novo exe e agenda a troca pra quando este processo sair."""
    if not is_frozen():
        raise RuntimeError("auto-update só funciona no .exe")

    log = log or (lambda m: None)
    exe = os.path.abspath(sys.executable)
    tmpdir = tempfile.gettempdir()
    new_exe = os.path.join(tmpdir, "mir4bot_new.exe")
    bat = os.path.join(tmpdir, "mir4bot_update.bat")

    log("Baixando atualização...")
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=120) as r, open(new_exe, "wb") as f:
        while True:
            chunk = r.read(65536)
            if not chunk:
                break
            f.write(chunk)

    pid = os.getpid()
    script = f"""@echo off
:wait
tasklist /FI "PID eq {pid}" 2>nul | find /I "{pid}" >nul
if not errorlevel 1 (
  ping -n 2 127.0.0.1 >nul
  goto wait
)
set /a tries=0
:move
move /y "{new_exe}" "{exe}" >nul 2>&1
if errorlevel 1 (
  set /a tries+=1
  if %tries% lss 15 (
    ping -n 2 127.0.0.1 >nul
    goto move
  )
)
start "" "{exe}"
del "%~f0"
"""
    with open(bat, "w", encoding="oem", errors="replace") as f:
        f.write(script)

    log("Aplicando atualização e reiniciando...")
    subprocess.Popen(
        ["cmd.exe", "/c", bat],
        creationflags=0x00000008 | 0x08000000,  # DETACHED_PROCESS | CREATE_NO_WINDOW
        close_fds=True,
    )
