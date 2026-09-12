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


class SteeringController:
    def __init__(self, config, output: Optional[SteerOutput] = None) -> None:
        self.config = config
        self.output = output or NullOutput()
        self.armed = False           # vom Fahrer gesetzt, geht bei jedem Stopp weg
        self.command = SteerCommand()
        self.last_sent_at = 0.0
        self.max_rate_deg_s = 25.0
        self.disengage_count = 0
        self._last_angle = 0.0
        self._last_angle_at = 0.0

    # -- Bedienung durch den Fahrer ---------------------------------------

    def arm(self) -> str:
        if not self.config.enabled:
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

    def disarm(self, reason: str = "vom Fahrer ausgeschaltet") -> None:
        if self.armed:
            self.disengage_count += 1
        self.armed = False
        self.command = SteerCommand(reason=reason)
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
            self.command = SteerCommand(
                engaged=False, angle_deg=0.0, reason=blocked,
                speed_ms=fix.speed_ms, cross_track_m=guidance.cross_track_m,
            )
            self._last_angle, self._last_angle_at = 0.0, now
        else:
            self.command = SteerCommand(
                engaged=True,
                angle_deg=self._rate_limit(guidance.steer_angle_deg, now),
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
        if not cfg.enabled:
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
        if not guidance.active:
            return "keine Spur aktiv"
        if not fix.valid:
            return "kein GPS-Fix"
        age = now - fix.received_at if fix.received_at else 99.0
        if age > 1.0:
            return f"GPS-Daten veraltet ({age:.1f} s)"
        if cfg.require_rtk and fix.rank < 4:
            return f"RTK nötig, aktuell: {fix.fix_label}"
        if fix.speed_ms < cfg.min_speed_ms:
            return "zu langsam"
        if fix.speed_ms > cfg.max_speed_ms:
            return "zu schnell"
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
            "configured": self.config.enabled,
            "armed": self.armed,
            "command": self.command.to_dict(),
            "output": self.output.status_dict(),
            "wheel_angle_deg": feedback.wheel_angle_deg,
            "driver_override": feedback.driver_override and feedback.fresh,
            "duty": feedback.duty,
            "disengagements": self.disengage_count,
        }
