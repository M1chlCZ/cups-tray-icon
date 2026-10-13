"""Printer status and personal printer settings: run with unittest from this directory."""
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
import json
import os
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import cups_tray_icon as status


class FakePixbuf:
    """Minimal stand-in for GdkPixbuf used by the ARGB conversion tests."""

    def __init__(self, width, height, channels, pixels):
        self.width = width
        self.height = height
        self.channels = channels
        self.pixels = pixels

    def get_width(self):
        return self.width

    def get_height(self):
        return self.height

    def get_rowstride(self):
        return self.width * self.channels

    def get_n_channels(self):
        return self.channels

    def get_pixels(self):
        return self.pixels


class ConfigTests(unittest.TestCase):
    def test_config_is_optional_and_rejects_invalid_shapes(self):
        with tempfile.TemporaryDirectory() as config_dir, patch.dict(os.environ, {"XDG_CONFIG_HOME": config_dir}):
            self.assertEqual(status.load_config(), {"usb_forwarders": {}, "notifications": True})
            self.assertEqual(status.load_forwarders(), {})
            config = Path(config_dir) / "cups-tray-icon/config.json"
            config.parent.mkdir()
            for value in ([], {"unknown": True}, {"usb_forwarders": []},
                          {"usb_forwarders": {"Office_Printer": ""}},
                          {"usb_forwarders": {"Office_Printer": 123}},
                          {"usb_forwarders": {}, "notifications": "yes"},
                          {"usb_forwarders": {}, "notifications": 1},
                          {"usb_forwarders": {}, "extra": True}):
                with self.subTest(value=value):
                    config.write_text(json.dumps(value))
                    with self.assertRaises(ValueError):
                        status.load_config()
            config.write_text("{invalid")
            with self.assertRaises(ValueError):
                status.load_config()

    def test_config_reads_notifications_and_forwarders(self):
        with tempfile.TemporaryDirectory() as config_dir, patch.dict(os.environ, {"XDG_CONFIG_HOME": config_dir}):
            config = Path(config_dir) / "cups-tray-icon/config.json"
            config.parent.mkdir()
            config.write_text(json.dumps({"usb_forwarders": {"Office_Printer": "SERIAL"}, "notifications": False}))
            self.assertEqual(status.load_config(),
                             {"usb_forwarders": {"Office_Printer": "SERIAL"}, "notifications": False})
            self.assertEqual(status.load_forwarders(), {"Office_Printer": "SERIAL"})

    def test_save_notifications_creates_and_preserves_config(self):
        with tempfile.TemporaryDirectory() as config_dir, patch.dict(os.environ, {"XDG_CONFIG_HOME": config_dir}):
            config = Path(config_dir) / "cups-tray-icon/config.json"
            status.save_notifications(False)
            self.assertEqual(json.loads(config.read_text()), {"notifications": False, "usb_forwarders": {}})
            config.write_text(json.dumps({"usb_forwarders": {"Office_Printer": "SERIAL"}, "notifications": False}))
            status.save_notifications(True)
            self.assertEqual(json.loads(config.read_text()),
                             {"notifications": True, "usb_forwarders": {"Office_Printer": "SERIAL"}})
            self.assertEqual(list(config.parent.glob("*.tmp")), [])
            self.assertTrue(status.load_config()["notifications"])

    def test_save_notifications_rewrites_invalid_config(self):
        with tempfile.TemporaryDirectory() as config_dir, patch.dict(os.environ, {"XDG_CONFIG_HOME": config_dir}):
            config = Path(config_dir) / "cups-tray-icon/config.json"
            config.parent.mkdir()
            config.write_text("{invalid")
            status.save_notifications(False)
            self.assertEqual(status.load_config(), {"usb_forwarders": {}, "notifications": False})


