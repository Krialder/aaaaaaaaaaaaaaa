"""Gemeinsame Host-Normalisierung für rules.py und ctbbl_import.py. Beide Seiten
müssen Adressen gleich zerlegen, sonst driften Sperr-Erkennung und Import auseinander
(DRY gilt für Wissen). Reine Funktionen ohne Mac-Bezug, per Selbsttest belegbar.
"""

from __future__ import annotations

import re

# Ein Schema besteht aus Buchstaben, Ziffern und + . -, enthält aber keinen Punkt vor
# dem Doppelpunkt in der Praxis; wir verlangen bewusst KEINEN Punkt, damit 'youtube.com:443'
# (Host mit Port) nicht faelschlich als Schema 'youtube.com' gelesen wird.
_SCHEME_RE = re.compile(r"^([a-z][a-z0-9+\-]*):")


def to_host(raw: str) -> str:
    """Reduziert eine Eingabe auf den reinen Host: klein, ohne 'www.', ohne Wildcard,
    Pfad, Query, Fragment, Zugangsdaten und Port. Leere Eingabe ergibt ''. Der Host darf
    punktlos sein (etwa 'localhost'); ob das zulässig ist, entscheidet der Aufrufer.
    """
    if not raw:
        return ""
    s = raw.strip().lower()
    if s.startswith("*."):
        s = s[2:]
    if "://" in s:
        s = s.split("://", 1)[1]
    s = s.split("/")[0].split("?")[0].split("#")[0]
    s = s.split("@")[-1]   # user:pass@host entfernen
    s = s.split(":")[0]    # Port entfernen
    if s.startswith("www."):
        s = s[4:]
    return s


def is_web_scheme(raw: str) -> bool:
    """True, wenn die Eingabe kein Schema hat oder http/https ist. Nicht-Web-Schemata
    (about:, chrome:, file:, data:, javascript:) ergeben False, damit interne
    Browser-Seiten nicht als Webseite behandelt und im Erlaubnislisten-Modus nicht
    faelschlich gemeldet werden.
    """
    if not raw:
        return True
    s = raw.strip().lower()
    if "://" in s:
        return s.split("://", 1)[0] in ("http", "https")
    m = _SCHEME_RE.match(s)
    if m:
        return m.group(1) in ("http", "https")
    return True
