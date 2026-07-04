#!/bin/bash
# Watchdog. Laeuft als root ueber einen LaunchDaemon (StartInterval), damit ein
# normales Benutzerkonto ihn nicht entladen kann. Aufgabe: sicherstellen, dass der
# Waechter-Agent in der Benutzersitzung vorhanden und geladen ist, und ihn aus einer
# geschuetzten, root-eigenen Kopie wiederherstellen, falls jemand ihn loescht oder
# entlaedt. install.sh ersetzt die __PLATZHALTER__.

set -u

TARGET_USER="__USER__"
TARGET_UID="__UID__"
INSTALL_DIR="__INSTALL_DIR__"
LABEL="com.accountability.monitor"
SRC="$INSTALL_DIR/$LABEL.plist"                                   # geschuetzte Quelle (root)
DEST="/Users/$TARGET_USER/Library/LaunchAgents/$LABEL.plist"      # aktive Kopie beim Nutzer

# 1. Agent-Datei wiederherstellen, falls sie fehlt oder veraendert wurde. cmp faengt
#    auch eine manipulierte Datei ab (jemand setzt die Argumente auf "true").
if [ ! -f "$DEST" ] || ! cmp -s "$SRC" "$DEST"; then
  install -d -o "$TARGET_USER" -m 755 "/Users/$TARGET_USER/Library/LaunchAgents"
  cp "$SRC" "$DEST"
  chown "$TARGET_USER" "$DEST"
fi

# 2. In die grafische Sitzung laden, falls nicht geladen. 'bootstrap gui/UID' ist der
#    moderne Weg; 'asuser ... load' der Rueckfall fuer aeltere macOS. Beide Fehler
#    werden geschluckt, weil der naechste Lauf es erneut versucht.
if ! launchctl print "gui/$TARGET_UID/$LABEL" >/dev/null 2>&1; then
  launchctl bootstrap "gui/$TARGET_UID" "$DEST" 2>/dev/null \
    || launchctl asuser "$TARGET_UID" launchctl load "$DEST" 2>/dev/null \
    || true
fi

# 3. Tages-Protokolle des Nutzers unveraenderlich machen. append-only (sappnd) kann nur
#    root setzen und entfernen; der als Nutzer laufende Monitor haengt weiter an, aber
#    niemand ausser einem Admin kann eine Datei loeschen oder aendern. Schon markierte
#    ueberspringen, damit der 30s-Takt nicht unnoetig arbeitet.
LOGDIR="/Users/$TARGET_USER/AccountabilityMonitor"
if [ -d "$LOGDIR" ]; then
  for f in "$LOGDIR"/*.jsonl; do
    [ -e "$f" ] || continue
    if ! ls -lO "$f" 2>/dev/null | grep -q sappnd; then
      chflags sappnd "$f" 2>/dev/null || true
    fi
  done
fi