class PrinterStatusTests(unittest.TestCase):
    def test_demo_never_reads_cups_or_local_config(self):
        with patch.dict(sys.modules, {"cups": None}), patch.object(status, "load_config", side_effect=AssertionError):
            cards = status.demo_snapshot()
        self.assertEqual([card["state"] for card in cards],
                         ["Idle", "Printing", "Idle", "Paused", "Disconnected"])
        self.assertEqual([card["severity"] for card in cards], ["ok", "ok", "warning", "error", "error"])
        self.assertEqual([card["queued"] for card in cards], [0, 2, 0, 1, 0])
        self.assertEqual([status.display_state(card["state"], card["severity"]) for card in cards],
                         ["Ready", "Printing", "Needs attention", "Needs attention", "Offline"])
        self.assertEqual([card["image"] for card in cards],
                         ["printer-ready.svg", "printer-ready.svg", "printer-warning.svg",
                          "printer-error.svg", "printer-offline.svg"])

    def test_snapshot_shows_local_usb_queues_even_when_switched_off(self):
        cups = Mock()
        connection = cups.Connection.return_value
        connection.getPrinters.return_value = {
            "Label_Printer": {"printer-state": 3, "device-uri": "usb://Example/Label_Printer?serial=ABC"},
            "Office_Printer": {"printer-state": 3,
                "device-uri": "socket://127.0.0.1:9100/?waiteof=false"},
            "Discovered_Printer": {"device-uri": "implicitclass://Discovered_Printer/"},
            "Network IPP": {"device-uri": "ipp://other-pc/printers/Printer"},
            "Network socket": {"device-uri": "socket://192.0.2.10:9100/"},
            "Bonjour": {"device-uri": "dnssd://Printer._ipp._tcp.local/"},
        }
        connection.getJobs.return_value = {}
        connection.getPrinterAttributes.return_value = {"printer-is-accepting-jobs": True}
        with tempfile.TemporaryDirectory() as config_dir:
            config = Path(config_dir) / "cups-tray-icon"
            config.mkdir()
            (config / "config.json").write_text(json.dumps({"usb_forwarders": {"Office_Printer": "EXAMPLE_SERIAL"}}))
            with patch.dict(sys.modules, {"cups": cups}), patch.dict(os.environ, {"XDG_CONFIG_HOME": config_dir}), patch.object(status.Path, "glob", return_value=[]):
                cards = status.snapshot()
        self.assertEqual([card["name"] for card in cards], ["Label_Printer", "Office_Printer"])
        self.assertTrue(all(card["state"] == "Disconnected" for card in cards))
        connection.getJobs.assert_called_once_with(which_jobs="not-completed", my_jobs=False,
            requested_attributes=["job-printer-uri", "job-state"])

    def test_queue_counts_include_other_users_and_held_jobs_only_in_own_queue(self):
        jobs = {1: {"job-printer-uri": "ipp://localhost/printers/Label%20printer", "job-state": 3},
                2: {"job-printer-uri": "ipp://localhost/printers/Label%20printer", "job-state": 4},
                3: {"job-printer-uri": "ipp://localhost/printers/Office_Printer", "job-state": 5},
                4: {"job-printer-uri": "ipp://localhost/printers/Label%20printer", "job-state": 9}}
        card = status.printer_card("Label printer", {"printer-state": 3}, jobs)
        self.assertEqual(card["queued"], 2)
        self.assertEqual(card["held"], 1)
        self.assertIn("waiting for attention", card["message"])

    def test_error_and_warning_suffixes_are_explained(self):
        card = status.printer_card("Label_Printer", {"printer-state": 5,
            "printer-state-reasons": ["media-empty-error", "cover-open-warning"]}, {})
        self.assertIn("paper", card["message"].lower())
        self.assertIn("cover", card["message"].lower())
        self.assertEqual(card["severity"], "error")

    def test_usb_offline_overrides_idle_without_claiming_physical_readiness(self):
        card = status.printer_card("Label_Printer", {"printer-state": 3,
            "device-uri": "usb://Example/Label_Printer?serial=ABC"}, {}, set())
        self.assertEqual(card["state"], "Disconnected")
        self.assertIn("USB", card["message"])
        card = status.printer_card("Label_Printer", {"printer-state": 3}, {})
        self.assertEqual(card["state"], "Idle")
        self.assertIn("reported", card["message"])

    def test_unknown_reason_and_original_message_are_preserved(self):
        card = status.printer_card("Printer", {"printer-state": 3,
            "printer-state-reasons": ["vendor-strange-fault-error"],
            "printer-state-message": "Original diagnostic <123>"}, {})
        self.assertEqual(card["severity"], "error")
        self.assertIn("vendor-strange-fault", card["message"])
        self.assertIn("Original diagnostic <123>", card["details"])

    def test_filter_failure_has_actionable_english(self):
        card = status.printer_card("Printer", {"printer-state": 5,
            "printer-state-reasons": ["cups-filter-error"],
            "printer-state-message": "Filter failed"}, {})
        self.assertIn("prepare", card["message"])

    def test_forwarded_usb_route_explains_bypassed_jobs(self):
        card = status.printer_card("Office_Printer", {"printer-state": 3}, {}, usb_serial="EXAMPLE_SERIAL")
        self.assertIn("directly", card["note"])
        self.assertIn("not included", card["note"])

    def test_warning_does_not_become_a_stopped_printer(self):
        card = status.printer_card("Printer", {"printer-state": 3,
            "printer-state-reasons": ["marker-supply-low-warning"]}, {})
        self.assertEqual(card["severity"], "warning")
        self.assertEqual(card["state"], "Idle")

    def test_stopped_job_explained_even_if_printer_is_idle(self):
        card = status.printer_card("Printer", {"printer-state": 3},
            {5: {"job-printer-uri": "ipp://localhost/printers/Printer", "job-state": 6}})
        self.assertEqual(card["severity"], "error")
        self.assertIn("stopped", card["message"])

    def test_primary_messages_avoid_technical_jargon(self):
        cards = [status.printer_card("Printer", {"printer-state": 3,
                 "printer-state-reasons": [reason]}, {}) for reason in status.REASONS]
        cards.append(status.printer_card("Printer", {"printer-state": 5,
            "printer-is-accepting-jobs": False}, {
                5: {"job-printer-uri": "ipp://localhost/printers/Printer", "job-state": 4},
                6: {"job-printer-uri": "ipp://localhost/printers/Printer", "job-state": 6}}))
        cards.append(status.printer_card("Printer", {"printer-state": 3,
            "printer-state-message": "Something broke"}, {}))
        for card in cards:
            with self.subTest(card=card["message"]):
                self.assertNotRegex(card["message"].lower(), r"\b(queue|spool|filter)s?\b")


