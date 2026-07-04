"""Regel-Engine: entscheidet, ob eine Beobachtung (aktive App + Browser-URL) eine
Meldung ausloest. Bewusst reine Funktionen ohne Seiteneffekte, damit sie ohne Mac
testbar sind; Cooldown und Versand liegen im Monitor, nicht hier.

Zwei Modelle, je fuer Apps und Webseiten getrennt waehlbar:
- Blockliste: Meldung nur bei ausdruecklich Verbotenem.
- Erlaubnisliste (allowlist): Meldung bei allem, was nicht ausdruecklich erlaubt ist.

Warum eigene Treffer-Logik statt "String enthaelt": Domain-Regeln muessen Subdomains
mitfangen (youtube.com auch m.youtube.com), duerfen aber nicht ueberschiessen
(notyoutube.com ist nicht youtube.com). Reines "in" wuerde beides falsch machen.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import hostutil

# Immer erlaubte Apps in der Erlaubnisliste, ohne dass der Nutzer sie pflegt. Ohne
# diese Grundmenge wuerde eine App-Erlaubnisliste bei jeder System-App im Vordergrund
# (Finder, Dock, Anmeldefenster, Systemdialoge) melden und damit unbrauchbar rauschen.
# Browser stehen mit drin, weil ihr Inhalt ueber die Web-Regeln beurteilt wird.
DEFAULT_BASELINE_APPS: frozenset[str] = frozenset({
    "finder", "dock", "systemuiserver", "loginwindow", "windowserver",
    "controlcenter", "notificationcenter", "spotlight", "coreautha",
    "universalcontrol", "screensaverengine", "wallpaper", "screentime",
    "system settings", "systemeinstellungen", "system preferences",
    "safari", "google chrome", "google chrome canary", "brave browser",
    "microsoft edge", "vivaldi", "opera", "arc",
})


def _mode(value: str) -> str:
    # Nur zwei gueltige Werte; alles andere faellt sicher auf Blockliste zurueck.
    return "allowlist" if str(value).strip().lower() == "allowlist" else "blocklist"


@dataclass(frozen=True)
class RuleSet:
    app_mode: str      # "blocklist" | "allowlist"
    web_mode: str      # "blocklist" | "allowlist"
    # Domains ohne Schema, klein, ohne "www." (z.B. "youtube.com").
    blocked_domains: frozenset[str]
    # App-Namen wie macOS sie als Prozessnamen meldet (z.B. "Steam"), case-insensitiv.
    blocked_apps: frozenset[str]
    # Erlaubnisliste: in allowlist der einzige zulaessige Satz, in blocklist die Ausnahmen.
    allowed_domains: frozenset[str]
    allowed_apps: frozenset[str]
    # Apps, deren blosses Erscheinen im Vordergrund immer gemeldet wird (Terminal-Umgehung).
    always_notify_apps: frozenset[str]
    # Grundmenge immer erlaubter Apps (Standard plus Zusaetze aus der Konfiguration).
    baseline_allow_apps: frozenset[str]

    @staticmethod
    def from_dict(d: dict) -> "RuleSet":
        norm = _normalize_domain

        def apps(key: str) -> frozenset[str]:
            return frozenset(a.strip().lower() for a in d.get(key, []) if a.strip())

        def domains(*keys: str) -> frozenset[str]:
            vals: list[str] = []
            for k in keys:
                vals += d.get(k, [])
            return frozenset(filter(None, (norm(x) for x in vals)))

        return RuleSet(
            app_mode=_mode(d.get("app_mode", "blocklist")),
            web_mode=_mode(d.get("web_mode", "blocklist")),
            blocked_domains=domains("blocked_domains"),
            blocked_apps=apps("blocked_apps"),
            # "allow_domains" bleibt als Altname akzeptiert (frueher nur Ausnahmen).
            allowed_domains=domains("allowed_domains", "allow_domains"),
            allowed_apps=apps("allowed_apps"),
            always_notify_apps=apps("always_notify_apps"),
            baseline_allow_apps=DEFAULT_BASELINE_APPS | apps("baseline_allow_apps"),
        )


@dataclass(frozen=True)
class Violation:
    # "website" | "app" | "watch-app" | "website-not-allowed" | "app-not-allowed"
    kind: str
    matched: str       # die Regel bzw. der Wert, der gemeldet wird (Domain oder App)
    observed: str      # was tatsaechlich gesehen wurde (URL bzw. App-Name)
    # dedup_key haelt gleiche Verstoesse zusammen, damit der Cooldown im Monitor greift.
    dedup_key: str


def _normalize_domain(raw: str) -> str:
    """Reduziert eine Eingabe auf den reinen Host, klein, ohne 'www.'.

    Akzeptiert 'https://youtube.com/foo', 'www.YouTube.com', '*.youtube.com' und
    'youtube.com' und liefert ueberall 'youtube.com'. Leere/kaputte Eingaben -> ''.

    Nicht-Web-Adressen liefern bewusst '': interne Browser-Seiten wie 'chrome://newtab/',
    'about:blank' oder 'file://...' sind keine Webseite und duerfen im Erlaubnislisten-
    Modus nicht als 'nicht erlaubte Seite' melden. Ebenso hostlose Ziele ohne Punkt
    (localhost, newtab), die keine oeffentliche Domain sind.
    """
    if not raw or not hostutil.is_web_scheme(raw):
        return ""
    host = hostutil.to_host(raw)
    if "." not in host:
        return ""
    return host


def _host_matches(host: str, domain: str) -> bool:
    # Treffer bei exakter Gleichheit oder echter Subdomain, nie bei Namens-Suffix.
    return host == domain or host.endswith("." + domain)


def evaluate(app_name: Optional[str], url: Optional[str], rules: RuleSet) -> Optional[Violation]:
    """Prueft eine einzelne Beobachtung. Reihenfolge ist Absicht:

    1. Immer-melden-Apps ueberschreiben jeden Modus (auch die Erlaubnisliste).
    2. Web: bei allowlist meldet jede nicht erlaubte Seite, bei blocklist nur gesperrte.
    3. App: analog. Die Grundmenge (System-Apps, Browser) gilt in allowlist als erlaubt.
    """
    app = (app_name or "").strip()
    app_lc = app.lower()
    host = _normalize_domain(url) if url else ""

    if app_lc and app_lc in rules.always_notify_apps:
        return Violation("watch-app", app, app, f"watch-app:{app_lc}")

    if host:
        allowed = any(_host_matches(host, d) for d in rules.allowed_domains)
        if rules.web_mode == "allowlist":
            if not allowed:
                return Violation("website-not-allowed", host, url or host, f"website:{host}")
        elif not allowed:
            hit = next((d for d in rules.blocked_domains if _host_matches(host, d)), None)
            if hit:
                return Violation("website", hit, url or host, f"website:{hit}")

    if app_lc:
        if rules.app_mode == "allowlist":
            if app_lc not in rules.allowed_apps and app_lc not in rules.baseline_allow_apps:
                return Violation("app-not-allowed", app, app, f"app:{app_lc}")
        elif app_lc not in rules.allowed_apps and app_lc in rules.blocked_apps:
            return Violation("app", app, app, f"app:{app_lc}")

    return None
