"""Tages-Rotation und Unveränderlichkeit der Protokolldateien. Eine Datei pro Tag:
gleicher Tag hängt an, ein neuer Tag beginnt eine neue Datei. Die reine Pfadlogik ist
ohne Mac testbar; das Setzen des append-only-Flags ist macOS-spezifisch und fällt auf
anderen Systemen still aus.

Warum append-only statt normaler Datei: das Flag 'sappnd' kann nur root setzen und
wieder entfernen. Der als Nutzer laufende Monitor darf weiter anhängen, aber niemand
außer einem Admin kann eine Tagesdatei löschen oder rückwirkend ändern.
"""

from __future__ import annotations

import glob as _glob
import os
import subprocess
import sys
from datetime import datetime, timezone


def daily_path(base_path: str, when: datetime | None = None) -> str:
    """Aus '.../activity_history.jsonl' wird '.../activity_history-2026-07-04.jsonl'.
    Das Datum steht in UTC, passend zu den UTC-Zeitstempeln in den Einträgen, damit ein
    Tageswechsel im Log und im Dateinamen an derselben Grenze liegt.
    """
    when = when or datetime.now(timezone.utc)
    directory, name = os.path.split(base_path)
    stem, ext = os.path.splitext(name)
    return os.path.join(directory, f"{stem}-{when:%Y-%m-%d}{ext}")


def daily_siblings(base_path: str) -> list[str]:
    """Alle Tagesdateien zu einem Basisnamen, nach Datum sortiert (es steht im Namen)."""
    directory, name = os.path.split(base_path)
    stem, ext = os.path.splitext(name)
    return sorted(_glob.glob(os.path.join(directory, f"{stem}-*{ext}")))


def make_append_only(path: str) -> bool:
    """Setzt das system-append-only-Flag. Nur root setzt und entfernt es; der Monitor
    darf weiter anhängen. Außerhalb von macOS ein No-op. Fehler werden geschluckt, weil
    ein fehlendes Flag den Betrieb nicht stoppen darf, nur die Härtung schwächt.
    """
    if sys.platform != "darwin":
        return False
    try:
        r = subprocess.run(["chflags", "sappnd", path], capture_output=True, timeout=5)
        return r.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False