class NotificationTests(unittest.TestCase):
    def card(self, name, severity="ok", held=0, stopped=0, message="Something needs attention."):
        return {"name": name, "title": name.replace("_", " "), "severity": severity,
                "held": held, "stopped": stopped, "message": message}

    def test_first_snapshot_only_sets_the_baseline(self):
        self.assertEqual(status.notification_events(None, [self.card("Printer", severity="error")]), [])

    def test_entering_error_severity_notifies_once(self):
        ok = [self.card("Printer")]
        error = [self.card("Printer", severity="error")]
        events = status.notification_events(ok, error)
        self.assertEqual([event["id"] for event in events], ["printer-Printer"])
        self.assertTrue(events[0]["urgent"])
        self.assertEqual(len(status.notification_events([self.card("Printer", severity="warning")], error)), 1)
        self.assertEqual(status.notification_events(error, error), [])
        self.assertEqual(status.notification_events(ok, [self.card("Printer", severity="warning")]), [])

    def test_new_waiting_documents_notify_once(self):
        idle = [self.card("Printer")]
        held = [self.card("Printer", held=2, severity="warning")]
        events = status.notification_events(idle, held)
        self.assertEqual([event["id"] for event in events], ["printer-Printer"])
        self.assertIn("2 documents", events[0]["body"])
        self.assertEqual(status.notification_events(held, held), [])
        self.assertEqual(status.notification_events(held, idle), [])

    def test_error_transition_supersedes_waiting_documents(self):
        idle = [self.card("Printer")]
        broken = [self.card("Printer", severity="error", held=1, message="Paper is empty.")]
        events = status.notification_events(idle, broken)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["body"], "Paper is empty.")

    def test_stopped_documents_count_as_waiting_documents(self):
        events = status.notification_events([self.card("Printer")], [self.card("Printer", stopped=3)])
        self.assertEqual(len(events), 1)
        self.assertIn("3 documents", events[0]["body"])

    def test_cups_unreachable_and_recovery_notify(self):
        cards = [self.card("Printer")]
        events = status.notification_events(cards, [], None, status.UNREACHABLE_MESSAGE)
        self.assertEqual([event["id"] for event in events], ["print-service"])
        self.assertTrue(events[0]["urgent"])
        self.assertIn("retry", events[0]["body"])
        self.assertEqual(status.notification_events([], [], status.UNREACHABLE_MESSAGE,
                                                    status.UNREACHABLE_MESSAGE), [])
        events = status.notification_events([], cards, status.UNREACHABLE_MESSAGE, None)
        self.assertEqual([event["id"] for event in events], ["print-service"])
        self.assertFalse(events[0]["urgent"])

    def test_new_printer_is_not_a_transition(self):
        self.assertEqual(status.notification_events([], [self.card("New", severity="error")]), [])


