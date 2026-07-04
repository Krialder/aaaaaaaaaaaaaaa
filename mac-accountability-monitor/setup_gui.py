"""Einrichtungs-GUI fuer den Accountability-Monitor. Fuehrt die betroffene Person in
einem Fenster durch alles: Meldewege eintragen und testen, Cold-Turkey-Liste einlesen,
macOS-Freigaben ausloesen, installieren und Status sehen.

Bewusster Aufbau: die reine Logik (Konfiguration bauen und pruefen) steht als freie
Funktionen oben und ist ohne Bildschirm testbar (python3 setup_gui.py --selftest).
Tkinter wird erst in main() geladen, damit der Selbsttest auch dort laeuft, wo keine
grafische Oberflaeche vorhanden ist.

Rechte-Modell: die GUI laeuft als normaler Benutzer. Nur der Installationsschritt
braucht Systemrechte und holt sie ueber den nativen macOS-Passwortdialog (osascript
'with administrator privileges'). So werden die Freigaben dem richtigen Benutzer
zugeordnet, was scheiterte, liefe die ganze GUI als root.
"""

from __future__ import annotations

import json
import os
import secrets
import subprocess
import sys

APP_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_NTFY_SERVER = "https://ntfy.sh"
DEFAULT_WATCH_APPS = "Terminal, iTerm2"


# ============================================================ reine Logik (testbar)

def generate_topic() -> str:
    """Ein unratbarer ntfy-Topic. Der Topic ist das einzige Geheimnis des Push-Wegs,
    also lang und zufaellig, nicht vom Nutzer frei erfunden.
    """
    return "acc-" + secrets.token_hex(10)


def build_config_dict(f: dict) -> dict:
    """Baut die config.json-Struktur aus den GUI-Feldern. Spiegelt config.example.json,
    damit monitor.py und notify.py dieselbe Form vorfinden.
    """
    watch = [a.strip() for a in f.get("watch_apps", "").split(",") if a.strip()]
    allowed_apps = [a.strip() for a in f.get("allowed_apps", "").split(",") if a.strip()]
    allowed_domains = [a.strip() for a in f.get("allowed_domains", "").split(",") if a.strip()]
    return {
        "ntfy": {
            "server": (f.get("ntfy_server") or DEFAULT_NTFY_SERVER).strip(),
            "topic": f.get("ntfy_topic", "").strip(),
            "token": f.get("ntfy_token", "").strip(),
            "priority": "high",
            "title": "Accountability Monitor",
        },
        "email": {
            "host": f.get("email_host", "").strip(),
            "port": _int(f.get("email_port"), 587),
            "user": f.get("email_user", "").strip(),
            "password": f.get("email_password", ""),
            "sender": f.get("email_sender", "").strip(),
            "recipient": f.get("email_recipient", "").strip(),
        },
        "poll_interval_seconds": _int(f.get("poll_interval"), 3),
        "cooldown_seconds": _int(f.get("cooldown"), 300),
        "heartbeat_seconds": _int(f.get("heartbeat"), 0),
        "notify_on_start": True,
        "history_path": "~/AccountabilityMonitor/activity_history.jsonl",
        "log_path": "~/AccountabilityMonitor/activity.jsonl",
        "log_raw_samples": False,
        "status_path": "~/AccountabilityMonitor/status.json",
        "generated_rules": "rules.generated.json",
        "spool_path": "~/AccountabilityMonitor/spool.pending.json",
        "spool_flush_seconds": 60,
        "spool_max": 500,
        "blind_after_samples": 20,
        "sysaudit_notify": True,
        "sysaudit_snapshot_path": "/Library/Application Support/AccountabilityMonitor/sysaudit_snapshot.json",
        "sysaudit_history_path": "/Library/Application Support/AccountabilityMonitor/sysaudit_history.jsonl",
        "rules": {
            "app_mode": _mode_value(f.get("app_mode")),
            "web_mode": _mode_value(f.get("web_mode")),
            "blocked_domains": [],
            "blocked_apps": [],
            "allowed_domains": allowed_domains,
            "allowed_apps": allowed_apps,
            "always_notify_apps": watch,
            "baseline_allow_apps": [],
        },
    }


