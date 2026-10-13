#!/usr/bin/python3
"""Friendly CUPS printer status and personal print settings for Linux desktops."""
import json
import os
import re
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlsplit

__version__ = "0.1.2"

TRAY_ICON_SIZES = (22, 24, 32)

UNREACHABLE_TITLE = "Cannot reach the print service on this computer."
UNREACHABLE_HINT = ("It will retry automatically. Check the local CUPS service and the "
                    "cups-tray-icon configuration file (config.json).")
UNREACHABLE_MESSAGE = UNREACHABLE_TITLE + " " + UNREACHABLE_HINT

REASONS = {
    "media-empty": "Load paper or labels into the printer.",
    "media-needed": "The printer needs paper or labels before it can continue.",
    "media-jam": "Paper is jammed. Clear the jam and close the cover.",
    "media-low": "Paper or labels are running low.",
    "cover-open": "Close the printer cover firmly.",
    "door-open": "Close the printer door firmly.",
    "offline": "The printer is switched off or disconnected.",
    "connecting-to-device": "The computer is trying to reach the printer. Check its power and connection.",
    "paused": "Printing has stopped. Fix the problem, then start it again from the CUPS web page.",
    "cups-filter-error": "CUPS could not prepare the document. The print driver or the document may need attention.",
    "cups-missing-filter": "A part of the print software is missing.",
    "marker-supply-empty": "Ink, toner or ribbon is empty. Check the printer's supplies.",
    "marker-supply-low": "Ink, toner or ribbon is running low.",
    "toner-empty": "Replace the empty toner cartridge.",
    "toner-low": "Toner is running low.",
    "marker-waste-full": "The waste container is full and needs attention.",
    "spool-area-full": "There is no space left for new documents on this computer.",
    "cups-held-for-authentication": "A document is waiting for permission or a password.",
    "timed-out": "The printer did not respond in time. Check its power and connection.",
    "shutdown": "The printer is shutting down.",
}

OPTION_GROUP_SPECS = (
    {"key": "media", "ppd_key": "PageSize", "label": "Paper size",
     "supported": "media-supported", "default": "media-default"},
    {"key": "print-color-mode", "ppd_key": "ColorMode", "label": "Color mode",
     "supported": "print-color-mode-supported", "default": "print-color-mode-default"},
    {"key": "sides", "ppd_key": "Duplex", "label": "Two-sided printing",
     "supported": "sides-supported", "default": "sides-default"},
    {"key": "media-type", "ppd_key": "MediaType", "label": "Paper type",
     "supported": "media-type-supported", "default": "media-type-default"},
)

OPTION_SLOTS = {
    "media": "media", "PageSize": "media",
    "print-color-mode": "color", "ColorMode": "color",
    "sides": "duplex", "Duplex": "duplex",
    "media-type": "type", "MediaType": "type",
}

OPTION_SLOT_ORDER = ("media", "color", "duplex", "type")

PAPER_SIZE_LABELS = {
    "iso_a3_297x420mm": "A3", "a3": "A3",
    "iso_a4_210x297mm": "A4", "a4": "A4",
    "iso_a5_148x210mm": "A5", "a5": "A5",
    "iso_a6_105x148mm": "A6", "a6": "A6",
    "jis_b5_182x257mm": "B5", "b5": "B5",
    "na_letter_8.5x11in": "Letter", "letter": "Letter",
    "na_legal_8.5x14in": "Legal", "legal": "Legal",
    "na_executive_7.25x10.5in": "Executive", "executive": "Executive",
    "na_tabloid_11x17in": "Tabloid", "tabloid": "Tabloid",
    "na_number-10_4.125x9.5in": "#10 envelope", "env10": "#10 envelope",
    "na_monarch_3.875x7.5in": "Monarch envelope", "envmonarch": "Monarch envelope",
    "iso_dl_110x220mm": "DL envelope", "envdl": "DL envelope",
    "iso_c5_162x229mm": "C5 envelope", "envc5": "C5 envelope",
    "na_index-4x6_4x6in": "4x6", "4x6": "4x6",
    "om_100x150mm_100x150mm": "10x15", "10x15": "10x15", "100x150": "100x150",
    "oe_photo-l_3.5x5in": "3.5x5", "3.5x5": "3.5x5",
    "custom": "Custom size",
}

SIDES_LABELS = {
    "one-sided": "One-sided", "simplex": "One-sided", "none": "One-sided",
    "duplexnotumble": "Two-sided (long edge)", "two-sided-long-edge": "Two-sided (long edge)",
    "duplextumble": "Two-sided (short edge)", "two-sided-short-edge": "Two-sided (short edge)",
}

COLOR_LABELS = {
    "color": "Color", "colour": "Color",
    "monochrome": "Monochrome", "mono": "Monochrome",
    "gray": "Monochrome", "grey": "Monochrome", "grayscale": "Monochrome", "black": "Monochrome",
    "automatic": "Automatic", "auto": "Automatic",
}

MEDIA_TYPE_LABELS = {
    "plain": "Plain paper", "stationery": "Stationery", "letterhead": "Letterhead",
    "photographic": "Photo paper", "photo": "Photo paper",
    "glossy": "Glossy paper", "high-gloss": "High-gloss paper", "semi-gloss": "Semi-gloss paper",
    "matte": "Matte paper", "labels": "Labels", "label": "Labels",
    "envelope": "Envelopes", "transparency": "Transparencies",
    "cardstock": "Card stock", "card-stock": "Card stock", "bond": "Bond paper",
    "recycled": "Recycled paper", "rough": "Rough paper", "preprinted": "Preprinted paper",
    "colored": "Colored paper", "coloured": "Colored paper", "archival": "Archival paper",
}

VALUE_LABELS = {"media": PAPER_SIZE_LABELS, "color": COLOR_LABELS,
                "duplex": SIDES_LABELS, "type": MEDIA_TYPE_LABELS}

READ_OPTIONS_FAILED = "The settings for this printer could not be read."
NO_OPTIONS_MESSAGE = "This printer has no adjustable options here."
APPLY_FAILED_MESSAGE = ("The printer did not accept these settings. "
                        "You can change them in the system printer settings.")


