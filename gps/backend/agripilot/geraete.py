"""Angeschlossene Geräte finden: Empfänger, Neigungssensor, Lenksteuerung.

Dasselbe, was scripts/scan_devices.py auf der Konsole tut - nur als Daten
statt als Text, damit die Oberfläche es zeigen und mit einem Druck übernehmen
kann. Der Fahrer steckt den F9P an, drückt „Geräte suchen“, sieht „COM5,
u-blox GNSS receiver" und drückt „Übernehmen“. Ohne Konfigurationsdatei,
ohne Neustart.

Die Suche ändert nichts. Sie öffnet die Geräte kurz, liest die Kennung und
gibt sie wieder frei. Was sie vorschlägt, sind Einstellungsänderungen im
Format von settings.uebernehmen - dieselbe Prüfung, derselbe Weg wie beim
Tippen von Hand.

Alles hier blockiert (serielle Ports abfragen, zwei Sekunden auf Phidgets
warten) und gehört deshalb in einen Thread, nicht in die Ereignisschleife.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable

WARTEZEIT_S = 2.0


@dataclass
class Fund:
    art: str            # "gnss" | "phidget" | "imu"
    kennung: str        # COM5, Seriennummer, UID
    name: str
    hinweis: str = ""   # "sieht nach dem F9P aus"
    details: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"art": self.art, "kennung": self.kennung, "name": self.name,
                "hinweis": self.hinweis, "details": self.details}


@dataclass
class Suchergebnis:
    funde: list[Fund] = field(default_factory=list)
    vorschlag: dict[str, Any] = field(default_factory=dict)   # Einstellungsänderungen
    probleme: list[str] = field(default_factory=list)          # was nicht ging, und warum
    dauer_s: float = 0.0

    def to_dict(self) -> dict:
        return {"funde": [f.to_dict() for f in self.funde], "vorschlag": self.vorschlag,
                "probleme": self.probleme, "dauer_s": round(self.dauer_s, 1)}


# ------------------------------------------------------------ Empfänger


def serielle_anschluesse() -> tuple[list[Fund], dict, list[str]]:
    try:
        from serial.tools import list_ports
    except ImportError:
        return [], {}, ["pyserial fehlt - ohne das Paket lässt sich kein Anschluss abfragen"]
    funde, vorschlag = [], {}
    for port in list_ports.comports():
        beschreibung = f"{port.description or ''} {port.manufacturer or ''}".lower()
        gnss = any(w in beschreibung for w in ("u-blox", "ublox", "gnss", "gps"))
        fund = Fund("gnss", port.device, port.description or port.device,
                    "sieht nach dem F9P aus" if gnss else "",
                    {"hersteller": port.manufacturer or "", "hwid": port.hwid or ""})
        funde.append(fund)
        if gnss and "gnss.port" not in vorschlag:
            vorschlag.update({"gnss.source": "serial", "gnss.port": port.device,
                              "gnss.baudrate": 115200})
    return funde, vorschlag, []


# -------------------------------------------------------------- Phidget


def phidget_kanaele() -> tuple[list[Fund], dict, list[str]]:
    from .actuators import phidget_available
    nutzbar, problem = phidget_available()
    if not nutzbar:
        return [], {}, [f"Phidget: {problem}"]
    gefunden: list[dict] = []

    def angesteckt(manager, kanal):  # noqa: ANN001
        try:
            gefunden.append({
                "klasse": kanal.getChannelClassName(),
                "geraet": kanal.getDeviceName(),
                "seriennummer": kanal.getDeviceSerialNumber(),
                "kanal": kanal.getChannel(),
            })
        except Exception:  # noqa: BLE001 - ein Kanal ohne Auskunft ist kein Fund
            pass

    try:
        from Phidget22.Devices.Manager import Manager
        manager = Manager()
        manager.setOnAttachHandler(angesteckt)
        manager.open()
        time.sleep(WARTEZEIT_S)
        manager.close()
    except ImportError:
        return [], {}, ["Phidget-Bibliothek fehlt (pip install phidget22)"]
    except Exception as exc:  # noqa: BLE001
        return [], {}, [f"Phidget-Treiber: {exc}"]

    funde = [Fund("phidget", f"{e['seriennummer']}/{e['kanal']}",
                  f"{e['geraet']} - {e['klasse']}", "",
                  {"seriennummer": e["seriennummer"], "kanal": e["kanal"], "klasse": e["klasse"]})
             for e in sorted(gefunden, key=lambda e: (e["seriennummer"], e["klasse"], e["kanal"]))]
    motoren = [e for e in gefunden if e["klasse"] == "PhidgetDCMotor"]
    winkel = [e for e in gefunden if e["klasse"] == "PhidgetVoltageRatioInput"]
    geber = [e for e in gefunden if e["klasse"] == "PhidgetEncoder"]
    vorschlag: dict[str, Any] = {}
    if motoren:
        motor = motoren[0]
        vorschlag.update({"steering.output": "phidget",
                          "phidget.serial_number": motor["seriennummer"],
                          "phidget.motor_channel": motor["kanal"]})
        for fund in funde:
            if fund.details.get("klasse") == "PhidgetDCMotor":
                fund.hinweis = "die Lenkmotorsteuerung"
                break
        if winkel:
            vorschlag.update({"phidget.feedback": "was", "phidget.was_channel": winkel[0]["kanal"]})
        elif geber:
            vorschlag.update({"phidget.feedback": "encoder",
                              "phidget.encoder_channel": geber[0]["kanal"]})
        else:
            vorschlag["phidget.feedback"] = "yaw_rate"
    return funde, vorschlag, []


# ---------------------------------------------------------- Tinkerforge


def tinkerforge_geraete(host: str = "localhost", port: int = 4223
                        ) -> tuple[list[Fund], dict, list[str]]:
    try:
        from tinkerforge.ip_connection import IPConnection
    except ImportError:
        return [], {}, ["Tinkerforge-Bibliothek fehlt (pip install tinkerforge)"]
    from .imu import IMU_BRICK_V1, IMU_BRICK_V2, IMU_BRICKLET_V3
    namen = {IMU_BRICK_V1: "IMU Brick 1.0", IMU_BRICK_V2: "IMU Brick 2.0",
             IMU_BRICKLET_V3: "IMU Bricklet 3.0"}
    gefunden: list[tuple[str, int]] = []
    ipcon = IPConnection()

    def aufgelistet(uid, connected_uid, position, hardware_version,  # noqa: ANN001
                    firmware_version, device_identifier, enumeration_type):
        if enumeration_type != IPConnection.ENUMERATION_TYPE_DISCONNECTED:
            gefunden.append((uid, device_identifier))

    try:
        ipcon.connect(host, port)
    except Exception as exc:  # noqa: BLE001
        return [], {}, [f"Kein Brick Daemon unter {host}:{port} ({exc}) - "
                        "brickd ist die Brücke zum IMU Brick"]
    try:
        ipcon.register_callback(IPConnection.CALLBACK_ENUMERATE, aufgelistet)
        ipcon.enumerate()
        time.sleep(WARTEZEIT_S)
    finally:
        ipcon.disconnect()
    funde, vorschlag = [], {}
    for uid, kennung in gefunden:
        imu = kennung in namen
        funde.append(Fund("imu", uid, namen.get(kennung, f"Tinkerforge-Gerät {kennung}"),
                          "der Neigungssensor" if imu else "", {"kennung": kennung}))
        if imu and "imu.uid" not in vorschlag:
            vorschlag.update({"imu.source": "tinkerforge", "imu.uid": uid,
                              "imu.host": host, "imu.port": port})
    return funde, vorschlag, []


# ------------------------------------------------------------------ alles


def suchen(imu_host: str = "localhost", imu_port: int = 4223,
           sucher: list[Callable[[], tuple[list[Fund], dict, list[str]]]] | None = None
           ) -> Suchergebnis:
    """Alle drei Wege abklappern. Ein Weg, der fehlschlägt, hindert die anderen nicht."""
    start = time.monotonic()
    ergebnis = Suchergebnis()
    schritte = sucher if sucher is not None else [
        serielle_anschluesse, phidget_kanaele,
        lambda: tinkerforge_geraete(imu_host, imu_port)]
    for schritt in schritte:
        try:
            funde, vorschlag, probleme = schritt()
        except Exception as exc:  # noqa: BLE001 - die Suche soll nie mit 500 enden
            ergebnis.probleme.append(f"{getattr(schritt, '__name__', 'Suche')}: {exc}")
            continue
        ergebnis.funde.extend(funde)
        ergebnis.vorschlag.update(vorschlag)
        ergebnis.probleme.extend(probleme)
    ergebnis.dauer_s = time.monotonic() - start
    return ergebnis
