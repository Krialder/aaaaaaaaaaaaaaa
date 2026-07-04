#!/bin/bash
# Doppelklickbarer Starter fuer die Einrichtungs-GUI (macOS).
#
# Erststart: einmal im Finder rechtsklicken > Oeffnen (Gatekeeper), danach reicht
# Doppelklick. Falls der Doppelklick nichts tut, fehlt das Ausfuehrungsrecht (geht beim
# Kopieren von anderen Systemen verloren); dann einmal im Terminal:
#   chmod +x "Accountability Setup.command"
#
# Diese GUI laeuft als normaler Benutzer. Adminrechte fragt sie erst beim Installieren
# ueber den nativen Passwortdialog ab, deshalb hier bewusst KEIN sudo.

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

PY="$(command -v python3 || true)"
if [ -z "$PY" ]; then
  osascript -e 'display dialog "python3 fehlt. Bitte zuerst die Command Line Tools installieren:\n\nIm Terminal: xcode-select --install" buttons {"OK"} default button "OK" with icon caution'
  exit 1
fi

# Pruefen, ob Tkinter vorhanden ist, sonst eine verstaendliche Meldung statt Absturz.
if ! "$PY" -c "import tkinter" >/dev/null 2>&1; then
  osascript -e 'display dialog "Die grafische Oberflaeche (Tkinter) fehlt in diesem python3.\n\nEntweder python.org-Python installieren oder (mit Homebrew): brew install python-tk\n\nAlternativ die Einrichtung ueber das Terminal: bash install.sh" buttons {"OK"} default button "OK" with icon caution'
  exit 1
fi

exec "$PY" "$DIR/setup_gui.py"