def document_count(count):
    """Format a document count with the right singular or plural wording."""
    return "1 document" if count == 1 else f"{count} documents"


def waiting_text(count):
    """Describe the waiting documents in one short natural phrase."""
    if count == 0:
        return "Nothing waiting"
    return document_count(count) + " waiting"


def display_state(state, severity):
    """Return the short status word shown on a printer card and in the tray tooltip."""
    if state == "Disconnected":
        return "Offline"
    if severity != "ok":
        return "Needs attention"
    if state == "Printing":
        return "Printing"
    if state == "Idle":
        return "Ready"
    return "Unknown"


def state_icon(state, severity):
    """Return the file name of the state icon that matches a printer state."""
    if state == "Disconnected":
        return "printer-offline.svg"
    if severity == "error" or state == "Paused":
        return "printer-error.svg"
    if severity == "warning":
        return "printer-warning.svg"
    if state in ("Idle", "Printing"):
        return "printer-ready.svg"
    return "printer-warning.svg"


def tray_icon(cards, error=None):
    """Return the tray icon name and SVG file for the worst state found."""
    if error:
        return "printer-offline", "printer-offline.svg"
    severities = [card["severity"] for card in cards]
    if "error" in severities:
        return "printer-error", "printer-error.svg"
    if "warning" in severities:
        return "printer-warning", "printer-warning.svg"
    return "printer-ready", "printer-ready.svg"


def summary_text(cards):
    """Summarize the overall printer state in one friendly sentence."""
    if not cards:
        return "No printers found."
    attention = sum(card["severity"] != "ok" for card in cards)
    if attention == 0:
        return "All printers are ready."
    if attention == 1:
        return "1 printer needs attention."
    return f"{attention} printers need attention."


def tooltip_text(cards, error=None, limit=6):
    """Build the tray tooltip text with one short line per printer."""
    if error:
        return "Cannot reach the print service. Retrying automatically."
    lines = []
    for card in cards[:limit]:
        title = card["title"]
        if len(title) > 32:
            title = title[:31] + "…"
        lines.append(f"{title} — {display_state(card['state'], card['severity'])}")
    remaining = len(cards) - limit
    if remaining == 1:
        lines.append("1 more printer")
    elif remaining > 0:
        lines.append(f"{remaining} more printers")
    return "\n".join(lines) if lines else "No printers found."


def printer_uri(name):
    """Return the CUPS web page address for a printer name."""
    return "http://localhost:631/printers/" + quote(name, safe="")


def printer_settings_command():
    """Return the first available external printer settings command, or None."""
    for command in (["system-config-printer"], ["gnome-control-center", "printers"]):
        if shutil.which(command[0]):
            return command
    return None


def prettify_option(value):
    """Turn a raw option value into a readable name for people."""
    words = str(value).replace("_", " ").split()
    if not words:
        return str(value)
    return " ".join(word[:1].upper() + word[1:] for word in words)


def option_label(key, value):
    """Return a friendly label for one option value, prettifying unknown names."""
    friendly = VALUE_LABELS.get(OPTION_SLOTS.get(key), {}).get(str(value).strip().lower())
    return friendly if friendly else prettify_option(value)


def override_value(key, overrides):
    """Return the user's personal override for one option, accepting IPP and PPD names."""
    names = [key]
    for spec in OPTION_GROUP_SPECS:
        if key in (spec["key"], spec["ppd_key"]):
            names = [spec["key"], spec["ppd_key"]]
            break
    for name in names:
        if name in overrides:
            return overrides[name]
    return None


def current_option_value(key, values, overrides, default=None):
    """Pick the current value: the user's override, else the printer default, else the first choice."""
    for candidate in (override_value(key, overrides or {}), default):
        if candidate is not None and candidate in values:
            return candidate
    return values[0]


def option_groups_from_attributes(attributes, overrides=None):
    """Build option groups from live CUPS/IPP attributes.

    Only groups with at least two supported values are included. Each group is
    a dictionary shaped like ``{"key": "media", "label": "Paper size",
    "options": [{"value": ..., "label": ...}], "current": ...}``. Paper sizes
    are sorted by their friendly name so the order stays stable.
    """
    overrides = overrides or {}
    groups = []
    for spec in OPTION_GROUP_SPECS:
        supported = attributes.get(spec["supported"]) or []
        if isinstance(supported, str):
            supported = [supported]
        values = list(dict.fromkeys(str(value) for value in supported if str(value).strip()))
        if len(values) < 2:
            continue
        options = [{"value": value, "label": option_label(spec["key"], value)} for value in values]
        if OPTION_SLOTS[spec["key"]] == "media":
            options.sort(key=lambda option: (option["label"].casefold(), option["value"]))
        groups.append({"key": spec["key"], "label": spec["label"], "options": options,
                       "current": current_option_value(spec["key"], values, overrides,
                                                       attributes.get(spec["default"]))})
    return groups


def option_groups_from_ppd(ppd_text, overrides=None):
    """Build option groups from a PPD, keeping classic PickOne groups with at least two options.

    Group keys keep their PPD names (``PageSize``, ``Duplex`` and so on)
    because those are the option names that lpoptions accepts for classic
    queues.
    """
    overrides = overrides or {}
    names = {spec["ppd_key"]: spec for spec in OPTION_GROUP_SPECS}
    declared = set()
    defaults = {}
    choices = {}
    for line in (ppd_text or "").splitlines():
        open_match = re.match(r"\s*\*OpenUI\s+\*([A-Za-z0-9_]+)(?:/[^:]*)?:\s*PickOne", line)
        if open_match:
            declared.add(open_match.group(1))
            continue
        option_match = re.match(r"\s*\*([A-Za-z0-9_]+)\s+([^/:\s]+)", line)
        if option_match and option_match.group(1) in names:
            values = choices.setdefault(option_match.group(1), [])
            if option_match.group(2) not in values:
                values.append(option_match.group(2))
            continue
        default_match = re.match(r"\s*\*Default([A-Za-z0-9_]+):\s*(\S+)", line)
        if default_match and default_match.group(1) in names:
            defaults[default_match.group(1)] = default_match.group(2)
    groups = []
    for spec in OPTION_GROUP_SPECS:
        name = spec["ppd_key"]
        values = choices.get(name, [])
        if name not in declared or len(values) < 2:
            continue
        options = [{"value": value, "label": option_label(name, value)} for value in values]
        if OPTION_SLOTS[name] == "media":
            options.sort(key=lambda option: (option["label"].casefold(), option["value"]))
        groups.append({"key": name, "label": spec["label"], "options": options,
                       "current": current_option_value(name, values, overrides, defaults.get(name))})
    return groups


