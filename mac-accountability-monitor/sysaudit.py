"""Konfig-Auditing: protokolliert Aenderungen an der Systemkonfiguration, nicht der
Nutzung. Es nimmt in festem Abstand eine Momentaufnahme ausgewaehlter Systemfakten
und vergleicht sie mit der vorigen. Jeder Unterschied wird als Aenderung mit von und
auf festgehalten.

Abgedeckt: installierte Programme (/Applications und ~/Applications), Pakete,
Admin-Konten, LaunchAgents (System und Nutzer) und LaunchDaemons, /etc/hosts,
Konfigurationsprofile (best effort), Sicherheitsschalter (SIP, Gatekeeper, Firewall),
macOS-Version.

Bewusster Aufbau wie beim Aktivitaetsverlauf: die Vergleichs- und Formatierlogik ist
rein und ohne Mac testbar; nur das Einsammeln der Fakten ruft Systembefehle. Ein
fehlgeschlagener Befehl liefert eine leere Kategorie, nie einen Absturz.

Laeuft als root-LaunchDaemon im Abstand (StartInterval). Jeder Lauf ist ein
Einzelschritt (--once). Die Tages-Protokolle sind append-only (nur root aenderbar).
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone

import dailylog

DEFAULT_SNAPSHOT = "/Library/Application Support/AccountabilityMonitor/sysaudit_snapshot.json"
DEFAULT_HISTORY = "/Library/Application Support/AccountabilityMonitor/sysaudit_history.jsonl"


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _run(cmd: list[str], timeout: int = 20) -> str:
    """Fuehrt einen Systembefehl aus und liefert stdout bei Erfolg, sonst leeren Text.
    Kein Werfen, damit ein fehlender Befehl (etwa auf einem Nicht-Mac) still bleibt.
    """
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return p.stdout if p.returncode == 0 else ""


# ------------------------------------------------------------------ Sammler (Mac-spezifisch)

def _apps_in(path: str) -> dict:
    out: dict[str, str] = {}
    try:
        for name in os.listdir(path):
            if name.endswith(".app"):
                out[name] = "present"
    except OSError:
        pass
    return out


def _packages() -> dict:
    txt = _run(["pkgutil", "--pkgs"])
    return {p.strip(): "installed" for p in txt.splitlines() if p.strip()}


def _admin_users() -> dict:
    # Mitglieder der Gruppe admin. Ein neuer Admin ist ein sicherheitsrelevanter Wechsel.
    txt = _run(["dscl", ".", "-read", "/Groups/admin", "GroupMembership"])
    members = txt.replace("GroupMembership:", "").split() if txt else []
    return {m: "admin" for m in members}


def _dir_listing(path: str) -> dict:
    out: dict[str, str] = {}
    try:
        for n in os.listdir(path):
            out[n] = "present"
    except OSError:
        pass
    return out


def _hosts() -> dict:
    # Jede aktive Zeile aus /etc/hosts als Eintrag. Ein hinzugefuegter Eintrag (etwa eine
    # umgeleitete Domain) ist ein typisches Manipulationszeichen.
    out: dict[str, str] = {}
    try:
        with open("/etc/hosts", "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                s = line.strip()
                if s and not s.startswith("#"):
                    out[s] = "present"
    except OSError:
        pass
    return out


def _profiles() -> dict:
    # Konfigurationsprofile, best effort: der genaue 'profiles'-Aufruf schwankt je nach
    # macOS-Version, deshalb mehrere Formen probieren und Kennungs-Zeilen sammeln.
    out: dict[str, str] = {}
    txt = _run(["profiles", "list", "-all"]) or _run(["profiles", "-P"]) or _run(["profiles", "show"])
    for line in txt.splitlines():
        s = line.strip()
        low = s.lower()
        if "profileidentifier" in low or "attribute: name" in low:
            out[s] = "present"
    return out


def _security() -> dict:
    d: dict[str, str] = {}
    sip = _run(["csrutil", "status"])
    if sip:
        d["SIP"] = "enabled" if "enabled" in sip else ("disabled" if "disabled" in sip else sip.strip())
    gk = _run(["spctl", "--status"])
    if gk:
        d["Gatekeeper"] = "enabled" if "enabled" in gk else "disabled"
    fw = _run(["defaults", "read", "/Library/Preferences/com.apple.alf", "globalstate"]).strip()
    if fw in ("0", "1", "2"):
        # 0 aus, 1 an fuer bestimmte Dienste, 2 an fuer alle eingehenden Verbindungen.
        d["Firewall"] = fw
    return d


def _os_version() -> dict:
    d: dict[str, str] = {}
    v = _run(["sw_vers", "-productVersion"]).strip()
    b = _run(["sw_vers", "-buildVersion"]).strip()
    if v:
        d["ProductVersion"] = v
    if b:
        d["BuildVersion"] = b
    return d


def snapshot(user_home: str | None = None) -> dict:
    """Sammelt alle Kategorien zu einer Momentaufnahme {Kategorie: {Eintrag: Wert}}.
    Die Nutzer-Kategorien (dessen Apps und LaunchAgents) nur, wenn das Home bekannt ist,
    denn der root-Daemon kennt den Zielbenutzer nicht von allein.
    """
    snap = {
        "apps": _apps_in("/Applications"),
        "packages": _packages(),
        "admin_users": _admin_users(),
        "launch_agents_system": _dir_listing("/Library/LaunchAgents"),
        "launch_daemons": _dir_listing("/Library/LaunchDaemons"),
        "hosts": _hosts(),
        "profiles": _profiles(),
        "security": _security(),
        "os_version": _os_version(),
    }
    if user_home:
        snap["user_apps"] = _apps_in(os.path.join(user_home, "Applications"))
        snap["user_launch_agents"] = _dir_listing(os.path.join(user_home, "Library", "LaunchAgents"))
    return snap


# ------------------------------------------------------------------ Vergleich und Format (rein)

def diff_snapshots(old: dict, new: dict) -> list[dict]:
    """Vergleicht zwei Momentaufnahmen und liefert Aenderungen. Ein Eintrag neu und
    nicht alt ist hinzugefuegt, alt und nicht neu ist entfernt, in beiden mit anderem
    Wert ist geaendert (mit von und auf). Reihenfolge stabil ueber sortierte Schluessel.
    """
    changes: list[dict] = []
    for cat in sorted(set(old) | set(new)):
        o = old.get(cat, {}) or {}
        n = new.get(cat, {}) or {}
        for item in sorted(set(o) | set(n)):
            in_o, in_n = item in o, item in n
            if in_n and not in_o:
                changes.append(_rec(cat, item, "added", None, n[item]))
            elif in_o and not in_n:
                changes.append(_rec(cat, item, "removed", o[item], None))
            elif o[item] != n[item]:
                changes.append(_rec(cat, item, "changed", o[item], n[item]))
    return changes


def _rec(category: str, item: str, action: str, frm, to) -> dict:
    return {"time": _now_iso(), "category": category, "item": item,
            "action": action, "from": frm, "to": to}


def format_changes(records: list[dict], limit: int = 100) -> list[str]:
    """Macht aus den Aenderungen lesbare Zeilen. Geaendert zeigt von und auf, damit ein
    umgelegter Schalter (etwa SIP von enabled auf disabled) sofort erkennbar ist.
    """
    lines: list[str] = []
    for r in records:
        if r.get("action") == "baseline":
            lines.append(f"{r.get('time', '?')}  [Basisaufnahme angelegt]")
            continue
        cat, item, act = r.get("category", "?"), r.get("item", "?"), r.get("action")
        if act == "added":
            lines.append(f"{r.get('time','?')}  [{cat}] + {item}")
        elif act == "removed":
            lines.append(f"{r.get('time','?')}  [{cat}] - {item}")
        elif act == "changed":
            lines.append(f"{r.get('time','?')}  [{cat}] {item}: {r.get('from')} -> {r.get('to')}")
    return lines[-limit:]


# ------------------------------------------------------------------ Ablauf und E/A

def _load_json(path: str):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def _save_json(path: str, data) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _append(path: str, record: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def read_history(base_path: str) -> list[dict]:
    """Liest den Auditing-Verlauf ueber alle Tagesdateien plus eine evtl. vorhandene
    Alt-Datei ohne Datum. Kaputte Zeilen werden uebersprungen.
    """
    files = dailylog.daily_siblings(base_path)
    if os.path.exists(base_path):
        files.append(base_path)
    recs: list[dict] = []
    for p in files:
        try:
            with open(p, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        recs.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        except OSError:
            continue
    return recs


def run_once(cfg: dict, snapshot_path: str, history_base: str, user_home: str | None = None) -> int:
    """Ein Auditing-Schritt: alte Aufnahme laden, neue nehmen, Unterschiede in die
    Tagesdatei schreiben, neue Aufnahme sichern. Beim ersten Lauf ohne alte Aufnahme nur
    eine Basisaufnahme, damit nicht die gesamte Ausstattung als lauter Neuzugaenge
    erscheint. Die Tagesdatei wird append-only gemacht (nur root aenderbar).
    """
    old = _load_json(snapshot_path)
    new = snapshot(user_home)
    hp = dailylog.daily_path(history_base)

    if old is None:
        _save_json(snapshot_path, new)
        _append(hp, {"time": _now_iso(), "action": "baseline"})
        dailylog.make_append_only(hp)
        print("Basisaufnahme angelegt.")
        return 0

    changes = diff_snapshots(old, new)
    for c in changes:
        _append(hp, c)
    if changes:
        dailylog.make_append_only(hp)
    _save_json(snapshot_path, new)

    if changes and cfg.get("sysaudit_notify", True):
        _notify_changes(cfg, changes)

    print(f"{len(changes)} Aenderung(en) erfasst.")
    return 0


def _notify_changes(cfg: dict, changes: list[dict]) -> None:
    """Meldet die Aenderungen ueber dieselben Kanaele wie der Monitor. Import erst hier,
    damit der Selbsttest ohne notify auskommt.
    """
    try:
        import notify
    except ImportError:
        return
    channels = notify.build_channels(cfg)
    if not channels:
        return
    body = "\n".join(format_changes(changes, limit=20))
    notify.dispatch(channels, "[Accountability] Systemaenderung", body, "info")


def selftest() -> int:
    failures = 0
    old = {
        "apps": {"A.app": "present", "B.app": "present"},
        "security": {"SIP": "enabled", "Gatekeeper": "enabled"},
        "admin_users": {"root": "admin"},
        "hosts": {"127.0.0.1 localhost": "present"},
    }
    new = {
        "apps": {"A.app": "present", "C.app": "present"},          # B weg, C neu
        "security": {"SIP": "disabled", "Gatekeeper": "enabled"},  # SIP umgelegt
        "admin_users": {"root": "admin", "eve": "admin"},          # neuer Admin
        "hosts": {"127.0.0.1 localhost": "present",
                  "0.0.0.0 www.google.com": "present"},            # neuer hosts-Eintrag
    }
    changes = diff_snapshots(old, new)
    by = {(c["category"], c["item"]): c for c in changes}

    def check(cat, item, action, frm, to):
        nonlocal failures
        c = by.get((cat, item))
        ok = c and c["action"] == action and c.get("from") == frm and c.get("to") == to
        print(f"[{'OK' if ok else 'FEHLER'}] {cat}/{item}: {action} {frm} -> {to}")
        if not ok:
            failures += 1

    check("apps", "B.app", "removed", "present", None)
    check("apps", "C.app", "added", None, "present")
    check("security", "SIP", "changed", "enabled", "disabled")
    check("admin_users", "eve", "added", None, "admin")
    check("hosts", "0.0.0.0 www.google.com", "added", None, "present")

    if len(changes) != 5:
        failures += 1
        print(f"[FEHLER] erwartet 5 Aenderungen, bekam {len(changes)}")
    else:
        print("[OK] genau 5 Aenderungen, keine Fehltreffer bei unveraenderten Werten")

    lines = format_changes(changes)
    if not any("SIP: enabled -> disabled" in ln for ln in lines):
        failures += 1
        print("[FEHLER] Formatierung zeigt den SIP-Wechsel nicht als von -> auf")
    else:
        print("[OK] Formatierung zeigt von -> auf")

    print(f"\nSYSAUDIT_SELFTEST_RESULT failures={failures}")
    return 1 if failures else 0


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Konfig-Auditing fuer macOS.")
    ap.add_argument("--config", help="Pfad zu config.json (fuer Meldewege und Pfade)")
    ap.add_argument("--user-home", help="Home des ueberwachten Benutzers (fuer dessen Apps/Agenten)")
    ap.add_argument("--once", action="store_true", help="ein Auditing-Schritt")
    ap.add_argument("--show", action="store_true", help="Verlauf der Systemaenderungen lesbar ausgeben")
    ap.add_argument("--selftest", action="store_true", help="Vergleichslogik ohne Mac pruefen")
    ap.add_argument("--limit", type=int, default=100)
    args = ap.parse_args(argv)

    if args.selftest:
        return selftest()

    cfg = {}
    if args.config:
        loaded = _load_json(args.config)
        if loaded:
            cfg = loaded

    snapshot_path = os.path.expanduser(cfg.get("sysaudit_snapshot_path", DEFAULT_SNAPSHOT))
    history_base = os.path.expanduser(cfg.get("sysaudit_history_path", DEFAULT_HISTORY))

    if args.show:
        lines = format_changes(read_history(history_base), args.limit)
        print("\n".join(lines) if lines else f"Kein Auditing-Verlauf zu {history_base}.")
        return 0

    if args.once:
        return run_once(cfg, snapshot_path, history_base, args.user_home)

    print("Bitte --once, --show oder --selftest angeben.", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