def _mode_value(v) -> str:
    """Wandelt die deutsche Auswahl der GUI in den Konfigurationswert. Akzeptiert auch
    die Konfigurationswerte selbst, damit ein direkt bearbeitetes config.json passt.
    """
    s = str(v or "").strip().lower()
    if s in ("allowlist", "erlaubnisliste", "whitelist"):
        return "allowlist"
    return "blocklist"


def validate(f: dict) -> list[str]:
    """Prueft die Eingaben und liefert eine Liste von Fehlermeldungen (leer = ok)."""
    errors: list[str] = []
    topic = f.get("ntfy_topic", "").strip()
    email_ok = bool(f.get("email_host", "").strip() and f.get("email_recipient", "").strip())

    if not topic and not email_ok:
        errors.append("Mindestens einen Meldeweg angeben: ntfy-Topic oder E-Mail (Host und Empfaenger).")
    if topic and ("HIER-EINEN" in topic or len(topic) < 8):
        errors.append("ntfy-Topic ist zu kurz oder noch der Platzhalter. Einen langen, unratbaren Wert setzen (Knopf 'Zufall').")
    if f.get("email_host", "").strip() and not f.get("email_recipient", "").strip():
        errors.append("E-Mail-Host gesetzt, aber kein Empfaenger.")
    try:
        _int(f.get("email_port"), 587, strict=True)
    except ValueError:
        errors.append("E-Mail-Port muss eine Zahl sein.")
    return errors


def _int(v, default: int, strict: bool = False) -> int:
    s = str(v).strip() if v is not None else ""
    if not s:
        return default
    if strict:
        return int(s)   # wirft ValueError, wenn keine Zahl
    try:
        return int(s)
    except ValueError:
        return default


def write_config(cfg: dict, path: str) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, ensure_ascii=False, indent=2)


def osascript_admin_argv(argv_items: list[str]) -> list[str]:
    """Baut den osascript-Aufruf, der '/bin/bash <script> <args...>' mit Adminrechten
    ausfuehrt. 'quoted form of' uebernimmt das Shell-Quoting, deshalb muessen Pfade mit
    Leerzeichen (etwa /Library/Application Support) nicht von Hand escaped werden.
    """
    expr = "quoted form of (item 1 of argv)"
    for i in range(2, len(argv_items) + 1):
        expr += f' & " " & quoted form of (item {i} of argv)'
    lines = [
        "on run argv",
        f'do shell script "/bin/bash " & {expr} with administrator privileges',
        "end run",
    ]
    cmd = ["osascript"]
    for ln in lines:
        cmd += ["-e", ln]
    cmd += argv_items
    return cmd


# ============================================================ Aktionen (nutzen Logik + Subprozesse)

def action_test_send(cfg: dict) -> str:
    """Schickt eine Testmeldung an alle konfigurierten Kanaele und fasst das Ergebnis
    als Text zusammen. Import erst hier, damit der Selbsttest ohne notify auskommt.
    """
    import notify
    channels = notify.build_channels(cfg)
    if not channels:
        return "Kein Meldeweg konfiguriert. Bitte ntfy-Topic oder E-Mail setzen."
    results = notify.dispatch(channels, "[Accountability] Test",
                              "Testmeldung aus der Einrichtung. Kommt sie an, stimmt der Weg.", "info")
    return "\n".join(f"{name}: {'ok' if ok else 'FEHLGESCHLAGEN'}" for name, ok in results.items())


