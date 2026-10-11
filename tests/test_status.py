"""Read-only printer status: run with unittest from this directory."""
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
import json
import os
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import cups_tray_icon as status


class PrinterStatusTests(unittest.TestCase):
    def test_config_is_optional_and_rejects_invalid_shapes(self):
        with tempfile.TemporaryDirectory() as config_dir, patch.dict(os.environ, {"XDG_CONFIG_HOME": config_dir}):
            self.assertEqual(status.load_forwarders(), {})
            config = Path(config_dir) / "cups-tray-icon/config.json"
            config.parent.mkdir()
            for value in ([], {"unknown": True}, {"usb_forwarders": []},
                          {"usb_forwarders": {"Office_Printer": ""}},
                          {"usb_forwarders": {"Office_Printer": 123}}):
                with self.subTest(value=value):
                    config.write_text(json.dumps(value))
                    with self.assertRaises(ValueError):
                        status.load_forwarders()
            config.write_text("{invalid")
            with self.assertRaises(ValueError):
                status.load_forwarders()

    def test_demo_never_reads_cups_or_local_config(self):
        with patch.dict(sys.modules, {"cups": None}), patch.object(status, "load_forwarders", side_effect=AssertionError):
            cards = status.demo_snapshot()
        self.assertEqual([card["queued"] for card in cards], [3, 2])
        self.assertEqual(cards[1]["severity"], "error")

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
        self.assertIn("held", card["message"].lower())

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


if __name__ == "__main__":
    unittest.main()