class PresentationTests(unittest.TestCase):
    def test_waiting_text_phrasing(self):
        self.assertEqual(status.waiting_text(0), "Nothing waiting")
        self.assertEqual(status.waiting_text(1), "1 document waiting")
        self.assertEqual(status.waiting_text(4), "4 documents waiting")

    def test_display_state_covers_every_case(self):
        self.assertEqual(status.display_state("Idle", "ok"), "Ready")
        self.assertEqual(status.display_state("Printing", "ok"), "Printing")
        self.assertEqual(status.display_state("Idle", "warning"), "Needs attention")
        self.assertEqual(status.display_state("Paused", "error"), "Needs attention")
        self.assertEqual(status.display_state("Disconnected", "error"), "Offline")
        self.assertEqual(status.display_state("Something", "ok"), "Unknown")

    def test_state_icon_matches_every_case(self):
        self.assertEqual(status.state_icon("Idle", "ok"), "printer-ready.svg")
        self.assertEqual(status.state_icon("Printing", "ok"), "printer-ready.svg")
        self.assertEqual(status.state_icon("Idle", "warning"), "printer-warning.svg")
        self.assertEqual(status.state_icon("Paused", "error"), "printer-error.svg")
        self.assertEqual(status.state_icon("Disconnected", "error"), "printer-offline.svg")
        self.assertEqual(status.state_icon("Something", "ok"), "printer-warning.svg")

    def test_tray_icon_uses_the_worst_state(self):
        ready = {"severity": "ok"}
        warning = {"severity": "warning"}
        error = {"severity": "error"}
        self.assertEqual(status.tray_icon([ready]), ("printer-ready", "printer-ready.svg"))
        self.assertEqual(status.tray_icon([ready, warning]), ("printer-warning", "printer-warning.svg"))
        self.assertEqual(status.tray_icon([ready, warning, error]), ("printer-error", "printer-error.svg"))
        self.assertEqual(status.tray_icon([], "broken"), ("printer-offline", "printer-offline.svg"))

    def test_summary_sentences(self):
        self.assertEqual(status.summary_text([]), "No printers found.")
        self.assertEqual(status.summary_text([{"severity": "ok"}, {"severity": "ok"}]), "All printers are ready.")
        self.assertEqual(status.summary_text([{"severity": "ok"}, {"severity": "warning"}]),
                         "1 printer needs attention.")
        self.assertEqual(status.summary_text([{"severity": "warning"}, {"severity": "error"}]),
                         "2 printers need attention.")

    def test_tooltip_lists_every_printer_and_truncates(self):
        cards = [{"title": "Office printer", "state": "Idle", "severity": "ok"},
                 {"title": "Label printer", "state": "Printing", "severity": "ok"},
                 {"title": "X" * 40, "state": "Paused", "severity": "error"}]
        lines = status.tooltip_text(cards).splitlines()
        self.assertEqual(lines, ["Office printer — Ready", "Label printer — Printing",
                                 "X" * 31 + "… — Needs attention"])
        cards = [{"title": f"Printer {index}", "state": "Idle", "severity": "ok"} for index in range(8)]
        lines = status.tooltip_text(cards, limit=6).splitlines()
        self.assertEqual(len(lines), 7)
        self.assertEqual(lines[-1], "2 more printers")
        lines = status.tooltip_text(cards[:7]).splitlines()
        self.assertEqual(lines[-1], "1 more printer")
        self.assertEqual(status.tooltip_text([], "broken"), "Cannot reach the print service. Retrying automatically.")
        self.assertEqual(status.tooltip_text([]), "No printers found.")

    def test_printer_uri_percent_encodes_the_name(self):
        self.assertEqual(status.printer_uri("Label printer"), "http://localhost:631/printers/Label%20printer")
        self.assertEqual(status.printer_uri("50%_Printer"), "http://localhost:631/printers/50%25_Printer")

    def test_printer_settings_command_uses_what_is_installed(self):
        both = lambda name: f"/usr/bin/{name}"
        self.assertEqual(_with_which(both), ["system-config-printer"])
        self.assertEqual(_with_which(lambda name: None), None)
        self.assertEqual(_with_which(lambda name: "/usr/bin/gnome-control-center" if name == "gnome-control-center"
                                     else None),
                         ["gnome-control-center", "printers"])

    def test_argb_pixmap_reorders_rgba_into_network_byte_order(self):
        pixbuf = FakePixbuf(2, 1, 4, bytes((1, 2, 3, 4, 5, 6, 7, 8)))
        self.assertEqual(status.argb_pixmap(pixbuf), (2, 1, bytes((4, 1, 2, 3, 8, 5, 6, 7))))

    def test_argb_pixmap_adds_opaque_alpha_without_alpha_channel(self):
        pixbuf = FakePixbuf(1, 1, 3, bytes((16, 32, 48)))
        self.assertEqual(status.argb_pixmap(pixbuf), (1, 1, bytes((255, 16, 32, 48))))


