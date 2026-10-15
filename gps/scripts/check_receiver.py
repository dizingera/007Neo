#!/usr/bin/env python3
"""Empfänger prüfen, bevor der Rest eingerichtet wird.

Zeigt die vorhandenen seriellen Anschlüsse und liest von einem davon mit, damit
bei der Montage sofort klar ist: kommt etwas an, wie gut ist der Fix, und
kommen die RTK-Korrekturen an?

    python3 check_receiver.py                 # Anschlüsse auflisten
    python3 check_receiver.py /dev/ttyACM0    # 20 Sekunden mitlesen
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from agripilot.nmea import NmeaParser  # noqa: E402


def list_ports() -> None:
    try:
        from serial.tools import list_ports
    except ImportError:
        print("pyserial fehlt:  pip install pyserial")
        return
    ports = list(list_ports.comports())
    if not ports:
        print("Keine seriellen Anschlüsse gefunden.")
        print("Ist der Empfänger eingesteckt? Prüfen mit:  lsusb  und  dmesg | tail")
        return
    print(f"{'Anschluss':<20} {'Beschreibung'}")
    for port in ports:
        print(f"{port.device:<20} {port.description}")
    print("\nMitlesen:  python3 check_receiver.py " + ports[0].device)


def watch(port: str, baudrate: int = 115200, seconds: int = 20) -> None:
    import serial
    parser = NmeaParser()
    print(f"Lese {port} mit {baudrate} Baud, {seconds} Sekunden ...\n")
    counts: dict[str, int] = {}
    last_report = 0.0
    with serial.Serial(port, baudrate, timeout=1) as link:
        end = time.time() + seconds
        while time.time() < end:
            raw = link.readline()
            if not raw:
                continue
            line = raw.decode("ascii", errors="ignore").strip()
            if line.startswith("$") and len(line) > 6:
                counts[line[3:6]] = counts.get(line[3:6], 0) + 1
            parser.feed(line)
            fix = parser.fix
            if time.time() - last_report >= 1.0 and fix.lat is not None:
                last_report = time.time()
                accuracy = f"{fix.accuracy_m * 100:.0f} cm" if fix.accuracy_m else "?"
                age = f", Korrekturen {fix.age_of_corrections:.0f} s alt" \
                    if fix.age_of_corrections else ""
                print(f"  {fix.lat:.7f} {fix.lon:.7f}  {fix.fix_label:<10} "
                      f"{fix.satellites:>2} Sat  ±{accuracy}{age}")
    print("\nEmpfangene Satztypen:", ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    if not counts:
        print("Nichts empfangen. Andere Baudrate probieren: 9600, 38400, 115200.")
    elif "GGA" not in counts:
        print("Achtung: keine GGA-Sätze. Im Empfänger GGA aktivieren –")
        print("ohne GGA gibt es keine Fix-Qualität und keine Höhe.")
    if parser.fix.fix_quality in (0, 1):
        print("Hinweis: kein RTK. Für zentimetergenaue Spurführung fehlen die Korrekturdaten.")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        list_ports()
    else:
        watch(sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 115200)
