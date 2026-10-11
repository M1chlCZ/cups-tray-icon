import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest


class InstallTests(unittest.TestCase):
    def test_install_update_and_uninstall_preserve_user_config(self):
        source = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix='cups tray % "$`\\ ') as root:
            env = dict(os.environ, XDG_DATA_HOME=root + "/data", XDG_CONFIG_HOME=root + "/config")
            def run(*args):
                subprocess.run([sys.executable, str(source / "install.py"), *args],
                               env=env, check=True, capture_output=True, text=True)
            run("--autostart")
            installed = Path(env["XDG_DATA_HOME"]) / "cups-tray-icon/cups_tray_icon.py"
            self.assertEqual(installed.read_bytes(), (source / "cups_tray_icon.py").read_bytes())
            desktop = Path(env["XDG_DATA_HOME"]) / "applications/cups-tray-icon.desktop"
            self.assertIn("%%", desktop.read_text())
            self.assertIn('" --show', desktop.read_text())
            autostart = Path(env["XDG_CONFIG_HOME"]) / "autostart/cups-tray-icon.desktop"
            self.assertNotIn("--show", autostart.read_text())
            if sys.platform == "linux":
                marker = installed.with_name("launched.txt")
                installed.write_text('from pathlib import Path\nimport sys\n'
                    'Path(__file__).with_name("launched.txt").write_text(" ".join(sys.argv[1:]))\n')
                subprocess.run(["gio", "launch", str(desktop)], check=True, capture_output=True)
                deadline = time.monotonic() + 5
                while not marker.exists() and time.monotonic() < deadline:
                    time.sleep(0.1)
                self.assertEqual(marker.read_text(), "--show")
            run()
            self.assertTrue(autostart.exists())
            config = Path(env["XDG_CONFIG_HOME"]) / "cups-tray-icon/config.json"
            config.parent.mkdir()
            config.write_text("{}")
            run("--uninstall")
            self.assertFalse(installed.exists())
            self.assertFalse(desktop.exists())
            self.assertFalse(autostart.exists())
            self.assertEqual(config.read_text(), "{}")


if __name__ == "__main__":
    unittest.main()
