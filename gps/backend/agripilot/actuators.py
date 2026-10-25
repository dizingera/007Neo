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
import threading
import time
from dataclasses import dataclass
from contextlib import asynccontextmanager
from functools import wraps
from typing import Optional

# Telegramm an eine externe Lenkplatine: Kennung, Version, Flags, Winkel in
# Hundertstel Grad, Geschwindigkeit in cm/s, Abweichung in mm, XOR-Prüfsumme.
_FRAME = struct.Struct("<2sBBhHhB")
_MAGIC = b"AP"
_VERSION = 1

MOTORTEST_MAX = 0.35            # höchstens so viel der vollen Leistung im Motortest
MOTORTEST_DAUER_MAX_S = 1.0     # und höchstens so lange am Stück
FEIN_SPRUNG_DEG = 6.0           # so weit fährt die Feinabstimmung aus der Mitte
FEIN_HALTEN_S = 2.5             # so lange hat der Regler je Sprung Zeit
FEIN_DURCHGAENGE = 12           # höchstens so viele Durchgänge hin und zurück
EINMESS_IMPULS_S = 0.35         # so lang ist ein Impuls beim Einmessen
EINMESS_REGELTAKT_S = 0.12      # Zugabe, damit der Regelkreis den Impuls sicher durchläuft
EINMESS_AUSROLLEN_S = 0.25      # nach dem Impuls ausrollen lassen, dann ablesen
EINMESS_MIN_ZAEHLWERTE = 12     # so viel muss sich der Drehgeber bewegen, damit der Motor als angelaufen gilt
# Ab 1.5.85: Bleibt der Lenkbefehl so lange aus (Programm hängt, kein GPS), steht der
# Motor - die 1065 hat unter Phidget21 keinen eigenen Failsafe, der das täte.
KOMMANDO_FRIST_S = 0.5
KEIN_BEFEHL = "Kein Lenkbefehl mehr - Motor angehalten"
# So lange darf die Rückmeldung (Drehgeber, Radwinkelsensor) beim Lenken fehlen,
# bevor der Ausgang als gestört gilt und die Lenkung pausiert.
RUECKMELDUNG_FRIST_S = 0.3
REGEL_TAKT_S = 0.02             # 50 Hz
REGEL_FADEN = True              # Tests, die den Takt selbst schalten, setzen das auf False

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


def phidget_bibliothek(config) -> tuple[Optional[str], str]:
    """Welche Phidget-Bibliothek benutzt wird: ("phidget22"|"phidget21"|None, Problem).

    "auto" nimmt Phidget22, wenn dessen Treiber da ist, sonst Phidget21 - auf
    dem Tablet am Traktor ist der ältere Treiber installiert, mit dem die
    MotorControl 1065 schon unter dem AgriPilot Terminal läuft.
    """
    from . import phidget21
    wahl = (getattr(config, "bibliothek", "auto") or "auto").lower()
    ok22, problem22 = phidget_available() if wahl in ("auto", "phidget22") else (False, "")
    if wahl == "phidget22":
        return ("phidget22", "") if ok22 else (None, problem22)
    if ok22 and wahl == "auto":
        return "phidget22", ""
    ok21, problem21 = phidget21.verfuegbar()
    if ok21:
        return "phidget21", ""
    if wahl == "phidget21":
        return None, problem21
    return None, f"{problem22} - und {problem21}"


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

    def abbrechen(self, grund: str = "Werkstattlauf abgebrochen") -> None:
        """Alle eigenständigen Bewegungen beenden (auch ohne scharfe Lenkung)."""

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


class SimulatorOutput(NullOutput):
    """Im Simulatorbetrieb: kein Motor, die Engine lenkt den virtuellen Traktor."""

    name = "simulator"

    async def start(self) -> None:
        self.ready = True
        self.status = "Simulator - lenkt den virtuellen Traktor"


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