def merge_option_groups(preferred, fallback):
    """Merge discovered groups, keeping the preferred (IPP) group for each slot."""
    groups = list(preferred)
    seen = {OPTION_SLOTS.get(group["key"]) for group in groups}
    for group in fallback:
        slot = OPTION_SLOTS.get(group["key"])
        if slot not in seen:
            groups.append(group)
            seen.add(slot)
    order = {slot: index for index, slot in enumerate(OPTION_SLOT_ORDER)}
    groups.sort(key=lambda group: order.get(OPTION_SLOTS.get(group["key"]), len(order)))
    return groups


def discover_option_groups(attributes, ppd_text, overrides=None):
    """Discover adjustable option groups from live IPP attributes and the PPD fallback."""
    preferred = option_groups_from_attributes(attributes or {}, overrides)
    fallback = option_groups_from_ppd(ppd_text or "", overrides)
    return merge_option_groups(preferred, fallback)


def parse_lpoptions(text):
    """Parse the key=value pairs printed by lpoptions into a dictionary."""
    options = {}
    for token in (text or "").split():
        key, separator, value = token.partition("=")
        if separator and key:
            options[key] = value
    return options


def read_personal_defaults(printer):
    """Read the user's personal defaults through lpoptions, returning an empty dictionary on failure."""
    try:
        result = subprocess.run(["lpoptions", "-p", printer], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return {}
    if result.returncode != 0:
        return {}
    return parse_lpoptions(result.stdout)


def build_lpoptions_command(printer, groups, selection):
    """Build the lpoptions command for the changed groups, rejecting unsupported values.

    ``selection`` maps group keys to the desired values, usually taken from the
    dialog combos. Only groups whose value differs from ``current`` are
    included. An unknown key or an unsupported value raises ``ValueError``
    before anything is started.
    """
    known = {group["key"]: group for group in groups}
    unknown = sorted(set(selection) - set(known))
    if unknown:
        raise ValueError("unknown printer option: " + ", ".join(unknown))
    arguments = []
    for group in groups:
        if group["key"] not in selection:
            continue
        value = selection[group["key"]]
        if value == group["current"]:
            continue
        if value not in {option["value"] for option in group["options"]}:
            raise ValueError(f"unsupported value for {group['key']}: {value}")
        arguments.extend(["-o", f"{group['key']}={value}"])
    if not arguments:
        return None
    return ["lpoptions", "-p", printer, *arguments]


def apply_printer_options(printer, groups, selection):
    """Apply the changed option groups through lpoptions, returning a friendly error or None."""
    try:
        command = build_lpoptions_command(printer, groups, selection)
    except ValueError:
        return APPLY_FAILED_MESSAGE
    if command is None:
        return None
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return APPLY_FAILED_MESSAGE
    return None if result.returncode == 0 else APPLY_FAILED_MESSAGE


def read_ppd_text(connection, printer):
    """Return the printer's PPD text through pycups, or an empty string when it is unavailable."""
    for name, arguments in (("getPPD3", (printer,)), ("getPPD3", (printer, 0)), ("getPPD", (printer,))):
        method = getattr(connection, name, None)
        if method is None:
            continue
        try:
            result = method(*arguments)
        except (AttributeError, TypeError):
            continue
        except Exception:
            return ""
        if isinstance(result, tuple):
            result = result[-1]
        if not isinstance(result, (str, bytes, os.PathLike)):
            return ""
        try:
            return Path(result).read_text(errors="replace")
        except OSError:
            return ""
    return ""


def printer_option_groups(printer):
    """Discover the adjustable option groups of one printer from the local CUPS server."""
    import cups
    cups.setServer("localhost")
    cups.setPort(631)
    connection = cups.Connection()
    attributes = connection.getPrinterAttributes(printer)
    overrides = read_personal_defaults(printer)
    return discover_option_groups(attributes, read_ppd_text(connection, printer), overrides)


def demo_option_groups():
    """Return fictional adjustable option groups for demo mode."""
    attributes = {
        "media-supported": ["iso_a4_210x297mm", "iso_a5_148x210mm", "na_letter_8.5x11in",
                            "om_100x150mm_100x150mm"],
        "media-default": "iso_a4_210x297mm",
        "print-color-mode-supported": ["color", "monochrome"],
        "print-color-mode-default": "color",
        "sides-supported": ["one-sided", "two-sided-long-edge"],
        "sides-default": "one-sided",
        "media-type-supported": ["plain", "photographic", "glossy"],
        "media-type-default": "plain",
    }
    return discover_option_groups(attributes, "", {})


def argb_pixmap(pixbuf):
    """Convert a GdkPixbuf-like object into an ARGB32 network byte order pixmap tuple.

    The StatusNotifierItem protocol expects a(iiay) pixmaps with ARGB bytes in
    network byte order, while GdkPixbuf stores pixels as RGB(A). The argument
    only needs the get_width, get_height, get_rowstride, get_n_channels and
    get_pixels methods, so the conversion stays testable without GTK.
    """
    width, height = pixbuf.get_width(), pixbuf.get_height()
    stride = pixbuf.get_rowstride()
    channels = pixbuf.get_n_channels()
    pixels = pixbuf.get_pixels()
    argb = bytearray(width * height * 4)
    index = 0
    for y in range(height):
        row = pixels[y * stride:(y + 1) * stride]
        for x in range(width):
            offset = x * channels
            alpha = 255 if channels < 4 else row[offset + 3]
            argb[index:index + 4] = bytes((alpha, row[offset], row[offset + 1], row[offset + 2]))
            index += 4
    return width, height, bytes(argb)


def printer_card(name, attrs, jobs, usb_serials=None, usb_serial=None):
    """Build the display card for one printer from CUPS attributes and jobs.

    ``options`` is a lazy placeholder: the adjustable option groups are read
    only when the user opens the settings dialog, so the five-second refresh
    stays fast.
    """
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
        messages.append(REASONS.get(key, "The printer reported: " + key + ". See the technical details below."))
        level = "warning" if reason.endswith("-warning") or key.endswith("-low") else "error"
        if severity != "error":
            severity = level
    raw_message = attrs.get("printer-state-message", "")
    if not messages and raw_message:
        if "filter failed" in raw_message.lower():
            messages.append(REASONS["cups-filter-error"])
            severity = "error"
        else:
            messages.append("The printer reported a problem. See the technical details for the original message.")
            if severity != "error":
                severity = "warning"
    if state == "Paused" and not messages:
        messages.append(REASONS["paused"])
    if attrs.get("printer-is-accepting-jobs") is False:
        messages.append("This printer is not accepting new documents.")
        severity = "error"
    if held:
        messages.append(document_count(held) + " waiting for attention. Open the CUPS web page to check why.")
        if severity != "error":
            severity = "warning"
    if stopped:
        messages.append(document_count(stopped) + " stopped because of a printing problem. Check the CUPS web page.")
        severity = "error"
    uri = attrs.get("device-uri", "")
    serial = usb_serial or parse_qs(urlsplit(uri).query).get("serial", [None])[0]
    if serial and usb_serials is not None and serial not in usb_serials:
        state, severity = "Disconnected", "error"
        messages.insert(0, "The USB printer is not connected. Check its power and USB cable.")
    note = "Jobs sent directly to the USB forwarder are not included in this CUPS listing." if usb_serial else ""
    return {"name": name, "title": attrs.get("printer-info") or name,
            "state": state, "severity": severity, "queued": len(own_jobs), "held": held,
            "stopped": stopped,
            "message": " ".join(dict.fromkeys(messages)) or "No problems reported by CUPS.",
            "details": "\n".join(filter(None, [raw_message, ", ".join(reasons)])),
            "note": note, "image": state_icon(state, severity), "options": None}


def config_path():
    """Return the path of the local configuration file."""
    config_home = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return config_home / "cups-tray-icon/config.json"


def load_config():
    """Read config.json, returning defaults when missing and raising ValueError when invalid."""
    try:
        config = json.loads(config_path().read_text())
    except FileNotFoundError:
        return {"usb_forwarders": {}, "notifications": True}
    if not isinstance(config, dict) or set(config) - {"usb_forwarders", "notifications"}:
        raise ValueError("config.json may contain only the optional usb_forwarders object and notifications flag")
    forwarders = config.get("usb_forwarders", {})
    if not isinstance(forwarders, dict) or not all(
        isinstance(name, str) and name.strip() and isinstance(serial, str) and serial.strip()
        for name, serial in forwarders.items()
    ):
        raise ValueError("usb_forwarders must map printer names to nonempty USB serial strings")
    notifications = config.get("notifications", True)
    if not isinstance(notifications, bool):
        raise ValueError("notifications must be true or false")
    return {"usb_forwarders": dict(forwarders), "notifications": notifications}


def load_forwarders():
    """Read only the USB forwarder mapping from config.json."""
    return load_config()["usb_forwarders"]


def save_notifications(enabled):
    """Store the notifications flag atomically, preserving usb_forwarders and creating the file when needed."""
    try:
        config = load_config()
    except ValueError:
        config = {"usb_forwarders": {}}
    config["notifications"] = bool(enabled)
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(config, indent=2, sort_keys=True) + "\n")
    os.replace(temporary, path)


