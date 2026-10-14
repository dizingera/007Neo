"""Phidget21: die ältere Bibliothek, mit der die MotorControl 1065 am Traktor läuft.

Auf dem Tablet ist der Phidget21-Treiber installiert, nicht Phidget22 - so
läuft die 1065 schon mit dem AgriPilot Terminal (C#), direkt über USB, ohne
COM-Port. Diese Datei spricht die ``phidget21.dll`` mit ``ctypes`` an: es
braucht kein weiteres Python-Paket, nur den Treiber, der ohnehin drauf ist.

Nach außen sieht ein geöffnetes Gerät aus wie die Phidget22-Klassen, die
``actuators.PhidgetOutput`` benutzt (``setTargetVelocity``, ``getPosition``,
``getVoltageRatio`` ...). So bleibt der Regelkreis derselbe, nur die unterste
Schicht ist eine andere.

Was die 1065 unter Phidget21 nicht kann - und was das heißt:

* **Kein Failsafe.** Die Platine hält den Motor nicht selbst an, wenn das
  Programm verstummt. Beim Beenden und beim Aufräumen wird die Geschwindigkeit
  ausdrücklich auf null gesetzt (auch über ``atexit``); gegen einen harten
  Absturz hilft nur der Not-Aus. Der ist hier Pflicht.
* **Keine Stromgrenze** zum Einstellen. Das Lenkrad bleibt von Hand
  übersteuerbar über die Motor- und Getriebeauslegung, nicht über Software.
* **Kein Positionsregler.** Geregelt wird immer in AgriPilot (eigener
  Regelkreis), mit dem Drehgeber am Motor oder dem Radwinkelsensor.
"""

from __future__ import annotations

import atexit
import ctypes
import os
import sys
from typing import Optional

_DLL = None
_DLL_FEHLER = ""
_OFFEN: list["MotorControl21"] = []

ANSCHLUSS_MS = 5000


class Phidget21Fehler(RuntimeError):
    pass


