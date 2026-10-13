#!/usr/bin/env python3
"""Install or remove CUPS Tray Icon for the current desktop user."""
import argparse
import os
from pathlib import Path
import shutil

FILES = ("cups_tray_icon.py", "LICENSE", "printer.svg", "label.svg",
         "cups-tray-icon.svg", "printer-ready.svg", "printer-warning.svg",
         "printer-error.svg", "printer-offline.svg")
ICON_FILES = ("cups-tray-icon.svg", "printer-ready.svg", "printer-warning.svg",
              "printer-error.svg", "printer-offline.svg")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    options = parser.add_mutually_exclusive_group()
    options.add_argument("--autostart", action="store_true", help="also start the tray icon at login")
    options.add_argument("--uninstall", action="store_true", help="remove the app, preserving local config")
    args = parser.parse_args()
    data_home = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share")
    config_home = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    destination = data_home / "cups-tray-icon"
    icons = data_home / "icons/hicolor/scalable/apps"
    desktop = data_home / "applications/cups-tray-icon.desktop"
    autostart = config_home / "autostart/cups-tray-icon.desktop"
    if args.uninstall:
        for path in (desktop, autostart, *(destination / name for name in FILES),
                     *(icons / name for name in ICON_FILES)):
            path.unlink(missing_ok=True)
        for directory in (destination, icons):
            if directory.exists() and not any(directory.iterdir()):
                directory.rmdir()
        print("Removed CUPS Tray Icon. Log out to stop a running instance. Local config was preserved.")
        return
    destination.mkdir(parents=True, exist_ok=True)
    icons.mkdir(parents=True, exist_ok=True)
    for name in FILES:
        shutil.copyfile(Path(__file__).with_name(name), destination / name)
    for name in ICON_FILES:
        shutil.copyfile(Path(__file__).with_name(name), icons / name)
    executable = str(destination / "cups_tray_icon.py").replace("%", "%%")
    for char in ("\\", '"', "`", "$"):
        executable = executable.replace(char, "\\" + char)
    executable = executable.replace("\\", "\\\\")
    entry = ("[Desktop Entry]\nType=Application\nName=CUPS Tray Icon\n"
             "Comment=See the status of your local printers in plain English\n"
             f'Exec=/usr/bin/python3 "{executable}"')
    footer = "\nIcon=cups-tray-icon\nTerminal=false\nCategories=System;Monitor;\nStartupNotify=false\n"
    desktop.parent.mkdir(parents=True, exist_ok=True)
    desktop.write_text(entry + " --show" + footer)
    if args.autostart or autostart.exists():
        autostart.parent.mkdir(parents=True, exist_ok=True)
        autostart.write_text(entry + footer)
    print("Installed CUPS Tray Icon. Open it from your desktop application menu.")


if __name__ == "__main__":
    main()