class IconTests(unittest.TestCase):
    def test_referenced_icons_exist_next_to_the_module(self):
        root = Path(status.__file__).resolve().parent
        cases = [("Idle", "ok"), ("Idle", "warning"), ("Paused", "error"), ("Disconnected", "error")]
        names = {status.state_icon(state, severity) for state, severity in cases}
        names.add("cups-tray-icon.svg")
        for name in sorted(names):
            with self.subTest(name=name):
                self.assertTrue((root / name).is_file(), name)


PPD_FIXTURE = """*PPD-Adobe: "4.3"
*OpenUI *PageSize/Page Size: PickOne
*DefaultPageSize: A4
*PageSize A3/A3: "<</PageSize [842 1191]>> setpagedevice"
*PageSize A4/A4: "<</PageSize [595 842]>> setpagedevice"
*PageSize A5/A5: "<</PageSize [420 595]>> setpagedevice"
*CloseUI: *PageSize
*OpenUI *ColorMode/Color Mode: PickOne
*DefaultColorMode: Gray
*ColorMode Gray/Grayscale: "<</cupsColorSpace 1>> setpagedevice"
*ColorMode Color/Color: "<</cupsColorSpace 2>> setpagedevice"
*CloseUI: *ColorMode
*OpenUI *Duplex/Two-Sided Printing: PickOne
*DefaultDuplex: None
*Duplex None/Off: "<</Duplex false>> setpagedevice"
*Duplex DuplexNoTumble/Long Edge: "<</Duplex true /Tumble false>> setpagedevice"
*Duplex DuplexTumble/Short Edge: "<</Duplex true /Tumble true>> setpagedevice"
*CloseUI: *Duplex
*OpenUI *MediaType/Media Type: PickOne
*DefaultMediaType: Plain
*MediaType Plain/Plain: "<</MediaType (Plain)>> setpagedevice"
*CloseUI: *MediaType
"""

