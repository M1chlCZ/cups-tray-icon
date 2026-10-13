import hashlib
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

LIBRARY_FILES = {"cups_tray_icon.py", "printer.svg", "label.svg",
                 "cups-tray-icon.svg", "printer-ready.svg", "printer-warning.svg",
                 "printer-error.svg", "printer-offline.svg"}
THEME_ICONS = {"cups-tray-icon.svg", "printer-ready.svg", "printer-warning.svg",
               "printer-error.svg", "printer-offline.svg"}


@unittest.skipUnless(shutil.which("dpkg-deb"), "Debian packaging tools are required")
class DebianPackageTests(unittest.TestCase):
    def test_package_payload_dependencies_and_repeatable_build(self):
        source = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            command = [sys.executable, str(source / "scripts/build_deb.py"), "0.1.2", "--output", directory]
            subprocess.run(command, check=True, capture_output=True)
            package = output / "cups-tray-icon_0.1.2_all.deb"
            digest = hashlib.sha256(package.read_bytes()).hexdigest()
            subprocess.run(command, check=True, capture_output=True)
            self.assertEqual(hashlib.sha256(package.read_bytes()).hexdigest(), digest)
            self.assertEqual((output / "SHA256SUMS").read_text(), f"{digest}  {package.name}\n")
            fields = subprocess.check_output(["dpkg-deb", "-f", str(package)], text=True)
            for field in ("Package: cups-tray-icon", "Version: 0.1.2", "Architecture: all",
                          "python3 (>= 3.10)", "python3-gi", "python3-cups", "gir1.2-gtk-3.0",
                          "librsvg2-common", "cups-daemon"):
                self.assertIn(field, fields)
            root = output / "root"
            subprocess.run(["dpkg-deb", "-R", str(package), str(root)], check=True)
            self.assertEqual((root / "usr/lib/cups-tray-icon/cups_tray_icon.py").read_bytes(),
                             (source / "cups_tray_icon.py").read_bytes())
            self.assertEqual({path.name for path in (root / "usr/lib/cups-tray-icon").iterdir()},
                             LIBRARY_FILES)
            theme = root / "usr/share/icons/hicolor/scalable/apps"
            self.assertEqual({path.name for path in theme.iterdir()}, THEME_ICONS)
            self.assertEqual((theme / "cups-tray-icon.svg").read_bytes(), (source / "cups-tray-icon.svg").read_bytes())
            self.assertEqual((root / "usr/bin/cups-tray-icon").stat().st_mode & 0o777, 0o755)
            desktop = (root / "usr/share/applications/cups-tray-icon.desktop").read_text()
            self.assertIn("Exec=cups-tray-icon --show", desktop)
            self.assertIn("Icon=cups-tray-icon", desktop)
            self.assertFalse((root / "etc").exists())
            self.assertFalse((root / "home").exists())
            self.assertEqual({path.name for path in (root / "DEBIAN").iterdir()}, {"control"})


if __name__ == "__main__":
    unittest.main()
