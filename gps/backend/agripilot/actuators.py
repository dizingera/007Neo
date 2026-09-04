"""Ausgänge der Lenkung.

Der Regler in steering.py entscheidet nur, *ob* und *wohin* gelenkt werden
soll. Wie dieser Wunsch bei der Mechanik ankommt, steht hier - und das ist je
nach Anlage grundverschieden:

* ``UdpOutput``     schickt den Sollwinkel an eine externe Lenkplatine, die
                    selbst regelt. Ein Telegramm, mehr passiert hier nicht.
* ``PhidgetOutput`` treibt einen Gleichstrommotor direkt über eine
                    Phidget-Motorsteuerung. Dann liegt der **Regelkreis hier**,
                    denn ein Motor kennt seinen Lenkwinkel nicht.
* ``NullOutput``    rechnet alles mit, bewegt aber nichts. Für Einbau,
                    Vorführung und Fehlersuche.

Zur Rückmeldung beim Phidget-Motor: ohne sie ist ein Lenkmotor eine offene
Steuerung - er dreht, und niemand weiß, wie weit. Drei Wege, in dieser
Reihenfolge zu empfehlen:

1. **Radwinkelsensor** am Achsschenkel. Misst genau das, was geregelt werden
   soll.
2. **Drehrate aus dem IMU.** Statt des Radwinkels wird geregelt, wie schnell
   sich der Traktor tatsächlich dreht - der Sollwert dafür folgt aus dem
   Einspurmodell. Braucht keinen zusätzlichen Sensor und ist erstaunlich
   gutmütig, weil genau die Größe geregelt wird, auf die es ankommt.
3. **Drehgeber am Motor.** Zählt Umdrehungen ab einer beim Scharfschalten
   gelernten Mitte. Notlösung: die Mitte läuft über den Tag weg.
"""

from __future__ import annotations

import asyncio
import math
import struct
import time
from dataclasses import dataclass
from typing import Optional

# Telegramm an eine externe Lenkplatine: Kennung, Version, Flags, Winkel in
# Hundertstel Grad, Geschwindigkeit in cm/s, Abweichung in mm, XOR-Prüfsumme.
_FRAME = struct.Struct("<2sBBhHhB")
_MAGIC = b"AP"
_VERSION = 1

_TREIBER_FEHLT = (
    "Phidget-Treiber fehlt - die Python-Bibliothek allein genügt nicht. "
    "Windows: Phidgets-Installer des Herstellers, Linux: libphidget22"
)


@dataclass
class SteerContext:
    """Was der Ausgang über den Fahrzustand wissen muss."""

    speed_ms: float = 0.0
    wheelbase_m: float = 2.6
    yaw_rate_deg_s: Optional[float] = None
    cross_track_m: float = 0.0


@dataclass
class OutputFeedback:
    """Was vom Ausgang zurückkommt."""

    wheel_angle_deg: Optional[float] = None
    driver_override: bool = False
    remote_switch: bool = False
    duty: float = 0.0
    received_at: float = 0.0

    @property
    def fresh(self) -> bool:
        return self.received_at > 0 and (time.time() - self.received_at) < 2.0


def phidget_available() -> tuple[bool, str]:
    """Ist die Phidget-Bibliothek samt Treiber benutzbar?

    Wird bewusst über die Versionsabfrage geprüft und nicht, indem ein Gerät
    erzeugt wird: ein halb erzeugtes Phidget-Objekt scheitert später noch einmal
    im Aufräumen und schreibt einen Fehler in die Ausgabe, den niemand mehr
    zuordnen kann.
    """
    try:
        from Phidget22.Phidget import Phidget
        Phidget.getLibraryVersion()
    except ImportError:
        return False, "Phidget-Bibliothek fehlt (pip install phidget22)"
    except OSError as exc:
        return False, f"{_TREIBER_FEHLT} ({exc})"
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)
    return True, ""


class SteerOutput:
    """Gemeinsame Schnittstelle."""

    name = "none"

    def __init__(self) -> None:
        self.feedback = OutputFeedback()
        self.status = "aus"
        self.ready = False

    async def start(self) -> None:
        self.ready = True
        self.status = "bereit"

    async def stop(self) -> None:
        self.ready = False

    def command(self, engaged: bool, angle_deg: float, context: SteerContext) -> None:
        """Wird bei jeder Position aufgerufen - auch beim Abschalten."""

    def on_arm(self) -> None:
        """Beim Scharfschalten: Regler zurücksetzen, Mitte lernen."""

    def status_dict(self) -> dict:
        return {"typ": self.name, "status": self.status, "bereit": self.ready,
                "duty": self.feedback.duty,
                "radwinkel": self.feedback.wheel_angle_deg}