def action_inspect_ctbbl(path: str) -> tuple[dict, str]:
    """Liest einen Cold-Turkey-Export und liefert (Regeln, Anzeigetext)."""
    import ctbbl_import
    data = ctbbl_import._load(path)
    result = ctbbl_import.extract(data)
    lines = [f"Domains ({len(result['blocked_domains'])}):"]
    lines += [f"  {d}" for d in result["blocked_domains"]]
    lines += [f"Apps ({len(result['blocked_apps'])}):"]
    lines += [f"  {a}" for a in result["blocked_apps"]]
    return result, "\n".join(lines)


def action_run_privileged_install(target_user: str) -> tuple[bool, str]:
    priv = os.path.join(APP_DIR, "install_privileged.sh")
    cmd = osascript_admin_argv([priv, target_user, APP_DIR, sys.executable])
    proc = subprocess.run(cmd, capture_output=True, text=True)
    out = (proc.stdout or "") + (proc.stderr or "")
    return ("INSTALL_OK" in out and proc.returncode == 0), out


def action_trigger_permissions(config_path: str) -> tuple[bool, str]:
    """Startet einen einzelnen Monitor-Durchlauf als aktueller Benutzer, damit macOS
    die Freigabe-Dialoge fuer System Events und den Browser zeigt.
    """
    monitor = os.path.join(APP_DIR, "monitor.py")
    proc = subprocess.run([sys.executable, monitor, "--config", config_path, "--once"],
                          capture_output=True, text=True)
    return proc.returncode == 0, (proc.stdout or "") + (proc.stderr or "")


# ============================================================ Selbsttest (ohne Tkinter)

def selftest() -> int:
    failures = 0

    t = generate_topic()
    if not (t.startswith("acc-") and len(t) >= 20):
        failures += 1
        print(f"[FEHLER] generate_topic zu schwach: {t}")
    else:
        print(f"[OK] generate_topic: {t}")

    good = {"ntfy_topic": t, "email_host": "smtp.example.com", "email_port": "587",
            "email_recipient": "partner@example.com", "watch_apps": "Terminal, iTerm2"}
    errs = validate(good)
    if errs:
        failures += 1
        print(f"[FEHLER] gueltige Eingabe abgelehnt: {errs}")
    else:
        print("[OK] gueltige Eingabe akzeptiert")

    bad = {"ntfy_topic": "HIER-EINEN-...", "email_port": "abc"}
    errs = validate(bad)
    if len(errs) < 2:
        failures += 1
        print(f"[FEHLER] Platzhalter/Port nicht erkannt: {errs}")
    else:
        print(f"[OK] fehlerhafte Eingabe erkannt ({len(errs)} Meldungen)")

    empty = validate({})
    if not empty:
        failures += 1
        print("[FEHLER] leere Eingabe (kein Meldeweg) nicht erkannt")
    else:
        print("[OK] leere Eingabe erkannt")

    cfg = build_config_dict(good)
    try:
        import notify
        names = sorted(c.name for c in notify.build_channels(cfg))
        if names != ["email", "ntfy"]:
            failures += 1
            print(f"[FEHLER] aus gebauter Konfig entstehen Kanaele {names}, erwartet ['email','ntfy']")
        else:
            print(f"[OK] gebaute Konfig ergibt Kanaele {names}")
    except ImportError:
        print("[WARN] notify nicht importierbar, Kanalpruefung uebersprungen")

    # osascript-Aufbau: quoted form muss fuer jedes Argument einmal vorkommen.
    cmd = osascript_admin_argv(["/a b/x.sh", "user", "/Library/Application Support/x", "/usr/bin/python3"])
    script = " ".join(cmd)
    if script.count("quoted form of") != 4:
        failures += 1
        print(f"[FEHLER] osascript-Aufbau falsch: {script}")
    else:
        print("[OK] osascript-Aufbau quotet alle vier Argumente")

    print(f"\nSETUP_SELFTEST_RESULT failures={failures}")
    return 1 if failures else 0


# ============================================================ GUI

