#!/usr/bin/env python3
"""Install or remove CUPS Tray Icon for the current desktop user."""
import argparse
import os
from pathlib import Path
import shutil


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    options = parser.add_mutually_exclusive_group()
    options.add_argument("--autostart", action="store_true", help="also start the tray icon at login")
    options.add_argument("--uninstall", action="store_true", help="remove the app, preserving local config")
    args = parser.parse_args()
    data_home = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share")
    config_home = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    destination = data_home / "cups-tray-icon"
    desktop = data_home / "applications/cups-tray-icon.desktop"
    autostart = config_home / "autostart/cups-tray-icon.desktop"
    files = ("cups_tray_icon.py", "printer.svg", "label.svg", "LICENSE")
    if args.uninstall:
        for path in (desktop, autostart, *(destination / name for name in files)):
            path.unlink(missing_ok=True)
        if destination.exists() and not any(destination.iterdir()):
            destination.rmdir()
        print("Removed CUPS Tray Icon. Log out to stop a running instance. Local config was preserved.")
        return
    destination.mkdir(parents=True, exist_ok=True)
    for name in files:
        shutil.copyfile(Path(__file__).with_name(name), destination / name)
    executable = str(destination / "cups_tray_icon.py").replace("%", "%%")
    for char in ("\\", '"', "`", "$"):
        executable = executable.replace(char, "\\" + char)
    executable = executable.replace("\\", "\\\\")
    entry = ("[Desktop Entry]\nType=Application\nName=CUPS Tray Icon\n"
             "Comment=Local printer queues and readable error messages\n"
             f'Exec=/usr/bin/python3 "{executable}"')
    footer = "\nIcon=printer\nTerminal=false\nCategories=System;Monitor;\nStartupNotify=false\n"
    desktop.parent.mkdir(parents=True, exist_ok=True)
    desktop.write_text(entry + " --show" + footer)
    if args.autostart or autostart.exists():
        autostart.parent.mkdir(parents=True, exist_ok=True)
        autostart.write_text(entry + footer)
    print("Installed CUPS Tray Icon. Open it from your desktop application menu.")


if __name__ == "__main__":
    main()
