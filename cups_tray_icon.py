#!/usr/bin/python3
"""Read-only CUPS printer status for Linux desktops."""
import json
import os
import re
import subprocess
import sys
import threading
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

REASONS = {
    "media-empty": "Load paper or labels into the printer.",
    "media-needed": "The printer needs paper or labels before it can continue.",
    "media-jam": "Paper is jammed. Clear the jam and close the cover.",
    "media-low": "Paper or labels are running low.",
    "cover-open": "Close the printer cover firmly.",
    "door-open": "Close the printer door firmly.",
    "offline": "The printer is offline. Check its power and connection.",
    "connecting-to-device": "CUPS is trying to reach the printer. Check its power and connection.",
    "paused": "The print queue is paused. Fix any printer problem, then resume the queue in CUPS.",
    "cups-filter-error": "CUPS could not prepare the document. The print driver or document needs checking.",
    "cups-missing-filter": "A required print driver component is missing.",
    "marker-supply-empty": "Ink, toner or ribbon is empty. Check the printer's supplies.",
    "marker-supply-low": "Ink, toner or ribbon is running low.",
    "toner-empty": "Replace the empty toner cartridge.",
    "toner-low": "Toner is running low.",
    "marker-waste-full": "The waste container is full and needs attention.",
    "spool-area-full": "The print server has run out of space for print jobs.",
    "cups-held-for-authentication": "A print job is waiting for permission or a password.",
    "timed-out": "The printer did not respond in time. Check its power and connection.",
    "shutdown": "The printer is shutting down.",
}


def printer_card(name, attrs, jobs, usb_serials=None, usb_serial=None):
    own_jobs = [job for job in jobs.values()
                if unquote(urlsplit(job.get("job-printer-uri", "")).path.rsplit("/", 1)[-1]) == name
                and job.get("job-state", 3) in (3, 4, 5, 6)]
    held = sum(job.get("job-state") == 4 for job in own_jobs)
    stopped = sum(job.get("job-state") == 6 for job in own_jobs)
    state = {3: "Idle", 4: "Printing", 5: "Paused"}.get(attrs.get("printer-state"), "Unknown")
    severity = "error" if state == "Paused" else "ok"
    messages = []
    reasons = attrs.get("printer-state-reasons", [])
    if isinstance(reasons, str):
        reasons = [reasons]
    for reason in reasons:
        if reason == "none":
            continue
        key = reason if reason in REASONS else re.sub(r"-(error|warning|report)$", "", reason)
        messages.append(REASONS.get(key, "The printer reported: " + key + ". See the details below."))
        level = "warning" if reason.endswith("-warning") or key.endswith("-low") else "error"
        if severity != "error":
            severity = level
    raw_message = attrs.get("printer-state-message", "")
    if not messages and raw_message:
        if "filter failed" in raw_message.lower():
            messages.append(REASONS["cups-filter-error"])
            severity = "error"
        else:
            messages.append("CUPS reported a message. See the details below.")
            if severity != "error":
                severity = "warning"
    if state == "Paused" and not messages:
        messages.append(REASONS["paused"])
    if attrs.get("printer-is-accepting-jobs") is False:
        messages.append("This queue is not accepting new print jobs.")
        severity = "error"
    if held:
        messages.append(f"{held} job(s) are held. Open CUPS to check why and release them when appropriate.")
        if severity != "error":
            severity = "warning"
    if stopped:
        messages.append(f"{stopped} job(s) stopped because of a printing problem. Check the job in CUPS.")
        severity = "error"
    uri = attrs.get("device-uri", "")
    serial = usb_serial or parse_qs(urlsplit(uri).query).get("serial", [None])[0]
    if serial and usb_serials is not None and serial not in usb_serials:
        state, severity = "Disconnected", "error"
        messages.insert(0, "The USB printer is not connected. Check its power and USB cable.")
    note = "Jobs sent directly to the USB forwarder are not included in this CUPS queue." if usb_serial else ""
    return {"name": name, "title": attrs.get("printer-info") or name,
            "state": state, "severity": severity, "queued": len(own_jobs), "held": held,
            "message": " ".join(dict.fromkeys(messages)) or "No problems reported by CUPS.",
            "details": "\n".join(filter(None, [raw_message, ", ".join(reasons)])),
            "note": note, "image": "label.svg" if "label" in name.lower() else "printer.svg"}


