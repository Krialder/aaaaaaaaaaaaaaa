#!/bin/bash
# Nicht-interaktiver Root-Teil der Installation. Einzige Quelle der Wahrheit fuer
# alles, was Systemrechte braucht; wird sowohl von der GUI (per osascript-Elevation)
# als auch von install.sh aufgerufen, damit beide Wege identisch installieren.
#
# Argumente (Positionen, weil osascript sie sauber als 'quoted form' uebergibt):
#   $1 TARGET_USER  Benutzer, in dessen Sitzung der Monitor laeuft (Pflicht)
#   $2 SRC_DIR      Verzeichnis mit den .py-Dateien, Templates und config.json (Pflicht)
#   $3 PYTHON       Pfad zu python3 (optional, sonst automatisch gesucht)
#
# Kein 'read', keine Rueckfragen: die GUI bzw. install.sh fuehren durch den Ablauf.
set -eu

TARGET_USER="${1:?TARGET_USER fehlt}"
SRC_DIR="${2:?SRC_DIR fehlt}"
PYTHON="${3:-$(command -v python3 || true)}"

if [ -z "$PYTHON" ]; then
  echo "FEHLER: python3 nicht gefunden. 'xcode-select --install' ausfuehren." >&2
  exit 1
fi
if [ ! -f "$SRC_DIR/config.json" ]; then
  echo "FEHLER: $SRC_DIR/config.json fehlt." >&2
  exit 1
fi

INSTALL_DIR="/Library/Application Support/AccountabilityMonitor"
MON_LABEL="com.accountability.monitor"
BAR_LABEL="com.accountability.statusbar"
DOG_LABEL="com.accountability.watchdog"
AUD_LABEL="com.accountability.sysaudit"

TARGET_UID="$(id -u "$TARGET_USER")"
USER_HOME="/Users/$TARGET_USER"
USER_LA="$USER_HOME/Library/LaunchAgents"
USER_LOG="$USER_HOME/AccountabilityMonitor"
STATUS_JSON="$USER_LOG/status.json"

echo "Installiere fuer '$TARGET_USER' (UID $TARGET_UID), python3: $PYTHON"

# 1. Code an geschuetzten, root-eigenen Ort. Ein normales Konto kann ihn dann nicht
#    veraendern (z.B. die Regeln entschaerfen).
mkdir -p "$INSTALL_DIR"
for f in monitor.py rules.py notify.py sampler.py statusbar.py ctbbl_import.py activity.py \
         sysaudit.py hostutil.py dailylog.py; do
  cp "$SRC_DIR/$f" "$INSTALL_DIR/$f"
