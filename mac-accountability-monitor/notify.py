"""Meldewege. Zwei Kanaele hinter einer gemeinsamen Schnittstelle (ntfy-Push und
E-Mail), damit der Monitor eine Meldung an alle konfigurierten Wege schickt, ohne
sie einzeln zu kennen. Nur Standardbibliothek (urllib, smtplib, email), damit auf
einem gesperrten Mac kein 'pip install' noetig ist.

Ein Versandfehler eines Kanals darf weder den anderen Kanal noch den Waechter
stoppen: jeder Kanal faengt seine Fehler selbst und meldet nur True/False zurueck.
"""

from __future__ import annotations

import smtplib
import ssl
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from email.message import EmailMessage
from typing import Optional, Protocol


class Channel(Protocol):
    name: str
    def send(self, subject: str, body: str, kind: str) -> bool: ...


# ------------------------------------------------------------------ ntfy

@dataclass
class NtfyChannel:
    server: str = "https://ntfy.sh"
    topic: str = ""
    token: str = ""            # optionaler Zugriffstoken bei privatem/self-hosted ntfy
    priority: str = "high"
    title: str = "Accountability Monitor"
    name: str = field(default="ntfy", init=False)

    def send(self, subject: str, body: str, kind: str) -> bool:
        if not self.topic:
            print("[ntfy] Topic leer, uebersprungen.")
            return False
        url = self.server.rstrip("/") + "/" + self.topic
        req = urllib.request.Request(url, data=body.encode("utf-8"), method="POST")
        # ntfy erwartet Metadaten als HTTP-Header; manche Server lehnen Nicht-ASCII
        # in Headern ab, deshalb bleibt der Titel ASCII, der Body darf UTF-8 sein.
        req.add_header("Title", self.title)
        req.add_header("Priority", self.priority)
        req.add_header("Tags", _tag_for(kind))
        if self.token:
            req.add_header("Authorization", "Bearer " + self.token)
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return 200 <= resp.status < 300
        except urllib.error.URLError as e:
            print(f"[ntfy] Versand fehlgeschlagen: {e}")
            return False


# ------------------------------------------------------------------ E-Mail

@dataclass
class EmailChannel:
    host: str = ""
    port: int = 587
    user: str = ""
    password: str = ""         # bei Gmail ein App-Passwort, nicht das Konto-Passwort
    sender: str = ""           # From-Adresse; leer -> user
    recipient: str = ""        # Adresse des Partners
    name: str = field(default="email", init=False)

    def send(self, subject: str, body: str, kind: str) -> bool:
        if not (self.host and self.recipient):
            print("[email] host oder recipient leer, uebersprungen.")
            return False
        msg = EmailMessage()
        # EmailMessage kodiert Kopfzeilen selbst, daher sind hier Umlaute erlaubt.
        msg["Subject"] = subject
        msg["From"] = self.sender or self.user
        msg["To"] = self.recipient
        msg.set_content(body)
        try:
            # Port 465 spricht direkt TLS, alles andere STARTTLS nach dem Verbinden.
            if self.port == 465:
                ctx = ssl.create_default_context()
                with smtplib.SMTP_SSL(self.host, self.port, timeout=15, context=ctx) as s:
                    if self.user:
                        s.login(self.user, self.password)
                    s.send_message(msg)
            else:
                with smtplib.SMTP(self.host, self.port, timeout=15) as s:
                    s.ehlo()
                    s.starttls(context=ssl.create_default_context())
                    if self.user:
                        s.login(self.user, self.password)
                    s.send_message(msg)
            return True
        except (smtplib.SMTPException, OSError) as e:
            print(f"[email] Versand fehlgeschlagen: {e}")
            return False


# ------------------------------------------------------------------ Aufbau und Text

def build_channels(cfg: dict) -> list[Channel]:
    """Baut die Kanalliste aus der Konfiguration. Ein Kanal entsteht nur, wenn seine
    Pflichtfelder gesetzt sind, damit eine leere Beispiel-Sektion nichts ausloest.
    """
    channels: list[Channel] = []

    n = cfg.get("ntfy", {})
    if n.get("topic"):
        channels.append(NtfyChannel(
            server=n.get("server", "https://ntfy.sh"),
            topic=n["topic"],
            token=n.get("token", ""),
            priority=n.get("priority", "high"),
            title=n.get("title", "Accountability Monitor"),
        ))

    e = cfg.get("email", {})
    if e.get("host") and e.get("recipient"):
        channels.append(EmailChannel(
            host=e["host"],
            port=_safe_port(e.get("port", 587)),
            user=e.get("user", ""),
            password=e.get("password", ""),
            sender=e.get("sender", ""),
            recipient=e["recipient"],
        ))

    return channels


def _safe_port(v) -> int:
    # Ein von Hand falsch gesetzter Port darf den Kanalaufbau (und damit den Monitor)
    # nicht kippen; im Zweifel der SMTP-Standard 587.
    try:
        return int(v)
    except (TypeError, ValueError):
        return 587


def dispatch(channels: list[Channel], subject: str, body: str, kind: str) -> dict[str, bool]:
    """Schickt dieselbe Meldung an alle Kanaele und liefert je Kanal Erfolg/Fehlschlag.
    Kein Kanal-Fehler bricht die Schleife ab.
    """
    results: dict[str, bool] = {}
    for ch in channels:
        results[ch.name] = ch.send(subject, body, kind)
    return results


# Eine Tabelle fuer beide Textbauer, damit Betreff und Rumpf nie auseinanderlaufen.
_LABELS = {
    "website": "Gesperrte Seite geoeffnet",
    "app": "Gesperrte App geoeffnet",
    "watch-app": "Beobachtete App geoeffnet",
    "website-not-allowed": "Nicht erlaubte Seite geoeffnet",
    "app-not-allowed": "Nicht erlaubte App geoeffnet",
    "info": "Monitor-Status",
}


def _tag_for(kind: str) -> str:
    return {"website": "no_entry", "app": "no_entry_sign",
            "website-not-allowed": "no_entry", "app-not-allowed": "no_entry_sign",
            "watch-app": "eyes", "info": "information_source",
            "blind": "warning"}.get(kind, "warning")


def subject_for(kind: str, matched: str) -> str:
    return f"[Accountability] {_LABELS.get(kind, 'Ereignis')}: {matched}"


def format_violation(kind: str, matched: str, observed: str, when_iso: str) -> str:
    """Einheitlicher Meldetext. Zeigt Regel und tatsaechliche Beobachtung, damit der
    Partner Fehlalarme von echten Verstoessen unterscheiden kann.
    """
    label = _LABELS.get(kind, "Ereignis")
    return f"{label}\nRegel: {matched}\nBeobachtet: {observed}\nZeit: {when_iso}"
