#!/bin/bash
# Kommandozeilen-Installer. Duenner Mantel um install_privileged.sh (den Root-Teil),
# damit CLI und GUI identisch installieren. Wer lieber klickt: setup_gui.py bzw. den
# Starter "Accountability Setup.command" doppelklicken.
#
# Zwei Schritte:
#   1) Ohne sudo:  bash install.sh        -> legt config.json an, du bearbeitest sie
#   2) Mit sudo:   sudo bash install.sh   -> installiert Monitor, Anzeige und Watchdog
set -eu

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ---------------------------------------------------------------- Schritt 1 (ohne sudo)
if [ "$(id -u)" -ne 0 ]; then
  if [ ! -f "$SCRIPT_DIR/config.json" ]; then
    cp "$SCRIPT_DIR/config.example.json" "$SCRIPT_DIR/config.json"
    echo "config.json aus Beispiel erstellt."
    echo ">> config.json bearbeiten: ntfy.topic (unratbar) und optional die email-Sektion."
    echo ">> Optional Cold-Turkey-Liste: export.ctbbl hierher, dann"
    echo "   python3 ctbbl_import.py --inspect export.ctbbl && \\"
    echo "   python3 ctbbl_import.py export.ctbbl -o rules.generated.json"
    echo ">> Danach: sudo bash install.sh"
    exit 0
  fi
  echo "config.json vorhanden. Jetzt mit sudo installieren: sudo bash install.sh"
  exit 0
fi

# ---------------------------------------------------------------- Schritt 2 (als root)
TARGET_USER="${SUDO_USER:-$(stat -f%Su /dev/console)}"
PYTHON="$(command -v python3 || true)"

bash "$SCRIPT_DIR/install_privileged.sh" "$TARGET_USER" "$SCRIPT_DIR" "$PYTHON"

INSTALL_DIR="/Library/Application Support/AccountabilityMonitor"
echo
echo "== Einmalig: macOS-Freigaben erteilen (als Benutzer $TARGET_USER, nicht als root) =="
echo "In einem normalen Terminal ausfuehren und die Dialoge fuer System Events und den"
echo "Browser bestaetigen (Systemeinstellungen > Datenschutz & Sicherheit > Automation"
echo "und > Bedienungshilfen):"
echo "   $PYTHON \"$INSTALL_DIR/monitor.py\" --config \"$INSTALL_DIR/config.json\" --once"
echo
echo "Damit es schon beim Hochfahren ohne Login-Fenster laeuft: automatische Anmeldung"
echo "in den Systemeinstellungen aktivieren. Deinstallieren: sudo bash uninstall.sh"
