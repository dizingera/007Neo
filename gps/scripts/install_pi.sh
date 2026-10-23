#!/usr/bin/env bash
# AgriPilot auf einem Raspberry Pi einrichten.
#
#   sudo bash scripts/install_pi.sh master
#   sudo bash scripts/install_pi.sh client 192.168.10.1
#   sudo AGRIPILOT_PORT=8090 bash scripts/install_pi.sh master   # anderer Port
#
# Das Skript ist wiederholbar: ein zweiter Aufruf aktualisiert die Installation,
# ohne Felder, Spuren oder aufgezeichnete Arbeiten anzufassen. Vorher sichert es
# Programm, Konfiguration und Datenbank nach /var/backups/agripilot/.
#
# Vom PC aus ausrollen: scripts/pi_ausrollen.py (packt, kopiert, ruft dies auf).

set -euo pipefail

ROLLE="${1:-master}"
MASTER_IP="${2:-192.168.10.1}"
PORT="${AGRIPILOT_PORT:-8080}"
ZIEL=/opt/agripilot
DATEN=/var/lib/agripilot
KONFIG=/etc/agripilot/config.yaml
SICHERUNG=/var/backups/agripilot
QUELLE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ $EUID -ne 0 ]]; then
  echo "Bitte mit sudo starten." >&2
  exit 1
fi
if [[ "$ROLLE" != "master" && "$ROLLE" != "client" ]]; then
  echo "Rolle muss 'master' oder 'client' sein." >&2
  exit 1
fi

echo "== Pakete =="
# avahi: der Pi ist danach als <hostname>.local erreichbar - ein Tablet in der
# Kabine soll keine IP-Adresse hinterherjagen, die der Router neu vergibt.
# openssl: Papiere für die verschlüsselte Verbindung (scripts/make_cert.sh).
PAKETE=(python3 python3-venv python3-pip avahi-daemon openssl)
if dpkg -s "${PAKETE[@]}" >/dev/null 2>&1; then
  echo "   schon da"
else
  apt-get update -qq
  apt-get install -y -qq "${PAKETE[@]}"
fi

echo "== Sicherung vor dem Update =="
if [[ -d "$ZIEL/backend" || -f "$DATEN/agripilot.db" ]]; then
  STAND="$SICHERUNG/vor-update-$(date +%Y%m%d-%H%M%S)"
  install -d -m 700 "$STAND"
  [[ -d "$ZIEL/backend" ]] && cp -a "$ZIEL/backend" "$ZIEL/frontend" "$STAND/" 2>/dev/null || true
  [[ -f "$KONFIG" ]] && cp -a "$KONFIG" "$STAND/"
  if [[ -f "$DATEN/agripilot.db" ]]; then
    # Über die SQLite-Sicherung, nicht cp: der Dienst kann gerade schreiben (WAL).
    python3 - "$DATEN/agripilot.db" "$STAND/agripilot.db" <<'PY'
import sqlite3, sys
quelle = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True)
ziel = sqlite3.connect(sys.argv[2])
quelle.backup(ziel)
ziel.close(); quelle.close()
PY
  fi
  echo "   $STAND"
  # Die letzten zehn Stände behalten.
  ls -1d "$SICHERUNG"/vor-update-* 2>/dev/null | sort | head -n -10 | xargs -r rm -rf
else
  echo "   Erstinstallation - nichts zu sichern"
fi

echo "== Benutzer und Verzeichnisse =="
id -u agripilot >/dev/null 2>&1 || useradd --system --home "$DATEN" --shell /usr/sbin/nologin agripilot
# dialout: Zugriff auf den GPS-Empfänger am seriellen Port
usermod -a -G dialout agripilot
install -d -o agripilot -g agripilot "$DATEN"
# Setups für die Arbeiter (Update vom Master): AgriPilot-Setup-<Version>.exe
install -d -o agripilot -g agripilot "$DATEN/setup"
install -d "$ZIEL" /etc/agripilot

echo "== Programm kopieren =="
rm -rf "$ZIEL/backend" "$ZIEL/frontend"
install -d "$ZIEL/backend"
cp -r "$QUELLE/backend/agripilot" "$ZIEL/backend/"
cp "$QUELLE/backend/requirements.txt" "$ZIEL/backend/"
cp -r "$QUELLE/frontend" "$ZIEL/"
rm -rf "$ZIEL/frontend/tests" "$ZIEL/frontend/node_modules"
find "$ZIEL/backend" -name __pycache__ -type d -prune -exec rm -rf {} +
cp "$QUELLE/README.md" "$ZIEL/" 2>/dev/null || true

echo "== Python-Umgebung =="
[[ -x "$ZIEL/venv/bin/python" ]] || python3 -m venv "$ZIEL/venv"
"$ZIEL/venv/bin/pip" install --quiet --upgrade pip
"$ZIEL/venv/bin/pip" install --quiet -r "$ZIEL/backend/requirements.txt"

