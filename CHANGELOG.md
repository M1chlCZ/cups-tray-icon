# Changelog

## [Unreleased]

- Printer settings behind a gear button on every card: paper size, color
  mode, two-sided printing and paper type, with friendly labels and only the
  choices the printer really offers.
- The settings dialog applies changes only to your own account on this
  computer through the local print service; jobs, other accounts and queue
  state are never touched, and "More options…" opens the system printer
  settings.
- Option discovery reads the printer's live attributes first and falls back
  to its PPD, is unit-tested from fixtures, and runs only when the dialog
  opens so the five-second status refresh stays fast.
- New friendly window design: header bar with the app icon, refresh and menu
  buttons, a plain-language summary line and printer cards with a status pill
  (icon and word), a natural waiting count, a per-printer "Open print queue"
  link and an expander renamed "Technical details".
- New icon family: an application icon and ready, warning, error and offline
  state icons used by the tray, the cards and the desktop entry.
- The tray icon now renders the state icon at 22, 24 and 32 pixels, uses the
  attention state when a printer needs attention, and shows one short line per
  printer (name and state) instead of a raw counter.
- Desktop notifications on state changes: a printer entering a problem state,
  a document starting to wait, and the print service becoming unreachable or
  recovering. Notifications are deduplicated on transitions and replace each
  other per printer.
- New `notifications` setting in `config.json` (default `true`), exposed as
  "Problem notifications" in the header menu; toggling rewrites the config
  file atomically and preserves `usb_forwarders`.
- Empty state with an explanation and an "Open printer settings" button that
  tries system-config-printer, GNOME settings, then the CUPS web page.
- Friendly banner and automatic retries when the print service is
  unreachable, with the existing configuration hint preserved.
- Demo mode now shows every state: ready, printing, a supply warning, an
  error with a waiting document, and a disconnected USB printer.
- Debian and per-user installs carry the new icons into the hicolor theme;
  pushing a `v*` tag builds the package and creates the release with notes.

## 0.1.2

- Build an architecture-independent Debian package for each GitHub release.
- Install runtime dependencies through APT and include a system desktop launcher.
- Verify package contents, reproducible builds, installation, GUI startup and removal.

## 0.1.1

- Documentation and internal cleanup.

## 0.1.0

- GTK 3 overview of local CUPS printers with a desktop tray icon.
- Queue counts, held jobs, USB presence and plain-English error messages.
- Optional local configuration for USB print forwarders.
- Per-user installation and optional login autostart.
- Demo mode with fictional printers, tests and Linux GUI checks.