class NullOutput(SteerOutput):
    """Rechnet mit, bewegt nichts."""

    name = "none"

    async def start(self) -> None:
        self.ready = True
        self.status = "nur Anzeige (kein Ausgang)"


class UdpOutput(SteerOutput):
    """Sollwinkel an eine externe Lenkplatine."""

    name = "udp"

    def __init__(self, host: str, port: int) -> None:
        super().__init__()
        self.host = host
        self.port = port
        self._transport: Optional[asyncio.DatagramTransport] = None

    async def start(self) -> None:
        loop = asyncio.get_running_loop()
        output = self

        class _Protocol(asyncio.DatagramProtocol):
            def datagram_received(self, data: bytes, addr) -> None:  # noqa: ANN001
                output._on_datagram(data)

        try:
            self._transport, _ = await loop.create_datagram_endpoint(
                _Protocol, remote_addr=(self.host, self.port)
            )
            self.ready = True
            self.status = f"Lenkplatine {self.host}:{self.port}"
        except Exception as exc:  # noqa: BLE001
            self.status = f"Fehler: {exc}"

    async def stop(self) -> None:
        if self._transport is not None:
            try:
                self._transport.sendto(self._frame(False, 0.0, SteerContext()))
            except Exception:  # noqa: BLE001
                pass
            self._transport.close()
            self._transport = None
        self.ready = False

    def command(self, engaged: bool, angle_deg: float, context: SteerContext) -> None:
        if self._transport is None:
            return
        try:
            self._transport.sendto(self._frame(engaged, angle_deg, context))
        except Exception:  # noqa: BLE001
            pass

    def _frame(self, engaged: bool, angle_deg: float, context: SteerContext) -> bytes:
        angle = int(max(-90.0, min(90.0, angle_deg)) * 100)
        speed = int(max(0.0, min(300.0, context.speed_ms)) * 100)
        cross = int(max(-30.0, min(30.0, context.cross_track_m)) * 1000)
        body = _FRAME.pack(_MAGIC, _VERSION, 1 if engaged else 0, angle, speed, cross, 0)
        checksum = 0
        for byte in body[:-1]:
            checksum ^= byte
        return body[:-1] + bytes([checksum])

    def _on_datagram(self, data: bytes) -> None:
        if len(data) < 6 or data[:2] != _MAGIC:
            return
        try:
            angle, flags = struct.unpack_from("<hB", data, 3)
        except struct.error:
            return
        self.feedback = OutputFeedback(
            wheel_angle_deg=angle / 100.0,
            driver_override=bool(flags & 0x01),
            remote_switch=bool(flags & 0x02),
            received_at=time.time(),
        )


