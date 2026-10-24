"""Lenkautomatik: die Entscheidung, ob gelenkt werden darf.

Dies ist das einzige Modul, das eine Maschine bewegen kann. Es ist deshalb so
gebaut, dass es lieber ablehnt als versucht. Gelenkt wird nur, wenn alles
gleichzeitig stimmt:

* in der Konfigurationsdatei freigegeben - eine bewusste Handlung beim Einbau,
* vom Fahrer für diese Sitzung scharf geschaltet,
* eine Spur ist aktiv und die Maschine ist nah genug daran,
* der Fix ist gut genug - eine RTK-Float-Lösung springt um Dezimeter,
* die Geschwindigkeit liegt im eingestellten Fenster und die Positionsdaten
  sind frisch.

Fällt eine Bedingung weg, geht sofort ein Abschaltbefehl raus, samt Grund im
Klartext. Wie der Befehl bei der Mechanik ankommt, steht in actuators.py; die
dortigen Ausgänge halten den Motor selbstständig an, wenn von hier nichts mehr
kommt - ein abgestürztes Programm endet damit in "Fahrer übernimmt" und nicht
in einer Maschine, die den Einschlag hält.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Optional

from .actuators import NullOutput, SteerContext, SteerOutput
from .guidance import GuidanceState
from .nmea import Fix


@dataclass
class SteerCommand:
    engaged: bool = False
    angle_deg: float = 0.0
    reason: str = "nicht scharf"
    speed_ms: float = 0.0
    cross_track_m: float = 0.0

    def to_dict(self) -> dict:
        return {
            "engaged": self.engaged,
            "angle_deg": self.angle_deg,
            "reason": self.reason,
        }


# Abschalten, das zum Arbeiten gehört und nichts über die Lenkung sagt.
GEWOLLTE_GRUENDE = frozenset({"vom Fahrer ausgeschaltet", "zu langsam", "keine Spur aktiv",
                              "Rückwärtsfahrt", "System wird beendet", "nicht scharf", "Aktualisierung"})

# Pausieren statt Rausfliegen (Andy, 24.10.2026: „einmal bin ich zu schnell, dann zu
# langsam, dann das … ich möchte nicht immer rausfliegen“). Wie bei Cerea bleibt die
# Lenkung bei allem, was von selbst vorbeigeht, scharf und greift danach wieder:
# Tempo außerhalb des Fensters, kurz kein RTK/GPS, ein Ausreißer, zu weit neben der
# Spur, Rückwärtsfahrt. Ganz aus - neu scharf schalten - nur, wenn der Fahrer
# eingreift oder ausschaltet, der Lenkausgang gestört ist oder das GPS länger weg ist.
TEMPO_ENTPRELLUNG_S = 1.0   # so lange darf das Tempo kurz aus dem Fenster springen (GPS-Rauschen)


def grund_kurz(reason: str) -> str:
    """Grund ohne Messwerte, damit gleiche Gründe zusammen gezählt werden:
    „zu weit von der Spur (1.20 m)“ -> „zu weit von der Spur“."""
    grund = (reason or "unbekannt").split(" (")[0]
    for vorne in ("RTK nötig", "Lenkausgang gestört"):
        if grund.startswith(vorne):
            return vorne
    return grund


def tempo_grenze_deg(tempo_ms: float, radstand_m: float, max_quer_ms2: float) -> Optional[float]:
    """Größter Einschlag (Grad), der bei diesem Tempo höchstens ``max_quer_ms2`` Querbeschleunigung
    verlangt - oder None, wenn nichts begrenzt (aus, oder im Schritttempo).

    Querbeschleunigung im Einspurmodell: a = v² · tan(δ) / L. Ein GPS-Sprung oder ein
    Spurwechsel bei 15 km/h darf den Traktor nicht mit vollem Einschlag herumreißen.
    """
    if max_quer_ms2 <= 0 or tempo_ms < 0.5:
        return None
    return math.degrees(math.atan(max_quer_ms2 * max(0.5, radstand_m) / (tempo_ms * tempo_ms)))


class SteeringController:
    def __init__(self, config, output: Optional[SteerOutput] = None,
                 simulator: bool = False) -> None:
        self.config = config
        self.output = output or NullOutput()
        # Im Simulator gibt es keinen Motor, den man freigeben müsste: die
        # Engine schreibt den Einschlag direkt in den virtuellen Traktor.
        self.simulator = simulator
        self.armed = False           # vom Fahrer gesetzt, geht bei jedem Stopp weg
        self.rueckwaerts = False     # setzt die Engine aus dem Kurs; Rückwärts lenkt nichts
        self.rtk_ueberbrueckt = False
        self.tempo_begrenzt = False    # der Einschlag wurde wegen des Tempos gekappt
        self.tempo_grenze_deg: Optional[float] = None
        # Wie oft die Lenkung von sich aus abgegeben hat, während sie lenkte (nicht der
        # Fahrer, nicht das Vorgewende) - für die Genauigkeit je Arbeit (genauigkeit.py).
        self.abgaben = 0  # setzt die Engine: kurzer RTK-Ausfall wird überbrückt
        # ... und warum (Grund ohne Messwerte -> Anzahl), damit ein Feldtest zeigt, was abschaltet.
        self.abgabe_gruende: dict[str, int] = {}
        self.command = SteerCommand()
        self.last_sent_at = 0.0
        self.max_rate_deg_s = 25.0
        self.disengage_count = 0
        self._last_angle = 0.0
        self._last_angle_at = 0.0
        self._tempo_aus_seit: Optional[float] = None

    # -- Bedienung durch den Fahrer ---------------------------------------

    @property
    def freigegeben(self) -> bool:
        return bool(self.config.enabled or self.simulator)

    def arm(self) -> str:
        if not self.freigegeben:
            self.armed = False
            return "Lenkautomatik ist in der Konfiguration deaktiviert"
        if not self.output.ready:
            self.armed = False
            return f"Lenkausgang nicht bereit: {self.output.status}"
        self.armed = True
        self._last_angle = 0.0
        self._last_angle_at = 0.0
        self.output.on_arm()
        return "scharf"

    def _abgabe(self, reason: str) -> None:
        """Die Lenkung hat von sich aus aufgehört zu lenken: zählen, mit Grund."""
        if reason in GEWOLLTE_GRUENDE:
            return
        self.abgaben += 1
        grund = grund_kurz(reason)
        self.abgabe_gruende[grund] = self.abgabe_gruende.get(grund, 0) + 1

    def disarm(self, reason: str = "vom Fahrer ausgeschaltet") -> None:
        if self.armed:
            self.disengage_count += 1
            if self.command.engaged:
                self._abgabe(reason)
        self.armed = False
        self.command = SteerCommand(reason=reason)
        self.output.command(False, 0.0, SteerContext())

    def pausieren(self, reason: str) -> None:
        """Jetzt nicht lenken, aber scharf bleiben: geht es wieder, lenkt sie von selbst."""
        if self.command.engaged:
            self.disengage_count += 1
            self._abgabe(reason)
        self.command = SteerCommand(reason=reason if self.armed else self.command.reason)
        self._last_angle, self._last_angle_at = 0.0, 0.0
        self.output.command(False, 0.0, SteerContext())

    # -- Hauptschleife ----------------------------------------------------

    def update(self, guidance: GuidanceState, fix: Fix,
               context: Optional[SteerContext] = None,
               now: Optional[float] = None) -> SteerCommand:
        """Entscheiden, ob gelenkt wird - und wie weit."""
        now = now or time.time()
        context = context or SteerContext()
        context.speed_ms = fix.speed_ms
        context.cross_track_m = guidance.cross_track_m

        blocked = self._blocking_reason(guidance, fix, now)
        if blocked:
            if self.command.engaged:
                self.disengage_count += 1
                self._abgabe(blocked)
            self.command = SteerCommand(
                engaged=False, angle_deg=0.0, reason=blocked,
                speed_ms=fix.speed_ms, cross_track_m=guidance.cross_track_m,
            )
            self._last_angle, self._last_angle_at = 0.0, now
        else:
            ziel = guidance.steer_angle_deg
            grenze = tempo_grenze_deg(fix.speed_ms, context.wheelbase_m,
                                      getattr(self.config, "max_querbeschleunigung_ms2", 0.0))
            self.tempo_grenze_deg = grenze
            self.tempo_begrenzt = grenze is not None and abs(ziel) > grenze
            if self.tempo_begrenzt:
                ziel = math.copysign(grenze, ziel)
            self.command = SteerCommand(
                engaged=True,
                angle_deg=self._rate_limit(ziel, now),
                reason="lenkt",
                speed_ms=fix.speed_ms,
                cross_track_m=guidance.cross_track_m,
            )
        self.output.command(self.command.engaged, self.command.angle_deg, context)
        self.last_sent_at = now
        return self.command

    def _rate_limit(self, target: float, now: float) -> float:
        """Nicht mehr Bewegung verlangen, als die Mechanik liefern kann.

        Ein Lenkmotor braucht rund eine Sekunde von Anschlag zu Anschlag. Ein
        Regler, der sofort auf einen großen Winkel springt, verlangt etwas, das
        nicht passiert, und korrigiert dann über, wenn es verspätet ankommt.
        """
        dt = min(0.5, now - self._last_angle_at) if self._last_angle_at else 0.1
        limit = self.max_rate_deg_s * max(0.02, dt)
        delta = max(-limit, min(limit, target - self._last_angle))
        self._last_angle = self._last_angle + delta
        self._last_angle_at = now
        return self._last_angle

    def _blocking_reason(self, guidance: GuidanceState, fix: Fix,
                         now: float) -> Optional[str]:
        cfg = self.config
        feedback = self.output.feedback
        if not self.freigegeben:
            return "in der Konfiguration deaktiviert"
        if not self.armed:
            return "nicht scharf"
        if not self.output.ready:
            return f"Lenkausgang gestört: {self.output.status}"
        if feedback.fresh and feedback.driver_override:
            # Der Fahrer hat ins Lenkrad gegriffen. Sofort übergeben und aus
            # bleiben, bis neu geschärft wird - ein stilles Wiedereinschalten
            # mitten in einer Handkorrektur wäre die übelste Überraschung.
            self.armed = False
            return "Fahrer hat eingegriffen"
        if self.rueckwaerts:
            # Die Regelung ist für Vorwärtsfahrt gerechnet: rückwärts kehrt sich
            # die Wirkung des Einschlags um - also nicht lenken. Seit 1.5.80 bleibt
            # sie dabei scharf: vorwärts, nah an der Spur und im Tempofenster greift
            # sie wieder (vorher musste nach jedem Zurücksetzen neu geschärft werden).
            return "Rückwärtsfahrt"
        if not guidance.active:
            return "keine Spur aktiv"
        if not fix.valid:
            return "kein GPS-Fix"
        age = now - fix.received_at if fix.received_at else 99.0
        if age > 1.0:
            return f"GPS-Daten veraltet ({age:.1f} s)"
        if cfg.require_rtk and fix.rank < 4 and not self.rtk_ueberbrueckt:
            return f"RTK nötig, aktuell: {fix.fix_label}"
        tempo = ("zu langsam" if fix.speed_ms < cfg.min_speed_ms
                 else "zu schnell" if fix.speed_ms > cfg.max_speed_ms else None)
        if tempo is None:
            self._tempo_aus_seit = None
        else:
            # Ein Tempo-Sprung im GPS (Ausreißer, Schlupf) soll nicht abschalten:
            # erst, wenn es eine Sekunde lang außerhalb bleibt. Wer nicht lenkt,
            # fängt gar nicht erst an.
            if self._tempo_aus_seit is None:
                self._tempo_aus_seit = now
            if not self.command.engaged or now - self._tempo_aus_seit >= TEMPO_ENTPRELLUNG_S:
                return tempo
        if abs(guidance.cross_track_m) > cfg.max_cross_track_m:
            return f"zu weit von der Spur ({guidance.cross_track_m:.2f} m)"
        return None

    # -- Aufbau und Abbau -------------------------------------------------

    async def start(self) -> None:
        await self.output.start()

    async def stop(self) -> None:
        self.disarm("System wird beendet")
        await self.output.stop()

    def status(self) -> dict:
        feedback = self.output.feedback
        return {
            "configured": self.freigegeben,
            "simulator": self.simulator,
            "armed": self.armed,
            "command": self.command.to_dict(),
            "output": self.output.status_dict(),
            "wheel_angle_deg": feedback.wheel_angle_deg,
            "driver_override": feedback.driver_override and feedback.fresh,
            "duty": feedback.duty,
            "disengagements": self.disengage_count,
            "tempo_begrenzt": self.tempo_begrenzt and self.command.engaged,
            "tempo_grenze_deg": round(self.tempo_grenze_deg, 1) if self.tempo_grenze_deg is not None else None,
        }
