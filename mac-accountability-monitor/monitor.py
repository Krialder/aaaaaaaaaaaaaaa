"""Accountability-Monitor: sichtbarer Waechter, der im Takt die aktive App und die
Browser-URL liest, gegen die Regeln prueft, in ein Tages-Protokoll schreibt und bei
einem Verstoss eine Meldung an alle konfigurierten Kanaele (ntfy, E-Mail) schickt.

Kein Tastatur- oder Inhaltsmitschnitt. Protokoll und Status liegen offen im
Home-Verzeichnis; die Tages-Protokolle sind append-only (nur Admin loeschbar).

Haertung gegen Aushebeln:
- Offline-Spool: unzustellbare Meldungen werden gepuffert und beim naechsten Netz
  erneut gesendet, damit ein kurzes WLAN-Aus keine Meldung verschluckt.
- Blind-Erkennung: sieht der Monitor wiederholt nichts (keine Vordergrund-App oder ein
  Browser vorn ohne lesbare URL), meldet er das aktiv, statt still zu verschwinden.

Aufrufe:
    python monitor.py --config config.json           # Dauerbetrieb (macOS)
    python monitor.py --config config.json --once     # ein einzelner Durchlauf
    python monitor.py --config config.json --show-history   # Verlauf lesbar
    python monitor.py --selftest                      # Logik ohne Mac pruefen
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from typing import Optional

import activity
import dailylog
import notify
import rules as rules_mod
import sampler as sampler_mod
from sampler import Sample, Sampler, default_sampler


def _now_iso() -> str:
    # UTC mit 'Z': eindeutig ueber Zeitzonen, sortierbar im Log.
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _expand(path: str) -> str:
    return os.path.expanduser(os.path.expandvars(path))


def load_config(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def build_ruleset(cfg: dict, base_dir: str) -> rules_mod.RuleSet:
    """Fuehrt die aus Cold Turkey erzeugten Regeln mit den manuellen aus config.json
    zusammen. Getrennt gehalten, damit ein erneuter Cold-Turkey-Import nur die
    generierte Datei ueberschreibt und manuelle Zusaetze nicht verliert.
    """
    generated = {"blocked_domains": [], "blocked_apps": []}
    gen_full = _generated_rules_path(cfg, base_dir)
    if os.path.exists(gen_full):
        with open(gen_full, "r", encoding="utf-8") as f:
            generated = json.load(f)

    manual = cfg.get("rules", {})
    merged = {
        "app_mode": manual.get("app_mode", "blocklist"),
        "web_mode": manual.get("web_mode", "blocklist"),
        "blocked_domains": list(generated.get("blocked_domains", [])) + list(manual.get("blocked_domains", [])),
        "blocked_apps": list(generated.get("blocked_apps", [])) + list(manual.get("blocked_apps", [])),
        "allowed_domains": list(manual.get("allowed_domains", [])) + list(manual.get("allow_domains", [])),
        "allowed_apps": list(manual.get("allowed_apps", [])),
        "always_notify_apps": list(manual.get("always_notify_apps", [])),
        "baseline_allow_apps": list(manual.get("baseline_allow_apps", [])),
    }
    return rules_mod.RuleSet.from_dict(merged)


def _generated_rules_path(cfg: dict, base_dir: str) -> str:
    gen_path = cfg.get("generated_rules", "rules.generated.json")
    return gen_path if os.path.isabs(gen_path) else os.path.join(base_dir, gen_path)


class Cooldown:
    """Verhindert Meldungs-Spam. Pro dedup_key fruehestens nach cooldown_seconds erneut
    melden. Nur im Speicher: nach einem Neustart darf einmal erneut gemeldet werden.
    """

    def __init__(self, seconds: int):
        self._seconds = seconds
        self._last: dict[str, float] = {}

    def should_send(self, key: str, now: float) -> bool:
        last = self._last.get(key)
        if last is None or now - last >= self._seconds:
            self._last[key] = now
            return True
        return False


class Notifier:
    """Versand mit Offline-Spool. emit() versucht den Direktversand; scheitert ein Kanal
    (etwa weil das Netz weg ist), landet die Meldung mit den offenen Kanaelen in einer
    Spool-Datei. flush() versucht die offenen Kanaele erneut und leert den Spool. Die
    Spool-Datei endet auf .json (nicht .jsonl), damit der append-only-Watchdog sie nicht
    unveraenderlich macht: sie muss beim Leeren neu geschrieben werden.
    """

    def __init__(self, channels: list, spool_path: str, spool_max: int = 500):
        self.channels = channels
        self.by_name = {c.name: c for c in channels}
        self.spool_path = spool_path
        self.spool_max = spool_max

    def emit(self, subject: str, body: str, kind: str) -> dict[str, bool]:
        results = notify.dispatch(self.channels, subject, body, kind)
        failed = [name for name, ok in results.items() if not ok]
        if failed:
            self._add({"subject": subject, "body": body, "kind": kind,
                       "pending": failed, "time": _now_iso()})
        return results

    def flush(self) -> None:
        events = self._load()
        if not events:
            return
        remaining = []
        for ev in events:
            still = []
            for name in ev.get("pending", []):
                ch = self.by_name.get(name)
                if ch is None:
                    continue  # Kanal aus der Konfiguration entfernt: Eintrag verwerfen
                if not ch.send(ev["subject"], ev["body"], ev.get("kind", "info")):
                    still.append(name)
            if still:
                ev["pending"] = still
                remaining.append(ev)
        self._save(remaining)

    def _load(self) -> list:
        try:
            with open(self.spool_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, list) else []
        except (OSError, json.JSONDecodeError):
            return []

    def _save(self, events: list) -> None:
        os.makedirs(os.path.dirname(self.spool_path), exist_ok=True)
        tmp = self.spool_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(events, f, ensure_ascii=False)
        os.replace(tmp, self.spool_path)

    def _add(self, ev: dict) -> None:
        events = self._load()
        events.append(ev)
        if len(events) > self.spool_max:
            events = events[-self.spool_max:]  # aelteste verwerfen, sonst waechst es unbegrenzt
        self._save(events)


class BlindDetector:
    """Erkennt, wenn der Monitor wiederholt nichts sieht, und macht daraus ein Signal.
    Zwei Faelle: keine Vordergrund-App lesbar (System-Events-Freigabe fehlt, der Monitor
    ist blind) oder ein bekannter Browser ist vorn, aber die URL ist nicht lesbar
    (Browser-Freigabe fehlt oder ein nicht auslesbarer Browser wie Firefox). Meldet je
    Episode genau einmal und einmal die Erholung, damit es nicht spammt.

    threshold ist die Zahl aufeinanderfolgender blinder Messungen bis zur Meldung; bei
    3s Takt sind 20 rund eine Minute, kurz genug zum Merken, lang genug gegen Ausreisser.
    """

    def __init__(self, threshold: int):
        self.threshold = max(1, threshold)
        self.blind_app = 0
        self.blind_url = 0
        self.alerted_app = False
        self.alerted_url = False

    def observe(self, sample: Sample) -> list[tuple[str, str]]:
        out: list[tuple[str, str]] = []

        if sample.app_name is None:
            self.blind_app += 1
            if self.blind_app >= self.threshold and not self.alerted_app:
                self.alerted_app = True
                out.append(("blind", "Keine Vordergrund-App lesbar. Die System-Events-"
                                     "Freigabe fehlt vermutlich, der Monitor sieht nichts."))
        else:
            if self.alerted_app:
                out.append(("recover", "Vordergrund wieder lesbar."))
            self.blind_app = 0
            self.alerted_app = False

        browser_blind = (sample.app_name is not None
                         and sampler_mod.is_browser(sample.app_name)
                         and not sample.url)
        if browser_blind:
            self.blind_url += 1
            if self.blind_url >= self.threshold and not self.alerted_url:
                self.alerted_url = True
                out.append(("blind", f"Browser '{sample.app_name}' ist vorn, aber die URL "
                                     f"ist nicht lesbar (Freigabe fehlt oder nicht auslesbarer Browser)."))
        else:
            if self.alerted_url:
                out.append(("recover", "Browser-URL wieder lesbar."))
            self.blind_url = 0
            self.alerted_url = False

        return out


STARTUP_MIN_INTERVAL = 600  # s: Startmeldungen fruehestens alle 10 min
STATUS_MAX_AGE = 30.0       # s: Status auch ohne Aenderung so oft auffrischen


def _rate_limited_start(marker_path: str, now: float) -> bool:
    """True, wenn seit der letzten Startmeldung genug Zeit vergangen ist. Persistiert in
    einer Datei, weil jeder Neustart ein neuer Prozess ist und ein Speicher-Cooldown
    verloren ginge. So meldet ein echter Kill-und-Neustart (Minuten auseinander) weiter,
    ein Absturz-Neustart-Loop (Sekunden) aber hoechstens einmal pro Fenster.
    """
    try:
        with open(marker_path, "r", encoding="utf-8") as f:
            last = float(f.read().strip())
    except (OSError, ValueError):
        last = 0.0
    if now - last < STARTUP_MIN_INTERVAL:
        return False
    try:
        os.makedirs(os.path.dirname(marker_path), exist_ok=True)
        with open(marker_path, "w", encoding="utf-8") as f:
            f.write(str(now))
    except OSError:
        pass
    return True


def _append_log(log_path: str, record: dict) -> None:
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def _write_status(status_path: str, status: dict) -> None:
    """Schreibt den Status atomar (temp + replace), damit die Menuleisten-Anzeige nie
    eine halb geschriebene Datei liest.
    """
    os.makedirs(os.path.dirname(status_path), exist_ok=True)
    tmp = status_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(status, f, ensure_ascii=False)
    os.replace(tmp, status_path)


def read_history(base_path: str) -> list[dict]:
    """Liest den Verlauf ueber alle Tagesdateien (base-YYYY-MM-DD.jsonl) plus eine evtl.
    vorhandene Alt-Datei ohne Datum. Kaputte Zeilen werden uebersprungen.
    """
    files = dailylog.daily_siblings(base_path)
    if os.path.exists(base_path):
        files.append(base_path)
    out: list[dict] = []
    for path in files:
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        out.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        except OSError:
            continue
    return out


def _sources_mtime(*paths: str) -> float:
    m = 0.0
    for p in paths:
        try:
            m = max(m, os.path.getmtime(p))
        except OSError:
            pass
    return m


def process_sample(sample: Sample, ruleset: rules_mod.RuleSet, cooldown: Cooldown,
                   notifier: Optional[Notifier], now: float, counters: Optional[dict] = None,
                   send: bool = True, raw_log_path: Optional[str] = None) -> Optional[rules_mod.Violation]:
    """Ein Beobachtungs-Schritt: bei Verstoss (und abgelaufenem Cooldown) melden (mit
    Offline-Spool). Gibt den erkannten Verstoss zurueck (fuer den Selbsttest). Der
    Verlauf wird ausserhalb ueber den ActivityTracker geschrieben; das rohe Je-Messung-
    Log nur, wenn raw_log_path gesetzt ist (Schalter log_raw_samples).
    """
    when = _now_iso()
    v = rules_mod.evaluate(sample.app_name, sample.url, ruleset)

    if raw_log_path:
        _append_log(raw_log_path, {
            "time": when,
            "app": sample.app_name,
            "url": sample.url,
            "violation": None if v is None else {"kind": v.kind, "matched": v.matched},
        })

    if v is not None and counters is not None:
        counters["violations"] = counters.get("violations", 0) + 1
        counters["last_violation"] = {"kind": v.kind, "matched": v.matched, "time": when}

    if v is not None and cooldown.should_send(v.dedup_key, now):
        subject = notify.subject_for(v.kind, v.matched)
        body = notify.format_violation(v.kind, v.matched, v.observed, when)
        if send and notifier is not None:
            notifier.emit(subject, body, v.kind)
        else:
            print(f"[dry-run] wuerde melden ({subject}):\n{body}\n")
    return v


def _configure(cfg: dict, base_dir: str) -> dict:
    """Buendelt allen aus der Konfiguration abgeleiteten Zustand, damit ein Hot-Reload
    ihn in einem Rutsch neu bauen kann.
    """
    channels = notify.build_channels(cfg)
    spool_path = _expand(cfg.get("spool_path", "~/AccountabilityMonitor/spool.pending.json"))
    return {
        "ruleset": build_ruleset(cfg, base_dir),
        "cooldown": Cooldown(int(cfg.get("cooldown_seconds", 300))),
        "channels": channels,
        "notifier": Notifier(channels, spool_path, int(cfg.get("spool_max", 500))),
        "history_base": _expand(cfg.get("history_path", "~/AccountabilityMonitor/activity_history.jsonl")),
        "raw_base": _expand(cfg.get("log_path", "~/AccountabilityMonitor/activity.jsonl"))
        if cfg.get("log_raw_samples", False) else None,
        "status_path": _expand(cfg.get("status_path", "~/AccountabilityMonitor/status.json")),
        "interval": float(cfg.get("poll_interval_seconds", 3)),
        "heartbeat": float(cfg.get("heartbeat_seconds", 0)),
        "notify_on_start": cfg.get("notify_on_start", True),
        "spool_flush": float(cfg.get("spool_flush_seconds", 60)),
    }


def run_loop(cfg: dict, base_dir: str, config_path: Optional[str], sampler: Sampler,
             once: bool = False) -> int:
    st = _configure(cfg, base_dir)
    tracker = activity.ActivityTracker()
    blind = BlindDetector(int(cfg.get("blind_after_samples", 20)))
    started = _now_iso()
    counters = {"violations": 0, "last_violation": None, "started": started}

    gen_full = _generated_rules_path(cfg, base_dir)
    reload_paths = tuple(p for p in (config_path, gen_full) if p)
    src_mtime = _sources_mtime(*reload_paths)

    ch_names = ", ".join(c.name for c in st["channels"]) or "KEINE"
    print(f"Monitor laeuft. Kanaele: {ch_names}. Verlauf: {st['history_base']}. "
          f"Intervall: {st['interval']}s.")
    if not st["channels"]:
        print("WARNUNG: kein Meldeweg konfiguriert. Es wird nur lokal geloggt.")

    # Startmeldung nur im Dauerbetrieb (nicht bei --once, das laeuft beim Ausloesen der
    # Freigaben) und ratenbegrenzt gegen Absturz-Neustart-Loops.
    start_marker = os.path.join(os.path.dirname(st["status_path"]), ".last_start")
    if st["channels"] and st["notify_on_start"] and not once \
            and _rate_limited_start(start_marker, time.time()):
        st["notifier"].emit(notify.subject_for("info", "Start"),
                            f"Monitor gestartet um {started}.", "info")

    last_heartbeat = time.time()
    last_flush = time.time()
    last_status_written = 0.0
    last_status_key = None
    try:
        while True:
            now = time.time()

            # Hot-Reload: aendert sich config.json oder die Regeldatei, Zustand neu bauen,
            # ohne den Waechter neu zu starten. Fehler beim Neuladen behalten den alten
            # Zustand, damit eine halb geschriebene Datei nichts kippt.
            if reload_paths and config_path:
                m = _sources_mtime(*reload_paths)
                if m != src_mtime:
                    src_mtime = m
                    try:
                        st = _configure(load_config(config_path), base_dir)
                        print("Konfiguration neu geladen.")
                    except (OSError, json.JSONDecodeError) as e:
                        print(f"Neuladen fehlgeschlagen, behalte alte Konfiguration: {e}")

            sample = sampler.sample()
            raw_daily = dailylog.daily_path(st["raw_base"]) if st["raw_base"] else None
            v = process_sample(sample, st["ruleset"], st["cooldown"], st["notifier"],
                               now, counters, raw_log_path=raw_daily)

            # Blind-Erkennung: sieht der Monitor nichts, das aktiv melden.
            for kind, body in blind.observe(sample):
                if kind == "blind":
                    st["notifier"].emit("[Accountability] Monitor sieht nichts", body, "blind")
                else:
                    st["notifier"].emit("[Accountability] Monitor-Status", body, "info")

            # Verlauf: nur bei einem Wechsel eine Zeile, in die Tagesdatei.
            rec = tracker.observe(sample.app_name, sample.url, now, _now_iso(), v is not None)
            if rec:
                _append_log(dailylog.daily_path(st["history_base"]), rec)

            # Status nur bei Aenderung schreiben, sonst hoechstens alle STATUS_MAX_AGE
            # Sekunden zur Frische. Spart rund 28000 Schreibvorgaenge pro Tag.
            status = {
                "running": True, "pid": os.getpid(), "started": started,
                "last_check": _now_iso(), "front_app": sample.app_name,
                "violations": counters["violations"], "last_violation": counters["last_violation"],
            }
            key = (status["front_app"], status["violations"], bool(status["last_violation"]))
            if key != last_status_key or now - last_status_written >= STATUS_MAX_AGE:
                _write_status(st["status_path"], status)
                last_status_written = now
                last_status_key = key

            # Offline-Spool periodisch leeren; das testet zugleich die Verbindung.
            if now - last_flush >= st["spool_flush"]:
                st["notifier"].flush()
                last_flush = now

            if st["heartbeat"] > 0 and st["channels"] and now - last_heartbeat >= st["heartbeat"]:
                st["notifier"].emit(notify.subject_for("info", "laeuft"),
                                    f"Monitor laeuft weiter. Verstoesse bisher: {counters['violations']}.", "info")
                last_heartbeat = now

            if once:
                return 0
            time.sleep(st["interval"])
    except KeyboardInterrupt:
        print("\nMonitor beendet.")
        rec = tracker.flush(time.time(), _now_iso())
        if rec:
            _append_log(dailylog.daily_path(st["history_base"]), rec)
        _write_status(st["status_path"], {"running": False, "stopped": _now_iso(),
                                          "violations": counters["violations"]})
        return 0


def selftest(cfg: Optional[dict], live: bool) -> int:
    """Beweist die Kernbehauptung ohne Mac: aus erfundenen Beobachtungen entstehen
    genau die erwarteten Verstoesse. Ohne --live wird der Versand nur simuliert.
    """
    ruleset = rules_mod.RuleSet.from_dict({
        "blocked_domains": ["youtube.com", "reddit.com"],
        "blocked_apps": ["Steam"],
        "always_notify_apps": ["Terminal", "iTerm2"],
        "allow_domains": ["edu.youtube.com"],
    })

    cases: list[tuple[Sample, Optional[str]]] = [
        (Sample("Google Chrome", "https://www.youtube.com/watch?v=x"), "website"),
        (Sample("Google Chrome", "https://m.youtube.com/feed"), "website"),
        (Sample("Google Chrome", "https://edu.youtube.com/course"), None),
        (Sample("Google Chrome", "https://notyoutube.com/"), None),
        (Sample("Safari", "https://old.reddit.com/r/test"), "website"),
        (Sample("Arc", "https://www.reddit.com/"), "website"),
        (Sample("Terminal", None), "watch-app"),
        (Sample("Steam", None), "app"),
        (Sample("Xcode", "https://developer.apple.com"), None),
        (Sample("Google Chrome", "about:blank"), None),          # interne Seite, keine Domain
    ]

    channels = notify.build_channels(cfg) if cfg else []
    tmp = os.environ.get("TEMP", "/tmp")
    notifier = Notifier(channels, os.path.join(tmp, "accountability_selftest_spool.json"))
    log_path = _expand((cfg or {}).get("log_path", os.path.join(tmp, "accountability_selftest.jsonl")))
    cooldown = Cooldown(0)  # im Test soll jeder Fall unabhaengig melden

    failures = 0
    for i, (sample, expected) in enumerate(cases, 1):
        v = process_sample(sample, ruleset, cooldown, notifier, float(i),
                           counters={}, send=live, raw_log_path=log_path)
        got = v.kind if v else None
        ok = got == expected
        if not ok:
            failures += 1
        print(f"[{'OK' if ok else 'FEHLER'}] {sample.app_name or '-':16} "
              f"{sample.url or '-':40} erwartet={expected} bekam={got}")

    # Zweite Behauptung: aus einer Beispiel-Konfiguration entstehen die richtigen Kanaele.
    demo = {"ntfy": {"topic": "x"}, "email": {"host": "smtp.example.com", "recipient": "a@b.c"}}
    names = sorted(c.name for c in notify.build_channels(demo))
    if names != ["email", "ntfy"]:
        failures += 1
        print(f"[FEHLER] Kanalaufbau erwartet ['email', 'ntfy'], bekam {names}")
    else:
        print(f"[OK] Kanalaufbau: {names}")

    # Dritte Behauptung: der Aktivitaetsverlauf erzeugt Wechsel mit korrekten Dauern.
    tr = activity.ActivityTracker()
    seq = [
        ("Chrome", "a.com", 0.0, False),
        ("Chrome", "a.com", 3.0, False),
        ("Chrome", "b.com", 10.0, False),
        ("Terminal", None, 15.0, True),
    ]
    recs = [r for r in (tr.observe(a, u, t, f"t{t}", b) for a, u, t, b in seq) if r]
    recs.append(tr.flush(20.0, "t20"))
    durs = [r["from"]["duration_s"] for r in recs if r["from"]]
    if durs != [10.0, 5.0, 5.0] or not recs[-1]["from"]["blocked"] or recs[0]["from"] is not None:
        failures += 1
        print(f"[FEHLER] Verlauf falsch: Dauern={durs}")
    else:
        print(f"[OK] Verlauf: Wechsel mit Dauern {durs}, letztes Segment gesperrt")

    # Vierte Behauptung: Erlaubnisliste meldet alles ausserhalb der Liste, interne
    # Seiten und System-Grundmenge nicht, Immer-melden-Apps immer.
    allow_rules = rules_mod.RuleSet.from_dict({
        "app_mode": "allowlist", "web_mode": "allowlist",
        "allowed_apps": ["Xcode"], "allowed_domains": ["github.com"],
        "always_notify_apps": ["Terminal"],
    })
    allow_cases: list[tuple[Sample, Optional[str]]] = [
        (Sample("Xcode", None), None),
        (Sample("Finder", None), None),
        (Sample("Steam", None), "app-not-allowed"),
        (Sample("Safari", "https://github.com/x"), None),
        (Sample("Safari", "https://facebook.com/"), "website-not-allowed"),
        (Sample("Google Chrome", "chrome://newtab/"), None),
        (Sample("Terminal", None), "watch-app"),
    ]
    for sample, expected in allow_cases:
        v = rules_mod.evaluate(sample.app_name, sample.url, allow_rules)
        got = v.kind if v else None
        ok = got == expected
        if not ok:
            failures += 1
        print(f"[{'OK' if ok else 'FEHLER'}] allowlist {sample.app_name or '-':10} "
              f"{sample.url or '-':30} erwartet={expected} bekam={got}")

    # Fuenfte Behauptung: Tages-Rotation datiert den Dateinamen korrekt.
    dp = dailylog.daily_path("/x/activity_history.jsonl", datetime(2026, 7, 4, tzinfo=timezone.utc))
    if not dp.endswith("activity_history-2026-07-04.jsonl"):
        failures += 1
        print(f"[FEHLER] daily_path datiert falsch: {dp}")
    else:
        print("[OK] daily_path datiert korrekt")

    # Sechste Behauptung: interne und hostlose Adressen ergeben keinen Host.
    internal = ["about:blank", "chrome://newtab/", "file:///x", "data:text/html,x", "http://localhost"]
    if any(rules_mod._normalize_domain(u) for u in internal):
        failures += 1
        print("[FEHLER] interne/hostlose URL ergab einen Host")
    else:
        print("[OK] interne/hostlose URLs ergeben keinen Host")

    # Siebte Behauptung: Blind-Erkennung meldet je Episode einmal und die Erholung.
    bd = BlindDetector(threshold=3)
    ev = []
    for _ in range(3):
        ev += bd.observe(Sample(None, None))          # System-Events blind -> 1 blind
    ev += bd.observe(Sample("Finder", None))          # Erholung -> 1 recover
    for _ in range(3):
        ev += bd.observe(Sample("Firefox", None))     # Browser vorn ohne URL -> 1 blind
    ev += bd.observe(Sample("Safari", "https://x.com"))  # Erholung -> 1 recover
    blinds = sum(1 for k, _ in ev if k == "blind")
    recovers = sum(1 for k, _ in ev if k == "recover")
    if blinds != 2 or recovers != 2:
        failures += 1
        print(f"[FEHLER] Blind-Erkennung: blinds={blinds} recovers={recovers} (erwartet 2/2)")
    else:
        print("[OK] Blind-Erkennung: je Episode eine Meldung und eine Erholung")

    # Achte Behauptung: Offline-Spool puffert bei Fehlschlag und leert bei Erfolg.
    class _FakeCh:
        def __init__(self, name, ok):
            self.name = name
            self._ok = ok
        def send(self, s, b, k):
            return self._ok

    spool = os.path.join(tmp, "accountability_selftest_spool2.json")
    try:
        os.remove(spool)
    except OSError:
        pass
    n_fail = Notifier([_FakeCh("ntfy", False)], spool)
    n_fail.emit("s", "b", "info")               # scheitert -> gepuffert
    pending_after_fail = len(n_fail._load())
    n_ok = Notifier([_FakeCh("ntfy", True)], spool)
    n_ok.flush()                                # gelingt -> geleert
    pending_after_flush = len(n_ok._load())
    if pending_after_fail != 1 or pending_after_flush != 0:
        failures += 1
        print(f"[FEHLER] Spool: nach Fehler={pending_after_fail}, nach Flush={pending_after_flush}")
    else:
        print("[OK] Offline-Spool: puffert bei Fehler, leert bei Erfolg")

    total = len(cases) + len(allow_cases) + 6
    print(f"\nSELFTEST_RESULT failures={failures} total={total}")
    return 1 if failures else 0


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Accountability-Monitor fuer macOS.")
    ap.add_argument("--config", help="Pfad zu config.json")
    ap.add_argument("--once", action="store_true", help="nur ein Durchlauf")
    ap.add_argument("--selftest", action="store_true", help="Logik ohne Mac pruefen")
    ap.add_argument("--live", action="store_true", help="im Selbsttest echt senden")
    ap.add_argument("--show-history", action="store_true", help="Aktivitaetsverlauf lesbar ausgeben")
    ap.add_argument("--test-notify", action="store_true", help="eine echte Testmeldung an die Kanaele senden")
    ap.add_argument("--limit", type=int, default=50, help="max. Zeilen fuer --show-history")
    args = ap.parse_args(argv)

    cfg = load_config(args.config) if args.config else None

    if args.selftest:
        return selftest(cfg, live=args.live)

    if args.test_notify:
        if not cfg:
            print("Fuer --test-notify wird --config benoetigt.", file=sys.stderr)
            return 2
        channels = notify.build_channels(cfg)
        if not channels:
            print("Kein Meldeweg konfiguriert. ntfy-Topic oder E-Mail in config.json setzen.")
            return 1
        results = notify.dispatch(channels, "[Accountability] Test",
                                  "Testmeldung. Kommt sie an, stimmt der Weg.", "info")
        for name, ok in results.items():
            print(f"{name}: {'ok' if ok else 'FEHLGESCHLAGEN'}")
        return 0 if all(results.values()) else 1

    if args.show_history:
        hp = _expand((cfg or {}).get("history_path", "~/AccountabilityMonitor/activity_history.jsonl"))
        lines = activity.format_history(read_history(hp), args.limit)
        print("\n".join(lines) if lines else f"Kein Verlauf zu {hp}.")
        return 0

    if not cfg:
        print("Ohne --selftest wird --config benoetigt.", file=sys.stderr)
        return 2

    config_path = os.path.abspath(args.config)
    base_dir = os.path.dirname(config_path)
    return run_loop(cfg, base_dir, config_path, default_sampler(), once=args.once)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
