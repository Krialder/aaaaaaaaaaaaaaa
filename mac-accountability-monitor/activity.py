"""Aktivitaetsverlauf: verwandelt die rohen Messungen in eine Zeitleiste von Wechseln,
aehnlich dem Windows-Aktivitaetsverlauf. Statt alle paar Sekunden dieselbe Zeile zu
schreiben, entsteht ein Eintrag nur, wenn sich die aktive App oder die Seite aendert,
mit Angabe von, auf, seit wann und wie lange.

Bewusst reine Logik ohne Seiteneffekte und ohne Mac-Bezug, damit sie per Selbsttest
belegbar ist. Der Monitor haengt das Schreiben der Datei aussen dran.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class Segment:
    app: Optional[str]
    url: Optional[str]
    since_iso: str
    since_ts: float
    blocked: bool


class ActivityTracker:
    """Haelt das aktuell laufende Segment und erzeugt bei einem Wechsel einen
    Aenderungs-Datensatz (von, auf, Dauer). Identitaet eines Segments ist (App, URL):
    ein Seitenwechsel im selben Browser gilt als neuer Eintrag, damit der Verlauf auch
    besuchte Seiten zeigt, nicht nur App-Wechsel.
    """

    def __init__(self):
        self.current: Optional[Segment] = None

    def observe(self, app: Optional[str], url: Optional[str], now_ts: float,
                when_iso: str, blocked: bool) -> Optional[dict]:
        if self.current is not None and self.current.app == app and self.current.url == url:
            return None  # nichts geaendert, kein Eintrag
        prev = self.current
        self.current = Segment(app, url, when_iso, now_ts, blocked)
        return _change(prev, self.current, when_iso, now_ts)

    def flush(self, now_ts: float, when_iso: str) -> Optional[dict]:
        """Schliesst beim Beenden das offene Segment ab (Wechsel nach 'nichts'), damit
        auch die letzte Dauer im Verlauf steht.
        """
        if self.current is None:
            return None
        prev = self.current
        self.current = None
        return _change(prev, None, when_iso, now_ts)


def _change(prev: Optional[Segment], new: Optional[Segment], when_iso: str, now_ts: float) -> dict:
    frm = None
    if prev is not None:
        frm = {"app": prev.app, "url": prev.url, "since": prev.since_iso,
               "duration_s": round(now_ts - prev.since_ts, 1), "blocked": prev.blocked}
    to = None
    if new is not None:
        to = {"app": new.app, "url": new.url, "blocked": new.blocked}
    return {"time": when_iso, "type": "change", "from": frm, "to": to}


def fmt_duration(seconds: float) -> str:
    s = int(round(seconds))
    if s < 60:
        return f"{s}s"
    m, s = divmod(s, 60)
    if m < 60:
        return f"{m}m {s}s"
    h, m = divmod(m, 60)
    return f"{h}h {m}m"


def format_history(records: list[dict], limit: int = 50) -> list[str]:
    """Macht aus den Aenderungs-Datensaetzen lesbare Zeilen (juengste zuletzt). Jede
    Zeile ist ein abgeschlossenes Segment: seit wann, wie lange, App, Seite, gesperrt.
    """
    lines: list[str] = []
    for rec in records:
        if rec.get("type") != "change":
            continue
        frm = rec.get("from")
        if not frm:
            continue  # der allererste Eintrag hat kein Vorher
        mark = "  [GESPERRT]" if frm.get("blocked") else ""
        app = frm.get("app") or "-"
        url = frm.get("url") or ""
        sep = "  " if url else ""
        lines.append(f"{frm.get('since', '?')}  ({fmt_duration(frm.get('duration_s', 0))})  "
                     f"{app}{sep}{url}{mark}")
    return lines[-limit:]