def main() -> int:
    if "--selftest" in sys.argv:
        return selftest()

    import getpass
    import threading
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox, scrolledtext

    target_user = getpass.getuser()
    config_path = os.path.join(APP_DIR, "config.json")
    status_path = os.path.expanduser("~/AccountabilityMonitor/status.json")

    root = tk.Tk()
    root.title("Accountability-Monitor Einrichtung")
    root.geometry("640x620")

    vars_: dict[str, tk.StringVar] = {}

    def sv(name, default=""):
        v = tk.StringVar(value=default)
        vars_[name] = v
        return v

    def fields() -> dict:
        return {k: v.get() for k, v in vars_.items()}

    def run_async(fn, on_done):
        # Lange Aktionen (Netz, Installation) im Hintergrund, Ergebnis zurueck in den
        # Tk-Thread ueber root.after, damit die Oberflaeche nicht einfriert.
        def worker():
            try:
                res = fn()
            except Exception as e:  # noqa: BLE001  Fehler soll in der GUI sichtbar werden
                res = ("EXC", str(e))
            root.after(0, lambda: on_done(res))
        threading.Thread(target=worker, daemon=True).start()

    nb = ttk.Notebook(root)
    nb.pack(fill="both", expand=True, padx=10, pady=10)

    # ---- Tab 1: Benachrichtigung
    t1 = ttk.Frame(nb)
    nb.add(t1, text="1. Benachrichtigung")

    def row(parent, label, var, r, show=None, width=40):
        ttk.Label(parent, text=label).grid(row=r, column=0, sticky="w", pady=3)
        e = ttk.Entry(parent, textvariable=var, width=width, show=show)
        e.grid(row=r, column=1, sticky="we", pady=3)
        return e

    ttk.Label(t1, text="ntfy-Push (Pflicht oder E-Mail)", font=("", 12, "bold")).grid(
        row=0, column=0, columnspan=3, sticky="w", pady=(4, 6))
    row(t1, "Server", sv("ntfy_server", DEFAULT_NTFY_SERVER), 1)
    row(t1, "Topic", sv("ntfy_topic"), 2)
    ttk.Button(t1, text="Zufall", command=lambda: vars_["ntfy_topic"].set(generate_topic())).grid(
        row=2, column=2, padx=4)
    row(t1, "Token (optional)", sv("ntfy_token"), 3)

    ttk.Separator(t1, orient="horizontal").grid(row=4, column=0, columnspan=3, sticky="we", pady=8)
    ttk.Label(t1, text="E-Mail (optional, zweiter Weg)", font=("", 12, "bold")).grid(
        row=5, column=0, columnspan=3, sticky="w", pady=(4, 6))
    row(t1, "SMTP-Host", sv("email_host"), 6)
    row(t1, "Port", sv("email_port", "587"), 7, width=8)
    row(t1, "Benutzer", sv("email_user"), 8)
    row(t1, "Passwort/App-Passwort", sv("email_password"), 9, show="*")
    row(t1, "Absender (optional)", sv("email_sender"), 10)
    row(t1, "Empfaenger (Partner)", sv("email_recipient"), 11)
    t1.columnconfigure(1, weight=1)

    def do_test():
        errs = validate(fields())
        if errs:
            messagebox.showwarning("Bitte pruefen", "\n".join(errs))
            return
        cfg = build_config_dict(fields())
        run_async(lambda: action_test_send(cfg),
                  lambda res: messagebox.showinfo("Testergebnis",
                      res if isinstance(res, str) else str(res)))

    ttk.Button(t1, text="Testmeldung senden", command=do_test).grid(
        row=12, column=0, columnspan=3, pady=10)

    # ---- Tab 2: Regeln
    t2 = ttk.Frame(nb)
    nb.add(t2, text="2. Regeln (Cold Turkey)")

    ttk.Label(t2, text="Regelmodus", font=("", 12, "bold")).pack(anchor="w", pady=(6, 4))
    modefr = ttk.Frame(t2)
    modefr.pack(fill="x")
    ttk.Label(modefr, text="Apps:").grid(row=0, column=0, sticky="w", padx=(0, 4))
    ttk.Combobox(modefr, textvariable=sv("app_mode", "Blockliste"),
                 values=["Blockliste", "Erlaubnisliste"], state="readonly", width=16
                 ).grid(row=0, column=1, padx=(0, 14))
    ttk.Label(modefr, text="Webseiten:").grid(row=0, column=2, sticky="w", padx=(0, 4))
    ttk.Combobox(modefr, textvariable=sv("web_mode", "Blockliste"),
                 values=["Blockliste", "Erlaubnisliste"], state="readonly", width=16
                 ).grid(row=0, column=3)
    ttk.Label(t2, text="Blockliste meldet Verbotenes, Erlaubnisliste meldet alles ausser dem Erlaubten.",
              foreground="grey").pack(anchor="w", pady=(2, 6))
    ttk.Label(t2, text="Erlaubte Apps (fuer Erlaubnisliste, Komma-getrennt):").pack(anchor="w")
    ttk.Entry(t2, textvariable=sv("allowed_apps")).pack(fill="x", pady=(0, 4))
    ttk.Label(t2, text="Erlaubte Seiten (fuer Erlaubnisliste, Komma-getrennt):").pack(anchor="w")
    ttk.Entry(t2, textvariable=sv("allowed_domains")).pack(fill="x")
    ttk.Separator(t2, orient="horizontal").pack(fill="x", pady=10)

    ttk.Label(t2, text="Cold-Turkey-Export (.ctbbl) einlesen (fuer Blockliste)", font=("", 12, "bold")).pack(
        anchor="w", pady=(0, 4))
    ctbbl_var = sv("ctbbl_path")
    fr = ttk.Frame(t2)
    fr.pack(fill="x")
    ttk.Entry(fr, textvariable=ctbbl_var).pack(side="left", fill="x", expand=True)
    ttk.Button(fr, text="Datei waehlen",
               command=lambda: ctbbl_var.set(filedialog.askopenfilename(
                   filetypes=[("Cold Turkey", "*.ctbbl"), ("Alle", "*.*")]) or ctbbl_var.get())
               ).pack(side="left", padx=4)

    ttk.Label(t2, text="Immer melden (Apps, Komma-getrennt):").pack(anchor="w", pady=(8, 0))
    ttk.Entry(t2, textvariable=sv("watch_apps", DEFAULT_WATCH_APPS)).pack(fill="x")

    out2 = scrolledtext.ScrolledText(t2, height=14)
    out2.pack(fill="both", expand=True, pady=6)
    imported = {"rules": None}

    def do_inspect():
        p = ctbbl_var.get().strip()
        if not p or not os.path.exists(p):
            messagebox.showwarning("Datei fehlt", "Bitte zuerst eine .ctbbl-Datei waehlen.")
            return
        try:
            rules, text = action_inspect_ctbbl(p)
        except Exception as e:  # noqa: BLE001
            messagebox.showerror("Fehler beim Lesen", str(e))
            return
        imported["rules"] = rules
        out2.delete("1.0", "end")
        out2.insert("end", text + "\n\nSieht das vollstaendig aus? Dann 'Uebernehmen'.")

    def do_apply():
        if not imported["rules"]:
            messagebox.showwarning("Erst pruefen", "Bitte zuerst 'Pruefen' ausfuehren.")
            return
        write_config(imported["rules"], os.path.join(APP_DIR, "rules.generated.json"))
        messagebox.showinfo("Uebernommen", "rules.generated.json geschrieben.")

    frb = ttk.Frame(t2)
    frb.pack(fill="x")
    ttk.Button(frb, text="Pruefen", command=do_inspect).pack(side="left")
    ttk.Button(frb, text="Uebernehmen", command=do_apply).pack(side="left", padx=6)

    # ---- Tab 3: Installation & Status
    t3 = ttk.Frame(nb)
    nb.add(t3, text="3. Installieren & Status")
    log3 = scrolledtext.ScrolledText(t3, height=16)
    log3.pack(fill="both", expand=True, pady=(6, 6))

    def log(msg):
        log3.insert("end", msg + "\n")
        log3.see("end")

    def save_config_or_warn() -> bool:
        errs = validate(fields())
        if errs:
            messagebox.showwarning("Bitte pruefen", "\n".join(errs))
            return False
        write_config(build_config_dict(fields()), config_path)
        return True

    def do_permissions():
        if not save_config_or_warn():
            return
        log("Loese macOS-Freigaben aus (ein Monitor-Durchlauf)...")
        log("Bitte die Dialoge fuer System Events und den Browser bestaetigen.")
        run_async(lambda: action_trigger_permissions(config_path),
                  lambda res: log("Freigabe-Lauf beendet. Falls kein Dialog kam: "
                                  "Systemeinstellungen > Datenschutz & Sicherheit > Automation pruefen.")
                  if isinstance(res, tuple) else log(str(res)))

    def do_install():
        if not save_config_or_warn():
            return
        log(f"Installiere als Benutzer '{target_user}'. Das Admin-Passwort wird abgefragt...")

        def done(res):
            if isinstance(res, tuple) and res and res[0] == "EXC":
                log("Fehler: " + res[1]); return
            ok, out = res
            log(out.strip())
            log("Installation erfolgreich." if ok else "Installation nicht bestaetigt (kein INSTALL_OK).")

        run_async(lambda: action_run_privileged_install(target_user), done)

    def do_uninstall():
        priv = os.path.join(APP_DIR, "uninstall.sh")
        cmd = osascript_admin_argv([priv, target_user])
        log("Deinstalliere. Das Admin-Passwort wird abgefragt...")
        run_async(lambda: subprocess.run(cmd, capture_output=True, text=True),
                  lambda r: log((getattr(r, "stdout", "") or "") + (getattr(r, "stderr", "") or "")))

    def do_status():
        try:
            with open(status_path, "r", encoding="utf-8") as fh:
                st = json.load(fh)
            log(f"Status: {'laeuft' if st.get('running') else 'gestoppt'} | "
                f"letzte Pruefung {st.get('last_check','-')} | Verstoesse {st.get('violations',0)}")
        except (OSError, json.JSONDecodeError):
            log("Noch keine Status-Datei. Nach der Installation und einem Login vorhanden.")

    def do_history():
        import monitor
        import activity as act
        hp = os.path.expanduser("~/AccountabilityMonitor/activity_history.jsonl")
        lines = act.format_history(monitor.read_history(hp), 40)
        if not lines:
            log("Noch kein Verlauf. Entsteht, sobald der Monitor laeuft und die aktive App wechselt.")
            return
        log("Aktivitaetsverlauf (juengste zuletzt):")
        for ln in lines:
            log("  " + ln)

    def do_sysaudit():
        import sysaudit
        hp = "/Library/Application Support/AccountabilityMonitor/sysaudit_history.jsonl"
        lines = sysaudit.format_changes(sysaudit.read_history(hp), 40)
        if not lines:
            log("Noch keine Systemaenderungen erfasst (oder noch nicht installiert).")
            return
        log("Systemaenderungen (juengste zuletzt):")
        for ln in lines:
            log("  " + ln)

    for text, cmd in [("1. macOS-Freigaben erteilen", do_permissions),
                      ("2. Installieren und aktivieren", do_install),
                      ("Status anzeigen", do_status),
                      ("Verlauf anzeigen", do_history),
                      ("Systemaenderungen anzeigen", do_sysaudit),
                      ("Deinstallieren", do_uninstall)]:
        ttk.Button(t3, text=text, command=cmd).pack(fill="x", pady=2)

    log("Ablauf: Tab 1 Meldeweg eintragen und testen, Tab 2 Cold-Turkey-Liste einlesen,")
    log("dann hier: erst Freigaben erteilen, dann installieren.")

    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