ATTRIBUTES_FIXTURE = {
    "media-supported": ["iso_a4_210x297mm", "iso_a5_148x210mm", "na_letter_8.5x11in",
                        "om_100x150mm_100x150mm"],
    "media-default": "iso_a4_210x297mm",
    "print-color-mode-supported": ["color", "monochrome"],
    "print-color-mode-default": "color",
    "sides-supported": ["one-sided", "two-sided-long-edge", "two-sided-short-edge"],
    "sides-default": "one-sided",
    "media-type-supported": ["plain", "photographic", "glossy", "labels"],
    "media-type-default": "plain",
}


class OptionDiscoveryTests(unittest.TestCase):
    def test_attribute_groups_are_friendly_and_paper_sizes_are_sorted(self):
        groups = status.option_groups_from_attributes(ATTRIBUTES_FIXTURE)
        self.assertEqual([group["key"] for group in groups],
                         ["media", "print-color-mode", "sides", "media-type"])
        self.assertEqual([group["label"] for group in groups],
                         ["Paper size", "Color mode", "Two-sided printing", "Paper type"])
        media = groups[0]
        self.assertEqual([option["label"] for option in media["options"]],
                         ["10x15", "A4", "A5", "Letter"])
        self.assertEqual([option["value"] for option in media["options"]],
                         ["om_100x150mm_100x150mm", "iso_a4_210x297mm", "iso_a5_148x210mm",
                          "na_letter_8.5x11in"])
        self.assertEqual(media["current"], "iso_a4_210x297mm")
        self.assertEqual([option["label"] for option in groups[1]["options"]], ["Color", "Monochrome"])
        self.assertEqual([option["label"] for option in groups[2]["options"]],
                         ["One-sided", "Two-sided (long edge)", "Two-sided (short edge)"])
        self.assertEqual([option["label"] for option in groups[3]["options"]],
                         ["Plain paper", "Photo paper", "Glossy paper", "Labels"])

    def test_override_beats_the_printer_default_in_both_naming_styles(self):
        groups = status.option_groups_from_attributes(ATTRIBUTES_FIXTURE, {"media": "iso_a5_148x210mm"})
        self.assertEqual(groups[0]["current"], "iso_a5_148x210mm")
        groups = status.option_groups_from_attributes(ATTRIBUTES_FIXTURE, {"PageSize": "na_letter_8.5x11in"})
        self.assertEqual(groups[0]["current"], "na_letter_8.5x11in")

    def test_single_value_groups_are_skipped_and_empty_discovery_returns_a_list(self):
        self.assertEqual(status.option_groups_from_attributes({"media-supported": ["iso_a4_210x297mm"]}), [])
        self.assertEqual(status.option_groups_from_ppd(""), [])
        self.assertEqual(status.discover_option_groups({}, "", {}), [])
        self.assertEqual(status.discover_option_groups(None, None), [])

    def test_ppd_groups_keep_ppd_names_and_skip_single_options(self):
        groups = status.option_groups_from_ppd(PPD_FIXTURE)
        self.assertEqual([group["key"] for group in groups], ["PageSize", "ColorMode", "Duplex"])
        self.assertEqual(groups[0]["current"], "A4")
        self.assertEqual([option["label"] for option in groups[0]["options"]], ["A3", "A4", "A5"])
        self.assertEqual(groups[1]["current"], "Gray")
        self.assertEqual([option["label"] for option in groups[1]["options"]], ["Monochrome", "Color"])
        self.assertEqual(groups[2]["current"], "None")
        self.assertEqual([option["label"] for option in groups[2]["options"]],
                         ["One-sided", "Two-sided (long edge)", "Two-sided (short edge)"])

    def test_ipp_groups_win_the_merge_and_the_ppd_fills_the_rest(self):
        ipp_groups = status.option_groups_from_attributes(ATTRIBUTES_FIXTURE)
        ppd_groups = status.option_groups_from_ppd(PPD_FIXTURE)
        merged = status.merge_option_groups(ipp_groups, ppd_groups)
        self.assertEqual([group["key"] for group in merged],
                         ["media", "print-color-mode", "sides", "media-type"])
        partial = status.discover_option_groups(
            {"print-color-mode-supported": ["color", "monochrome"],
             "print-color-mode-default": "monochrome"}, PPD_FIXTURE)
        self.assertEqual([group["key"] for group in partial], ["PageSize", "print-color-mode", "Duplex"])
        self.assertEqual(partial[1]["current"], "monochrome")

    def test_demo_option_groups_offer_the_common_settings(self):
        groups = status.demo_option_groups()
        self.assertEqual([group["key"] for group in groups],
                         ["media", "print-color-mode", "sides", "media-type"])
        self.assertEqual(groups[0]["current"], "iso_a4_210x297mm")