done
chown -R root:wheel "$INSTALL_DIR"
chmod 755 "$INSTALL_DIR"
chmod 644 "$INSTALL_DIR"/*.py

# 2. Konfiguration und Regeln: root-eigen, fuer Gruppe staff nur lesbar (der Monitor
#    laeuft als Nutzer und muss lesen), nicht schreibbar.
cp "$SRC_DIR/config.json" "$INSTALL_DIR/config.json"
chown root:staff "$INSTALL_DIR/config.json"; chmod 640 "$INSTALL_DIR/config.json"
if [ -f "$SRC_DIR/rules.generated.json" ]; then
  cp "$SRC_DIR/rules.generated.json" "$INSTALL_DIR/rules.generated.json"
  chown root:staff "$INSTALL_DIR/rules.generated.json"; chmod 640 "$INSTALL_DIR/rules.generated.json"
fi

# 3. Log- und Agent-Verzeichnisse im Home des Nutzers.
install -d -o "$TARGET_USER" -m 755 "$USER_LOG"
install -d -o "$TARGET_USER" -m 755 "$USER_LA"

# 4. Watchdog-Skript mit echten Werten fuellen.
sed -e "s#__USER__#$TARGET_USER#g" -e "s#__UID__#$TARGET_UID#g" \
    -e "s#__INSTALL_DIR__#$INSTALL_DIR#g" \
    "$SRC_DIR/watchdog.sh" > "$INSTALL_DIR/watchdog.sh"
chown root:wheel "$INSTALL_DIR/watchdog.sh"; chmod 755 "$INSTALL_DIR/watchdog.sh"

# 5. Monitor-Agent: geschuetzte Quelle in INSTALL_DIR und aktive Kopie beim Nutzer.
sed -e "s#__PYTHON__#$PYTHON#g" -e "s#__MONITOR__#$INSTALL_DIR/monitor.py#g" \
    -e "s#__CONFIG__#$INSTALL_DIR/config.json#g" \
    -e "s#__STDOUT__#$USER_LOG/monitor.out.log#g" -e "s#__STDERR__#$USER_LOG/monitor.err.log#g" \
    "$SRC_DIR/com.accountability.monitor.plist.template" > "$INSTALL_DIR/$MON_LABEL.plist"
chown root:wheel "$INSTALL_DIR/$MON_LABEL.plist"; chmod 644 "$INSTALL_DIR/$MON_LABEL.plist"
cp "$INSTALL_DIR/$MON_LABEL.plist" "$USER_LA/$MON_LABEL.plist"
chown "$TARGET_USER" "$USER_LA/$MON_LABEL.plist"

# 6. Menuleisten-Agent beim Nutzer.
sed -e "s#__PYTHON__#$PYTHON#g" -e "s#__STATUSBAR__#$INSTALL_DIR/statusbar.py#g" \
    -e "s#__STATUS_JSON__#$STATUS_JSON#g" \
    -e "s#__STDOUT__#$USER_LOG/statusbar.out.log#g" -e "s#__STDERR__#$USER_LOG/statusbar.err.log#g" \
    "$SRC_DIR/com.accountability.statusbar.plist.template" > "$USER_LA/$BAR_LABEL.plist"
chown "$TARGET_USER" "$USER_LA/$BAR_LABEL.plist"

# 7. Watchdog-Daemon systemweit.
sed -e "s#__WATCHDOG__#$INSTALL_DIR/watchdog.sh#g" \
    -e "s#__STDOUT__#/var/log/accountability-watchdog.out.log#g" \
    -e "s#__STDERR__#/var/log/accountability-watchdog.err.log#g" \
    "$SRC_DIR/com.accountability.watchdog.plist.template" > "/Library/LaunchDaemons/$DOG_LABEL.plist"
chown root:wheel "/Library/LaunchDaemons/$DOG_LABEL.plist"; chmod 644 "/Library/LaunchDaemons/$DOG_LABEL.plist"

# 7b. Konfig-Auditing-Daemon systemweit (Systemaenderungen). Bekommt das Home des
#     Zielbenutzers, damit es auch dessen Apps und LaunchAgents mit aufnimmt.
sed -e "s#__PYTHON__#$PYTHON#g" -e "s#__SYSAUDIT__#$INSTALL_DIR/sysaudit.py#g" \
    -e "s#__CONFIG__#$INSTALL_DIR/config.json#g" -e "s#__USER_HOME__#$USER_HOME#g" \
    -e "s#__STDOUT__#/var/log/accountability-sysaudit.out.log#g" \
    -e "s#__STDERR__#/var/log/accountability-sysaudit.err.log#g" \
    "$SRC_DIR/com.accountability.sysaudit.plist.template" > "/Library/LaunchDaemons/$AUD_LABEL.plist"
chown root:wheel "/Library/LaunchDaemons/$AUD_LABEL.plist"; chmod 644 "/Library/LaunchDaemons/$AUD_LABEL.plist"

# 8. Dienste laden. Reihenfolge: Agenten zuerst, dann der Watchdog, damit dieser sie
#    beim ersten Lauf bereits geladen vorfindet. Alte Instanzen vorher entladen, damit
#    ein erneuter Lauf (Update) die neue Fassung nimmt.
launchctl bootout "gui/$TARGET_UID/$MON_LABEL" 2>/dev/null || true
launchctl bootout "gui/$TARGET_UID/$BAR_LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$TARGET_UID" "$USER_LA/$MON_LABEL.plist" 2>/dev/null \
  || launchctl asuser "$TARGET_UID" launchctl load "$USER_LA/$MON_LABEL.plist" 2>/dev/null || true
launchctl bootstrap "gui/$TARGET_UID" "$USER_LA/$BAR_LABEL.plist" 2>/dev/null \
  || launchctl asuser "$TARGET_UID" launchctl load "$USER_LA/$BAR_LABEL.plist" 2>/dev/null || true

launchctl bootout "system/$DOG_LABEL" 2>/dev/null || true
launchctl bootstrap system "/Library/LaunchDaemons/$DOG_LABEL.plist" 2>/dev/null \
  || launchctl load "/Library/LaunchDaemons/$DOG_LABEL.plist" 2>/dev/null || true

launchctl bootout "system/$AUD_LABEL" 2>/dev/null || true
launchctl bootstrap system "/Library/LaunchDaemons/$AUD_LABEL.plist" 2>/dev/null \
  || launchctl load "/Library/LaunchDaemons/$AUD_LABEL.plist" 2>/dev/null || true

echo "INSTALL_OK"
