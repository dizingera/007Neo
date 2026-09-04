"""Neigungs- und Drehratensensor (IMU).

Warum ein IMU an einer GPS-Anlage hängt, sieht man am besten an einer Zahl: die
Antenne sitzt gut drei Meter über dem Boden. Steht der Traktor auf sechs Grad
Seitenhang, steht die Antenne rund 31 cm neben dem Punkt, den sie zu messen
glaubt - und genau um diesen Betrag wandert die Spur. Kein RTK-Empfänger der
Welt merkt das, denn er misst ja die Antenne völlig korrekt.

Der IMU liefert drei Dinge:

* **Neigung (Roll/Nick)** für den Hangausgleich - der eigentliche Grund.
* **Drehrate (Gierrate)** als Rückmeldung für die Lenkung, wenn kein
  Radwinkelsensor verbaut ist.
* **Kurs** bei langsamer Fahrt, wo aus GPS-Bewegung kein brauchbarer Kurs kommt.

Unterstützt werden die Tinkerforge-Geräte (IMU Brick 2.0, IMU Brick 1.0 und
IMU Bricklet 3.0). Sie hängen an einem laufenden Brick Daemon (brickd), der
üblicherweise lokal auf Port 4223 lauscht.
"""

from __future__ import annotations

import asyncio
import math
import time
from dataclasses import dataclass
from typing import Optional

# Geräte-Kennungen aus der Tinkerforge-Bibliothek
IMU_BRICK_V1 = 16
IMU_BRICK_V2 = 18
IMU_BRICKLET_V3 = 2161


@dataclass
class Attitude:
    """Lage des Fahrzeugs im Raum."""

    roll_deg: float = 0.0        # + = nach rechts geneigt
    pitch_deg: float = 0.0       # + = Nase nach oben
    yaw_deg: Optional[float] = None
    yaw_rate_deg_s: float = 0.0  # + = Drehung nach rechts
    calibration: int = 0         # 0..3, vom Sensor gemeldet
    received_at: float = 0.0

    @property
    def fresh(self) -> bool:
        return self.received_at > 0 and (time.time() - self.received_at) < 1.0

    def to_dict(self) -> dict:
        return {
            "roll_deg": self.roll_deg,
            "pitch_deg": self.pitch_deg,
            "yaw_deg": self.yaw_deg,
            "yaw_rate_deg_s": self.yaw_rate_deg_s,
            "calibration": self.calibration,
            "fresh": self.fresh,
        }


class ImuSource:
    """Gemeinsame Schnittstelle aller Sensorquellen."""

    def __init__(self) -> None:
        self.attitude = Attitude()
        self.running = False
        self.status = "aus"
        self.roll_offset = 0.0
        self.pitch_offset = 0.0

    async def run(self) -> None:  # pragma: no cover - in Unterklassen
        raise NotImplementedError

    async def stop(self) -> None:
        self.running = False

    @property
    def healthy(self) -> bool:
        return self.running and self.attitude.fresh

    def level_here(self) -> dict:
        """Aktuelle Lage als "eben" merken.

        Der Sensor sitzt nie exakt waagerecht in der Kabine, und ein Grad
        Montagefehler sind bei drei Metern Antennenhöhe schon fünf Zentimeter
        Dauerversatz. Deshalb wird einmal auf ebenem Boden genullt.
        """
        self.roll_offset += self.attitude.roll_deg
        self.pitch_offset += self.attitude.pitch_deg
        return {"roll_offset": self.roll_offset, "pitch_offset": self.pitch_offset}

    def _publish(self, roll: float, pitch: float, yaw: Optional[float],
                 yaw_rate: float, calibration: int = 3) -> None:
        self.attitude = Attitude(
            roll_deg=roll - self.roll_offset,
            pitch_deg=pitch - self.pitch_offset,
            yaw_deg=yaw,
            yaw_rate_deg_s=yaw_rate,
            calibration=calibration,
            received_at=time.time(),
        )