class OptionLabelTests(unittest.TestCase):
    def test_known_values_get_friendly_labels(self):
        self.assertEqual(status.option_label("media", "iso_a4_210x297mm"), "A4")
        self.assertEqual(status.option_label("PageSize", "A4"), "A4")
        self.assertEqual(status.option_label("media", "iso_a5_148x210mm"), "A5")
        self.assertEqual(status.option_label("media", "iso_a6_105x148mm"), "A6")
        self.assertEqual(status.option_label("media", "na_letter_8.5x11in"), "Letter")
        self.assertEqual(status.option_label("media", "na_legal_8.5x14in"), "Legal")
        self.assertEqual(status.option_label("media", "om_100x150mm_100x150mm"), "10x15")
        self.assertEqual(status.option_label("media", "100x150"), "100x150")
        self.assertEqual(status.option_label("sides", "two-sided-short-edge"), "Two-sided (short edge)")
        self.assertEqual(status.option_label("Duplex", "DuplexTumble"), "Two-sided (short edge)")
        self.assertEqual(status.option_label("Duplex", "None"), "One-sided")
        self.assertEqual(status.option_label("print-color-mode", "monochrome"), "Monochrome")
        self.assertEqual(status.option_label("ColorMode", "Gray"), "Monochrome")
        self.assertEqual(status.option_label("media-type", "photographic"), "Photo paper")
        self.assertEqual(status.option_label("MediaType", "Glossy"), "Glossy paper")
        self.assertEqual(status.option_label("media-type", "labels"), "Labels")

    def test_unknown_values_are_prettified(self):
        self.assertEqual(status.prettify_option("vendor_special_size"), "Vendor Special Size")
        self.assertEqual(status.prettify_option("tray_2"), "Tray 2")
        self.assertEqual(status.option_label("media", "vendor_special_size"), "Vendor Special Size")


class LpOptionsTests(unittest.TestCase):
    def test_parse_lpoptions_reads_pairs_and_ignores_noise(self):
        self.assertEqual(status.parse_lpoptions("media=iso_a4_210x297mm sides=two-sided-long-edge"),
                         {"media": "iso_a4_210x297mm", "sides": "two-sided-long-edge"})
        self.assertEqual(status.parse_lpoptions("=broken lonely"), {})
        self.assertEqual(status.parse_lpoptions(None), {})

    def test_read_personal_defaults_tolerates_missing_and_failing_lpoptions(self):
        with patch.object(status.subprocess, "run", side_effect=FileNotFoundError):
            self.assertEqual(status.read_personal_defaults("Office_Printer"), {})
        with patch.object(status.subprocess, "run", return_value=Mock(returncode=1, stdout="")):
            self.assertEqual(status.read_personal_defaults("Office_Printer"), {})
        with patch.object(status.subprocess, "run",
                          return_value=Mock(returncode=0, stdout="PageSize=A5 Duplex=DuplexNoTumble")):
            self.assertEqual(status.read_personal_defaults("Office_Printer"),
                             {"PageSize": "A5", "Duplex": "DuplexNoTumble"})


