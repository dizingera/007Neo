#!/usr/bin/env bash
# Zertifikate für die Kabinenanzeige erzeugen.
#
#   sudo bash scripts/make_cert.sh
#
# Warum überhaupt: Ein Android-Tablet als Kabinenanzeige bekommt drei Dinge nur
# über HTTPS - die Kachel auf dem Startbildschirm (echte Installation statt
# Verknüpfung), den Zwischenspeicher für die Oberfläche, und das Wachhalten des
# Bildschirms. Über http://192.168.x.x lässt der Browser all das bewusst nicht
# zu. Auf einem Traktor im Feld gibt es kein Let's Encrypt, also eigene Papiere.
#
# Warum eine eigene CA und nicht nur ein selbst signiertes Serverzertifikat:
# Android vertraut einem einzelnen selbst signierten Zertifikat auch dann nicht,
# wenn man es installiert - vertraut wird nur einer Stelle, die ausstellt.
# Deshalb hier zwei Papiere: eine kleine eigene Ausgabestelle (die einmal aufs
# Tablet wandert) und ein davon signiertes Serverzertifikat (das auf dem Pi
# bleibt).
#
# Die Namen im Zertifikat müssen zu dem passen, was im Tablet in der Adresszeile
# steht. Eingetragen werden deshalb Hostname, <Hostname>.local und alle aktuellen
# IPv4-Adressen. Bekommt der Pi später eine andere Adresse, dieses Skript erneut
# laufen lassen.

set -euo pipefail

ORDNER=/etc/agripilot/tls
TAGE=3650
NAME="$(hostname)"

if [[ $EUID -ne 0 ]]; then
  echo "Bitte mit sudo starten." >&2
  exit 1
fi
command -v openssl >/dev/null || { echo "openssl fehlt: apt-get install openssl" >&2; exit 1; }

install -d -m 750 "$ORDNER"

# Alle IPv4-Adressen dieses Pi einsammeln, damit auch der direkte Aufruf über
# die Adresse ohne Warnung durchgeht.
ADRESSEN=()
while read -r ip; do
  [[ -n "$ip" ]] && ADRESSEN+=("$ip")
done < <(hostname -I | tr ' ' '\n')

SAN="DNS:${NAME},DNS:${NAME}.local,DNS:localhost,IP:127.0.0.1"
for ip in "${ADRESSEN[@]}"; do SAN="${SAN},IP:${ip}"; done

echo "== Ausgabestelle (einmalig) =="
if [[ -f "$ORDNER/ca.crt" && -f "$ORDNER/ca.key" ]]; then
  echo "   $ORDNER/ca.crt existiert - bleibt bestehen."
  echo "   (Sonst müssten alle Tablets neu eingerichtet werden.)"
else
  openssl req -x509 -newkey rsa:2048 -nodes -days "$TAGE" \
    -keyout "$ORDNER/ca.key" -out "$ORDNER/ca.crt" \
    -subj "/CN=AgriPilot Hof-CA/O=AgriPilot" \
    -addext "basicConstraints=critical,CA:TRUE,pathlen:0" \
    -addext "keyUsage=critical,keyCertSign,cRLSign" 2>/dev/null
  echo "   neu angelegt: $ORDNER/ca.crt"
fi

echo "== Serverzertifikat =="
openssl req -newkey rsa:2048 -nodes \
  -keyout "$ORDNER/server.key" -out "$ORDNER/server.csr" \
  -subj "/CN=${NAME}/O=AgriPilot" 2>/dev/null

openssl x509 -req -in "$ORDNER/server.csr" -days "$TAGE" \
  -CA "$ORDNER/ca.crt" -CAkey "$ORDNER/ca.key" -CAcreateserial \
  -out "$ORDNER/server.crt" \
  -extfile <(printf "subjectAltName=%s\nextendedKeyUsage=serverAuth\n" "$SAN") 2>/dev/null
rm -f "$ORDNER/server.csr"

chown -R root:agripilot "$ORDNER" 2>/dev/null || true
chmod 640 "$ORDNER"/*.key
chmod 644 "$ORDNER"/*.crt

echo "   Namen im Zertifikat: $SAN"
echo
echo "In /etc/agripilot/config.yaml eintragen:"
echo
echo "server:"
echo "  tls_cert: $ORDNER/server.crt"
echo "  tls_key: $ORDNER/server.key"
echo
echo "Danach: systemctl restart agripilot"
echo
echo "Auf dem Android-Tablet EINMAL einrichten:"
echo "  1. http://${NAME}.local:8080/ca.crt aufrufen und die Datei speichern."
echo "  2. Einstellungen > Sicherheit > Verschlüsselung und Anmeldedaten >"
echo "     Zertifikat installieren > CA-Zertifikat > die Datei wählen."
echo "  3. Danach https://${NAME}.local:8080 aufrufen - ohne Warnung."
echo "  4. Chrome-Menü > 'App installieren' legt die Kachel an."
