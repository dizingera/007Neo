#!/usr/bin/env bash
# AgriPilot auf einem Raspberry Pi einrichten.
#
#   sudo bash scripts/install_pi.sh master
#   sudo bash scripts/install_pi.sh client 192.168.10.1
#
# Das Skript ist wiederholbar: ein zweiter Aufruf aktualisiert die Installation,
# ohne Felder, Spuren oder aufgezeichnete Arbeiten anzufassen.

set -euo pipefail

ROLLE="${1:-master}"
MASTER_IP="${2:-192.168.10.1}"
ZIEL=/opt/agripilot
DATEN=/var/lib/agripilot
KONFIG=/etc/agripilot/config.yaml
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
apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-pip git

echo "== Benutzer und Verzeichnisse =="
id -u agripilot >/dev/null 2>&1 || useradd --system --home "$DATEN" --shell /usr/sbin/nologin agripilot
# dialout: Zugriff auf den GPS-Empfänger am seriellen Port
usermod -a -G dialout agripilot
install -d -o agripilot -g agripilot "$DATEN"
install -d "$ZIEL" /etc/agripilot

echo "== Programm kopieren =="
rm -rf "$ZIEL/backend" "$ZIEL/frontend"
cp -r "$QUELLE/backend" "$QUELLE/frontend" "$ZIEL/"
cp "$QUELLE/README.md" "$ZIEL/" 2>/dev/null || true

echo "== Python-Umgebung =="
python3 -m venv "$ZIEL/venv"
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
  source: serial          # serial | tcp | udp | simulator
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
  port: 8080
  data_dir: $DATEN
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
  use_master_rtcm: true
  sync_interval_s: 30

steering:
  enabled: false
  output: udp
  host: 192.168.5.9
  port: 8888
  require_rtk: true

server:
  host: 0.0.0.0
  port: 8080
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
sleep 2

echo
echo "Fertig. Anzeige öffnen unter:  http://$(hostname -I | awk '{print $1}'):8080"
echo "Status:   systemctl status agripilot"
echo "Protokoll: journalctl -u agripilot -f"
echo
if [[ "$ROLLE" == "master" ]]; then
  echo "Nächster Schritt: Korrekturquelle in $KONFIG eintragen"
  echo "(corrections.source), dann 'systemctl restart agripilot'."
fi
