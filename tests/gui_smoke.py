"""Check the actual GTK demo and exported tray interface in a private session bus."""
from pathlib import Path
import subprocess
import sys
import time

from gi.repository import Gio, GLib

process = subprocess.Popen([sys.executable, str(Path(__file__).resolve().parents[1] / "cups_tray_icon.py"), "--demo"])
bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
try:
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("GTK application exited before the tray was ready")
        try:
            response = bus.call_sync("io.github.M1chlCZ.CupsTrayIcon.Demo", "/StatusNotifierItem",
                "org.freedesktop.DBus.Properties", "GetAll",
                GLib.Variant("(s)", ("org.kde.StatusNotifierItem",)), None,
                Gio.DBusCallFlags.NONE, 1000, None)
            properties = response.unpack()[0]
            if properties["IconName"] == "printer-error" and "2 queues" in properties["ToolTip"][3]:
                break
        except GLib.Error:
            pass
        time.sleep(0.1)
    else:
        raise RuntimeError("Tray did not publish the demo printer state")
    bus.call_sync("io.github.M1chlCZ.CupsTrayIcon.Demo", "/StatusNotifierItem",
        "org.kde.StatusNotifierItem", "Activate", GLib.Variant("(ii)", (0, 0)), None,
        Gio.DBusCallFlags.NONE, 1000, None)
    print("GTK demo rendered two queues and exported an activatable tray item.")
finally:
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