class PhidgetOutput(SteerOutput):
    """Gleichstrom-Lenkmotor an einer Phidget-Motorsteuerung.

    Der Regelkreis läuft hier mit 50 Hz in einer eigenen Aufgabe - deutlich
    schneller als die zehn Positionen pro Sekunde vom Empfänger, weil ein
    Motor sonst zwischen den Stützstellen überschwingt.

    Zwei Sicherheiten stecken in der Hardware und nicht im Programm:

    * **Stromgrenze.** Bewusst niedrig. Der Fahrer muss das Lenkrad jederzeit
      gegen den Motor bewegen können - das ist die letzte Rückfallebene, wenn
      Software und Elektronik gleichzeitig versagen.
    * **Failsafe der Phidget-Steuerung.** Sie bekommt eine Frist gesetzt und
      hält den Motor selbstständig an, wenn wir nicht mehr melden. Ein
      abgestürztes Programm oder ein abgezogenes USB-Kabel führen damit zum
      Stillstand des Motors, nicht zu einem festgehaltenen Einschlag.
    """

    name = "phidget"

    def __init__(self, config, imu=None) -> None:
        super().__init__()
        self.config = config
        self.imu = imu
        self.running = False
        self.motor = None
        self.was = None
        self.encoder = None
        self.mode = config.feedback

        self._target_angle = 0.0
        self._engaged = False
        self._context = SteerContext()
        self._integral = 0.0
        self._last_error = 0.0
        self._last_step = 0.0
        self._encoder_centre = 0.0
        self._override_since: Optional[float] = None
        self.last_error_text = ""

    # -- Aufbau -----------------------------------------------------------

    async def start(self) -> None:
        usable, problem = phidget_available()
        if not usable:
            self.status = problem
            self.last_error_text = problem
            return
        from Phidget22.Devices.DCMotor import DCMotor
        try:
            # Der native Treiber des Herstellers wird erst hier geladen, nicht
            # beim Import. Ohne ihn kommt ein OSError - der darf den Start des
            # Systems nicht mitreißen, sondern gehört als Klartext auf die
            # Systemseite.
            self.motor = DCMotor()
            if self.config.serial_number >= 0:
                self.motor.setDeviceSerialNumber(self.config.serial_number)
            self.motor.setChannel(self.config.motor_channel)
            self.motor.openWaitForAttachment(5000)
            self.motor.setAcceleration(
                _clamp(self.config.acceleration,
                       self.motor.getMinAcceleration(), self.motor.getMaxAcceleration()))
            self.motor.setCurrentLimit(
                _clamp(self.config.current_limit_a,
                       self.motor.getMinCurrentLimit(), self.motor.getMaxCurrentLimit()))
            self.motor.setTargetVelocity(0.0)
            self._enable_failsafe()
            self._open_feedback()
            self.ready = True
            self.status = (f"Motor {self.motor.getDeviceSerialNumber()}"
                           f"/{self.config.motor_channel}, Rückmeldung: {self.mode}")
        except OSError as exc:
            self.status = f"{_TREIBER_FEHLT} ({exc})"
            self.last_error_text = _TREIBER_FEHLT
            await self._release()
            return
        except Exception as exc:  # noqa: BLE001
            self.status = f"Fehler beim Öffnen: {exc}"
            self.last_error_text = str(exc)
            await self._release()
            return

        self.running = True
        asyncio.create_task(self._control_loop())

    def _enable_failsafe(self) -> None:
        try:
            self.motor.enableFailsafe(int(self.config.failsafe_ms))
        except Exception:  # noqa: BLE001 - ältere Geräte kennen das nicht
            self.last_error_text = ("Diese Motorsteuerung kennt keinen Failsafe - "
                                    "Not-Aus ist damit Pflicht")

    def _open_feedback(self) -> None:
        """Rückmeldegerät öffnen. Fehlt es, bleibt der Regelkreis offen -
        deshalb wird hier nichts stillschweigend übersprungen."""
        if self.mode == "was":
            from Phidget22.Devices.VoltageRatioInput import VoltageRatioInput
            self.was = VoltageRatioInput()
            if self.config.serial_number >= 0:
                self.was.setDeviceSerialNumber(self.config.serial_number)
            self.was.setChannel(self.config.was_channel)
            self.was.openWaitForAttachment(5000)
        elif self.mode == "encoder":
            from Phidget22.Devices.Encoder import Encoder
            self.encoder = Encoder()
            if self.config.serial_number >= 0:
                self.encoder.setDeviceSerialNumber(self.config.serial_number)
            self.encoder.setChannel(self.config.encoder_channel)
            self.encoder.openWaitForAttachment(5000)
            self.encoder.setEnabled(True)
            self._encoder_centre = self.encoder.getPosition()

    async def stop(self) -> None:
        self.running = False
        await self._release()
        self.ready = False

    async def _release(self) -> None:
        for device, stopper in ((self.motor, True), (self.was, False), (self.encoder, False)):
            if device is None:
                continue
            try:
                if stopper:
                    device.setTargetVelocity(0.0)
                device.close()
            except Exception:  # noqa: BLE001
                pass
        self.motor = self.was = self.encoder = None

    # -- Betrieb ----------------------------------------------------------

    def command(self, engaged: bool, angle_deg: float, context: SteerContext) -> None:
        self._engaged = engaged
        self._target_angle = angle_deg
        self._context = context
        if not engaged:
            self._integral = 0.0

    def on_arm(self) -> None:
        self._integral = 0.0
        self._last_error = 0.0
        self._override_since = None
        if self.encoder is not None:
            # Beim Scharfschalten steht das Rad gerade - das ist die Mitte.
            try:
                self._encoder_centre = self.encoder.getPosition()
            except Exception:  # noqa: BLE001
                pass

    async def _control_loop(self) -> None:
        interval = 0.02          # 50 Hz
        while self.running:
            try:
                self._step(interval)
            except Exception as exc:  # noqa: BLE001
                self.last_error_text = str(exc)
                self._safe_stop()
            await asyncio.sleep(interval)

    def _step(self, dt: float) -> None:
        if self.motor is None:
            return
        measured = self.measured_angle()
        if not self._engaged:
            self._safe_stop()
            self.feedback = OutputFeedback(wheel_angle_deg=measured, duty=0.0,
                                           received_at=time.time())
            return

        error = self._error(measured)
        self._integral = _clamp(self._integral + error * dt,
                                -self.config.integral_limit, self.config.integral_limit)
        derivative = (error - self._last_error) / dt if dt > 0 else 0.0
        self._last_error = error

        duty = (self.config.gain_p * error
                + self.config.gain_i * self._integral
                + self.config.gain_d * derivative)
        duty = _clamp(duty, -self.config.max_duty, self.config.max_duty)
        if self.config.invert_motor:
            duty = -duty

        try:
            self.motor.setTargetVelocity(duty)
            self.motor.resetFailsafe()
        except Exception as exc:  # noqa: BLE001
            self.last_error_text = str(exc)
            return

        self.feedback = OutputFeedback(
            wheel_angle_deg=measured,
            driver_override=self._detect_override(measured, duty, dt),
            duty=duty,
            received_at=time.time(),
        )

    def _error(self, measured: Optional[float]) -> float:
        """Regelabweichung in der Größe, die tatsächlich gemessen wird."""
        if self.mode == "yaw_rate":
            # Sollgierrate aus dem Einspurmodell: wie schnell sich der Traktor
            # bei diesem Radwinkel und dieser Geschwindigkeit drehen müsste.
            wanted = math.degrees(
                self._context.speed_ms / max(0.5, self._context.wheelbase_m)
                * math.tan(math.radians(_clamp(self._target_angle, -45.0, 45.0)))
            )
            actual = self._context.yaw_rate_deg_s
            if actual is None:
                return 0.0        # ohne Drehrate wird nicht geregelt
            return wanted - actual
        if measured is None:
            return 0.0
        return self._target_angle - measured

    def measured_angle(self) -> Optional[float]:
        try:
            if self.was is not None:
                ratio = self.was.getVoltageRatio()
                angle = ((ratio - self.config.was_centre_ratio)
                         * self.config.was_deg_per_ratio)
                return -angle if self.config.was_invert else angle
            if self.encoder is not None:
                counts = self.encoder.getPosition() - self._encoder_centre
                return counts / max(1e-6, self.config.encoder_counts_per_deg)
        except Exception:  # noqa: BLE001
            return None
        return None

    def _detect_override(self, measured: Optional[float], duty: float,
                         dt: float) -> bool:
        """Greift der Fahrer ins Lenkrad?

        Erkennbar nur mit einer Rückmeldung am Rad: das Rad läuft dann gegen die
        Richtung, in die der Motor drückt. Ohne Sensor - also im Betrieb allein
        über die Drehrate - kann das Programm den Eingriff nicht sehen; dort
        bleibt die niedrige Stromgrenze die Absicherung, und ein Not-Aus ist
        Pflicht.
        """
        if measured is None or abs(duty) < 0.1:
            self._override_since = None
            return False
        rate = (measured - (self.feedback.wheel_angle_deg or measured)) / max(dt, 1e-3)
        against = (rate * duty) < 0 and abs(rate) > 25.0
        if not against:
            self._override_since = None
            return False
        now = time.time()
        if self._override_since is None:
            self._override_since = now
        return (now - self._override_since) > 0.3

    def _safe_stop(self) -> None:
        if self.motor is None:
            return
        try:
            self.motor.setTargetVelocity(0.0)
            self.motor.resetFailsafe()
        except Exception:  # noqa: BLE001
            pass

    def status_dict(self) -> dict:
        data = super().status_dict()
        data.update({
            "rueckmeldung": self.mode,
            "hinweis": self.last_error_text,
        })
        return data


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def build_output(config, imu=None) -> SteerOutput:
    """Ausgang aus der Konfiguration wählen."""
    kind = (config.steering.output or "none").lower()
    if kind == "phidget":
        return PhidgetOutput(config.phidget, imu)
    if kind == "udp":
        return UdpOutput(config.steering.host, config.steering.port)
    return NullOutput()