def load_forwarders():
    config_home = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    path = config_home / "cups-tray-icon/config.json"
    try:
        config = json.loads(path.read_text())
    except FileNotFoundError:
        return {}
    if not isinstance(config, dict) or set(config) - {"usb_forwarders"}:
        raise ValueError("config.json must contain only the optional usb_forwarders object")
    forwarders = config.get("usb_forwarders", {})
    if not isinstance(forwarders, dict) or not all(
        isinstance(name, str) and name.strip() and isinstance(serial, str) and serial.strip()
        for name, serial in forwarders.items()
    ):
        raise ValueError("usb_forwarders must map queue names to nonempty USB serial strings")
    return forwarders


def snapshot():
    import cups
    forwarders = load_forwarders()
    cups.setServer("localhost")
    cups.setPort(631)
    connection = cups.Connection()
    printers = {name: attrs for name, attrs in connection.getPrinters().items()
                if attrs.get("device-uri", "").startswith("usb://") or name in forwarders}
    jobs = connection.getJobs(which_jobs="not-completed", my_jobs=False,
                              requested_attributes=["job-printer-uri", "job-state"])
    serials = set()
    try:
        for path in Path("/sys/bus/usb/devices").glob("*/serial"):
            serials.add(path.read_text().strip())
    except OSError:
        serials = None
    for name, attrs in printers.items():
        attrs.update(connection.getPrinterAttributes(name, requested_attributes=["printer-is-accepting-jobs"]))
    return [printer_card(name, attrs, jobs, serials, forwarders.get(name)) for name, attrs in
            sorted(printers.items())]


def demo_snapshot():
    jobs = {index: {"job-printer-uri": f"ipp://localhost/printers/{name}", "job-state": state}
            for index, (name, state) in enumerate([
                ("Label_Printer", 5), ("Label_Printer", 3), ("Label_Printer", 3),
                ("Office_Printer", 3), ("Office_Printer", 3)])}
    return [printer_card("Label_Printer", {"printer-info": "Label printer", "printer-state": 4}, jobs),
            printer_card("Office_Printer", {"printer-info": "Office printer", "printer-state": 5,
                "printer-state-reasons": ["media-empty-error"]}, jobs)]


