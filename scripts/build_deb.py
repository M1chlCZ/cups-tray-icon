#!/usr/bin/env python3
"""Build a reproducible Debian package without root or maintainer scripts."""
import argparse
import gzip
import hashlib
import os
from pathlib import Path
import re
import subprocess
import tempfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("version")
    parser.add_argument("--output", type=Path, default=Path("dist"))
    args = parser.parse_args()
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", args.version):
        parser.error("version must have the form 1.2.3")
    source = Path(__file__).resolve().parents[1]
    epoch = int(os.environ.get("SOURCE_DATE_EPOCH", "0"))
    args.output.mkdir(parents=True, exist_ok=True)
    package = args.output.resolve() / f"cups-tray-icon_{args.version}_all.deb"
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        def write(name, content, mode=0o644):
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content.encode() if isinstance(content, str) else content)
            path.chmod(mode)
        for name in ("cups_tray_icon.py", "printer.svg", "label.svg"):
            write(f"usr/lib/cups-tray-icon/{name}", (source / name).read_bytes())
        write("usr/bin/cups-tray-icon", '#!/bin/sh\nexec /usr/bin/python3 /usr/lib/cups-tray-icon/cups_tray_icon.py "$@"\n', 0o755)
        write("usr/share/applications/cups-tray-icon.desktop",
              "[Desktop Entry]\nType=Application\nName=CUPS Tray Icon\n"
              "Comment=Local printer queues and readable error messages\n"
              "Exec=cups-tray-icon --show\nIcon=printer\nTerminal=false\n"
              "Categories=System;Monitor;\nStartupNotify=false\n")
        write("usr/share/doc/cups-tray-icon/copyright", (source / "LICENSE").read_bytes())
        write("usr/share/doc/cups-tray-icon/README.md", (source / "README.md").read_bytes())
        write("usr/share/doc/cups-tray-icon/changelog.gz", gzip.compress((source / "CHANGELOG.md").read_bytes(), mtime=0))
        size = sum(path.stat().st_size for path in root.rglob("*") if path.is_file())
        write("DEBIAN/control", f"Package: cups-tray-icon\nVersion: {args.version}\n"
              "Section: utils\nPriority: optional\nArchitecture: all\n"
              "Maintainer: M1chlCZ <66187879+M1chlCZ@users.noreply.github.com>\n"
              f"Installed-Size: {(size + 1023) // 1024}\n"
              "Depends: python3 (>= 3.10), python3-gi, python3-cups, gir1.2-gtk-3.0, librsvg2-common, cups-daemon\n"
              "Homepage: https://github.com/M1chlCZ/cups-tray-icon\n"
              "Description: Desktop tray overview for local CUPS printers\n"
              " Shows queue counts, USB presence and plain-English printer errors.\n"
              " Requires a graphical desktop with a StatusNotifierItem tray host.\n")
        for path in [root, *root.rglob("*")]:
            if path.is_dir():
                path.chmod(0o755)
            os.utime(path, (epoch, epoch))
        subprocess.run(["dpkg-deb", "--root-owner-group", "-Zxz", "--build", str(root), str(package)],
                       check=True, env=dict(os.environ, SOURCE_DATE_EPOCH=str(epoch)))
    digest = hashlib.sha256(package.read_bytes()).hexdigest()
    (args.output / "SHA256SUMS").write_text(f"{digest}  {package.name}\n")
    print(package)


if __name__ == "__main__":
    main()
