"""Menuleisten-Anzeige (macOS). Zeigt sichtbar in der Menuleiste, dass der Monitor
laeuft, wann zuletzt geprueft wurde, und wie viele Verstoesse es gab. Sie liest nur
die Status-Datei, die monitor.py schreibt, und steuert den Waechter nicht.

Bewusst getrennt vom Monitor: die Anzeige ist ein Komfort, kein Teil des kritischen
Pfads. Faellt sie aus, laeuft die Ueberwachung weiter.

Braucht PyObjC (AppKit). Auf vielen Macs mit den Command Line Tools bereits dabei;
falls nicht, im Admin-Fenster einmalig: pip3 install pyobjc-framework-Cocoa

Start:  python3 statusbar.py [pfad/zu/status.json]
Nicht auf echter Hardware getestet, siehe README, Abschnitt Grenzen.
"""

from __future__ import annotations

import json
import os
import sys

try:
    import AppKit
    from Foundation import NSObject, NSTimer
except ImportError:
    print("PyObjC (AppKit) fehlt. Installieren mit: pip3 install pyobjc-framework-Cocoa")
    raise SystemExit(1)


DEFAULT_STATUS = os.path.expanduser("~/AccountabilityMonitor/status.json")
REFRESH_SECONDS = 5.0

# Modul-Variable statt eigenem Objective-C-init: der eigene init-Weg in PyObjC ist
# heikel, und die Anzeige braucht nur genau einen Statuspfad.
STATUS_PATH = DEFAULT_STATUS


def _read_status(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


class AppDelegate(NSObject):
    def applicationDidFinishLaunching_(self, _notification):
        self.status_path = STATUS_PATH
        bar = AppKit.NSStatusBar.systemStatusBar()
        self.item = bar.statusItemWithLength_(AppKit.NSVariableStatusItemLength)
        self.menu = AppKit.NSMenu.alloc().init()
        self.item.setMenu_(self.menu)
        self.refresh_(None)
        # Timer haelt die Anzeige aktuell, ohne dass der Nutzer etwas tun muss.
        self.timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            REFRESH_SECONDS, self, "refresh:", None, True)

    def refresh_(self, _timer):
        st = _read_status(self.status_path)
        running = bool(st.get("running"))
        violations = st.get("violations", 0)
        last_v = st.get("last_violation")

        # Ampel im Menuleisten-Titel: grau nicht aktiv, rot letzter Zustand Verstoss,
        # sonst gruen. Text statt Bild, damit keine Grafikdatei noetig ist.
        if not running:
            dot = "⚪ Monitor"      # weisser Kreis
        elif last_v:
            dot = "\U0001f534 Monitor"  # roter Kreis
        else:
            dot = "\U0001f7e2 Monitor"  # gruener Kreis
        self.item.button().setTitle_(dot)

        self.menu.removeAllItems()
        self._add(f"Status: {'laeuft' if running else 'gestoppt'}")
        self._add(f"Letzte Pruefung: {st.get('last_check', '-')}")
        self._add(f"Vordergrund: {st.get('front_app', '-')}")
        self._add(f"Verstoesse: {violations}")
        if last_v:
            self._add(f"Zuletzt: {last_v.get('kind')} {last_v.get('matched')} ({last_v.get('time')})")
        self.menu.addItem_(AppKit.NSMenuItem.separatorItem())
        quit_item = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Anzeige beenden", "terminate:", "q")
        self.menu.addItem_(quit_item)

    def _add(self, title: str):
        # Deaktivierte Zeile: reine Anzeige, nicht anklickbar.
        it = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, None, "")
        it.setEnabled_(False)
        self.menu.addItem_(it)


def main(argv: list[str]) -> int:
    global STATUS_PATH
    if len(argv) > 1:
        STATUS_PATH = argv[1]
    app = AppKit.NSApplication.sharedApplication()
    # Accessory: nur Menuleiste, kein Dock-Symbol.
    app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)
    delegate = AppDelegate.alloc().init()
    app.setDelegate_(delegate)
    app.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
