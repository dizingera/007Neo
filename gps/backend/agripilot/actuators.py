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


class PhidgetPositionOutput(SteerOutput):
    """Lenkmotor über den Positionsregler der Phidget-Steuerung.

    Der bessere Weg, wenn ein Drehgeber am Motor sitzt und bekannt ist, wie
    viele Zählwerte einem Grad Radeinschlag entsprechen. Dann bekommt die
    Platine über ``RescaleFactor`` ihre Einheit in Grad gesetzt, und der
    Sollwinkel geht direkt als Zahl in Grad hinüber - der PID läuft in der
    Firmware mit ihrer eigenen, schnellen Taktung statt hier in Python.

    Das ist nicht nur bequemer, es ist auch ruhiger: der Regelkreis hängt nicht
    mehr an den zehn Positionen pro Sekunde vom Empfänger und nicht an der
    Laufzeit des Programms.

    Der Drehgeber zählt relativ, kennt also keine Geradeausstellung. Sie wird
    beim Scharfschalten gelernt: die aktuelle Stellung wird per
    ``addPositionOffset`` auf glatt null geschoben. Deshalb gilt unverändert -
    beim Scharfschalten stehen die Räder gerade.
    """

    name = "phidget-position"

    def __init__(self, config) -> None:
        super().__init__()
        self.config = config
        self.controller = None
        self.centred = False
        self.target_deg = 0.0
        self.last_error_text = ""
        self._error_since: Optional[float] = None

    # -- Aufbau -----------------------------------------------------------

    async def start(self) -> None:
        usable, problem = phidget_available()
        if not usable:
            self.status = problem
            self.last_error_text = problem
            return
        from Phidget22.Devices.MotorPositionController import MotorPositionController
        cfg = self.config
        try:
            controller = MotorPositionController()
            if cfg.serial_number >= 0:
                controller.setDeviceSerialNumber(cfg.serial_number)
            controller.setChannel(cfg.motor_channel)
            controller.openWaitForAttachment(5000)

            # Ab hier rechnet die Platine in Grad Radeinschlag statt in
            # Zählwerten - das ist der Kern dieser Betriebsart.
            controller.setRescaleFactor(
                (-1.0 if cfg.invert_motor else 1.0) / max(1e-6, cfg.counts_per_deg)
            )
            controller.setCurrentLimit(
                _clamp(cfg.current_limit_a,
                       controller.getMinCurrentLimit(), controller.getMaxCurrentLimit()))
            controller.setVelocityLimit(
                _clamp(cfg.velocity_limit,
                       controller.getMinVelocityLimit(), controller.getMaxVelocityLimit()))
            controller.setDeadBand(cfg.dead_band_deg)
            controller.setNormalizePID(bool(cfg.normalize_pid))
            controller.setKp(cfg.position_kp)
            controller.setKi(cfg.position_ki)
            controller.setKd(cfg.position_kd)
            if cfg.stall_velocity > 0:
                controller.setStallVelocity(cfg.stall_velocity)
            controller.setEngaged(False)
            try:
                controller.enableFailsafe(int(cfg.failsafe_ms))
            except Exception:  # noqa: BLE001
                self.last_error_text = (
                    "Diese Steuerung kennt keinen Failsafe - Not-Aus ist Pflicht")
            self.controller = controller
            self.ready = True
            self.status = (f"Positionsregler {controller.getDeviceSerialNumber()}"
                           f"/{cfg.motor_channel}, {cfg.counts_per_deg:g} Zählwerte je Grad")
        except OSError as exc:
            self.status = f"{_TREIBER_FEHLT} ({exc})"
            self.last_error_text = _TREIBER_FEHLT
            await self._release()
        except Exception as exc:  # noqa: BLE001
            self.status = f"Fehler beim Öffnen: {exc}"
            self.last_error_text = str(exc)
            await self._release()

    async def stop(self) -> None:
        await self._release()
        self.ready = False

    async def _release(self) -> None:
        if self.controller is not None:
            try:
                self.controller.setEngaged(False)
                self.controller.close()
            except Exception:  # noqa: BLE001
                pass
        self.controller = None
        self.centred = False

    # -- Betrieb ----------------------------------------------------------

    def on_arm(self) -> None:
        self.learn_centre()

    def learn_centre(self) -> dict:
        """Aktuelle Stellung als Geradeaus merken.

        ``addPositionOffset`` verschiebt die Zählung der Platine, sodass hier
        anschließend wirklich null steht - besser als ein Nullpunkt, den nur
        dieses Programm kennt und der bei jedem Fehler mitwandert.
        """
        if self.controller is None:
            raise RuntimeError("Kein Lenkmotor verbunden")
        try:
            self.controller.addPositionOffset(-self.controller.getPosition())
            self.controller.setTargetPosition(0.0)
            self.centred = True
            self._error_since = None
            return {"centre_deg": self.controller.getPosition()}
        except Exception as exc:  # noqa: BLE001
            self.last_error_text = str(exc)
            raise RuntimeError(f"Mitte konnte nicht gelernt werden: {exc}") from exc

    def command(self, engaged: bool, angle_deg: float, context: SteerContext) -> None:
        if self.controller is None:
            return
        limit = self.config.max_wheel_angle_deg
        self.target_deg = _clamp(angle_deg, -limit, limit)
        try:
            if engaged and self.centred:
                self.controller.setTargetPosition(self.target_deg)
                self.controller.setEngaged(True)
            else:
                # Nicht scharf: Motor stromlos, damit von Hand gelenkt werden
                # kann - ein gehaltener Sollwert wäre hier das Falsche.
                self.controller.setEngaged(False)
                self._error_since = None
            self.controller.resetFailsafe()
        except Exception as exc:  # noqa: BLE001
            self.last_error_text = str(exc)
            return
        self._read_back(engaged)

    def _read_back(self, engaged: bool) -> None:
        try:
            measured = self.controller.getPosition()
            duty = self.controller.getDutyCycle()
        except Exception:  # noqa: BLE001
            return
        self.feedback = OutputFeedback(
            wheel_angle_deg=measured,
            driver_override=self._detect_override(measured, duty, engaged),
            duty=duty,
            received_at=time.time(),
        )

    def _detect_override(self, measured: float, duty: float, engaged: bool) -> bool:
        """Der Motor drückt, das Rad folgt nicht.

        Mit einem Positionsregler ist das erkennbar, ohne einen weiteren Sensor:
        bleibt die Abweichung groß, während die Platine schon nahe an ihrer
        Leistungsgrenze arbeitet, hält entweder der Fahrer dagegen oder die
        Mechanik klemmt. Beides sind Gründe abzugeben, deshalb wird nicht
        zwischen ihnen unterschieden.
        """
        if not engaged:
            self._error_since = None
            return False
        error = abs(self.target_deg - measured)
        pushing = abs(duty) > self.config.velocity_limit * 0.8
        if error < self.config.override_deg or not pushing:
            self._error_since = None
            return False
        now = time.time()
        if self._error_since is None:
            self._error_since = now
        return (now - self._error_since) > 0.5

    def status_dict(self) -> dict:
        data = super().status_dict()
        data.update({
            "rueckmeldung": "Drehgeber am Positionsregler",
            "zaehlwerte_je_grad": self.config.counts_per_deg,
            "mitte_gelernt": self.centred,
            "soll_grad": self.target_deg,
            "hinweis": self.last_error_text,
        })
        return data


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def build_output(config, imu=None) -> SteerOutput:
    """Ausgang aus der Konfiguration wählen."""
    kind = (config.steering.output or "none").lower()
    if kind == "phidget":
        # Mit Drehgeber und bekanntem Zählwert je Grad regelt die Platine
        # selbst - das ist der ruhigere Weg und deshalb die Voreinstellung.
        if (config.phidget.control or "position").lower() == "position":
            return PhidgetPositionOutput(config.phidget)
        return PhidgetOutput(config.phidget, imu)
    if kind == "udp":
        return UdpOutput(config.steering.host, config.steering.port)
    return NullOutput()
