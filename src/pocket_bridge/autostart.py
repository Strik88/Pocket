"""Start Pocket Bridge (tray icon, no browser) when the user logs in.

macOS: a LaunchAgent in ~/Library/LaunchAgents
Windows: a value under HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run
Linux: a .desktop file in ~/.config/autostart
"""

from __future__ import annotations

import os
import plistlib
import sys
from pathlib import Path

from .config import config_dir

LABEL = "nl.pocketbridge.app"
WIN_VALUE = "PocketBridge"


def _python() -> str:
    """pythonw.exe on Windows so no console window appears."""
    exe = Path(sys.executable)
    if sys.platform == "win32":
        w = exe.with_name("pythonw.exe")
        if w.exists():
            return str(w)
    return str(exe)


def command() -> list[str]:
    return [_python(), "-m", "pocket_bridge", "tray", "--no-browser"]


def _mac_plist() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def _linux_desktop() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "autostart" / "pocket-bridge.desktop"


def is_enabled() -> bool:
    if sys.platform == "darwin":
        return _mac_plist().exists()
    if sys.platform == "win32":
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run") as key:
                winreg.QueryValueEx(key, WIN_VALUE)
                return True
        except OSError:
            return False
    return _linux_desktop().exists()


def enable() -> str:
    cmd = command()
    if sys.platform == "darwin":
        path = _mac_plist()
        path.parent.mkdir(parents=True, exist_ok=True)
        log = config_dir() / "pocket-bridge.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        with path.open("wb") as f:
            plistlib.dump(
                {
                    "Label": LABEL,
                    "ProgramArguments": cmd,
                    "RunAtLoad": True,
                    "ProcessType": "Interactive",
                    "StandardOutPath": str(log),
                    "StandardErrorPath": str(log),
                    "EnvironmentVariables": {"POCKET_BRIDGE_HOME": str(config_dir())},
                },
                f,
            )
        return str(path)
    if sys.platform == "win32":
        import subprocess
        import winreg

        value = subprocess.list2cmdline(cmd)
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, WIN_VALUE, 0, winreg.REG_SZ, value)
        return r"HKCU\Software\Microsoft\Windows\CurrentVersion\Run\PocketBridge"
    path = _linux_desktop()
    path.parent.mkdir(parents=True, exist_ok=True)
    exec_line = " ".join(f'"{c}"' if " " in c else c for c in cmd)
    path.write_text(
        "[Desktop Entry]\nType=Application\nName=Pocket Bridge\n"
        f"Exec=env POCKET_BRIDGE_HOME=\"{config_dir()}\" {exec_line}\nX-GNOME-Autostart-enabled=true\n",
        encoding="utf-8",
    )
    return str(path)


def disable() -> None:
    if sys.platform == "darwin":
        _mac_plist().unlink(missing_ok=True)
    elif sys.platform == "win32":
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_SET_VALUE) as key:
                winreg.DeleteValue(key, WIN_VALUE)
        except OSError:
            pass
    else:
        _linux_desktop().unlink(missing_ok=True)


def set_enabled(on: bool) -> bool:
    enable() if on else disable()
    return is_enabled()