def _werkstattlauf(methode):
    @wraps(methode)
    async def lauf(self, *args, **kwargs):
        async with self._werkstatt():
            return await methode(self, *args, **kwargs)
    return lauf


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
        self.bibliothek = "phidget22"
        self._geraet21 = None
        self._test_duty = 0.0
        self._test_bis = 0.0
        self._anschlag: Optional[list] = None      # [kleinster, größter] Zählstand beim Ausmessen
        self._test_strom_max = 0.0
        # Regeltest (Feinabstimmung am Stand): der Regler fährt einen Zielwinkel an,
        # ohne dass die Lenkung scharf ist, und schreibt mit, wie der Winkel folgt.
        self._regeltest_ziel: Optional[float] = None
        self._regeltest_bis = 0.0
        self._regeltest_start = 0.0
        self._regeltest_spur: list = []

        self._target_angle = 0.0
        self._engaged = False
        self._context = SteerContext()
        self._integral = 0.0
        self._last_error = 0.0
        self._last_step = 0.0
        self._encoder_centre = 0.0
        self._override_since: Optional[float] = None
        self.last_error_text = ""
        self._kommando_at = 0.0                 # monotonic: letzter Lenkbefehl
        self.mitte_gelernt = False              # „Mitte lernen“ gedrückt, seit verbunden
        self._ohne_messung_seit: Optional[float] = None
        self._status_ok = ""
        self._thread: Optional[threading.Thread] = None
        self._motor_lock = threading.RLock()
        self._kommando_generation = 0
        self._werkstatt_task = None
        self._werkstatt_loop = None
        self._werkstatt_event = None
        self._werkstatt_grund = ""

    # -- Aufbau -----------------------------------------------------------

    async def start(self) -> None:
        bibliothek, problem = phidget_bibliothek(self.config)
        if bibliothek is None:
            self.status = problem
            self.last_error_text = problem
            return
        self.bibliothek = bibliothek
        try:
            if bibliothek == "phidget21":
                self._oeffnen21()
            else:
                self._oeffnen22()
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
                           f"/{self.config.motor_channel} ({bibliothek}), Rückmeldung: {self.mode}")
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
        self._status_ok = self.status
        # Der Regelkreis läuft in einem eigenen Faden, nicht in der Ereignisschleife:
        # rechnet das Programm gerade etwas Größeres (Saatplan, Abgleich), bleibt der
        # Takt trotzdem bei 50 Hz, und der Motor läuft nicht mit der letzten Leistung weiter.
        if REGEL_FADEN:
            self._thread = threading.Thread(target=self._faden, name="lenkmotor", daemon=True)
            self._thread.start()

    def _faden(self) -> None:
        letzter = time.monotonic()
        while self.running:
            jetzt = time.monotonic()
            dt = min(0.1, max(0.005, jetzt - letzter))
            letzter = jetzt
            try:
                self._step(dt)
            except Exception as exc:  # noqa: BLE001
                self.last_error_text = str(exc)
                self._safe_stop()
            time.sleep(max(0.0, REGEL_TAKT_S - (time.monotonic() - jetzt)))

    def _oeffnen22(self) -> None:
        # Der native Treiber des Herstellers wird erst hier geladen, nicht beim
        # Import. Ohne ihn kommt ein OSError - der darf den Start des Systems
        # nicht mitreißen, sondern gehört als Klartext auf die Systemseite.
        from Phidget22.Devices.DCMotor import DCMotor
        self.motor = DCMotor()
        if self.config.serial_number >= 0:
            self.motor.setDeviceSerialNumber(self.config.serial_number)
        self.motor.setChannel(self.config.motor_channel)
        self.motor.openWaitForAttachment(5000)

    def _oeffnen21(self) -> None:
        """MotorControl über Phidget21 (z. B. 1065): ein Gerät, Motor, Drehgeber
        und Analogeingänge darin - alles über dieselbe Verbindung."""
        from . import phidget21
        self._geraet21 = phidget21.MotorControl21()
        self._geraet21.oeffnen(self.config.serial_number)
        self.motor = phidget21.Motor(self._geraet21, self.config.motor_channel)

    def _enable_failsafe(self) -> None:
        try:
            self.motor.enableFailsafe(int(self.config.failsafe_ms))
        except Exception:  # noqa: BLE001 - ältere Geräte kennen das nicht
            self.last_error_text = ("Diese Motorsteuerung kennt keinen Failsafe - "
                                    "Not-Aus ist damit Pflicht")

    def _open_feedback(self) -> None:
        """Rückmeldegerät öffnen. Fehlt es, bleibt der Regelkreis offen -
        deshalb wird hier nichts stillschweigend übersprungen."""
        if self.bibliothek == "phidget21":
            from . import phidget21
            if self.mode == "was":
                self.was = phidget21.Spannungsverhaeltnis(self._geraet21, self.config.was_channel)
            elif self.mode == "encoder":
                self.encoder = phidget21.Drehgeber(self._geraet21, self.config.encoder_channel)
                self._encoder_centre = self.encoder.getPosition()
            return
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
        self.abbrechen("Werkstattlauf beim Beenden abgebrochen")
        self.running = False
        if self._thread is not None:
            await asyncio.to_thread(self._thread.join, 1.0)
            self._thread = None
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
        if self._geraet21 is not None:
            self._geraet21.schliessen()      # hält den Motor an, auch wenn das Öffnen mittendrin scheiterte
            self._geraet21 = None
        self.motor = self.was = self.encoder = None

    # -- Betrieb ----------------------------------------------------------
    @property
    def werkstatt_aktiv(self) -> bool:
        return self._werkstatt_task is not None

    @asynccontextmanager
    async def _werkstatt(self):
        task = asyncio.current_task()
        if self._werkstatt_task is task:
            self._werkstatt_pruefen()
            yield
            return
        with self._motor_lock:
            if self.werkstatt_aktiv:
                raise RuntimeError("Es läuft bereits ein Werkstattlauf - erst abbrechen")
            if self._engaged:
                raise RuntimeError("Die Lenkung ist scharf - erst ausschalten")
            self._werkstatt_task = task
            self._werkstatt_loop = asyncio.get_running_loop()
            self._werkstatt_event = asyncio.Event()
            self._werkstatt_grund = ""
        try:
            yield
        finally:
            with self._motor_lock:
                self._kommando_generation += 1
                self._test_bis = self._test_duty = 0.0
                self._regeltest_ziel = None
                self._regeltest_bis = 0.0
                self._safe_stop()
                self._werkstatt_task = self._werkstatt_loop = self._werkstatt_event = None

    def _werkstatt_pruefen(self) -> None:
        if self._werkstatt_grund:
            raise RuntimeError(self._werkstatt_grund)

    async def _werkstatt_warten(self, dauer_s: float) -> None:
        self._werkstatt_pruefen()
        try:
            await asyncio.wait_for(self._werkstatt_event.wait(), dauer_s)
        except asyncio.TimeoutError:
            pass
        self._werkstatt_pruefen()

    def abbrechen(self, grund: str = "Werkstattlauf abgebrochen") -> None:
        with self._motor_lock:
            self._kommando_generation += 1
            self._engaged = False
            self._test_bis = self._test_duty = 0.0
            self._regeltest_ziel = None
            self._regeltest_bis = 0.0
            self._integral = self._last_error = 0.0
            if self.werkstatt_aktiv:
                self._werkstatt_grund = grund
                self._werkstatt_loop.call_soon_threadsafe(self._werkstatt_event.set)
            self._safe_stop()

    def command(self, engaged: bool, angle_deg: float, context: SteerContext) -> None:
        with self._motor_lock:
            self._kommando_generation += 1
            self._engaged = engaged
            self._target_angle = angle_deg
            self._context = context
            self._kommando_at = time.monotonic()
            if not engaged:
                self._integral = 0.0
                self._last_error = 0.0
                self._safe_stop()

    def on_arm(self) -> None:
        self._integral = 0.0
        self._last_error = 0.0
        self._override_since = None
        if self.encoder is not None and not self.mitte_gelernt:
            # Ohne gelernte Mitte gilt: beim Scharfschalten steht das Rad gerade.
            # Mit „Mitte lernen“ bleibt die gelernte - sonst verschob jedes
            # Scharfschalten (auch mit schräg stehenden Rädern) die Mitte, und die
            # aus der Fahrt gelernte Radmitte passte nicht mehr dazu.
            try:
                self._encoder_centre = self.encoder.getPosition()
            except Exception:  # noqa: BLE001
                pass

    def learn_centre(self) -> dict:
        """Die aktuelle Radstellung als Geradeaus lernen (Lenkung → Mitte lernen).

        Gilt, solange die Motorsteuerung verbunden bleibt: der Drehgeber zählt
        relativ, nach dem Abstecken muss sie neu gelernt werden.
        """
        if self.encoder is None:
            if self.was is not None:
                raise RuntimeError("Der Radwinkelsensor misst die Mitte selbst - "
                                   "sie steht in den Einstellungen (Mitte des Sensors)")
            raise RuntimeError("Ohne Drehgeber gibt es keine Mitte zu lernen")
        try:
            self._encoder_centre = self.encoder.getPosition()
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"Drehgeber nicht lesbar: {exc}") from exc
        self.config.mitte_versatz_deg = 0.0      # die aus der Fahrt gelernte Korrektur gehörte zur alten Mitte
        self.mitte_gelernt = True
        return {"centre_counts": self._encoder_centre}

    async def _control_loop(self) -> None:
        """Derselbe Regelkreis in der Ereignisschleife, mit festem Takt - für Tests,
        deren nachgebildeter Motor je Aufruf einen Schritt weiterrückt."""
        while self.running:
            try:
                self._step(REGEL_TAKT_S)
            except Exception as exc:  # noqa: BLE001
                self.last_error_text = str(exc)
                self._safe_stop()
            await asyncio.sleep(REGEL_TAKT_S)

    @_werkstattlauf
    async def motortest(self, richtung: int, anteil: float, dauer_s: float) -> dict:
        """Den Motor kurz und schwach in eine Richtung drehen lassen - ein Test
        der Verbindung, nicht der Lenkung.

        Nur wenn die Lenkung nicht scharf ist, höchstens ``MOTORTEST_MAX`` der
        Leistung und höchstens ``MOTORTEST_DAUER_MAX_S`` Sekunden. Der Regelkreis
        schaltet danach von selbst wieder auf Stillstand. Zurück kommt, wie weit
        sich der Drehgeber bewegt hat - bewegt sich der Motor, aber der Drehgeber
        nicht, ist er nicht am Motor angeschlossen oder zählt nicht.
        """
        if not self.ready or self.motor is None:
            raise RuntimeError(f"Lenkmotor nicht bereit: {self.status}")
        if self._engaged:
            raise RuntimeError("Die Lenkung ist scharf - erst ausschalten")
        anteil = _clamp(abs(float(anteil)), 0.05, MOTORTEST_MAX)
        dauer_s = _clamp(float(dauer_s), 0.1, MOTORTEST_DAUER_MAX_S)
        vorher = self._drehgeber_roh()
        vorzeichen = (1.0 if richtung >= 0 else -1.0) * (-1.0 if self.config.invert_motor else 1.0)
        self._test_duty = vorzeichen * anteil
        self._test_strom_max = 0.0
        self._test_bis = time.time() + dauer_s
        await self._werkstatt_warten(dauer_s + 0.2)
        self._test_bis = 0.0
        self._safe_stop()
        nachher = self._drehgeber_roh()
        bewegt = None if vorher is None or nachher is None else nachher - vorher
        ergebnis = {
            "richtung": "rechts" if richtung >= 0 else "links",
            "leistung": anteil, "dauer_s": dauer_s,
            "drehgeber_vorher": vorher, "drehgeber_nachher": nachher,
            "drehgeber_bewegt": bewegt,
            "zaehlwerte_je_s": None if bewegt is None else round(abs(bewegt) / dauer_s),
            "strom_max_a": round(self._test_strom_max, 2),
            "bibliothek": self.bibliothek,
        }
        diagnose = getattr(self.motor, "diagnose", None)
        if diagnose is not None:
            ergebnis.update(diagnose())
        return ergebnis

    async def _impuls(self, vorzeichen: float, anteil: float, dauer_s: float) -> Optional[int]:
        """Ein kurzer Impuls; zurück kommt, wie weit sich der Drehgeber bewegt hat."""
        vorher = self._drehgeber_roh()
        self._test_duty = vorzeichen * (-1.0 if self.config.invert_motor else 1.0) * anteil
        self._test_bis = time.time() + dauer_s
        await self._werkstatt_warten(dauer_s + EINMESS_REGELTAKT_S)
        self._test_bis = 0.0
        self._safe_stop()
        await self._werkstatt_warten(EINMESS_AUSROLLEN_S)   # ausrollen lassen, dann ablesen
        nachher = self._drehgeber_roh()
        return None if vorher is None or nachher is None else nachher - vorher

    @_werkstattlauf
    async def einmessen(self) -> dict:
        """Den Motor am Stand einmessen: ab welcher Leistung er anläuft.

        Abwechselnd nach rechts und links, jeweils ein kurzer Impuls, in kleinen
        Stufen von 8 % aufwärts bis ``MOTORTEST_MAX``. Die erste Stufe, bei der
        sich der Drehgeber merklich bewegt, ist die Losbrechleistung. Daraus
        folgt die Mindestleistung für den Regelkreis (Losbrechleistung plus ein
        Viertel Reserve). Die Räder stehen dabei möglichst in der Mitte: abwechselnd
        rechts und links bleibt die Lenkung in etwa dort.
        """
        if not self.ready or self.motor is None:
            raise RuntimeError(f"Lenkmotor nicht bereit: {self.status}")
        if self._engaged:
            raise RuntimeError("Die Lenkung ist scharf - erst ausschalten")
        if self._drehgeber_roh() is None:
            raise RuntimeError("Kein Drehgeber auslesbar - ohne ihn lässt sich der Motor nicht einmessen")
        schwelle = EINMESS_MIN_ZAEHLWERTE
        los = {1: None, -1: None}
        tempo = {1: 0.0, -1: 0.0}
        stufe = 0.08
        while stufe <= MOTORTEST_MAX + 1e-9 and None in los.values():
            for richtung in (1, -1):
                if los[richtung] is not None:
                    continue
                bewegt = await self._impuls(richtung, stufe, EINMESS_IMPULS_S)
                if bewegt is not None and abs(bewegt) >= schwelle:
                    los[richtung] = round(stufe, 2)
                    tempo[richtung] = abs(bewegt) / EINMESS_IMPULS_S
            stufe = round(stufe + 0.03, 2)
        gefunden = [v for v in los.values() if v is not None]
        vorschlag = None
        if gefunden:
            vorschlag = round(min(0.5, max(0.12, max(gefunden) * 1.25)), 2)
        return {
            "losbrech_rechts": los[1], "losbrech_links": los[-1],
            "zaehlwerte_je_s_rechts": round(tempo[1]), "zaehlwerte_je_s_links": round(tempo[-1]),
            "mindestleistung_vorschlag": vorschlag,
            "hinweis": ("" if gefunden and None not in los.values() else
                        "In mindestens einer Richtung hat sich bis zur Höchstleistung des Tests nichts "
                        "bewegt - Motor, Kupplung oder Drehgeber prüfen."),
        }

    @_werkstattlauf
    async def richtung_testen(self, anteil: float = 0.2, dauer_s: float = 0.4) -> dict:
        """Ein Impuls "nach rechts" (so wie der Regler es meint, mit der
        eingestellten Motor-Umkehr). Zurück: wie weit der Drehgeber roh gezählt hat."""
        if not self.ready or self.motor is None:
            raise RuntimeError(f"Lenkmotor nicht bereit: {self.status}")
        if self._engaged:
            raise RuntimeError("Die Lenkung ist scharf - erst ausschalten")
        anteil = _clamp(abs(float(anteil)), 0.05, MOTORTEST_MAX)
        bewegt = await self._impuls(1.0, anteil, _clamp(float(dauer_s), 0.1, MOTORTEST_DAUER_MAX_S))
        return {"bewegt": bewegt, "leistung": anteil}

    def anschlag_start(self) -> dict:
        """Ausmessen von Anschlag zu Anschlag: ab jetzt wird der kleinste und größte
        Zählstand des Drehgebers mitgeschrieben. Gelenkt wird von Hand, der Motor
        bleibt stromlos."""
        if not self.ready or self.motor is None:
            raise RuntimeError(f"Lenkmotor nicht bereit: {self.status}")
        if self._engaged:
            raise RuntimeError("Die Lenkung ist scharf - erst ausschalten")
        roh = self._drehgeber_roh()
        if roh is None:
            raise RuntimeError("Kein Drehgeber auslesbar")
        self._anschlag = [roh, roh]
        return {"start": roh}

    def anschlag_ende(self, gesamtwinkel_deg: float) -> dict:
        if self._anschlag is None:
            raise RuntimeError("Das Ausmessen wurde nicht gestartet")
        klein, gross = self._anschlag
        self._anschlag = None
        spanne = gross - klein
        if spanne < 50:
            raise RuntimeError(f"Der Drehgeber hat sich kaum bewegt ({spanne} Zählwerte) - "
                               "von Anschlag zu Anschlag lenken")
        winkel = max(5.0, float(gesamtwinkel_deg))
        return {"klein": klein, "gross": gross, "zaehlwerte": spanne, "gesamtwinkel_deg": winkel,
                "zaehlwerte_je_grad": round(spanne / winkel, 2)}

    def _drehgeber_roh(self) -> Optional[int]:
        """Zählstand des Drehgebers am Motor, auch wenn er nicht die Rückmeldung ist."""
        try:
            if self.encoder is None and self.bibliothek == "phidget21" and self._geraet21 is not None:
                from . import phidget21
                return phidget21.Drehgeber(self._geraet21, self.config.encoder_channel).getPosition()
            if self.encoder is not None:
                return int(self.encoder.getPosition())
        except Exception:  # noqa: BLE001
            return None
        return None

    def _step(self, dt: float) -> None:
        with self._motor_lock:
            generation = self._kommando_generation
        if self.motor is None:
            return
        if self._anschlag is not None:
            roh = self._drehgeber_roh()
            if roh is not None:
                self._anschlag[0] = min(self._anschlag[0], roh)
                self._anschlag[1] = max(self._anschlag[1], roh)
        if time.time() < self._test_bis and not self._engaged:
            try:
                with self._motor_lock:
                    if generation != self._kommando_generation:
                        return
                    self.motor.setTargetVelocity(self._test_duty)
                    self.motor.resetFailsafe()
                diagnose = getattr(self.motor, "diagnose", None)
                if diagnose is not None:
                    self._test_strom_max = max(self._test_strom_max, diagnose().get("strom_a", 0.0))
            except Exception as exc:  # noqa: BLE001
                self.last_error_text = str(exc)
                self._test_bis = 0.0
            return
        measured = self.measured_angle()
        jetzt = time.time()
        regeltest = (self._regeltest_ziel is not None and jetzt < self._regeltest_bis
                     and not self._engaged)
        if regeltest:
            self._regeltest_spur.append((jetzt - self._regeltest_start, measured))
        engaged = self._engaged
        if engaged and time.monotonic() - self._kommando_at > KOMMANDO_FRIST_S:
            # Kein frischer Lenkbefehl: nicht mit dem alten Sollwert weiterlenken.
            engaged = False
            self._integral = 0.0
            self.last_error_text = KEIN_BEFEHL
        elif engaged and self.last_error_text == KEIN_BEFEHL:
            self.last_error_text = ""
        self._rueckmeldung_pruefen(measured, engaged)
        if engaged and self.mode == "yaw_rate" and (
                self._context.yaw_rate_deg_s is None
                or not math.isfinite(self._context.yaw_rate_deg_s)):
            self._integral = self._last_error = 0.0
            self._safe_stop()
            self.feedback = OutputFeedback(duty=0.0, received_at=time.time())
            return
        if not engaged and not regeltest:
            self._safe_stop()
            self.feedback = OutputFeedback(wheel_angle_deg=measured, duty=0.0,
                                           received_at=time.time())
            return

        if regeltest:
            error = 0.0 if measured is None else self._regeltest_ziel - measured
        else:
            error = self._error(measured)
        self._integral = _clamp(self._integral + error * dt,
                                -self.config.integral_limit, self.config.integral_limit)
        derivative = (error - self._last_error) / dt if dt > 0 else 0.0
        self._last_error = error

        duty = (self.config.gain_p * error
                + self.config.gain_i * self._integral
                + self.config.gain_d * derivative)
        # Mindestleistung gegen die Haftreibung - nur wo noch gelenkt werden
        # muss: außerhalb des Totbands und nur bei einem Radwinkel als Regelgröße.
        if (self.mode != "yaw_rate" and abs(error) > self.config.dead_band_deg
                and 0.0 < abs(duty) < self.config.min_duty):
            duty = math.copysign(self.config.min_duty, duty)
        elif self.mode != "yaw_rate" and abs(error) <= self.config.dead_band_deg:
            duty = 0.0
        duty = _clamp(duty, -self.config.max_duty, self.config.max_duty)
        rad_duty = duty                 # in Radrichtung (+ rechts), vor der Motor-Umkehr
        if self.config.invert_motor:
            duty = -duty

        try:
            with self._motor_lock:
                # Ein bereits berechneter Wert darf ein späteres AUS nicht überholen.
                if generation != self._kommando_generation:
                    return
                self.motor.setTargetVelocity(duty)
                self.motor.resetFailsafe()
        except Exception as exc:  # noqa: BLE001
            self.last_error_text = str(exc)
            return

        # Der Eingriff wird in Radrichtung erkannt: mit der umgekehrten Motorleistung
        # verglichen (bis 1.5.84) sah bei "Motor umkehren" jedes zügige Nachlaufen des
        # Rads wie ein Fahrer aus, der dagegenhält - und die Lenkung ging aus.
        self.feedback = OutputFeedback(
            wheel_angle_deg=measured,
            driver_override=False if regeltest else self._detect_override(measured, rad_duty, dt),
            duty=duty,
            received_at=time.time(),
        )

    def _rueckmeldung_pruefen(self, measured: Optional[float], engaged: bool) -> None:
        """Lenken ohne Radwinkel heißt blind lenken: dann gilt der Ausgang als gestört.

        Die Lenkautomatik pausiert dann („Lenkausgang gestört“) und greift wieder,
        sobald Drehgeber oder Radwinkelsensor wieder Werte liefern.
        """
        if self.mode not in ("encoder", "was", "yaw_rate"):
            return
        messwert = self._context.yaw_rate_deg_s if self.mode == "yaw_rate" else measured
        if messwert is not None and math.isfinite(messwert):
            self._ohne_messung_seit = None
            if not self.ready and self.motor is not None and self._status_ok:
                self.ready = True
                self.status = self._status_ok
            return
        if not engaged:
            return
        jetzt = time.monotonic()
        if self._ohne_messung_seit is None:
            self._ohne_messung_seit = jetzt
        if (self.mode == "yaw_rate"
                or jetzt - self._ohne_messung_seit > RUECKMELDUNG_FRIST_S) and self.ready:
            self.ready = False
            self.status = ("Rückmeldung fehlt: " +
                           ({"encoder": "Drehgeber", "was": "Radwinkelsensor",
                             "yaw_rate": "Gierrate"}[self.mode]) +
                           " liefert keine Werte")
            self.last_error_text = self.status

    @_werkstattlauf
    async def sprung(self, ziel_deg: float, halten_s: float) -> list:
        """Den Regler einen Radwinkel anfahren lassen und mitschreiben."""
        self._integral = 0.0
        self._last_error = 0.0
        self._regeltest_spur = []
        self._regeltest_start = time.time()
        self._regeltest_ziel = float(ziel_deg)
        self._regeltest_bis = self._regeltest_start + halten_s
        try:
            await self._werkstatt_warten(halten_s + 0.05)
        finally:
            self._regeltest_ziel = None
            self._regeltest_bis = 0.0
            self._safe_stop()
        return list(self._regeltest_spur)

    @_werkstattlauf
    async def feinabstimmen(self, sprung_deg: float = FEIN_SPRUNG_DEG,
                            durchgaenge: int = FEIN_DURCHGAENGE) -> dict:
        """P/I/D des Motorreglers am Stand einstellen (siehe lenkung_einrichten).

        Die aktuelle Stellung gilt dabei als Mitte; gefahren wird ``sprung_deg``
        nach rechts und zurück. Gelingt es nicht, gehen die Ausgangswerte zurück.
        """
        from . import lenkung_einrichten as le
        if not self.ready or self.motor is None:
            raise RuntimeError(f"Lenkmotor nicht bereit: {self.status}")
        if self._engaged:
            raise RuntimeError("Die Lenkung ist scharf - erst ausschalten")
        if self.mode not in ("encoder", "was") or self.measured_angle() is None:
            raise RuntimeError("Die Feinabstimmung braucht einen Drehgeber oder Radwinkelsensor "
                               "als Rückmeldung (Menü → Lenkung → Lenkmotor → Rückmeldung)")
        cfg = self.config
        start = {"gain_p": cfg.gain_p, "gain_i": cfg.gain_i, "gain_d": cfg.gain_d,
                 "max_duty": cfg.max_duty}
        if self.encoder is not None and not self.mitte_gelernt:
            self._encoder_centre = self.encoder.getPosition()     # hier ist die Mitte
        mitte = self.measured_angle() or 0.0     # mit gelernter Mitte: von hier aus und wieder hierher
        verlauf = []
        ergebnis = {"gut": False, "text": ""}
        uebernehmen = False
        try:
            for _ in range(int(durchgaenge)):
                hin = await self.sprung(mitte + sprung_deg, FEIN_HALTEN_S)
                zurueck = await self.sprung(mitte, FEIN_HALTEN_S)
                befunde = [le.bewerte_sprung(hin, mitte, mitte + sprung_deg),
                           le.bewerte_sprung(zurueck, mitte + sprung_deg, mitte)]
                ergebnis = le.feinabstimmung_schritt(befunde, cfg.gain_p, cfg.gain_i, cfg.gain_d,
                                                     leistung=cfg.max_duty, sprung_deg=sprung_deg)
                verlauf.append({"p": cfg.gain_p, "i": cfg.gain_i, "d": cfg.gain_d, "leistung": cfg.max_duty,
                                "hin": befunde[0], "zurueck": befunde[1], "text": ergebnis["text"]})
                cfg.gain_p, cfg.gain_i, cfg.gain_d = ergebnis["p"], ergebnis["i"], ergebnis["d"]
                cfg.max_duty = ergebnis.get("leistung") or cfg.max_duty
                if ergebnis["fertig"]:
                    break
            else:
                ergebnis = {**ergebnis, "gut": False,
                            "text": f"Nach {durchgaenge} Durchgängen nicht sauber - Ausgangswerte zurück."}
            if not ergebnis.get("gut"):
                bestes = le.bester_durchgang(verlauf)
                if bestes is not None:
                    cfg.gain_p, cfg.gain_i, cfg.gain_d = bestes["p"], bestes["i"], bestes["d"]
                    cfg.max_duty = bestes["leistung"]
                    ergebnis = {**ergebnis, "gut": True,
                                "text": f"Schneller geht es mit diesem Motor nicht ohne Überschwingen: "
                                        f"90 % nach {bestes['anstieg_s']:.2f} s. Diese Werte gelten."}
            uebernehmen = bool(ergebnis.get("gut"))
        finally:
            self._safe_stop()
            if not uebernehmen:
                cfg.gain_p, cfg.gain_i, cfg.gain_d = start["gain_p"], start["gain_i"], start["gain_d"]
                cfg.max_duty = start["max_duty"]
        return {"gut": bool(ergebnis.get("gut")), "text": ergebnis["text"], "start": start,
                "ende": {"gain_p": cfg.gain_p, "gain_i": cfg.gain_i, "gain_d": cfg.gain_d,
                         "max_duty": cfg.max_duty},
                "verlauf": verlauf}

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

    def mitte_korrigieren(self, grad: float) -> None:
        """Die Radmitte um ``grad`` verschieben (aus der Fahrt gelernt, radmitte.py)."""
        self.config.mitte_versatz_deg = round(float(getattr(self.config, "mitte_versatz_deg", 0.0)) + grad, 3)

    def measured_angle(self) -> Optional[float]:
        roh = self._roher_winkel()
        if roh is None:
            return None
        return roh - float(getattr(self.config, "mitte_versatz_deg", 0.0) or 0.0)

    def _roher_winkel(self) -> Optional[float]:
        try:
            if self.was is not None:
                ratio = self.was.getVoltageRatio()
                angle = ((ratio - self.config.was_centre_ratio)
                         * self.config.was_deg_per_ratio)
                return -angle if self.config.was_invert else angle
            if self.encoder is not None:
                counts = self.encoder.getPosition() - self._encoder_centre
                if getattr(self.config, "encoder_invert", False):
                    counts = -counts
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
        vorher = self.feedback.wheel_angle_deg
        rate = (measured - (measured if vorher is None else vorher)) / max(dt, 1e-3)
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
            with self._motor_lock:
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
        if self.encoder is not None:
            # Für Lenkung → Am Stand: „Mitte lernen“ mit Ist- und Sollwinkel.
            gemessen = self.measured_angle()
            data.update({"mitte_gelernt": self.mitte_gelernt,
                         "zaehlwerte_je_grad": self.config.encoder_counts_per_deg,
                         "radwinkel": None if gemessen is None else round(gemessen, 2),
                         "soll_grad": round(self._target_angle, 2)})
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
        # Tastverhältnis -1..1 nahe der Grenze. Bis 1.5.84 stand hier die
        # Geschwindigkeitsgrenze (Grad/s) als Maßstab - das wurde nie erreicht.
        pushing = abs(duty) > 0.8
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
        # Unter Phidget21 gibt es keinen Positionsregler auf der Platine: dann
        # regelt immer AgriPilot selbst.
        if (config.phidget.control or "position").lower() == "position" \
                and phidget_bibliothek(config.phidget)[0] != "phidget21":
            return PhidgetPositionOutput(config.phidget)
        return PhidgetOutput(config.phidget, imu)
    if kind == "udp":
        return UdpOutput(config.steering.host, config.steering.port)
    return NullOutput()
