#!/usr/bin/env python3
"""Alle angeschlossenen Geräte auflisten - und die passenden Zeilen für die
Konfiguration gleich mit ausgeben.

Damit muss niemand raten, an welchem Anschluss der Empfänger hängt, welche
Kanäle die Phidget-Steuerung anbietet oder welche UID der IMU Brick hat:

    python3 scan_devices.py

Der Aufruf ändert nichts. Er öffnet die Geräte nur kurz, liest ihre Kennung und
gibt sie wieder frei.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))


def kopf(text: str) -> None:
    print(f"\n{text}\n" + "-" * len(text))


def serielle_anschluesse() -> None:
    kopf("GNSS-Empfänger (serielle Anschlüsse)")
    try:
        from serial.tools import list_ports
    except ImportError:
        print("  pyserial fehlt:  pip install pyserial")
        return
    ports = list(list_ports.comports())
    if not ports:
        print("  Nichts gefunden. Steckt der Empfänger? (Windows: Geräte-Manager)")
        return
    kandidat = None
    for port in ports:
        marke = ""
        beschreibung = f"{port.description} {port.manufacturer or ''}".lower()
        if "u-blox" in beschreibung or "ublox" in beschreibung or "gnss" in beschreibung:
            marke = "   <-- sieht nach dem F9P aus"
            kandidat = kandidat or port.device
        print(f"  {port.device:<14} {port.description}{marke}")
    if kandidat:
        print("\n  gnss:")
        print("    source: serial")
        print(f"    port: {kandidat}")
        print("    baudrate: 115200")


def phidget_kanaele() -> None:
    kopf("Phidget-Geräte")
    import time
    gefunden = []

    def angesteckt(manager, kanal):  # noqa: ANN001
        try:
            gefunden.append({
                "klasse": kanal.getChannelClassName(),
                "geraet": kanal.getDeviceName(),
                "seriennummer": kanal.getDeviceSerialNumber(),
                "kanal": kanal.getChannel(),
            })
        except Exception:  # noqa: BLE001
            pass

    # Die Bibliothek lädt den nativen Treiber des Herstellers erst beim
    # Erzeugen des ersten Objekts - der Fehler kommt also nicht beim Import.
    from agripilot.actuators import phidget_available
    nutzbar, problem = phidget_available()
    if not nutzbar:
        print(f"  {problem}")
        print("  Windows: Phidgets-Installer von phidgets.com installieren.")
        print("  Linux:   libphidget22 und libusb installieren.")
        return

    try:
        from Phidget22.Devices.Manager import Manager
        manager = Manager()
        manager.setOnAttachHandler(angesteckt)
        manager.open()
        time.sleep(2.0)
        manager.close()
    except ImportError:
        print("  Phidget-Bibliothek fehlt:  pip install phidget22")
        return
    except OSError as fehler:
        print(f"  Phidget-Treiber nicht geladen ({fehler}).")
        print("  Windows: Phidgets-Installer von phidgets.com installieren.")
        print("  Linux:   libphidget22 und libusb installieren.")
        return
    except Exception as fehler:  # noqa: BLE001
        print(f"  Fehler: {fehler}")
        return

    if not gefunden:
        print("  Nichts gefunden. Läuft der Phidget-Dienst, ist das Gerät angesteckt?")
        return

    for eintrag in sorted(gefunden, key=lambda e: (e["seriennummer"], e["klasse"])):
        print(f"  {eintrag['klasse']:<24} {eintrag['geraet']}  "
              f"Seriennummer {eintrag['seriennummer']}, Kanal {eintrag['kanal']}")

    motoren = [e for e in gefunden if e["klasse"] == "PhidgetDCMotor"]
    winkel = [e for e in gefunden if e["klasse"] == "PhidgetVoltageRatioInput"]
    geber = [e for e in gefunden if e["klasse"] == "PhidgetEncoder"]
    if motoren:
        motor = motoren[0]
        print("\n  phidget:")
        print(f"    serial_number: {motor['seriennummer']}")
        print(f"    motor_channel: {motor['kanal']}")
        if winkel:
            print("    feedback: was          # Radwinkelsensor gefunden")
            print(f"    was_channel: {winkel[0]['kanal']}")
        elif geber:
            print("    feedback: encoder      # nur Drehgeber vorhanden")
            print(f"    encoder_channel: {geber[0]['kanal']}")
        else:
            print("    feedback: yaw_rate     # keine Rückmeldung am Rad -> über den IMU")
        print("    current_limit_a: 2.0     # bewusst niedrig: von Hand übersteuerbar")
    else:
        print("\n  Keine Motorsteuerung dabei - ohne die kann nicht gelenkt werden.")


def tinkerforge_geraete() -> None:
    kopf("Tinkerforge-Geräte (IMU)")
    try:
        from tinkerforge.ip_connection import IPConnection
    except ImportError:
        print("  Bibliothek fehlt:  pip install tinkerforge")
        return
    from agripilot.imu import IMU_BRICK_V1, IMU_BRICK_V2, IMU_BRICKLET_V3

    import time
    gefunden = []
    ipcon = IPConnection()

    def aufgelistet(uid, connected_uid, position, hardware_version,
                    firmware_version, device_identifier, enumeration_type):
        if enumeration_type != IPConnection.ENUMERATION_TYPE_DISCONNECTED:
            gefunden.append((uid, device_identifier))

    try:
        ipcon.connect("localhost", 4223)
    except Exception as exc:  # noqa: BLE001
        print(f"  Kein Brick Daemon erreichbar ({exc}).")
        print("  brickd installieren und starten - er ist die Brücke zum IMU Brick.")
        return

    ipcon.register_callback(IPConnection.CALLBACK_ENUMERATE, aufgelistet)
    ipcon.enumerate()
    time.sleep(2.0)
    ipcon.disconnect()

    if not gefunden:
        print("  Brick Daemon läuft, meldet aber kein Gerät.")
        return

    namen = {IMU_BRICK_V1: "IMU Brick 1.0", IMU_BRICK_V2: "IMU Brick 2.0",
             IMU_BRICKLET_V3: "IMU Bricklet 3.0"}
    imu_uid = None
    for uid, kennung in gefunden:
        name = namen.get(kennung, f"Gerät {kennung}")
        marke = ""
        if kennung in namen:
            marke = "   <-- der Neigungssensor"
            imu_uid = imu_uid or uid
        print(f"  {uid:<8} {name}{marke}")
    if imu_uid:
        print("\n  imu:")
        print("    source: tinkerforge")
        print(f"    uid: {imu_uid}")
        print("    terrain_compensation: true")


if __name__ == "__main__":
    print("AgriPilot - angeschlossene Geräte")
    serielle_anschluesse()
    phidget_kanaele()
    tinkerforge_geraete()
    print("\nDie ausgegebenen Blöcke in die Konfigurationsdatei übernehmen,")
    print("danach den Dienst neu starten.")
    print("\nFehlt noch der Abschnitt 'corrections' für die RTK-Korrekturen -")
    print("wie er je nach Basisstation aussieht, steht in docs/HARDWARE.md.")