class TinkerforgeImu(ImuSource):
    """IMU Brick über den Brick Daemon.

    Die Tinkerforge-Bibliothek arbeitet mit eigenen Threads und Rückrufen. Hier
    wird deshalb nur abgeholt und abgelegt; die Auswertung passiert im normalen
    Ablauf des Programms, wenn die nächste Position eintrifft.
    """

    def __init__(self, host: str = "localhost", port: int = 4223,
                 uid: str = "", axis_map: str = "standard",
                 poll_hz: float = 20.0) -> None:
        super().__init__()
        self.host = host
        self.port = port
        self.uid = uid
        self.axis_map = axis_map
        self.poll_hz = poll_hz
        self.device_name = ""
        self._ipcon = None
        self._device = None

    async def run(self) -> None:
        self.running = True
        backoff = 2.0
        while self.running:
            try:
                await self._session()
                backoff = 2.0
            except Exception as exc:  # noqa: BLE001 - Anzeige darf nie ausfallen
                self.status = f"Fehler: {exc}"
                self._close()
                await asyncio.sleep(backoff)
                backoff = min(30.0, backoff * 2)

    async def _session(self) -> None:
        try:
            from tinkerforge.ip_connection import IPConnection
        except ImportError:
            self.status = "tinkerforge fehlt (pip install tinkerforge)"
            await asyncio.sleep(10)
            return

        loop = asyncio.get_running_loop()
        ipcon = IPConnection()
        await loop.run_in_executor(None, ipcon.connect, self.host, self.port)
        self._ipcon = ipcon

        uid, identifier = await self._find_device(ipcon)
        self._device, self.device_name, reader = self._open_device(uid, identifier, ipcon)
        self.status = f"{self.device_name} ({uid})"

        interval = 1.0 / max(1.0, self.poll_hz)
        while self.running:
            roll, pitch, yaw, yaw_rate, calibration = await loop.run_in_executor(None, reader)
            self._publish(roll, pitch, yaw, yaw_rate, calibration)
            await asyncio.sleep(interval)

    async def _find_device(self, ipcon) -> tuple[str, int]:
        """Angeschlossene Geräte auflisten und den IMU heraussuchen."""
        from tinkerforge.ip_connection import IPConnection

        found: list[tuple[str, int]] = []
        done = asyncio.Event()
        loop = asyncio.get_running_loop()

        def on_enumerate(uid, connected_uid, position, hardware_version,
                         firmware_version, device_identifier, enumeration_type):
            if enumeration_type == IPConnection.ENUMERATION_TYPE_DISCONNECTED:
                return
            if device_identifier in (IMU_BRICK_V1, IMU_BRICK_V2, IMU_BRICKLET_V3):
                if not self.uid or uid == self.uid:
                    found.append((uid, device_identifier))
                    loop.call_soon_threadsafe(done.set)

        ipcon.register_callback(IPConnection.CALLBACK_ENUMERATE, on_enumerate)
        ipcon.enumerate()
        try:
            await asyncio.wait_for(done.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            raise ConnectionError(
                "Kein IMU Brick gefunden - läuft brickd, und ist das Gerät angesteckt?"
            ) from None
        return found[0]

    def _open_device(self, uid: str, identifier: int, ipcon):
        """Gerät öffnen und eine Leseroutine liefern, die immer dasselbe liefert.

        Die drei Geräte unterscheiden sich in Reihenfolge und Einheit der Werte;
        diese Unterschiede bleiben hier und nicht im restlichen Programm.
        """
        if identifier == IMU_BRICK_V2:
            from tinkerforge.brick_imu_v2 import BrickIMUV2
            device = BrickIMUV2(uid, ipcon)

            def read():
                heading, roll, pitch = device.get_orientation()      # 1/16 Grad
                _, _, gyro_z = device.get_angular_velocity()          # 1/16 Grad/s
                calibration = (device.get_all_data().calibration_status >> 4) & 0x03
                return (*self._map_axes(roll / 16.0, pitch / 16.0),
                        heading / 16.0, gyro_z / 16.0, calibration)

            return device, "IMU Brick 2.0", read

        if identifier == IMU_BRICKLET_V3:
            from tinkerforge.bricklet_imu_v3 import BrickletIMUV3
            device = BrickletIMUV3(uid, ipcon)

            def read():
                heading, roll, pitch = device.get_orientation()
                _, _, gyro_z = device.get_angular_velocity()
                calibration = (device.get_all_data().calibration_status >> 4) & 0x03
                return (*self._map_axes(roll / 16.0, pitch / 16.0),
                        heading / 16.0, gyro_z / 16.0, calibration)

            return device, "IMU Bricklet 3.0", read

        from tinkerforge.brick_imu import BrickIMU
        device = BrickIMU(uid, ipcon)

        def read():
            roll, pitch, yaw = device.get_orientation()               # 1/100 Grad
            _, _, gyro_z = device.get_angular_velocity()              # 1/14,375 Grad/s
            return (*self._map_axes(roll / 100.0, pitch / 100.0),
                    yaw / 100.0, gyro_z / 14.375, 3)

        return device, "IMU Brick 1.0", read

    def _map_axes(self, roll: float, pitch: float) -> tuple[float, float]:
        """Einbaulage berücksichtigen.

        Der Sensor liegt selten so im Gehäuse, wie es das Datenblatt annimmt.
        Statt jeden Anwender im Quelltext suchen zu lassen, wird die Einbaulage
        in der Konfiguration eingestellt.
        """
        if self.axis_map == "swapped":          # quer eingebaut
            return pitch, roll
        if self.axis_map == "inverted":         # kopfüber
            return -roll, -pitch
        if self.axis_map == "swapped_inverted":
            return -pitch, -roll
        return roll, pitch

    def _close(self) -> None:
        if self._ipcon is not None:
            try:
                self._ipcon.disconnect()
            except Exception:  # noqa: BLE001
                pass
        self._ipcon = None
        self._device = None

    async def stop(self) -> None:
        self.running = False
        self._close()


class SimulatedImu(ImuSource):
    """IMU für den Simulator.

    Fährt einen leichten Seitenhang mit, damit der Hangausgleich sichtbar wird,
    und leitet die Drehrate aus der Lenkung des virtuellen Traktors ab.
    """

    def __init__(self, simulator=None, slope_deg: float = 4.0,
                 wavelength_m: float = 120.0) -> None:
        super().__init__()
        self.simulator = simulator
        self.slope_deg = slope_deg
        self.wavelength_m = wavelength_m

    async def run(self) -> None:
        self.running = True
        self.status = "Simulator"
        while self.running:
            roll = 0.0
            yaw_rate = 0.0
            yaw = None
            if self.simulator is not None:
                # Ein sanft welliger Hang quer zur Fahrtrichtung
                roll = self.slope_deg * math.sin(
                    2 * math.pi * self.simulator.east / self.wavelength_m
                )
                yaw = self.simulator.heading
                steer = math.radians(self.simulator.steer_deg)
                if abs(steer) > 1e-6:
                    yaw_rate = math.degrees(
                        self.simulator.speed_ms / self.simulator.wheelbase * math.tan(steer)
                    )
            self._publish(roll, 0.0, yaw, yaw_rate)
            await asyncio.sleep(0.05)


def build_source(config, simulator=None) -> Optional[ImuSource]:
    """Sensorquelle aus der Konfiguration wählen."""
    kind = (config.imu.source or "aus").lower()
    if kind in ("", "aus", "none", "off"):
        return None
    if kind == "simulator":
        return SimulatedImu(simulator)
    return TinkerforgeImu(config.imu.host, config.imu.port,
                          config.imu.uid, config.imu.axis_map)


def terrain_offset(attitude: Attitude, antenna_height_m: float,
                   roll_sign: float = 1.0) -> tuple[float, float]:
    """Wie weit die Antenne durch die Neigung neben dem Boden steht.

    Ergebnis in Fahrzeugkoordinaten: (nach rechts, nach vorn) in Metern. Genau
    dieser Betrag wird von der gemessenen Position abgezogen, um vom Punkt der
    Antenne auf den Punkt am Boden zu kommen.
    """
    roll = math.radians(attitude.roll_deg * roll_sign)
    pitch = math.radians(attitude.pitch_deg)
    return (antenna_height_m * math.sin(roll),
            antenna_height_m * math.sin(pitch))