echo "== Konfiguration =="
if [[ -f "$KONFIG" ]]; then
  echo "   $KONFIG existiert bereits - bleibt unverändert."
else
  HOSTNAME_KURZ="$(hostname)"
  if [[ "$ROLLE" == "master" ]]; then
    cat > "$KONFIG" <<YAML
# AgriPilot - Master. Dieser Pi hält die Daten und die RTK-Verbindung.
gnss:
  source: serial          # serial | tcp | udp | simulator | aus (Master ohne Empfänger)
  port: /dev/ttyACM0      # mit scripts/check_receiver.py prüfen
  baudrate: 115200
  rtcm_out: auto          # Korrekturen zurück an den Empfänger

corrections:
  # RTK-Korrekturen. Quelle je nach Anlage:
  #   ntrip  - Caster (Dienst, oder eigene Basis mit Caster wie RTKBase)
  #   tcp    - roher RTCM3-Strom von einer eigenen Basis, ohne Anmeldung
  #   serial - Funkmodem an diesem Rechner
  #   aus    - Funkmodem steckt direkt am Empfänger, oder kein RTK
  source: aus
  host: ''
  port: 2101
  mountpoint: ''
  username: ''
  password: ''
  serial_port: ''
  baudrate: 115200
  send_gga: true

network:
  role: master
  device_id: $HOSTNAME_KURZ
  device_name: Master
  rtcm_relay_port: 2102   # gibt die Korrekturen an die anderen Traktoren weiter

steering:
  enabled: false          # NUR einschalten, wenn Lenkmotor und Not-Aus installiert sind
  output: udp
  host: 192.168.5.9
  port: 8888
  require_rtk: true

server:
  host: 0.0.0.0
  port: $PORT
  data_dir: $DATEN
  # Verschlüsselte Verbindung für ein Android-Tablet als Kabinenanzeige.
  # Erst 'sudo bash scripts/make_cert.sh' laufen lassen, dann diese zwei
  # Zeilen einkommentieren und den Dienst neu starten.
  # tls_cert: /etc/agripilot/tls/server.crt
  # tls_key: /etc/agripilot/tls/server.key
YAML
  else
    cat > "$KONFIG" <<YAML
# AgriPilot - Client. Holt Korrekturen und Felddaten vom Master.
gnss:
  source: serial
  port: /dev/ttyACM0
  baudrate: 115200
  rtcm_out: auto

network:
  role: client
  device_id: $HOSTNAME_KURZ
  device_name: $HOSTNAME_KURZ
  master_url: http://$MASTER_IP:8080
  rtcm_relay_port: 2102
  use_master_rtcm: false
  sync_interval_s: 30

steering:
  enabled: false
  output: udp
  host: 192.168.5.9
  port: 8888
  require_rtk: true

server:
  host: 0.0.0.0
  port: $PORT
  data_dir: $DATEN
YAML
  fi
  chmod 640 "$KONFIG"
  chown root:agripilot "$KONFIG"
  echo "   $KONFIG angelegt (Rolle: $ROLLE)."
fi

echo "== Dienst =="
cp "$QUELLE/scripts/agripilot.service" /etc/systemd/system/agripilot.service
systemctl daemon-reload
systemctl enable agripilot
systemctl restart agripilot
# Warten, bis er antwortet - ein Fehler beim Start soll hier auffallen, nicht erst am Traktor.
LAEUFT=0
for _ in $(seq 1 45); do
  if python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:$PORT/openapi.json', timeout=2)" 2>/dev/null; then
    LAEUFT=1
    break
  fi
  sleep 1
done
if [[ $LAEUFT == 1 ]]; then
  echo "   läuft auf Port $PORT"
else
  echo "Der Dienst antwortet nicht auf Port $PORT: journalctl -u agripilot -n 50" >&2
  exit 1
fi

echo
echo "Fertig. Anzeige öffnen unter:"
echo "   http://$(hostname).local:$PORT          (Name, überlebt einen Adresswechsel)"
echo "   http://$(hostname -I | awk '{print $1}'):$PORT"
echo "Status:   systemctl status agripilot"
echo "Protokoll: journalctl -u agripilot -f"
echo
if [[ "$ROLLE" == "master" ]]; then
  echo "Nächster Schritt: Korrekturquelle in $KONFIG eintragen"
  echo "(corrections.source), dann 'systemctl restart agripilot'."
fi
echo
echo "Android-Tablet als Kabinenanzeige: 'sudo bash scripts/make_cert.sh'."
echo "Ohne HTTPS bleibt die Anzeige eine Webseite - mit HTTPS wird sie eine"
echo "Kachel, die den Bildschirm wachhält."
