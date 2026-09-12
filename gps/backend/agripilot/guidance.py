"""Guidance: turn a position into "how far off the pass am I, and where do I steer".

Three pattern types cover almost all field work:

* AB line - two points define a direction, and the field is covered by parallel
  passes spaced one working width apart.
* Curve - a recorded track is repeated at one width spacing, for headlands and
  irregular fields.
* Contour - the stored field boundary itself is the pattern: ring 0 is the
  boundary, every further ring lies one working width further in.  No A/B point
  is needed, and the ring number does not depend on which way round the
  boundary happened to be recorded.

All three reduce to the same question: signed lateral distance to the nearest
pass.  Everything the driver sees (lightbar, centimetres off, pass number) and
everything the autosteer needs (steer angle) comes out of that one number plus
the heading error.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Literal, Optional, Sequence

from .geo import (
    Point,
    angle_difference,
    distance,
    heading_deg,
    normalize_heading,
    point_in_polygon,
    project_on_segment,
    simplify,
)

# "turn" ist kein Muster, das man anlegt - es ist der Zustand während einer
# geplanten Wende (siehe headland.py) und erscheint nur in der Ausgabe.
Mode = Literal["ab", "curve", "contour", "turn"]


def _forward_vector(heading: float) -> Point:
    h = math.radians(heading)
    return math.sin(h), math.cos(h)


def _right_vector(heading: float) -> Point:
    h = math.radians(heading)
    return math.cos(h), -math.sin(h)


@dataclass
class VehicleProfile:
    """Machine geometry.

    The antenna is never where the work happens.  Guidance is computed for the
    *tool* centre, otherwise every pass is offset by the antenna position and
    the overlap looks fine on screen while the field shows stripes.
    """

    name: str = "Traktor"
    width_m: float = 3.0            # working width of the implement
    overlap_m: float = 0.0          # deliberate overlap; reduces effective spacing
    wheelbase_m: float = 2.6
    antenna_forward_m: float = 1.2  # antenna ahead of the rear axle (+ = forward)
    antenna_right_m: float = 0.0    # antenna right of the centre line
    antenna_height_m: float = 3.0   # Boden bis Antenne - Maßstab des Hangausgleichs
    tool_offset_m: float = 0.0      # implement pulled off-centre (+ = right)
    tool_trailing_m: float = 0.0    # distance from rear axle back to the tool
    trailed: bool = False           # gezogenes Gerät statt starrer Anbau
    hitch_length_m: float = 4.0     # Zugpunkt bis Geräteachse (nur gezogen)
    max_steer_deg: float = 35.0
    steer_gain: float = 0.9         # Stanley k: higher = harder pull back to line
    steer_softening: float = 1.2    # m/s added to the denominator; tames low speed
    max_steer_rate_deg_s: float = 25.0  # how fast the steering actuator may move
    sections: int = 1               # number of switchable sections across the width

    # -- Abstimmung bei Tempo (aus dem Cerea-Handbuch, RWFVLIMIT/RWFVLFACTOR) --
    # "The autoguiding can go into resonance ... with increasing speed ... it is
    # necessary to lower the guiding aggressiveness." Dieselbe Einstellung, die
    # im Schritttempo sauber nachführt, schaukelt bei zwölf km/h auf: der Fehler
    # wird schneller eingefahren, als die Lenkung ihn abbauen kann.
    speed_gain_limit_kmh: float = 0.0   # 0 = aus; darüber beginnt die Absenkung
    speed_gain_rest: float = 0.55       # Restanteil bei doppelter Schwelle

    # Zeit zwischen der Position vom Empfänger und der tatsächlichen
    # Radbewegung (Cerea führt das als Aktordelay in seiner MPC-Methode).
    # Ohne Ausgleich läuft die Maschine in schnellen Kurven hinterher.
    actuator_latency_ms: int = 0

    # Anteil der Überdeckung, ab dem ein offenes Teilstück schließt (Cerea: pcc).
    section_pcc: float = 0.9

    @property
    def spacing_m(self) -> float:
        """Distance between passes: working width minus intentional overlap."""
        return max(0.1, self.width_m - self.overlap_m)

    def tool_position(self, antenna: Point, heading: float) -> Point:
        """Project the antenna position onto the tool centre.

        Rotate the (right, forward) offsets into the local frame along the
        vehicle heading, then subtract them from the antenna position.
        """
        h = math.radians(heading)
        forward = (math.sin(h), math.cos(h))
        right = (math.cos(h), -math.sin(h))
        # From antenna back to the rear axle, then back to the tool.
        back = self.antenna_forward_m + self.tool_trailing_m
        side = self.tool_offset_m - self.antenna_right_m
        return (
            antenna[0] - forward[0] * back + right[0] * side,
            antenna[1] - forward[1] * back + right[1] * side,
        )

    def steer_position(self, antenna: Point, heading: float) -> Point:
        """Der Punkt, auf den gelenkt wird: die Hinterachse, seitlich um den
        Geräteversatz verschoben - nie das Gerät selbst.

        Ein Punkt hinter der Achse wandert beim Einlenken erst zur falschen
        Seite (die Achse dreht, das Heck schwenkt aus). Ein Regler, der diesen
        Punkt auf die Spur zwingen soll, schaukelt sich auf: bei fünf Metern
        Abstand im Simulator auf ±3,5 m, mit der Lenkung im Sekundentakt an und
        aus. Auf gerader Spur läuft das Gerät ohnehin in der Achsspur, also
        reicht es, die Achse zu führen; ein seitlicher Geräteversatz wird als
        Versatz der Achse mitgenommen. Markiert wird weiter am Gerät.
        """
        forward = _forward_vector(heading)
        right = _right_vector(heading)
        side = self.tool_offset_m - self.antenna_right_m
        return (
            antenna[0] - forward[0] * self.antenna_forward_m + right[0] * side,
            antenna[1] - forward[1] * self.antenna_forward_m + right[1] * side,
        )

    def hitch_position(self, antenna: Point, heading: float) -> Point:
        """Der Zugpunkt - Mitte Hinterachse, seitlicher Antennenversatz heraus."""
        forward = _forward_vector(heading)
        right = _right_vector(heading)
        return (
            antenna[0] - forward[0] * self.antenna_forward_m
            - right[0] * self.antenna_right_m,
            antenna[1] - forward[1] * self.antenna_forward_m
            - right[1] * self.antenna_right_m,
        )

    def implement_position(self, antenna: Point, heading: float,
                           implement_heading: Optional[float] = None) -> Point:
        """Wo das Gerät steht - dort wird markiert.

        Beim starr angebauten Gerät ist das der Werkzeugpunkt aus
        ``tool_position``: es steht immer genau hinter dem Fahrzeug.

        Beim gezogenen Gerät nicht. Es hängt am Zugpunkt und richtet sich nach
        *seiner eigenen* Ausrichtung aus, die dem Fahrzeug nachläuft (siehe
        ``ImplementHeading``). In der Kurve steht es deshalb spürbar innerhalb
        der Fahrspur - genau der Unterschied, den ein starrer Versatz
        verschweigt und den man abends an den Streifen im Feld sieht.
        """
        if not self.trailed:
            return self.tool_position(antenna, heading)
        gezogen_kurs = heading if implement_heading is None else implement_heading
        hitch = self.hitch_position(antenna, heading)
        forward = _forward_vector(gezogen_kurs)
        right = _right_vector(gezogen_kurs)
        laenge = max(0.5, self.hitch_length_m)
        return (
            hitch[0] - forward[0] * laenge + right[0] * self.tool_offset_m,
            hitch[1] - forward[1] * laenge + right[1] * self.tool_offset_m,
        )


@dataclass
class GuidanceState:
    """Everything the cab display and the steering controller need."""

    active: bool = False
    mode: Mode = "ab"
    cross_track_m: float = 0.0     # + = vehicle right of the target pass
    pass_number: int = 0
    heading_error_deg: float = 0.0
    target_heading_deg: float = 0.0
    steer_angle_deg: float = 0.0   # + = steer right
    reversed_direction: bool = False
    distance_along_m: float = 0.0
    lightbar: int = 0              # LED offset, + = drift to the right
    message: str = ""
    # Was "rechts vom Fahrer" im Muster bedeutet: +1, wenn ein positiver Versatz
    # des Musters nach rechts des Fahrers geht, -1 sonst (rückwärts gefahrene
    # AB-Spur, Kontur gegen den Uhrzeigersinn). Damit "10 cm rechts" immer
    # rechts vom Sitz aus heißt - nicht rechts von A nach B, nicht "nach innen".
    right_sign: float = 1.0

    def to_dict(self) -> dict:
        return {
            "active": self.active,
            "mode": self.mode,
            "cross_track_m": self.cross_track_m,
            "cross_track_cm": self.cross_track_m * 100.0,
            "pass_number": self.pass_number,
            "heading_error_deg": self.heading_error_deg,
            "target_heading_deg": self.target_heading_deg,
            "steer_angle_deg": self.steer_angle_deg,
            "reversed": self.reversed_direction,
            "distance_along_m": self.distance_along_m,
            "lightbar": self.lightbar,
            "message": self.message,
        }


class GuidanceLine:
    """A reference pattern plus the passes derived from it."""

    def __init__(self, mode: Mode, points: Sequence[Point], spacing: float,
                 name: str = "", line_id: str = "", derived: bool = False) -> None:
        if len(points) < 2:
            raise ValueError("Eine Führungslinie braucht mindestens zwei Punkte")
        if mode == "contour" and len(points) < 3:
            raise ValueError("Die Kontur braucht eine Feldgrenze mit mindestens "
                             "drei Punkten")
        self.mode: Mode = mode
        self.name = name
        self.id = line_id
        # Abgeleitet heißt: entsteht bei jedem Laden neu aus etwas anderem (die
        # Kontur aus der Feldgrenze) und wird deshalb nicht als eigene Spur
        # gespeichert. Eine gespeicherte Kopie liefe der Grenze davon, sobald
        # jemand sie neu abfährt - und niemand würde es merken.
        self.derived = derived or mode == "contour"
        self.spacing = max(0.1, spacing)
        self.nudge_m = 0.0  # manual sideways trim of the whole pattern
        # Fahrgassen: alle fahrgasse_m Meter eine Spur, die frei bleibt und auf
        # der später gedüngt und gespritzt wird. Gehört zu einer Saison.
        self.fahrgasse_m = 0.0
        self.saison = 0
        if mode == "ab":
            self.points = [tuple(points[0]), tuple(points[-1])]
        else:
            self.points = [tuple(p) for p in simplify(points, 0.15)]

    # -- geometry ---------------------------------------------------------

    def _ab_lateral(self, p: Point) -> tuple[float, float, float]:
        """Signed distance from the infinite AB line, its heading, and progress."""
        a, b = self.points[0], self.points[-1]
        head = heading_deg(a, b)
        h = math.radians(head)
        forward = (math.sin(h), math.cos(h))
        right = (math.cos(h), -math.sin(h))
        dx, dy = p[0] - a[0], p[1] - a[1]
        lateral = dx * right[0] + dy * right[1]
        along = dx * forward[0] + dy * forward[1]
        return lateral, head, along

    def _curve_lateral(self, p: Point) -> tuple[float, float, float]:
        """Nearest point on the recorded track: distance, segment heading, progress."""
        best = (float("inf"), 0.0, 0.0, 0.0)
        travelled = 0.0
        for i in range(len(self.points) - 1):
            a, b = self.points[i], self.points[i + 1]
            seg_len = distance(a, b)
            foot, t, lateral = project_on_segment(p, a, b)
            d = distance(p, foot)
            if d < best[0]:
                best = (d, lateral, heading_deg(a, b), travelled + t * seg_len)
            travelled += seg_len
        _, lateral, head, along = best
        return lateral, head, along

    def _contour_lateral(self, p: Point) -> tuple[float, float, float, Point]:
        """Abstand zur Ringspur, gemessen **nach innen**.

        Die Feldgrenze ist ein geschlossener Ring, also wird auch das
        Schlussstück vom letzten zum ersten Punkt mitgerechnet - sonst hätte der
        Ring genau dort eine Lücke, an der die Aufzeichnung zufällig endete.

        Gemessen wird entlang der nach innen zeigenden Normalen, nicht "rechts
        der Fahrtrichtung". Damit hängt die Ringnummer nicht davon ab, ob die
        Grenze im oder gegen den Uhrzeigersinn abgefahren wurde: Ring 0 ist
        immer die Grenze, Ring 1 immer eine Arbeitsbreite weiter drinnen. Für
        den Lichtbalken rechnet ``solve`` das anschließend in die Fahrtrichtung
        zurück.
        """
        bester = (float("inf"), 0.0, 0.0, 0.0, 0.0)   # d, quer, kurs, weg, i
        gefahren = 0.0
        n = len(self.points)
        for i in range(n):
            a, b = self.points[i], self.points[(i + 1) % n]
            stueck = distance(a, b)
            fuss, t, quer = project_on_segment(p, a, b)
            d = distance(p, fuss)
            if d < bester[0]:
                bester = (d, quer, heading_deg(a, b), gefahren + t * stueck, i)
            gefahren += stueck
        _, quer, kurs, weg, i = bester

        # `quer` ist positiv rechts der Kantenrichtung. Ob das nach innen oder
        # nach außen zeigt, sagt eine Probe kurz neben dem Fußpunkt.
        a, b = self.points[int(i)], self.points[(int(i) + 1) % n]
        mitte = ((a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0)
        rx, ry = _right_vector(kurs)
        probe = (mitte[0] + rx * 0.25, mitte[1] + ry * 0.25)
        innen = (rx, ry) if point_in_polygon(probe, self.points) else (-rx, -ry)
        if innen[0] * rx + innen[1] * ry < 0:
            quer = -quer
        return quer, kurs, weg, innen

    def solve(self, position: Point, vehicle_heading: float,
              speed_ms: float, profile: VehicleProfile) -> GuidanceState:
        """Compute the guidance state for a tool position and heading."""
        innen: Optional[Point] = None
        if self.mode == "ab":
            lateral, line_heading, along = self._ab_lateral(position)
        elif self.mode == "contour":
            lateral, line_heading, along, innen = self._contour_lateral(position)
        else:
            lateral, line_heading, along = self._curve_lateral(position)

        lateral -= self.nudge_m

        # Which pass are we on, and how far off its centre?
        pass_number = round(lateral / self.spacing)
        cross_track = lateral - pass_number * self.spacing

        # Passes are driven in both directions.  Compare the heading against the
        # line and its reverse and keep whichever the driver is actually doing,
        # otherwise every second pass reports a 180 degree error.
        target = line_heading
        reversed_dir = abs(angle_difference(line_heading, vehicle_heading)) > 90.0
        if reversed_dir:
            target = normalize_heading(line_heading + 180.0)
        heading_error = angle_difference(target, vehicle_heading)

        # On a reversed pass "right of the line" flips too.
        right_sign = -1.0 if reversed_dir else 1.0
        if innen is not None:
            # Auf dem Ring wurde nach innen gemessen. Der Lichtbalken zeigt aber
            # "links/rechts der Spur" - also zurück in die Fahrtrichtung drehen.
            # Beide Vektoren stehen senkrecht auf derselben Kante, das Produkt
            # ist damit +1 oder -1: es kehrt das Vorzeichen um oder lässt es.
            rx, ry = _right_vector(target)
            right_sign = 1.0 if innen[0] * rx + innen[1] * ry >= 0 else -1.0
        signed_xte = cross_track * right_sign

        steer = self._steer_angle(signed_xte, heading_error, speed_ms, profile)

        return GuidanceState(
            active=True,
            mode=self.mode,
            cross_track_m=signed_xte,
            pass_number=pass_number,
            heading_error_deg=heading_error,
            target_heading_deg=target,
            steer_angle_deg=steer,
            reversed_direction=reversed_dir,
            distance_along_m=along,
            lightbar=lightbar_offset(signed_xte),
            right_sign=right_sign,
        )

    def _steer_angle(self, cross_track: float, heading_error: float,
                     speed_ms: float, profile: VehicleProfile) -> float:
        """Stanley controller.

        Two terms: line up with the pass (heading error) and close the remaining
        gap (cross track).  The gap term is divided by speed so the correction is
        gentle when fast and firm when crawling; the softening constant stops it
        from exploding as speed approaches zero.
        """
        faktor = speed_gain_factor(speed_ms, profile)
        approach = math.degrees(
            math.atan2(profile.steer_gain * faktor * cross_track,
                       abs(speed_ms) + profile.steer_softening)
        )
        steer = heading_error * faktor - approach
        limit = profile.max_steer_deg
        return max(-limit, min(limit, steer))

    # -- drawing helpers --------------------------------------------------

    def pass_geometry(self, centre_pass: int, count: int = 4,
                      length: float = 400.0) -> list[dict]:
        """Geometry of the neighbouring passes so the cab display can draw them."""
        result = []
        for offset in range(centre_pass - count, centre_pass + count + 1):
            # Ring -1 läge außerhalb der Feldgrenze. Ihn zu zeichnen hieße, eine
            # Spur anzubieten, auf der nicht gearbeitet wird.
            if self.mode == "contour" and offset < 0:
                continue
            shift = offset * self.spacing + self.nudge_m
            punkte = self._shift(shift, length)
            if not punkte:
                continue
            result.append({
                "pass": offset,
                "points": [list(p) for p in punkte],
                "closed": self.mode == "contour",
            })
        return result

    def _inward(self) -> float:
        """+1, wenn die Linksnormale nach innen zeigt, sonst -1.

        Aus dem Umlaufsinn der gespeicherten Grenze, einmal je Zeichnung.
        """
        n = len(self.points)
        flaeche = sum(self.points[i][0] * self.points[(i + 1) % n][1]
                      - self.points[(i + 1) % n][0] * self.points[i][1]
                      for i in range(n))
        return 1.0 if flaeche > 0 else -1.0

    def _shift(self, shift: float, length: float) -> list[Point]:
        if self.mode == "ab":
            a, b = self.points[0], self.points[-1]
            head = math.radians(heading_deg(a, b))
            forward = (math.sin(head), math.cos(head))
            right = (math.cos(head), -math.sin(head))
            mid = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
            half = max(length, distance(a, b)) / 2
            base = [
                (mid[0] - forward[0] * half, mid[1] - forward[1] * half),
                (mid[0] + forward[0] * half, mid[1] + forward[1] * half),
            ]
            return [(x + right[0] * shift, y + right[1] * shift) for x, y in base]
        pts = self.points
        out: list[Point] = []
        if self.mode == "contour":
            # Ring: geschlossen versetzen, und zwar nach innen. Der Umlaufsinn
            # der Grenze wird dabei herausgerechnet, sonst wäre Ring 1 mal
            # drinnen und mal draußen - je nachdem, wie herum jemand das Feld
            # abgefahren hat.
            nach_innen = -self._inward()
            n = len(pts)
            for i, p in enumerate(pts):
                a, b = pts[(i - 1) % n], pts[(i + 1) % n]
                right = _right_vector(heading_deg(a, b))
                out.append((p[0] + right[0] * shift * nach_innen,
                            p[1] + right[1] * shift * nach_innen))
            return out
        # Curve: offset each vertex along the local normal.  Good enough for
        # drawing; the guidance maths above never relies on it.
        for i, p in enumerate(pts):
            a = pts[max(0, i - 1)]
            b = pts[min(len(pts) - 1, i + 1)]
            right = _right_vector(heading_deg(a, b))
            out.append((p[0] + right[0] * shift, p[1] + right[1] * shift))
        return out

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "mode": self.mode,
            "points": [list(p) for p in self.points],
            "spacing_m": self.spacing,
            "nudge_m": self.nudge_m,
            "derived": self.derived,
            "fahrgasse_m": self.fahrgasse_m,
            "saison": self.saison,
            # Jede wievielte Spur eine Fahrgasse ist - 0 heißt keine.
            "fahrgasse_jede": self.fahrgasse_jede(),
        }

    def fahrgasse_jede(self) -> int:
        """Jede wievielte Spur eine Fahrgasse ist (0 = keine Fahrgassen)."""
        if self.fahrgasse_m <= 0 or self.spacing <= 0:
            return 0
        return max(1, round(self.fahrgasse_m / self.spacing))

    def ist_fahrgasse(self, pass_number: int) -> bool:
        jede = self.fahrgasse_jede()
        return jede > 0 and pass_number % jede == 0


def speed_gain_factor(speed_ms: float, profile: VehicleProfile) -> float:
    """Wie stark bei Tempo abgesenkt wird - 1.0 heißt: unverändert.

    Linear zwischen Schwelle und doppelter Schwelle, danach bleibt es beim
    Restanteil. Absichtlich keine Kurve: ein Wert, den man im Feld nachvollziehen
    kann, ist mehr wert als einer, der sich besser liest.
    """
    grenze = profile.speed_gain_limit_kmh
    if grenze <= 0.0:
        return 1.0
    kmh = abs(speed_ms) * 3.6
    if kmh <= grenze:
        return 1.0
    anteil = min(1.0, (kmh - grenze) / grenze)
    rest = max(0.05, min(1.0, profile.speed_gain_rest))
    return 1.0 - anteil * (1.0 - rest)


def lightbar_offset(cross_track_m: float, led_cm: float = 5.0,
                    leds: int = 10) -> int:
    """Map centimetres of error onto lightbar LEDs.

    5 cm per LED is a useful compromise: with RTK it shows real drift, with a
    plain GPS receiver it does not flicker across the whole bar.
    """
    steps = int(round(cross_track_m * 100.0 / led_cm))
    return max(-leds, min(leds, steps))


@dataclass
class ImplementHeading:
    """Die nachlaufende Ausrichtung eines gezogenen Geräts.

    Ein angebautes Gerät dreht sich mit dem Fahrzeug; ein gezogenes nicht. Es
    hängt an der Deichsel und schwenkt erst ein, während es gezogen wird - das
    klassische Anhängernachlaufmodell:

        Drehrate = Geschwindigkeit / Deichsellänge · sin(Winkel zum Fahrzeug)

    Zwei Dinge fallen daraus heraus, und beide entsprechen der Erfahrung: im
    Stand dreht sich nichts, egal wie sehr am Lenkrad gedreht wird, und je
    kürzer die Deichsel, desto schneller folgt das Gerät.

    Die Begrenzung der Drehrate ist keine Physik, sondern Vorsicht: bei einer
    sehr kurzen Deichsel und einem großen Zeitschritt würde das Modell sonst
    über die Fahrzeugausrichtung hinausschießen und anfangen zu schwingen -
    ein Rechenfehler, der auf dem Bildschirm wie ein schlingerndes Gerät
    aussähe.
    """

    max_rate_deg_s: float = 170.0
    value: Optional[float] = None

    def reset(self, heading: Optional[float] = None) -> None:
        """Nach einem Positionssprung: lieber neu anfangen als weiterschleppen."""
        self.value = heading

    def update(self, vehicle_heading: float, speed_ms: float, dt: float,
               profile: VehicleProfile) -> float:
        if self.value is None:
            self.value = vehicle_heading
        if not profile.trailed:
            self.value = vehicle_heading
            return self.value
        if dt <= 0.0 or dt > 5.0:
            return self.value
        laenge = max(0.5, profile.hitch_length_m)
        winkel = angle_difference(vehicle_heading, self.value)
        rate = math.degrees(abs(speed_ms) / laenge * math.sin(math.radians(winkel)))
        rate = max(-self.max_rate_deg_s, min(self.max_rate_deg_s, rate))
        self.value = normalize_heading(self.value + rate * dt)
        return self.value


@dataclass
class HeadingFilter:
    """Produces a usable heading from noisy inputs.

    Course over ground is derived from movement, so it is garbage below walking
    pace - exactly when the driver is lining up at the headland.  This keeps the
    last good heading while slow, prefers a dual-antenna heading when present,
    and smooths the rest.
    """

    min_speed_ms: float = 0.5
    smoothing: float = 0.35
    value: Optional[float] = None
    _history: list[float] = field(default_factory=list)

    def update(self, course: Optional[float], true_heading: Optional[float],
               speed_ms: float, yaw_rate_deg_s: Optional[float] = None,
               dt: float = 0.0) -> Optional[float]:
        if true_heading is not None:
            self.value = true_heading
            return self.value
        if course is None or speed_ms < self.min_speed_ms:
            # Zu langsam für einen Kurs aus der Bewegung. Liegt eine Drehrate
            # vom Neigungssensor an, wird der letzte gute Kurs damit
            # weitergeschrieben - sonst dreht sich am Vorgewende die Anzeige
            # nicht mit, obwohl der Traktor längst quer steht.
            if (yaw_rate_deg_s is not None and self.value is not None
                    and 0.0 < dt < 1.0):
                self.value = normalize_heading(self.value + yaw_rate_deg_s * dt)
            return self.value
        if self.value is None:
            self.value = course
            return self.value
        # Smooth on the shortest path so 359 -> 001 does not swing the long way.
        delta = angle_difference(course, self.value)
        self.value = normalize_heading(self.value + delta * self.smoothing)
        return self.value