class SettingsApplyTests(unittest.TestCase):
    def groups(self):
        return status.option_groups_from_attributes(ATTRIBUTES_FIXTURE)

    def test_command_contains_only_changed_groups_in_group_order(self):
        command = status.build_lpoptions_command("Office_Printer", self.groups(),
                                                 {"sides": "two-sided-long-edge", "media": "iso_a5_148x210mm"})
        self.assertEqual(command, ["lpoptions", "-p", "Office_Printer",
                                   "-o", "media=iso_a5_148x210mm",
                                   "-o", "sides=two-sided-long-edge"])
        self.assertIsNone(status.build_lpoptions_command("Office_Printer", self.groups(),
                                                         {"media": "iso_a4_210x297mm"}))
        self.assertIsNone(status.build_lpoptions_command("Office_Printer", [], {}))
        self.assertIsNone(status.build_lpoptions_command("Office_Printer", self.groups(), {}))

    def test_unsupported_values_and_unknown_keys_are_rejected_before_running(self):
        with self.assertRaises(ValueError):
            status.build_lpoptions_command("Office_Printer", self.groups(), {"media": "trade_poster"})
        with self.assertRaises(ValueError):
            status.build_lpoptions_command("Office_Printer", self.groups(), {"tray": "1"})
        with patch.object(status.subprocess, "run") as run:
            self.assertEqual(status.apply_printer_options("Office_Printer", self.groups(),
                                                          {"media": "trade_poster"}),
                             status.APPLY_FAILED_MESSAGE)
        run.assert_not_called()

    def test_apply_runs_lpoptions_and_reports_failures(self):
        command = ["lpoptions", "-p", "Office_Printer", "-o", "media=iso_a5_148x210mm"]
        with patch.object(status.subprocess, "run", return_value=Mock(returncode=0)) as run:
            self.assertIsNone(status.apply_printer_options("Office_Printer", self.groups(),
                                                           {"media": "iso_a5_148x210mm"}))
        run.assert_called_once_with(command, capture_output=True, text=True, timeout=15)
        with patch.object(status.subprocess, "run", return_value=Mock(returncode=1)):
            self.assertEqual(status.apply_printer_options("Office_Printer", self.groups(),
                                                          {"media": "iso_a5_148x210mm"}),
                             status.APPLY_FAILED_MESSAGE)
        with patch.object(status.subprocess, "run", side_effect=OSError):
            self.assertEqual(status.apply_printer_options("Office_Printer", self.groups(),
                                                          {"media": "iso_a5_148x210mm"}),
                             status.APPLY_FAILED_MESSAGE)


class LiveDiscoveryTests(unittest.TestCase):
    def test_live_discovery_reads_attributes_ppd_and_overrides(self):
        with tempfile.TemporaryDirectory() as directory:
            ppd = Path(directory) / "printer.ppd"
            ppd.write_text(PPD_FIXTURE)
            cups = Mock()
            connection = cups.Connection.return_value
            connection.getPrinterAttributes.return_value = {
                "media-supported": ["iso_a4_210x297mm", "iso_a5_148x210mm"],
                "media-default": "iso_a5_148x210mm",
            }
            connection.getPPD3.side_effect = AttributeError("getPPD3 is unavailable")
            connection.getPPD.return_value = str(ppd)
            with patch.dict(sys.modules, {"cups": cups}), patch.object(
                    status.subprocess, "run", return_value=Mock(returncode=0, stdout="Duplex=DuplexTumble")):
                groups = status.printer_option_groups("Office_Printer")
        cups.setServer.assert_called_once_with("localhost")
        cups.setPort.assert_called_once_with(631)
        connection.getPrinterAttributes.assert_called_once_with("Office_Printer")
        self.assertEqual([group["key"] for group in groups], ["media", "ColorMode", "Duplex"])
        self.assertEqual(groups[0]["current"], "iso_a5_148x210mm")
        self.assertEqual(groups[1]["current"], "Gray")
        self.assertEqual(groups[2]["current"], "DuplexTumble")


def _with_which(which):
    with patch.object(status.shutil, "which", side_effect=which):
        return status.printer_settings_command()


if __name__ == "__main__":
    unittest.main()