def run_desktop():
    import gi
    gi.require_version("Gtk", "3.0")
    gi.require_version("Gdk", "3.0")
    from gi.repository import Gdk, Gio, GLib, Gtk

    class PrinterStatus(Gtk.Application):
        def __init__(self):
            self.demo = "--demo" in sys.argv
            super().__init__(application_id="io.github.M1chlCZ.CupsTrayIcon" + (".Demo" if self.demo else ""),
                             flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
            self.add_main_option("show", 0, GLib.OptionFlags.NONE, GLib.OptionArg.NONE, "Open printer status", None)
            self.add_main_option("demo", 0, GLib.OptionFlags.NONE, GLib.OptionArg.NONE, "Show fictional printers without contacting CUPS", None)
            self.busy = False
            self.last_data = None

        def do_startup(self):
            Gtk.Application.do_startup(self)
            self.hold()
            self.window = Gtk.ApplicationWindow(application=self, title="CUPS Tray Icon")
            self.window.set_default_size(640, 560)
            self.window.set_icon_name("printer")
            self.window.connect("delete-event", lambda *_: self.window.hide_on_delete())
            self.window.connect("key-press-event", self.key_pressed)
            outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin=18)
            self.window.add(outer)
            heading = Gtk.Label(xalign=0)
            heading.set_markup("<span size='x-large' weight='bold'>Local printers</span>")
            outer.pack_start(heading, False, False, 0)
            self.updated = Gtk.Label(label="Connecting to CUPS…", xalign=0)
            outer.pack_start(self.updated, False, False, 0)
            scroller = Gtk.ScrolledWindow()
            scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
            self.cards = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
            scroller.add(self.cards)
            outer.pack_start(scroller, True, True, 0)
            footer = Gtk.Label(label="CUPS queue status does not confirm that paper has physically printed.",
                               xalign=0, wrap=True)
            outer.pack_start(footer, False, False, 0)
            buttons = Gtk.Box(spacing=8)
            refresh = Gtk.Button(label="Refresh")
            refresh.connect("clicked", lambda *_: self.refresh())
            buttons.pack_start(refresh, False, False, 0)
            cups_button = Gtk.Button(label="Open CUPS")
            cups_button.connect("clicked", lambda *_: Gtk.show_uri_on_window(
                self.window, "http://localhost:631/printers/", Gdk.CURRENT_TIME))
            buttons.pack_start(cups_button, False, False, 0)
            close = Gtk.Button(label="Close")
            close.connect("clicked", lambda *_: self.window.hide())
            buttons.pack_end(close, False, False, 0)
            outer.pack_start(buttons, False, False, 0)
            self.setup_tray()
            self.refresh()
            GLib.timeout_add_seconds(5, self.refresh)

        def do_command_line(self, command_line):
            if command_line.get_options_dict().contains("show") or self.demo:
                self.show_window()
            return 0

        def key_pressed(self, _window, event):
            if event.keyval == Gdk.KEY_Escape:
                self.window.hide()
                return True
            return False

        def show_window(self):
            self.window.show_all()
            self.window.present()
            self.refresh()

        def setup_tray(self):
            self.bus = self.get_dbus_connection()
            self.properties = {
                "Category": GLib.Variant("s", "Hardware"), "Id": GLib.Variant("s", "cups-tray-icon"),
                "Title": GLib.Variant("s", "CUPS Tray Icon"), "Status": GLib.Variant("s", "Active"),
                "WindowId": GLib.Variant("u", 0), "IconName": GLib.Variant("s", "printer"),
                "IconPixmap": GLib.Variant("a(iiay)", []),
                "AttentionIconName": GLib.Variant("s", "printer-error"),
                "AttentionIconPixmap": GLib.Variant("a(iiay)", []),
                "OverlayIconName": GLib.Variant("s", ""), "OverlayIconPixmap": GLib.Variant("a(iiay)", []),
                "ItemIsMenu": GLib.Variant("b", False), "Menu": GLib.Variant("o", "/"),
                "ToolTip": GLib.Variant("(sa(iiay)ss)", ("printer", [], "CUPS Tray Icon", "Checking printers…")),
            }
            properties_xml = "".join(f'<property name="{name}" type="{value.get_type_string()}" access="read"/>'
                                     for name, value in self.properties.items())
            methods_xml = "".join(f'<method name="{name}"><arg type="i" direction="in"/><arg type="i" direction="in"/></method>'
                                  for name in ("Activate", "SecondaryActivate", "ContextMenu"))
            self.interface = "org.kde.StatusNotifierItem"
            node = Gio.DBusNodeInfo.new_for_xml('<node><interface name="' + self.interface + '">' +
                properties_xml + methods_xml + '<signal name="NewIcon"/><signal name="NewToolTip"/>' +
                '</interface></node>')
            self.bus.register_object("/StatusNotifierItem", node.interfaces[0], self.tray_method,
                                     lambda _c, _s, _p, _i, name: self.properties.get(name), None)
            self.watcher = Gio.bus_watch_name_on_connection(self.bus, "org.kde.StatusNotifierWatcher",
                Gio.BusNameWatcherFlags.NONE, self.register_tray, lambda *_: None)

        def register_tray(self, *_):
            self.bus.call("org.kde.StatusNotifierWatcher", "/StatusNotifierWatcher",
                "org.kde.StatusNotifierWatcher", "RegisterStatusNotifierItem",
                GLib.Variant("(s)", ("/StatusNotifierItem",)), None, Gio.DBusCallFlags.NONE, 3000,
                None, self.registered, None)

        def registered(self, connection, result, _data):
            try:
                connection.call_finish(result)
            except GLib.Error as error:
                print(f"Tray registration: {error}", file=sys.stderr)

        def tray_method(self, _connection, _sender, _path, _interface, method, _params, invocation):
            if method in ("Activate", "SecondaryActivate", "ContextMenu"):
                self.show_window()
                invocation.return_value(None)
            else:
                invocation.return_dbus_error("org.freedesktop.DBus.Error.UnknownMethod", "Unknown method")

        def refresh(self):
            if not self.busy:
                self.busy = True
                threading.Thread(target=self.poll, daemon=True).start()
            return True

        def poll(self):
            try:
                if self.demo:
                    data = demo_snapshot()
                else:
                    result = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--snapshot"],
                        capture_output=True, text=True, timeout=8, check=True)
                    data = json.loads(result.stdout)
                error = None
            except (subprocess.SubprocessError, OSError, ValueError):
                data, error = [], "Cannot read printer status. Check the local CUPS service and cups-tray-icon/config.json. Retrying automatically."
            GLib.idle_add(self.render, data, error)

        def text_label(self, text):
            label = Gtk.Label(label=text, xalign=0, wrap=True, selectable=True)
            label.set_max_width_chars(62)
            return label

        def render(self, data, error):
            self.busy = False
            self.updated.set_text(error or ("Demo · fictional printers and jobs" if self.demo else
                "Updated " + GLib.DateTime.new_now_local().format("%H:%M:%S") + " · refreshes every 5 seconds"))
            self.updated.set_line_wrap(True)
            if (data, error) != self.last_data:
                self.last_data = (data, error)
                for child in self.cards.get_children():
                    self.cards.remove(child)
                for card in data:
                    frame = Gtk.Frame()
                    row = Gtk.Box(spacing=16, margin=14)
                    frame.add(row)
                    if card["image"]:
                        image = Gtk.Image.new_from_file(str(Path(__file__).with_name(card["image"])))
                    else:
                        image = Gtk.Image.new_from_icon_name("printer", Gtk.IconSize.DIALOG)
                    image.set_valign(Gtk.Align.START)
                    image.get_accessible().set_name(card["title"])
                    row.pack_start(image, False, False, 0)
                    details = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
                    row.pack_start(details, True, True, 0)
                    title = self.text_label("")
                    title.set_markup("<b>" + GLib.markup_escape_text(card["title"]) + "</b>")
                    details.pack_start(title, False, False, 0)
                    color = {"ok": "#217a48", "warning": "#946000", "error": "#b52d31"}[card["severity"]]
                    state = self.text_label("")
                    state.set_markup(f'<span foreground="{color}" weight="bold">{card["state"]}</span> · '
                                     f'{card["queued"]} job(s) in queue')
                    details.pack_start(state, False, False, 0)
                    details.pack_start(self.text_label(card["message"]), False, False, 0)
                    if card["note"]:
                        details.pack_start(self.text_label(card["note"]), False, False, 0)
                    diagnostic = Gtk.Expander(label="Queue details")
                    diagnostic.add(self.text_label(card["name"] + "\n" + card["details"]))
                    details.pack_start(diagnostic, False, False, 0)
                    self.cards.pack_start(frame, False, False, 0)
                if not data:
                    self.cards.pack_start(self.text_label(error or "No local USB printer queues have been added yet."), False, False, 0)
                self.cards.show_all()
                attention = error or any(card["severity"] != "ok" for card in data)
                icon = "printer-error" if attention else "printer"
                summary = error or f'{len(data)} queues · {sum(card["queued"] for card in data)} job(s)'
                self.properties["IconName"] = GLib.Variant("s", icon)
                self.properties["ToolTip"] = GLib.Variant("(sa(iiay)ss)", (icon, [], "CUPS Tray Icon", summary))
                self.bus.emit_signal(None, "/StatusNotifierItem", "org.freedesktop.DBus.Properties",
                    "PropertiesChanged", GLib.Variant("(sa{sv}as)", (self.interface, self.properties, [])))
                for signal in ("NewIcon", "NewToolTip"):
                    self.bus.emit_signal(None, "/StatusNotifierItem", self.interface, signal, None)
            return False

    return PrinterStatus().run(sys.argv)


if __name__ == "__main__":
    if "--snapshot" in sys.argv:
        print(json.dumps(demo_snapshot() if "--demo" in sys.argv else snapshot()))
    else:
        raise SystemExit(run_desktop())