def _kandidaten() -> list[str]:
    if sys.platform.startswith("win"):
        programme = [os.environ.get("ProgramFiles", r"C:\Program Files"),
                     os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")]
        return ["phidget21.dll"] + [os.path.join(p, "Phidgets", "phidget21.dll") for p in programme if p]
    if sys.platform == "darwin":
        return ["/Library/Frameworks/Phidget21.framework/Versions/Current/Phidget21"]
    return ["libphidget21.so.0", "libphidget21.so"]


def _laden():
    """Die DLL einmal laden. Unter Windows mit stdcall (WinDLL), wie der Treiber gebaut ist."""
    global _DLL, _DLL_FEHLER
    if _DLL is not None or _DLL_FEHLER:
        return _DLL
    lader = ctypes.WinDLL if sys.platform.startswith("win") else ctypes.CDLL
    fehler = []
    for pfad in _kandidaten():
        try:
            _DLL = lader(pfad)
            break
        except OSError as exc:
            fehler.append(f"{pfad}: {exc}")
    if _DLL is None:
        _DLL_FEHLER = "Phidget21-Treiber nicht gefunden (" + "; ".join(fehler[:2]) + ")"
    return _DLL


def verfuegbar() -> tuple[bool, str]:
    """Ist der Phidget21-Treiber da? Prüft über die Versionsabfrage, ohne ein Gerät zu öffnen."""
    dll = _laden()
    if dll is None:
        return False, _DLL_FEHLER
    try:
        text = ctypes.c_char_p()
        dll.CPhidget_getLibraryVersion(ctypes.byref(text))
    except Exception as exc:  # noqa: BLE001
        return False, f"Phidget21-Treiber unbrauchbar ({exc})"
    return True, ""


def bibliotheksversion() -> str:
    dll = _laden()
    if dll is None:
        return ""
    text = ctypes.c_char_p()
    dll.CPhidget_getLibraryVersion(ctypes.byref(text))
    return (text.value or b"").decode("latin-1", "replace")


def _pruefen(dll, code: int, was: str) -> None:
    if code == 0:
        return
    text = ctypes.c_char_p()
    try:
        dll.CPhidget_getErrorDescription(code, ctypes.byref(text))
        beschreibung = (text.value or b"").decode("latin-1", "replace")
    except Exception:  # noqa: BLE001
        beschreibung = ""
    raise Phidget21Fehler(f"{was}: Phidget21-Fehler {code}" + (f" ({beschreibung})" if beschreibung else ""))


class MotorControl21:
    """Eine Phidget-MotorControl (1060/1064/1065) über Phidget21."""

    def __init__(self, dll=None) -> None:
        self.dll = dll or _laden()
        if self.dll is None:
            raise Phidget21Fehler(_DLL_FEHLER or "Phidget21-Treiber nicht gefunden")
        self.handle = ctypes.c_void_p()
        _pruefen(self.dll, self.dll.CPhidgetMotorControl_create(ctypes.byref(self.handle)), "Anlegen")
        self.offen = False

    # -- Verbindung -------------------------------------------------------

    def oeffnen(self, seriennummer: int = -1, warten_ms: int = ANSCHLUSS_MS) -> None:
        _pruefen(self.dll, self.dll.CPhidget_open(self.handle, int(seriennummer)), "Öffnen")
        self.offen = True
        _OFFEN.append(self)
        try:
            _pruefen(self.dll, self.dll.CPhidget_waitForAttachment(self.handle, int(warten_ms)),
                     "Warten auf die Motorsteuerung")
        except Phidget21Fehler:
            self.schliessen()
            raise

    def schliessen(self) -> None:
        if not self.offen:
            return
        try:
            for i in range(max(1, self._zahl("CPhidgetMotorControl_getMotorCount"))):
                self.dll.CPhidgetMotorControl_setVelocity(self.handle, i, ctypes.c_double(0.0))
        except Exception:  # noqa: BLE001
            pass
        try:
            self.dll.CPhidget_close(self.handle)
            self.dll.CPhidget_delete(self.handle)
        except Exception:  # noqa: BLE001
            pass
        self.offen = False
        if self in _OFFEN:
            _OFFEN.remove(self)

    # -- Auskunft ---------------------------------------------------------

    def _zahl(self, funktion: str, *index: int) -> int:
        wert = ctypes.c_int()
        _pruefen(self.dll, getattr(self.dll, funktion)(self.handle, *index, ctypes.byref(wert)), funktion)
        return wert.value

    def _kommazahl(self, funktion: str, *index: int) -> float:
        wert = ctypes.c_double()
        _pruefen(self.dll, getattr(self.dll, funktion)(self.handle, *index, ctypes.byref(wert)), funktion)
        return wert.value

    def seriennummer(self) -> int:
        return self._zahl("CPhidget_getSerialNumber")

    def name(self) -> str:
        text = ctypes.c_char_p()
        _pruefen(self.dll, self.dll.CPhidget_getDeviceName(self.handle, ctypes.byref(text)), "Name")
        return (text.value or b"").decode("latin-1", "replace")

    def anzahl(self) -> dict:
        ergebnis = {}
        for schluessel, funktion in (("motoren", "CPhidgetMotorControl_getMotorCount"),
                                     ("drehgeber", "CPhidgetMotorControl_getEncoderCount"),
                                     ("sensoren", "CPhidgetMotorControl_getSensorCount")):
            try:
                ergebnis[schluessel] = self._zahl(funktion)
            except Exception:  # noqa: BLE001 - ältere Platinen kennen nicht alles
                ergebnis[schluessel] = 0
        return ergebnis

    # -- Motor ------------------------------------------------------------

    def geschwindigkeit(self, kanal: int, anteil: float) -> None:
        """Anteil -1..1 der vollen Leistung; Phidget21 rechnet in Prozent."""
        prozent = max(-100.0, min(100.0, anteil * 100.0))
        _pruefen(self.dll, self.dll.CPhidgetMotorControl_setVelocity(
            self.handle, int(kanal), ctypes.c_double(prozent)), "Geschwindigkeit")

    def beschleunigung_grenzen(self, kanal: int) -> tuple[float, float]:
        return (self._kommazahl("CPhidgetMotorControl_getAccelerationMin", int(kanal)),
                self._kommazahl("CPhidgetMotorControl_getAccelerationMax", int(kanal)))

    def beschleunigung(self, kanal: int, prozent_je_s: float) -> None:
        _pruefen(self.dll, self.dll.CPhidgetMotorControl_setAcceleration(
            self.handle, int(kanal), ctypes.c_double(prozent_je_s)), "Beschleunigung")

    def drehgeber(self, kanal: int) -> int:
        return self._zahl("CPhidgetMotorControl_getEncoderPosition", int(kanal))

    def sensor(self, kanal: int) -> int:
        """Analogeingang 0..1000."""
        return self._zahl("CPhidgetMotorControl_getSensorValue", int(kanal))

    def strom(self, kanal: int) -> float:
        return self._kommazahl("CPhidgetMotorControl_getCurrent", int(kanal))


@atexit.register
def _alles_anhalten() -> None:
    """Beim Beenden jeden Motor anhalten - die 1065 hat keinen Failsafe."""
    for geraet in list(_OFFEN):
        geraet.schliessen()


def suchen(warten_ms: int = 1500) -> Optional[dict]:
    """Die erste angesteckte MotorControl finden: Seriennummer, Name, Kanäle.

    Öffnet das Gerät kurz und gibt es wieder frei - ein anderes Programm (das
    AgriPilot Terminal) darf es in der Zeit nicht offen haben.
    """
    geraet = MotorControl21()
    try:
        geraet.oeffnen(-1, warten_ms)
    except Phidget21Fehler:
        return None
    try:
        return {"seriennummer": geraet.seriennummer(), "name": geraet.name(), **geraet.anzahl()}
    finally:
        geraet.schliessen()


# -- Phidget22-Ansicht: was actuators.PhidgetOutput aufruft ---------------

class Motor:
    """DCMotor-artige Sicht auf einen Kanal der MotorControl."""

    def __init__(self, geraet: MotorControl21, kanal: int) -> None:
        self.geraet, self.kanal = geraet, kanal

    def setTargetVelocity(self, anteil: float) -> None:  # noqa: N802 - wie Phidget22
        self.geraet.geschwindigkeit(self.kanal, anteil)

    # Phidget22 rechnet die Beschleunigung als Anteil je Sekunde, Phidget21 in
    # Prozent je Sekunde - deshalb hier mal hundert.
    def getMinAcceleration(self) -> float:  # noqa: N802
        return self.geraet.beschleunigung_grenzen(self.kanal)[0] / 100.0

    def getMaxAcceleration(self) -> float:  # noqa: N802
        return self.geraet.beschleunigung_grenzen(self.kanal)[1] / 100.0

    def setAcceleration(self, anteil_je_s: float) -> None:  # noqa: N802
        self.geraet.beschleunigung(self.kanal, anteil_je_s * 100.0)

    def getMinCurrentLimit(self) -> float:  # noqa: N802
        return 0.0

    def getMaxCurrentLimit(self) -> float:  # noqa: N802
        return 100.0

    def setCurrentLimit(self, _ampere: float) -> None:  # noqa: N802 - unter Phidget21 nicht einstellbar
        return None

    def enableFailsafe(self, _ms: int) -> None:  # noqa: N802
        raise Phidget21Fehler("Die MotorControl kennt unter Phidget21 keinen Failsafe")

    def resetFailsafe(self) -> None:  # noqa: N802
        return None

    def getDeviceSerialNumber(self) -> int:  # noqa: N802
        return self.geraet.seriennummer()

    def close(self) -> None:
        self.geraet.schliessen()


class Drehgeber:
    def __init__(self, geraet: MotorControl21, kanal: int) -> None:
        self.geraet, self.kanal = geraet, kanal

    def getPosition(self) -> int:  # noqa: N802
        return self.geraet.drehgeber(self.kanal)

    def setEnabled(self, _an: bool) -> None:  # noqa: N802
        return None

    def close(self) -> None:
        return None              # das Gerät schließt der Motor


class Spannungsverhaeltnis:
    """Radwinkelsensor am Analogeingang: 0..1000 als Verhältnis 0..1."""

    def __init__(self, geraet: MotorControl21, kanal: int) -> None:
        self.geraet, self.kanal = geraet, kanal

    def getVoltageRatio(self) -> float:  # noqa: N802
        return self.geraet.sensor(self.kanal) / 1000.0

    def close(self) -> None:
        return None