def notification_events(previous, current, previous_error=None, error=None):
    """Return desktop notifications for the changes between two status snapshots.

    ``previous`` and ``current`` are card lists, while the error arguments
    carry the CUPS problem text. The first snapshot only establishes a
    baseline. Every event has a stable ``id`` so a newer notification for the
    same printer replaces the previous one instead of stacking. Events are
    only produced on state transitions, never on every refresh.
    """
    events = []
    if previous is None:
        return events
    if error and not previous_error:
        events.append({"id": "print-service", "title": "Print service unavailable",
                       "body": UNREACHABLE_MESSAGE, "urgent": True})
    elif previous_error and not error:
        events.append({"id": "print-service", "title": "Print service restored",
                       "body": "The print service on this computer is reachable again.",
                       "urgent": False})
    if error:
        return events
    previous_cards = {card["name"]: card for card in previous}
    for card in current:
        old = previous_cards.get(card["name"])
        if old is None:
            continue
        waiting = card.get("held", 0) + card.get("stopped", 0)
        previous_waiting = old.get("held", 0) + old.get("stopped", 0)
        if old["severity"] != "error" and card["severity"] == "error":
            events.append({"id": f"printer-{card['name']}", "title": card["title"] + " needs attention",
                           "body": card["message"], "urgent": True})
        elif previous_waiting == 0 and waiting > 0:
            events.append({"id": f"printer-{card['name']}", "title": "A document is waiting",
                           "body": f"{document_count(waiting)} waiting on {card['title']}. "
                                   "Open the CUPS web page to check.",
                           "urgent": False})
    return events


