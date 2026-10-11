# CUPS Tray Icon

[![CI](https://github.com/M1chlCZ/cups-tray-icon/actions/workflows/ci.yml/badge.svg)](https://github.com/M1chlCZ/cups-tray-icon/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

A small Linux tray app for local CUPS printers. Click the printer icon to see
queue counts, USB connection status and errors in plain English.

![CUPS Tray Icon showing two fictional printers, a print queue and a paper warning](docs/screenshot.png)

*The actual GTK interface in demo mode. Printer names, jobs and errors are fictional.*

## Features

- Local USB printer queues, including configured USB forwarders.
- Separate cards with printer illustrations, queue counts and held-job warnings.
- Explanations for missing paper, open covers, driver failures and other CUPS errors.
- USB presence checks where a device serial is available.
- Status refresh every five seconds, with a timeout when CUPS does not respond.
- Desktop tray integration and optional startup at login.

The app reads status. It does not print documents, cancel jobs or change printer
settings. Network-discovered queues are hidden by default. Configured local
printers stay visible when switched off, so queued work remains visible too.

## Requirements

- Linux with Python 3.10 or newer.
- GTK 3, PyGObject and pycups.
- A local CUPS server with printers already configured.
- A desktop panel that supports **StatusNotifierItem** tray icons.

The interface was checked on Raspberry Pi OS with labwc and wf-panel-pi.
Other panels need to provide the same tray protocol. A GNOME desktop may need
a tray extension. This is a desktop-session app; run it as your normal user.

## Install a Debian package

For Debian 12+, Ubuntu 22.04+ and current Raspberry Pi OS, download the `.deb`
and `SHA256SUMS` from the [latest release](https://github.com/M1chlCZ/cups-tray-icon/releases/latest).
From the download directory:

```sh
sha256sum --check SHA256SUMS
sudo apt update
sudo apt install ./cups-tray-icon_0.1.2_all.deb
cups-tray-icon --show
```

Use the filename from your downloaded release. APT installs the declared Python,
GTK, pycups, SVG and CUPS-daemon dependencies. The same `all` package works on ARM
and x86 systems with those dependencies; desktop tray support is still required.
The package adds an application-menu entry and a `cups-tray-icon` command.
It does not change printer queues or enable autostart for every user.

To start it automatically for your user:

```sh
mkdir -p "${XDG_CONFIG_HOME:-$HOME/.config}/autostart"
sed 's/ --show$//' /usr/share/applications/cups-tray-icon.desktop > "${XDG_CONFIG_HOME:-$HOME/.config}/autostart/cups-tray-icon.desktop"
```

Upgrade by installing the next release's `.deb` the same way. There is no APT
repository configured for automatic updates. Remove the package with
`sudo apt remove cups-tray-icon`; local config remains untouched. Remove the
autostart entry separately and log out to stop a running instance.

If you previously used `install.py`, uninstall that user copy first so its menu
entry does not shadow the system package. Your printer config is preserved.

## Run from source

On Debian, Ubuntu or Raspberry Pi OS:

```sh
sudo apt install python3-gi gir1.2-gtk-3.0 python3-cups librsvg2-common
git clone https://github.com/M1chlCZ/cups-tray-icon.git
cd cups-tray-icon
/usr/bin/python3 cups_tray_icon.py --show
```

Closing the window or pressing Escape keeps the tray icon running. A second
launch opens the existing instance. **Open CUPS** opens the local CUPS web page.

## Install for your user

```sh
/usr/bin/python3 install.py --autostart
```

Open **CUPS Tray Icon** from your application menu. On the next login, only the
tray icon starts; the window opens when you click it. Omit `--autostart` if you
prefer manual startup. Run the installer again after updating the checkout.

Files are installed below `~/.local/share/cups-tray-icon`, with a desktop menu
entry and, when enabled, `~/.config/autostart/cups-tray-icon.desktop`.
`XDG_DATA_HOME` and `XDG_CONFIG_HOME` are respected. No `sudo` is needed to install
the app itself.

To remove it:

```sh
/usr/bin/python3 install.py --uninstall
```

Log out to stop any running instance. Uninstalling preserves your local config
and all CUPS printers and jobs. To disable autostart alone, remove its desktop
entry from your autostart directory.

## USB forwarders

A locally attached USB printer may appear in CUPS as a socket queue when a
service such as `p910nd` forwards data to it. Add those queues explicitly in
`~/.config/cups-tray-icon/config.json`:

```json
{
  "usb_forwarders": {
    "Office_Printer": "EXAMPLE_USB_SERIAL"
  }
}
```

Replace the example with the exact CUPS queue name and that printer's USB serial.
Only include printers physically attached to this computer. The app uses this
mapping to include the queue and check USB presence. It does not create or
configure a forwarder. If `XDG_CONFIG_HOME` is set, the config belongs there instead.

No config file is required for ordinary `usb://` queues. Invalid config is
reported as unavailable status, rather than an empty healthy printer list.
Keep your config local; `config.json` is excluded from Git.

## Demo and diagnostics

```sh
/usr/bin/python3 cups_tray_icon.py --demo
/usr/bin/python3 cups_tray_icon.py --snapshot
```

Demo mode uses fictional data and does not contact CUPS or read local config.
`--snapshot` reads real status and prints JSON; review printer names and CUPS
diagnostics before sharing its output. Combine `--demo --snapshot` for sample JSON.

If the window opens but the icon is missing, check that your panel has a
StatusNotifierItem host. If CUPS is unavailable, the app retries automatically.
Unknown device errors retain the original text under **Queue details**.

## What status can tell you

CUPS queue status does not prove that a page physically printed. Some drivers
provide limited hardware information, and USB presence alone does not mean a
printer is ready. The app does not independently query ink levels or printer firmware.

Jobs sent straight to a USB forwarder, bypassing CUPS, cannot be counted here.
The app requests active job states and queue identifiers, not document names,
owners or document contents. It creates no network listener and sends no telemetry.

## Development

The state and installer tests use Python's standard library:

```sh
python3 -B -m unittest discover -s tests -v
```

The GTK smoke check needs the runtime dependencies plus `xvfb` and `dbus-x11`:

```sh
xvfb-run -a dbus-run-session -- /usr/bin/python3 tests/gui_smoke.py
```

CI installs the `.deb` through APT, runs the tests against the package and checks
removal. The smoke check uses demo data and does not need a printer
or a running CUPS server. The packaging test runs when `dpkg-deb` is available.
Issues and focused pull requests are welcome; omit
device serials, private addresses and document data from reports.

## Release packages

Publishing a GitHub release with a `vMAJOR.MINOR.PATCH` tag runs the verification
workflow, then attaches `cups-tray-icon_VERSION_all.deb` and `SHA256SUMS`.
Existing assets are not silently replaced on a rerun. The package contains no
maintainer scripts and stores no per-user configuration.

To build it on a Debian-based system:

```sh
export SOURCE_DATE_EPOCH="$(git log -1 --format=%ct)"
python3 scripts/build_deb.py 0.1.2
```

The output is in `dist/`. Builds use root ownership in the archive without
requiring root to build. The same source, timestamp and packaging tool versions
produce the same archive.

## License

[MIT](LICENSE). The two printer illustrations are included under the same license.
