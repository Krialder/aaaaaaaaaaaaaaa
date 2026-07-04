# Accountability-Monitor (macOS)

Ein sichtbarer Wächter für den Mac, der im Sekundentakt die aktive App und die aktive Browser-URL liest, sie gegen eine Regelliste prüft, alles lokal protokolliert und bei einem Verstoß eine Meldung an den Admin schickt (über ntfy-Push und optional E-Mail). Einvernehmliches Accountability-Werkzeug: die betroffene Person sieht das Protokoll und eine Menüleisten-Anzeige und kann alles einsehen.

## Was das Werkzeug tut und was nicht

Es erfasst pro Messung nur zwei Dinge: den Namen der Vordergrund-App und, falls ein bekannter Browser vorn ist, die URL des aktiven Tabs. Beides über sichtbares AppleScript.

Abgrenzung: Es gibt keinen Tastatur-Mitschnitt, keine Aufzeichnung von Eingaben, Passwörtern oder Inhalten, keine Screenshots und nichts Verstecktes. Eine Menüleisten-Anzeige zeigt dauerhaft, dass und was läuft.

## Bestandteile

- **Monitor** (`monitor.py`): der Wächter. Läuft als LaunchAgent in der Benutzersitzung.
- **Menüleisten-Anzeige** (`statusbar.py`): zeigt Status, letzte Prüfung und Zahl der Verstöße. Reiner Betrachter, steuert den Monitor nicht.
- **Watchdog** (`watchdog.sh`): läuft als root-Daemon, hält den Monitor am Leben und stellt ihn wieder her, falls jemand ihn entlädt oder löscht. („trotz Cold Turkey", siehe unten)
- **Konfig-Auditing** (`sysaudit.py`): protokolliert als root-Daemon Änderungen an der Systemkonfiguration (installierte Programme, Admin-Konten, Sicherheitsschalter).

## Autostart, und was „trotz Cold Turkey" bedeutet

Zwei Punkte, die oft verwechselt werden:

- **Start beim Hochfahren:** Der Monitor braucht die grafische Sitzung (nur dort antworten System Events und die Browser), also startet er mit dem Login, nicht vor dem Login. Damit es schon beim Einschalten ohne Anmeldemaske losgeht, in den Systemeinstellungen die **automatische Anmeldung** aktivieren.
- **Trotz Cold Turkey und App-Blocker:** Diese Programme verwalten keine launchd-Dienste. Sie beenden Apps aus ihrer Liste und sperren Webseiten, aber sie entfernen den Wächter nicht, solange `python3` nicht selbst gesperrt ist. Cold Turkey, das das Terminal sperrt, schließt zusätzlich die Lücke zum manuellen Abschalten. Der Watchdog härtet das ab: als root-Daemon ist er ohne sudo nicht zu entladen und stellt den Monitor alle 30 Sekunden wieder her.

Grenze dieser Härtung: Manipulationssicherheit setzt voraus, dass das **Alltagskonto kein Admin** ist. Ein Admin kann mit sudo alles entfernen. Ideal also ein Standard-Benutzerkonto für den Alltag; die Einrichtung läuft einmalig mit einem Admin-Konto.

## Voraussetzungen

- macOS mit `python3` (`xcode-select --install`).
- Für den Admin die **ntfy**-App (iOS/Android, kostenlos), abonniert auf denselben Topic wie in `config.json`. Optional zusätzlich E-Mail.
- Für die Menüleisten-Anzeige PyObjC. Oft schon vorhanden; falls nicht, im Admin-Fenster einmalig `pip3 install pyobjc-framework-Cocoa`.

## Auf den Mac holen (erster Schritt)

Empfohlen ist `git clone`, weil dabei die Ausführungsrechte der Skripte erhalten bleiben und keine Gatekeeper-Quarantäne entsteht:

```bash
git clone <REPO-URL> ~/Downloads/mac-accountability-monitor
cd ~/Downloads/mac-accountability-monitor
```

Wer stattdessen die ZIP von GitHub lädt oder die Dateien von Hand kopiert: auch in Ordnung, kostet aber ein paar Handgriffe im Terminal, weil dabei die Ausführungsrechte verloren gehen und eine Quarantäne dazukommen kann. Schritt für Schritt:

1. Terminal öffnen (Programme, Dienstprogramme, Terminal).
2. In den geladenen Ordner wechseln. Am einfachsten: `cd` und ein Leerzeichen tippen, dann den Ordner aus dem Finder ins Terminal-Fenster ziehen (das fügt den Pfad ein), dann Enter. Beispiel:

   ```bash
   cd ~/Downloads/mac-accountability-monitor
   ```

3. Ausführungsrechte setzen und die Quarantäne lösen. Bei Erfolg kommt **keine** Ausgabe, das ist unter macOS normal (Stille heißt geklappt):

   ```bash
   chmod +x "Accountability Setup.command" install.sh uninstall.sh install_privileged.sh watchdog.sh
   xattr -dr com.apple.quarantine .
   ```

Prüfen mit `ls -l`: vor den fünf Dateien muss jetzt ein `x` in den Rechten stehen (etwa `-rwxr-xr-x`). Kein sudo nötig, es sind deine eigenen Dateien. Mach das im Einrichtungs-Fenster mit Admin-Rechten, also bevor Cold Turkey das Terminal sperrt.

Wohin: ein normaler Ordner, auf den das Admin-Konto zugreift (Downloads, Schreibtisch). **Nicht** in einen von Cold Turkey gesperrten Ordner, sonst kommst du im gesperrten Zustand nicht mehr an die Dateien.

Nach der Einrichtung diesen Ordner **löschen**. Bei der Einrichtung entsteht hier `config.json` mit ntfy-Topic und E-Mail-Passwort; der laufende Code hat davon eine eigene, geschützte Kopie unter `/Library/Application Support/AccountabilityMonitor`, du brauchst den Ordner danach also nicht mehr. Für Updates neu holen und erneut installieren.

## Einrichtung per GUI (empfohlen)

Der einfachste Weg für die betroffene Person: **„Accountability Setup.command" doppelklicken**. Beim allerersten Mal im Finder rechtsklicken und „Öffnen" wählen (Gatekeeper fragt einmal nach). Falls der Doppelklick nichts tut, fehlt das Ausführungsrecht (geht beim Kopieren zwischen Systemen verloren); dann einmalig im Terminal `chmod +x "Accountability Setup.command"`.

Die GUI führt durch drei Schritte:

1. **Benachrichtigung:** ntfy-Topic eintragen (Knopf „Zufall" erzeugt einen unratbaren) und optional die E-Mail-Felder. „Testmeldung senden" prüft sofort, ob der Weg ankommt.
2. **Regeln:** oben den Regelmodus für Apps und Webseiten wählen (Blockliste oder Erlaubnisliste) und für die Erlaubnisliste die erlaubten Apps und Seiten eintragen. Für die Blockliste die exportierte `.ctbbl`-Datei wählen, „Prüfen" zeigt die gefundenen Domains und Apps, „Übernehmen" schreibt die Regeldatei.
3. **Installieren und Status:** erst „macOS-Freigaben erteilen" (löst die Automation-Dialoge aus), dann „Installieren und aktivieren". Die Knöpfe „Verlauf anzeigen" und „Systemänderungen anzeigen" zeigen die beiden Protokolle.

Wichtig zum Rechte-Modell: Die GUI läuft als normaler Benutzer. Das Admin-Passwort fragt sie **nur beim Installieren** über den nativen macOS-Dialog ab, nicht die ganze Zeit. Das ist Absicht, denn die Freigaben müssen dem Benutzer gehören, nicht root. Du musst die GUI also nicht als Administrator starten, du tippst das Passwort, wenn der Dialog kommt.

Braucht Tkinter im `python3`. Fehlt es, sagt der Starter es und nennt die Abhilfe (`brew install python-tk` oder python.org-Python). Dann geht immer noch der Kommandozeilen-Weg unten.

## Einrichtung per Kommandozeile (Alternative)

1. **Konfiguration.** `bash install.sh` (ohne sudo) erzeugt `config.json`. Darin setzen:
   - `ntfy.topic` auf einen langen, unratbaren Wert (der Topic ist das einzige Geheimnis).
   - optional die `email`-Sektion (bei Gmail ein **App-Passwort**, Host `smtp.gmail.com`, Port `587`; `recipient` ist die Adresse des Admin).

2. **Cold-Turkey-Liste übernehmen.** In Cold Turkey die Blocks exportieren, die Datei als `export.ctbbl` daneben legen. Zuerst prüfen, dann übernehmen:

   ```bash
   python3 ctbbl_import.py --inspect export.ctbbl
   python3 ctbbl_import.py export.ctbbl -o rules.generated.json
   ```

   Der Zwischenschritt ist Absicht: die genaue `.ctbbl`-Struktur ist nicht offiziell dokumentiert, deshalb sammelt der Parser defensiv alles, was wie Domain oder App aussieht, und ein Mensch bestätigt es. Die App-Erkennung ist die unsicherere Seite; fehlende Apps in `config.json` unter `rules.blocked_apps` ergänzen.

3. **Installieren.** `sudo bash install.sh`. Das kopiert den Code nach `/Library/Application Support/AccountabilityMonitor` (root-eigen) und richtet Monitor-Agent, Anzeige-Agent, Watchdog- und Auditing-Daemon ein.

4. **Freigaben auslösen.** Der Installer nennt den Befehl, den du **als Benutzer** (nicht root) einmal ausführst, um die Automation-Dialoge zu bestätigen (Systemeinstellungen, Datenschutz & Sicherheit, **Automation** und **Bedienungshilfen**). Aus launchd heraus erscheinen die Dialoge oft nicht, deshalb dieser Handlauf.

## Prüfen, dass es wirkt

- **Logik ohne Mac:** `python3 monitor.py --selftest` muss `SELFTEST_RESULT failures=0` melden. Ebenso `python3 sysaudit.py --selftest`.
- **Echter Versand:** `python3 monitor.py --selftest --live --config config.json` schickt die Testmeldungen wirklich an ntfy und E-Mail. Beim Partner müssen sie ankommen.
- **Echte Beobachtung:** Auf dem Mac eine gesperrte Seite oder das Terminal öffnen, prüfen, dass eine Meldung kommt und der Verlauf eine Zeile bekommt.
- **Watchdog:** `sudo launchctl bootout gui/$(id -u)/com.accountability.monitor` (Monitor entladen) und prüfen, dass er binnen 30 Sekunden von selbst wiederkommt.

## Meldewege

- **ntfy:** App installieren, denselben `topic` abonnieren. Push mit Regel, Beobachtung und Zeit.
- **E-Mail:** je Verstoß eine Mail an `recipient`. Robust und ohne App, aber langsamer als Push.

Gleiche Verstöße werden erst nach `cooldown_seconds` (Standard 300) erneut gemeldet. Eine Startmeldung geht beim (Neu-)Start raus, damit der Partner ein Abschalten und Wiederkommen bemerkt, aber höchstens alle zehn Minuten, damit ein Absturz-Neustart-Loop nicht spammt; der Freigabe-Lauf (`--once`) meldet gar nicht. `heartbeat_seconds` > 0 schickt zusätzlich in festem Abstand eine Lebendmeldung.

## Regelmodus: Blockliste oder Erlaubnisliste

Für Apps und Webseiten getrennt wählbar (in der GUI unter Tab 2, in `config.json` als `rules.app_mode` und `rules.web_mode`):

- **Blockliste** (`blocklist`): Meldung nur bei ausdrücklich Verbotenem. Die Sperrlisten kommen aus dem Cold-Turkey-Import (`blocked_domains`, `blocked_apps`), Ausnahmen stehen in `allowed_domains`.
- **Erlaubnisliste** (`allowlist`): Meldung bei allem, was nicht ausdrücklich erlaubt ist. Die erlaubten Werte stehen in `allowed_apps` und `allowed_domains`.

Weil beide Seiten unabhängig sind, geht auch die Mischung: nur erlaubte Webseiten (`web_mode` allowlist), Apps aber nur sperren (`app_mode` blocklist).

Ehrlicher Hinweis zur App-Erlaubnisliste: Auf einem Mac kommen ständig System-Apps in den Vordergrund (Finder, Dock, Anmeldefenster, Systemdialoge). Damit die nicht dauernd als „nicht erlaubt" melden, gilt eine eingebaute Grundmenge dieser System-Apps und der bekannten Browser immer als erlaubt (`DEFAULT_BASELINE_APPS` in `rules.py`), erweiterbar über `rules.baseline_allow_apps`. Deine `allowed_apps` kommen oben drauf. Trotzdem ist die Erlaubnisliste die strengere und redseligere Wahl; sie lohnt, wenn nur wenige Programme überhaupt zulässig sein sollen.

Interne Browser-Seiten (`chrome://newtab/`, `about:blank`, `file://...`) und hostlose Ziele ohne Punkt (localhost) gelten nicht als Webseite und lösen im Erlaubnislisten-Modus keine Meldung aus.

Die Immer-melden-Apps (`always_notify_apps`, etwa Terminal) überschreiben beide Modi und melden in jedem Fall.

Änderungen an `config.json` oder der Regeldatei übernimmt der laufende Monitor automatisch (Hot-Reload beim nächsten Takt), ohne Neustart.

## Aktivitätsverlauf

Der Monitor führt einen Verlauf wie der Windows-Aktivitätsverlauf: eine Zeile nur, wenn sich etwas **ändert** (andere App oder andere Seite), mit von, auf, seit wann, wie lange und ob gesperrt. Das ist verlustfrei und lesbar, statt alle drei Sekunden dieselbe Zeile zu schreiben.

Eine Datei pro Tag: gleicher Tag hängt an `activity_history-JJJJ-MM-TT.jsonl` an, ein neuer Tag beginnt eine neue Datei. Das begrenzt die Dateigröße und macht das Löschen einzelner Tage nachvollziehbar. Jede Tagesdatei ist **append-only** (macOS-Flag `sappnd`, gesetzt vom root-Watchdog): der Monitor hängt weiter an, aber niemand außer einem Admin kann eine Datei löschen oder rückwirkend ändern. `--show-history` liest über alle Tage zusammen.

Lesbar anzeigen mit `python3 monitor.py --show-history --config config.json`, in der GUI über „Verlauf anzeigen". Beispielausgabe:

```
2026-07-04T14:03:00Z  (9m 0s)   Google Chrome  https://mail.google.com/
2026-07-04T14:12:00Z  (2m 30s)  Google Chrome  https://www.youtube.com/  [GESPERRT]
2026-07-04T14:14:30Z  (15s)     Terminal  [GESPERRT]
```

Die gerade offene App erscheint erst, wenn sie endet (dann steht ihre Dauer fest). Wer zusätzlich die rohe Messung im Sekundentakt will (deutlich größer, selten nötig), setzt in `config.json` `"log_raw_samples": true`.

## Systemänderungen (Konfig-Auditing)

Getrennt vom Nutzungsverlauf protokolliert `sysaudit.py` Änderungen an der **Systemkonfiguration**. Alle fünf Minuten nimmt ein root-Daemon eine Momentaufnahme ausgewählter Fakten und vergleicht sie mit der vorigen. Abgedeckt: installierte Programme (`/Applications` und `~/Applications`), Pakete (`pkgutil`), Mitglieder der Admin-Gruppe, LaunchAgents (System und Nutzer) und LaunchDaemons, Einträge in `/etc/hosts`, Konfigurationsprofile (best effort, der genaue `profiles`-Aufruf schwankt je macOS-Version), Sicherheitsschalter (SIP, Gatekeeper, Firewall) und die macOS-Version. Jeder Unterschied wird als Änderung mit von und auf festgehalten und optional (`sysaudit_notify`) an dieselben Meldewege geschickt. Auch dieser Verlauf rotiert täglich und ist append-only.

Der erste Lauf legt nur eine Basisaufnahme an, damit nicht die gesamte vorhandene Ausstattung fälschlich als lauter Neuzugänge erscheint. Danach zählt nur, was sich gegenüber der Basis ändert.

Lesbar anzeigen mit `python3 sysaudit.py --show --config config.json`, in der GUI über „Systemänderungen anzeigen". Beispielausgabe:

```
2026-07-04T18:00:00Z  [apps] + Steam.app
2026-07-04T18:05:00Z  [admin_users] + eve
2026-07-04T18:10:00Z  [security] SIP: enabled -> disabled
```

So sieht der Partner, wenn ein neues Programm auftaucht, ein Admin-Konto dazukommt oder ein Sicherheitsschalter umgelegt wird. Die Datei liegt root-eigen unter `/Library/Application Support/AccountabilityMonitor/sysaudit_history.jsonl` und ist lesbar, aber nicht durch das Alltagskonto änderbar.

## Geheimnisse und ihr Schutz

Die Meldewege brauchen Geheimnisse: den ntfy-Topic (und gegebenenfalls Token) und das E-Mail-Passwort. Diese stehen in `config.json`, die der Monitor lesen muss. Weil der Monitor als der überwachte Benutzer läuft, kann dieser Benutzer die Datei ebenfalls lesen. Das ist keine Nachlässigkeit, sondern prinzipiell so: ein Prozess kann seine Sende-Geheimnisse nicht vor genau dem Konto verbergen, unter dem er läuft.

Praktische Härtung, die den Schaden begrenzt:

- ntfy: einen Access-Token mit **nur Publish-Recht** für den Topic anlegen, statt einen mit Lese-Recht. Dann kann ein neugieriger Nutzer den Topic zwar sehen, aber die Meldungen nicht mitlesen, höchstens selbst welche senden.
- E-Mail: ein **eigenes Absender-Konto** nur für diesen Zweck, mit App-Passwort. Wird es bekannt, ist nur dieses Wegwerf-Konto betroffen, nicht das Hauptpostfach.
- Das Alltagskonto sollte **kein Admin** sein (siehe Autostart). Ein Admin kommt ohnehin an alles.

Vollständig verbergen ließe sich das nur mit einem privilegierten Sende-Daemon: root liest die Geheimnisse, der Monitor reicht nur Ereignisse über einen Spool weiter. Das ist bewusst nicht eingebaut, weil es viel zusätzliche, hier nicht auf echter Hardware getestete Fläche wäre. Als Ausbau ist es möglich.

## Härtung gegen Aushebeln

Ein Nutzer ohne Admin-Rechte kann das Mitschneiden nicht vollständig verhindern (siehe unten), aber die stillen Wege lassen sich in laute verwandeln:

- **Offline-Spool:** Schlägt ein Versand fehl (etwa weil das WLAN aus ist), landet die Meldung in `spool.pending.json` und wird beim nächsten erfolgreichen Durchlauf erneut gesendet. Ein kurzes Netz-Aus verschluckt also keine Meldung mehr, es verzögert sie nur. Der Spool endet bewusst auf `.json`, damit ihn der append-only-Schutz nicht einfriert.
- **Blind-Erkennung:** Sieht der Monitor wiederholt nichts, meldet er das aktiv. Zwei Fälle: keine Vordergrund-App lesbar (die System-Events-Freigabe wurde entzogen, der Monitor ist blind) oder ein bekannter Browser ist vorn, aber die URL ist nicht lesbar. So wird aus dem stillen Blindmachen ein Signal beim Partner. Meldet je Episode einmal, plus die Erholung.
- **Mehr Browser:** Neben Safari, Chrome, Brave, Edge, Vivaldi, Opera, Arc werden Firefox und Verwandte (Tor Browser, LibreWolf, Waterfox, Mullvad, Orion, Min, Zen, DuckDuckGo) als Browser erkannt. Ihre URL lässt sich per AppleScript nicht lesen; deshalb lösen sie die Blind-Erkennung aus, statt unbemerkt zu bleiben. Das Ausweichen auf Firefox wird so sichtbar.

Was das **nicht** leistet, ehrlich: Das Ablesen läuft prinzipbedingt in der Sitzung des überwachten Nutzers mit dessen Freigaben, also hat dieser Nutzer technische Kontrolle darüber. Er kann den Prozess in kurzen Fenstern beenden (der root-Watchdog holt ihn binnen 30 Sekunden zurück), gefälschte Zeilen ans append-only-Log anhängen (löschen kann er nichts), oder ein anderes Gerät nutzen, das dieses Werkzeug nie sieht. Vollständige Manipulationssicherheit gegen einen kundigen Nutzer braucht ein betreutes Gerät (MDM, ein nicht entfernbares Configuration Profile) plus ein Nicht-Admin-Konto. Das ist die Grenze, an der Software allein nicht mehr reicht.

## Grenzen, ehrlich benannt

- Die AppleScript-Sampler, der launchd-Autostart, der Watchdog, das Konfig-Auditing samt Sammlern, der append-only-Schutz (`chflags`), die Menüleisten-Anzeige und die GUI samt Passwort-Elevation sind **nicht auf echter Mac-Hardware getestet** (Entwicklung auf Windows). Geprüft ist die Logik per Selbsttest (Monitor 23 Fälle inklusive Blind-Erkennung und Offline-Spool, GUI-Logik und Konfig-Auditing separat, alle `failures=0`) und der Aufbau des GUI-Fensters. Alles Mac-Spezifische mit den Schritten oben auf dem Zielgerät verifizieren, besonders die Passwort-Elevation und die TCC-Freigaben.
- **Arc** ist enthalten, aber sein AppleScript-Verhalten ist uneinheitlich; im Zweifel den echten Tab-URL-Abruf auf dem Gerät prüfen. Ebenso unterstützt: Safari, Chrome, Brave, Edge, Vivaldi, Opera. Andere Browser meldet der Monitor nur als App, ohne URL.
- Der Monitor misst im Takt. Eine Seite, die kürzer offen ist als `poll_interval_seconds`, kann durchrutschen. Kleineres Intervall verringert das, kostet mehr Last.
- Manipulationssicherheit hängt daran, dass das Alltagskonto kein Admin ist (siehe oben).
- `config.json` enthält Token und gegebenenfalls E-Mail-Passwort und ist für den überwachten Nutzer lesbar. Das ist prinzipbedingt, siehe „Geheimnisse und ihr Schutz".
- Der append-only-Schutz greift, sobald der root-Watchdog die Tagesdatei markiert hat (bis zu 30 Sekunden nach dem ersten Schreiben). In diesem kurzen Fenster ist eine frische Datei noch löschbar. Zudem schützt das Flag die Datei, nicht das Verzeichnis: das Umbenennen des ganzen Ordners bleibt möglich (die Daten gehen dabei nicht verloren, liegen nur woanders).
- Die Konfigurationsprofile im Auditing sind best effort, weil der `profiles`-Aufruf je macOS-Version unterschiedlich ausfällt; im Zweifel auf dem Gerät prüfen.
- Wir lesen Cold Turkeys Liste nur über die offizielle Export-Funktion und spiegeln sie. Wir umgehen Cold Turkey nicht.

## Deinstallieren

```bash
sudo bash uninstall.sh
```

Stoppt und entfernt Monitor, Anzeige, Watchdog und Auditing. Die Tages-Protokolle bleiben bewusst liegen und sind append-only. Zum Löschen als Admin erst das Flag lösen: `sudo chflags -R nosappnd ~/AccountabilityMonitor && rm -rf ~/AccountabilityMonitor`.

## Dateien

| Datei | Zweck |
|-------|-------|
| `Accountability Setup.command` | Doppelklickbarer Starter für die Einrichtungs-GUI |
| `setup_gui.py` | Einrichtungs-GUI (Tkinter); `--selftest` prüft die reine Logik |
| `monitor.py` | Hauptschleife: messen, prüfen, loggen, melden; `--selftest`, `--show-history` |
| `rules.py` | Regel-Engine (Domain- und App-Treffer, Ausnahmen, Modi) |
| `hostutil.py` | gemeinsame Host-Normalisierung für Regeln und Import |
| `activity.py` | Aktivitätsverlauf: Wechsel mit von/auf/Dauer, lesbare Ausgabe |
| `dailylog.py` | Tages-Rotation der Protokolle und append-only-Schutz |
| `sysaudit.py` | Konfig-Auditing: Systemänderungen mit von/auf; `--once`, `--show`, `--selftest` |
| `notify.py` | Meldewege ntfy und E-Mail über die Standardbibliothek |
| `sampler.py` | liest aktive App und Browser-URL per osascript; FakeSampler für Tests |
| `statusbar.py` | Menüleisten-Anzeige (PyObjC), reiner Statusbetrachter |
| `watchdog.sh` | root-Watchdog, hält den Monitor am Leben |
| `ctbbl_import.py` | wandelt einen Cold-Turkey-Export in `rules.generated.json` |
| `config.example.json` | Vorlage für `config.json` |
| `com.accountability.*.plist.template` | Vorlagen für Monitor-, Anzeige-, Watchdog- und Auditing-Dienst |
| `install_privileged.sh` | nicht-interaktiver Root-Teil; von GUI und install.sh genutzt |
| `install.sh` / `uninstall.sh` | Einrichtung und Entfernung über die Kommandozeile |
