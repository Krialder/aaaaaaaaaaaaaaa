"""Sampler: liest den Ist-Zustand des Macs. Zwei Umsetzungen hinter derselben
Schnittstelle, damit der Monitor auf Windows testbar bleibt:

- MacSampler nutzt osascript (AppleScript). Sichtbar, kein Tastatur-Mitschnitt,
  nur App-Name und aktive Tab-URL.
- FakeSampler spielt ein Skript erfundener Beobachtungen ab (Selbsttest).

Wichtig: Wir fragen eine Browser-URL nur ab, wenn dieser Browser gerade im
Vordergrund ist. AppleScript wuerde eine geschlossene App sonst starten, und ein
Waechter, der Programme oeffnet, waere ein Eigentor.
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from typing import Optional, Protocol


@dataclass(frozen=True)
class Sample:
    app_name: Optional[str]
    url: Optional[str]


class Sampler(Protocol):
    def sample(self) -> Sample: ...


# App-Name (wie System Events ihn meldet) -> AppleScript-Dialekt fuer die aktive URL.
# Safari und Chromium sprechen unterschiedliche Befehle ("current tab" vs "active tab").
_BROWSERS: dict[str, str] = {
    "Safari": "safari",
    "Safari Technology Preview": "safari",
    "Google Chrome": "chromium",
    "Google Chrome Canary": "chromium",
    "Chromium": "chromium",
    "Brave Browser": "chromium",
    "Microsoft Edge": "chromium",
    "Vivaldi": "chromium",
    "Opera": "chromium",
    "Yandex": "chromium",
    "Arc": "arc",
}

# Browser, die wir als solche ERKENNEN, auch wenn wir ihre URL nicht lesen koennen.
# Firefox und seine Verwandten bieten kein AppleScript fuer die aktive Tab-URL; sie
# tauchen deshalb als 'Browser vorn, aber keine URL' auf und werden von der
# Blind-Erkennung im Monitor als verdaechtig gemeldet, statt still zu verschwinden.
_URLLESS_BROWSERS: frozenset[str] = frozenset({
    "Firefox", "Firefox Developer Edition", "Firefox Nightly", "Waterfox",
    "LibreWolf", "Mullvad Browser", "Tor Browser", "Orion", "Min", "Zen Browser",
    "DuckDuckGo",
})
KNOWN_BROWSERS: frozenset[str] = frozenset(_BROWSERS) | _URLLESS_BROWSERS


def is_browser(app_name: Optional[str]) -> bool:
    return app_name in KNOWN_BROWSERS


def _osascript(script: str) -> Optional[str]:
    """Fuehrt ein AppleScript aus und liefert die getrimmte Ausgabe oder None.

    Kein globales Fehler-werfen: fehlende Fenster oder verweigerte Freigaben sollen
    eine leere Beobachtung ergeben, nicht den Waechter stoppen.
    """
    try:
        out = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0:
        return None
    val = out.stdout.strip()
    return val or None


class MacSampler:
    def frontmost_app(self) -> Optional[str]:
        # System Events liefert den Prozessnamen der Vordergrund-App.
        return _osascript(
            'tell application "System Events" to get name of first process whose frontmost is true'
        )

    def active_url(self, app_name: str) -> Optional[str]:
        dialect = _BROWSERS.get(app_name)
        if dialect == "safari":
            return _osascript(f'tell application "{app_name}" to get URL of current tab of front window')
        if dialect == "chromium":
            return _osascript(f'tell application "{app_name}" to get URL of active tab of front window')
        if dialect == "arc":
            # Arc kennt "active tab of front window", verlangt aber gelegentlich ein
            # offenes Fenster; fehlt es, liefert _osascript None statt eines Fehlers.
            return _osascript('tell application "Arc" to get URL of active tab of front window')
        return None

    def sample(self) -> Sample:
        app = self.frontmost_app()
        url = self.active_url(app) if app else None
        return Sample(app_name=app, url=url)


class FakeSampler:
    """Spielt eine feste Liste von Beobachtungen ab und wiederholt die letzte, damit
    der Monitor-Loop nicht ins Leere laeuft. Nur fuer Selbsttest und Windows.
    """

    def __init__(self, script: list[Sample]):
        self._script = list(script)
        self._i = 0

    def sample(self) -> Sample:
        if not self._script:
            return Sample(None, None)
        s = self._script[min(self._i, len(self._script) - 1)]
        self._i += 1
        return s


def default_sampler() -> Sampler:
    if sys.platform == "darwin":
        return MacSampler()
    raise RuntimeError(
        "MacSampler laeuft nur auf macOS. Auf anderen Systemen nur --selftest verwenden."
    )