def snapshot():
    """Read the local USB printer queues and their jobs from CUPS."""
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
    """Return fictional printers that exercise every state and severity."""
    jobs = {index: {"job-printer-uri": f"ipp://localhost/printers/{name}", "job-state": state}
            for index, (name, state) in enumerate([
                ("Label_Printer", 3), ("Label_Printer", 3), ("Receipt_Printer", 4)])}
    return [printer_card("Office_Printer", {"printer-info": "Office printer", "printer-state": 3}, jobs),
            printer_card("Label_Printer", {"printer-info": "Label printer", "printer-state": 4}, jobs),
            printer_card("Color_Printer", {"printer-info": "Color printer", "printer-state": 3,
                "printer-state-reasons": ["media-low-warning"]}, jobs),
            printer_card("Receipt_Printer", {"printer-info": "Receipt printer", "printer-state": 5,
                "printer-state-reasons": ["media-empty-error"]}, jobs),
            printer_card("USB_Printer", {"printer-info": "USB label printer", "printer-state": 3,
                "device-uri": "usb://Demo/USB_Printer?serial=DEMO-SERIAL"}, jobs, usb_serials=set())]


def run_desktop():
    """Run the GTK tray application."""
    import gi
    gi.require_version("Gtk", "3.0")
    gi.require_version("Gdk", "3.0")
    from gi.repository import Gdk, GdkPixbuf, Gio, GLib, Gtk, Pango

    pill_css = b"""
.status-pill { border-radius: 10px; padding: 2px 10px; }
.status-pill-ok { background-color: #e0f0e7; color: #217a48; }
.status-pill-warning { background-color: #f7eed9; color: #946000; }
.status-pill-error { background-color: #f9e3e4; color: #b52d31; }
.status-pill-neutral { background-color: #e9edf0; color: #4a5863; }
.settings-error { color: #b52d31; }
"""

    class PrinterStatus(Gtk.Application):
        """The tray application: polls CUPS, builds the window and exports the tray item."""

        def __init__(self):
            self.demo = "--demo" in sys.argv
            super().__init__(application_id="io.github.M1chlCZ.CupsTrayIcon" + (".Demo" if self.demo else ""),
                             flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
            self.add_main_option("show", 0, GLib.OptionFlags.NONE, GLib.OptionArg.NONE, "Open printer status", None)
            self.add_main_option("demo", 0, GLib.OptionFlags.NONE, GLib.OptionArg.NONE,
                                 "Show fictional printers without contacting CUPS", None)
            self.busy = False
            self.last_data = None
            self.notifications_enabled = True
            self.settings_pending = set()

        def do_startup(self):
            Gtk.Application.do_startup(self)
            self.hold()
            try:
                self.notifications_enabled = load_config()["notifications"]
            except ValueError:
                self.notifications_enabled = True
            show_action = Gio.SimpleAction.new("show", None)
            show_action.connect("activate", lambda *_: self.show_window())
            self.add_action(show_action)
            self.window = Gtk.ApplicationWindow(application=self, title="Printers")
            self.window.set_default_size(640, 580)
            header = Gtk.HeaderBar(show_close_button=True)
            header.set_title("Printers")
            app_icon = self.icon_image("cups-tray-icon.svg", 24)
            if app_icon is not None:
                header.pack_start(app_icon)
            refresh_button = Gtk.Button.new_from_icon_name("view-refresh-symbolic", Gtk.IconSize.BUTTON)
            refresh_button.set_tooltip_text("Refresh now (Ctrl+R or F5)")
            refresh_button.get_accessible().set_name("Refresh")
            refresh_button.connect("clicked", lambda *_: self.refresh())
            header.pack_end(refresh_button)
            menu_button = Gtk.MenuButton()
            menu_button.set_image(Gtk.Image.new_from_icon_name("open-menu-symbolic", Gtk.IconSize.BUTTON))
            menu_button.set_tooltip_text("Menu")
            menu_button.get_accessible().set_name("Menu")
            menu = Gtk.Menu()
            self.notifications_item = Gtk.CheckMenuItem(label="Problem notifications")
            self.notifications_item.set_active(self.notifications_enabled)
            self.notifications_item.connect("toggled", self.notifications_toggled)
            menu.append(self.notifications_item)
            cups_item = Gtk.MenuItem(label="Open CUPS web page")
            cups_item.connect("activate", lambda *_: self.open_cups_page())
            menu.append(cups_item)
            about_item = Gtk.MenuItem(label="About")
            about_item.connect("activate", lambda *_: self.show_about())
            menu.append(about_item)
            menu.show_all()
            menu_button.set_popup(menu)
            header.pack_end(menu_button)
            self.window.set_titlebar(header)
            icon_file = Path(__file__).with_name("cups-tray-icon.svg")
            if icon_file.exists():
                try:
                    self.window.set_icon_from_file(str(icon_file))
                except GLib.Error:
                    pass
            self.window.set_icon_name("cups-tray-icon")
            self.window.connect("delete-event", lambda *_: self.window.hide_on_delete())
            self.window.connect("key-press-event", self.key_pressed)
            provider = Gtk.CssProvider()
            provider.load_from_data(pill_css)
            screen = Gdk.Screen.get_default()
            if screen is not None:
                Gtk.StyleContext.add_provider_for_screen(screen, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
            outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, margin=16)
            self.window.add(outer)
            self.summary = Gtk.Label(xalign=0)
            self.summary.set_markup("<b>Checking printers…</b>")
            self.summary.set_no_show_all(True)
            self.summary.show()
            outer.pack_start(self.summary, False, False, 0)
            self.caption = Gtk.Label(label="Waiting for the first status report…", xalign=0)
            self.caption.get_style_context().add_class("dim-label")
            outer.pack_start(self.caption, False, False, 0)
            self.banner = Gtk.InfoBar()
            self.banner.set_message_type(Gtk.MessageType.WARNING)
            self.banner.set_show_close_button(False)
            self.banner_label = Gtk.Label(xalign=0, wrap=True)
            self.banner.get_content_area().add(self.banner_label)
            self.banner.show_all()
            self.banner.set_no_show_all(True)
            self.banner.hide()
            outer.pack_start(self.banner, False, False, 0)
            scroller = Gtk.ScrolledWindow()
            scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
            self.cards = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
            scroller.add(self.cards)
            outer.pack_start(scroller, True, True, 0)
            footer = Gtk.Label(label="Your settings apply to your account on this computer; the app cannot confirm "
                               "that paper has physically printed.",
                               xalign=0, wrap=True)
            footer.get_style_context().add_class("dim-label")
            outer.pack_start(footer, False, False, 0)
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
            if event.keyval == Gdk.KEY_F5 or (event.keyval in (Gdk.KEY_r, Gdk.KEY_R)
                                              and event.state & Gdk.ModifierType.CONTROL_MASK):
                self.refresh()
                return True
            return False

        def show_window(self):
            """Show, raise and refresh the printer window."""
            self.window.show_all()
            self.window.present()
            self.refresh()

        def icon_pixbuf(self, name, size):
            """Load a bundled SVG icon as a pixbuf scaled to the requested size."""
            path = Path(__file__).with_name(name)
            if not path.exists():
                return None
            try:
                return GdkPixbuf.Pixbuf.new_from_file_at_scale(str(path), size, size, True)
            except GLib.Error:
                return None

        def icon_image(self, name, size):
            """Return a Gtk.Image for a bundled SVG icon, or None when it cannot be loaded."""
            pixbuf = self.icon_pixbuf(name, size)
            return Gtk.Image.new_from_pixbuf(pixbuf) if pixbuf is not None else None

        def text_label(self, text):
            label = Gtk.Label(label=text, xalign=0, wrap=True, selectable=True)
            label.set_max_width_chars(62)
            return label

        def build_pill(self, card):
            """Build the status pill with an icon and a state word, never color alone."""
            display = display_state(card["state"], card["severity"])
            key = {"Ready": "ok", "Printing": "ok", "Needs attention": card["severity"],
                   "Offline": "error"}.get(display, "neutral")
            pill = Gtk.Box(spacing=6)
            context = pill.get_style_context()
            context.add_class("status-pill")
            context.add_class("status-pill-" + key)
            icon = self.icon_image(state_icon(card["state"], card["severity"]), 16)
            if icon is not None:
                pill.pack_start(icon, False, False, 0)
            pill.pack_start(Gtk.Label(label=display), False, False, 0)
            pill.set_valign(Gtk.Align.CENTER)
            pill.get_accessible().set_name("Status: " + display)
            return pill

        def build_settings_button(self, card):
            """Build the flat gear button that opens one printer's settings dialog."""
            button = Gtk.Button()
            button.set_image(Gtk.Image.new_from_icon_name("emblem-system-symbolic", Gtk.IconSize.BUTTON))
            button.set_relief(Gtk.ReliefStyle.NONE)
            button.set_valign(Gtk.Align.CENTER)
            button.set_tooltip_text("Settings for " + card["title"])
            button.get_accessible().set_name("Settings for " + card["title"])
            button.connect("clicked", lambda *_: self.open_settings(card))
            return button

        def build_card(self, card):
            """Build one printer card with a sentence, waiting count, link and details."""
            frame = Gtk.Frame()
            row = Gtk.Box(spacing=14, margin=14)
            frame.add(row)
            image = self.icon_image(card["image"], 44)
            if image is None:
                image = Gtk.Image.new_from_icon_name("printer", Gtk.IconSize.DIALOG)
            image.set_valign(Gtk.Align.START)
            row.pack_start(image, False, False, 0)
            details = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            row.pack_start(details, True, True, 0)
            heading = Gtk.Box(spacing=10)
            title = Gtk.Label(xalign=0)
            title.set_markup("<b>" + GLib.markup_escape_text(card["title"]) + "</b>")
            title.set_ellipsize(Pango.EllipsizeMode.END)
            heading.pack_start(title, True, True, 0)
            heading.pack_end(self.build_settings_button(card), False, False, 0)
            heading.pack_end(self.build_pill(card), False, False, 0)
            details.pack_start(heading, False, False, 0)
            details.pack_start(self.text_label(card["message"]), False, False, 0)
            waiting = self.text_label(waiting_text(card["queued"]))
            if card["queued"] == 0:
                waiting.get_style_context().add_class("dim-label")
            details.pack_start(waiting, False, False, 0)
            if card["note"]:
                note = self.text_label(card["note"])
                note.get_style_context().add_class("dim-label")
                details.pack_start(note, False, False, 0)
            queue_link = Gtk.LinkButton.new_with_label(printer_uri(card["name"]), "Open print queue")
            queue_link.set_halign(Gtk.Align.START)
            queue_link.set_tooltip_text(printer_uri(card["name"]))
            details.pack_start(queue_link, False, False, 0)
            diagnostic = Gtk.Expander(label="Technical details")
            diagnostic.add(self.text_label(card["name"] + "\n" + card["details"]))
            details.pack_start(diagnostic, False, False, 0)
            frame.get_accessible().set_name(card["title"] + ": " + display_state(card["state"], card["severity"]))
            return frame

        def open_settings(self, card):
            """Fetch one printer's option groups in the background and open its settings dialog."""
            if card["name"] in self.settings_pending:
                return
            self.settings_pending.add(card["name"])
            threading.Thread(target=self.fetch_options, args=(card,), daemon=True).start()

        def fetch_options(self, card):
            """Read one printer's option groups off the main loop and open the settings dialog."""
            try:
                if self.demo:
                    groups = demo_option_groups()
                else:
                    result = subprocess.run(
                        [sys.executable, str(Path(__file__).resolve()), "--printer-options", card["name"]],
                        capture_output=True, text=True, timeout=15, check=True)
                    groups = json.loads(result.stdout)
                    if not isinstance(groups, list):
                        raise ValueError("printer options must be a list")
                error = None
            except (subprocess.SubprocessError, OSError, ValueError):
                groups, error = [], READ_OPTIONS_FAILED
            GLib.idle_add(self.show_settings_dialog, card, groups, error)

        def show_settings_dialog(self, card, groups, error):
            """Show the settings dialog for one printer and apply the values the user chooses."""
            self.settings_pending.discard(card["name"])
            dialog = Gtk.Dialog(title="Settings for " + card["title"], transient_for=self.window, modal=True)
            content = dialog.get_content_area()
            content.set_spacing(10)
            content.set_margin_top(12)
            content.set_margin_bottom(12)
            content.set_margin_start(12)
            content.set_margin_end(12)
            hint = Gtk.Label(label="Applies to your account on this computer.", xalign=0)
            hint.get_style_context().add_class("dim-label")
            content.pack_start(hint, False, False, 0)
            notice = Gtk.Label(xalign=0, wrap=True)
            notice.set_no_show_all(True)
            content.pack_start(notice, False, False, 0)
            combos = {}
            first_combo = None
            for group in groups:
                caption = Gtk.Label(label=group["label"], xalign=0)
                content.pack_start(caption, False, False, 0)
                combo = Gtk.ComboBoxText()
                for option in group["options"]:
                    combo.append(option["value"], option["label"])
                combo.set_active_id(group["current"])
                combo.get_accessible().set_name(group["label"])
                content.pack_start(combo, False, False, 0)
                combos[group["key"]] = combo
                if first_combo is None:
                    first_combo = combo
            if error:
                notice.get_style_context().add_class("settings-error")
                notice.set_text(error)
                notice.show()
            elif not groups:
                notice.set_text(NO_OPTIONS_MESSAGE)
                notice.show()
            dialog.add_button("More options…", Gtk.ResponseType.HELP)
            if groups and not error:
                dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
                apply_button = dialog.add_button("Apply", Gtk.ResponseType.APPLY)
                apply_button.set_can_default(True)
                apply_button.grab_default()
            dialog.show_all()
            if first_combo is not None:
                first_combo.grab_focus()
            while True:
                response = dialog.run()
                if response == Gtk.ResponseType.HELP:
                    self.open_printer_settings()
                    continue
                if response == Gtk.ResponseType.APPLY and combos:
                    selection = {key: combo.get_active_id() for key, combo in combos.items()}
                    message = self.apply_settings(card, groups, selection)
                    if message:
                        notice.get_style_context().add_class("settings-error")
                        notice.set_text(message)
                        notice.show()
                        continue
                    self.refresh()
                break
            dialog.destroy()

        def apply_settings(self, card, groups, selection):
            """Apply the chosen settings for one printer, returning a friendly error or None."""
            if self.demo:
                return None
            return apply_printer_options(card["name"], groups, selection)

        def build_empty_state(self):
            """Build the friendly explanation shown when no local printers were found."""
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, margin_top=24)
            icon = self.icon_image("cups-tray-icon.svg", 64)
            if icon is not None:
                icon.set_halign(Gtk.Align.CENTER)
                box.pack_start(icon, False, False, 0)
            title = Gtk.Label(xalign=0)
            title.set_markup("<b>No printers connected to this computer were found.</b>")
            title.set_line_wrap(True)
            box.pack_start(title, False, False, 0)
            explanation = self.text_label("This app lists printers attached to this computer or named in your "
                                          "configuration file. Printers managed by another computer stay hidden.")
            box.pack_start(explanation, False, False, 0)
            button = Gtk.Button(label="Open printer settings")
            button.set_halign(Gtk.Align.START)
            button.set_tooltip_text("Tries the system printer settings, then the CUPS web page")
            button.connect("clicked", lambda *_: self.open_printer_settings())
            box.pack_start(button, False, False, 0)
            return box

        def caption_text(self):
            """Return the timestamp line under the summary."""
            stamp = GLib.DateTime.new_now_local().format("%H:%M:%S")
            prefix = "Demo · " if self.demo else ""
            return f"{prefix}Updated {stamp} · refreshes every 5 seconds"

        def set_summary(self, data, error):
            """Show the overall state sentence, hiding it while CUPS is unreachable."""
            if error:
                self.summary.hide()
                return
            self.summary.set_markup("<b>" + GLib.markup_escape_text(summary_text(data)) + "</b>")
            self.summary.show()

        def set_banner(self, error):
            """Show or hide the friendly unreachable banner."""
            if error:
                self.banner_label.set_markup("<b>" + GLib.markup_escape_text(UNREACHABLE_TITLE) + "</b>\n" +
                                             GLib.markup_escape_text(UNREACHABLE_HINT))
                self.banner.show()
            else:
                self.banner.hide()

        def open_cups_page(self):
            """Open the local CUPS web page in the default browser."""
            try:
                Gtk.show_uri_on_window(self.window, "http://localhost:631/printers/", Gdk.CURRENT_TIME)
            except GLib.Error as error:
                print(f"Cannot open the CUPS web page: {error}", file=sys.stderr)

        def open_printer_settings(self):
            """Open printer settings, falling back silently to the CUPS web page."""
            command = printer_settings_command()
            if command:
                try:
                    subprocess.Popen(command)
                    return
                except OSError:
                    pass
            self.open_cups_page()

        def notifications_toggled(self, item):
            """Persist the notifications preference and withdraw stale notifications."""
            self.notifications_enabled = item.get_active()
            try:
                save_notifications(self.notifications_enabled)
            except OSError as error:
                print(f"Cannot write the configuration file: {error}", file=sys.stderr)
            if not self.notifications_enabled and self.last_data:
                for card in self.last_data[0]:
                    self.withdraw_notification(f"printer-{card['name']}")
                self.withdraw_notification("print-service")

        def show_about(self):
            """Show the About dialog with the version, license and the promise that jobs stay untouched."""
            dialog = Gtk.AboutDialog(transient_for=self.window, modal=True)
            dialog.set_program_name("CUPS Tray Icon")
            dialog.set_version(__version__)
            dialog.set_comments("Shows local printer status in plain English. Printer settings change only your own "
                                "defaults; jobs and queue state are never touched.")
            dialog.set_license_type(Gtk.License.MIT_X11)
            dialog.set_website("https://github.com/M1chlCZ/cups-tray-icon")
            pixbuf = self.icon_pixbuf("cups-tray-icon.svg", 96)
            if pixbuf is not None:
                dialog.set_logo(pixbuf)
            else:
                dialog.set_logo_icon_name("printer")
            dialog.run()
            dialog.destroy()

        def setup_tray(self):
            """Export the StatusNotifierItem interface with icons and a friendly tooltip."""
            self.bus = self.get_dbus_connection()
            ready_icon = self.tray_pixmaps("printer-ready.svg")
            self.properties = {
                "Category": GLib.Variant("s", "Hardware"), "Id": GLib.Variant("s", "cups-tray-icon"),
                "Title": GLib.Variant("s", "CUPS Tray Icon"), "Status": GLib.Variant("s", "Active"),
                "WindowId": GLib.Variant("u", 0), "IconName": GLib.Variant("s", "printer-ready"),
                "IconPixmap": GLib.Variant("a(iiay)", ready_icon),
                "AttentionIconName": GLib.Variant("s", "printer-warning"),
                "AttentionIconPixmap": GLib.Variant("a(iiay)", self.tray_pixmaps("printer-warning.svg")),
                "OverlayIconName": GLib.Variant("s", ""), "OverlayIconPixmap": GLib.Variant("a(iiay)", []),
                "ItemIsMenu": GLib.Variant("b", False), "Menu": GLib.Variant("o", "/"),
                "ToolTip": GLib.Variant("(sa(iiay)ss)", ("printer-ready", ready_icon,
                                                         "Printers", "Checking printers…")),
            }
            properties_xml = "".join(f'<property name="{name}" type="{value.get_type_string()}" access="read"/>'
                                     for name, value in self.properties.items())
            methods_xml = "".join(f'<method name="{name}"><arg type="i" direction="in"/><arg type="i" direction="in"/></method>'
                                  for name in ("Activate", "SecondaryActivate", "ContextMenu"))
            self.interface = "org.kde.StatusNotifierItem"
            node = Gio.DBusNodeInfo.new_for_xml('<node><interface name="' + self.interface + '">' +
                properties_xml + methods_xml + '<signal name="NewIcon"/><signal name="NewToolTip"/></interface></node>')
            self.bus.register_object("/StatusNotifierItem", node.interfaces[0], self.tray_method,
                                     lambda _c, _s, _p, _i, name: self.properties.get(name), None)
            self.watcher = Gio.bus_watch_name_on_connection(self.bus, "org.kde.StatusNotifierWatcher",
                Gio.BusNameWatcherFlags.NONE, self.register_tray, lambda *_: None)

        def tray_pixmaps(self, svg_name):
            """Render a state icon at the tray sizes as a list of ARGB pixmap tuples."""
            pixmaps = []
            for size in TRAY_ICON_SIZES:
                pixbuf = self.icon_pixbuf(svg_name, size)
                if pixbuf is not None:
                    pixmaps.append(argb_pixmap(pixbuf))
            return pixmaps

        def update_tray(self, data, error):
            """Publish the state icon, attention state and friendly tooltip on the tray item."""
            icon_name, svg_name = tray_icon(data, error)
            pixmaps = self.tray_pixmaps(svg_name)
            attention = bool(error) or any(card["severity"] != "ok" for card in data)
            self.properties["IconName"] = GLib.Variant("s", icon_name)
            self.properties["IconPixmap"] = GLib.Variant("a(iiay)", pixmaps)
            self.properties["AttentionIconName"] = GLib.Variant("s", icon_name)
            self.properties["AttentionIconPixmap"] = GLib.Variant("a(iiay)", pixmaps)
            self.properties["Status"] = GLib.Variant("s", "NeedsAttention" if attention else "Active")
            self.properties["ToolTip"] = GLib.Variant("(sa(iiay)ss)", (icon_name, pixmaps, "Printers",
                                                                       tooltip_text(data, error)))
            self.bus.emit_signal(None, "/StatusNotifierItem", "org.freedesktop.DBus.Properties",
                "PropertiesChanged", GLib.Variant("(sa{sv}as)", (self.interface, self.properties, [])))
            for signal in ("NewIcon", "NewToolTip", "NewStatus"):
                self.bus.emit_signal(None, "/StatusNotifierItem", self.interface, signal, None)

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
            """Start a background status read unless one is already running."""
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
                data, error = [], UNREACHABLE_MESSAGE
            GLib.idle_add(self.render, data, error)

        def send_transition_notifications(self, previous_cards, data, previous_error, error):
            """Send one desktop notification per printer that changed into a problem state."""
            if not self.notifications_enabled:
                return
            for event in notification_events(previous_cards, data, previous_error, error):
                notification = Gio.Notification.new(event["title"])
                notification.set_body(event["body"])
                if event["urgent"]:
                    notification.set_priority(Gio.NotificationPriority.HIGH)
                notification.set_default_action("app.show")
                self.send_notification(event["id"], notification)

        def render(self, data, error):
            """Render one status snapshot into the window and the tray item."""
            self.busy = False
            self.caption.set_text(self.caption_text())
            if (data, error) == self.last_data:
                self.set_banner(error)
                return False
            previous_cards, previous_error = self.last_data if self.last_data is not None else (None, None)
            self.send_transition_notifications(previous_cards, data, previous_error, error)
            self.last_data = (data, error)
            self.set_banner(error)
            self.set_summary(data, error)
            for child in self.cards.get_children():
                self.cards.remove(child)
            if error:
                pass
            elif data:
                for card in data:
                    self.cards.pack_start(self.build_card(card), False, False, 0)
            else:
                self.cards.pack_start(self.build_empty_state(), False, False, 0)
            self.cards.show_all()
            self.update_tray(data, error)
            return False

    return PrinterStatus().run(sys.argv)


if __name__ == "__main__":
    if "--printer-options" in sys.argv:
        index = sys.argv.index("--printer-options") + 1
        printer = sys.argv[index] if index < len(sys.argv) else ""
        try:
            groups = demo_option_groups() if "--demo" in sys.argv else printer_option_groups(printer)
        except Exception:
            print("[]")
            raise SystemExit(1)
        print(json.dumps(groups))
    elif "--snapshot" in sys.argv:
        print(json.dumps(demo_snapshot() if "--demo" in sys.argv else snapshot()))
    else:
        raise SystemExit(run_desktop())
