#!/bin/bash
# Entfernt Monitor, Menuleisten-Anzeige und Watchdog. Als root ausfuehren:
#   sudo bash uninstall.sh
# Das lokale Aktivitaets-Log bleibt bewusst liegen, damit nichts unbemerkt verschwindet.

set -u

if [ "$(id -u)" -ne 0 ]; then
  echo "Bitte mit sudo ausfuehren: sudo bash uninstall.sh"
  exit 1
fi

# Benutzer: erstes Argument (von der GUI-Elevation, wo SUDO_USER fehlt), sonst
# SUDO_USER (klassisches sudo), sonst der an der Konsole angemeldete Benutzer.
TARGET_USER="${1:-${SUDO_USER:-$(stat -f%Su /dev/console)}}"
TARGET_UID="$(id -u "$TARGET_USER")"
USER_LA="/Users/$TARGET_USER/Library/LaunchAgents"
INSTALL_DIR="/Library/Application Support/AccountabilityMonitor"

# 1. Watchdog und Auditing zuerst stoppen, sonst setzt der Watchdog den Monitor
#    waehrend der Deinstallation neu auf.
for daemon in com.accountability.watchdog com.accountability.sysaudit; do
  launchctl bootout "system/$daemon" 2>/dev/null \
    || launchctl unload "/Library/LaunchDaemons/$daemon.plist" 2>/dev/null || true
  rm -f "/Library/LaunchDaemons/$daemon.plist"
done

# 2. Geschuetzte Quellen sofort entfernen, damit ein evtl. noch laufender Watchdog-Tick
#    den Monitor nicht wiederherstellen kann (Rennen vermeiden).
rm -f "$INSTALL_DIR/watchdog.sh" "$INSTALL_DIR/com.accountability.monitor.plist" 2>/dev/null || true

# 3. Nutzer-Agenten stoppen und entfernen.
for label in com.accountability.monitor com.accountability.statusbar; do
  launchctl bootout "gui/$TARGET_UID/$label" 2>/dev/null \
    || launchctl asuser "$TARGET_UID" launchctl unload "$USER_LA/$label.plist" 2>/dev/null || true
  rm -f "$USER_LA/$label.plist"
done

# 4. append-only-Flags im Installationsort loesen (sonst schlaegt rm fehl), dann weg.
chflags -R nosappnd,noschg "$INSTALL_DIR" 2>/dev/null || true
rm -rf "$INSTALL_DIR"

echo "Monitor, Anzeige, Watchdog und Auditing entfernt."
echo "Die Tages-Protokolle unter /Users/$TARGET_USER/AccountabilityMonitor sind append-only"
echo "und bleiben bewusst erhalten. Zum Loeschen als Admin:"
echo "   sudo chflags -R nosappnd \"/Users/$TARGET_USER/AccountabilityMonitor\" && rm -rf \"/Users/$TARGET_USER/AccountabilityMonitor\""
