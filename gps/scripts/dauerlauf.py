#!/usr/bin/env python3
"""Dauerlauf: Stunden Feldarbeit im Zeitraffer, mit Störungen, ohne Hardware.

    python scripts/dauerlauf.py [--stunden 3] [--seed 1] [--ohne-stoerungen]

Ein virtueller Traktor (Einspurmodell mit träger Lenkung) fährt mit
eingeschalteter Lenkautomatik Bahn um Bahn über ein Feld, wendet am Vorgewende
und markiert die Fläche. Dazwischen passiert, was im Feld passiert:

* RTK fällt auf Float oder ganz aus,
* der Empfänger schweigt ein paar Sekunden,
* eine Position springt (Wiedereinfang nach Baumreihe),
* der Traktor setzt zurück,
* der Fahrer greift ein und schaltet die Lenkung aus,
* das Programm wird mitten in der Arbeit beendet und neu gestartet.

Die Uhr ist virtuell: ``time.time`` wird ersetzt, drei Stunden Arbeit dauern
wenige Minuten. Geprüft wird laufend, nicht am Ende - bei der ersten verletzten
Regel steht die genaue Stelle im Bericht:

  1. Lenkt die Automatik, dann nur mit RTK-fix, frischen Daten, im erlaubten
     Tempo und höchstens ``max_cross_track_m`` neben der Spur.
  2. Nach einem Ausfall (kein RTK, keine Daten) lenkt sie innerhalb einer Sekunde
     nicht mehr.
  3. Nach einem Neustart ist die Lenkung unscharf.
  4. Rückwärts lenkt sie nicht, und der Kurs zeigt trotzdem in Fahrzeugrichtung.
  5. Die bearbeitete Fläche geht über einen Neustart nicht verloren.
  6. Auf der Geraden bleibt der Spurfehler klein (Mittel und Spitze werden
     gemeldet).
  7. Kein Programmfehler, keine einzelne Position dauert länger als eine Sekunde.

Exit-Code 0 = alles eingehalten, 1 = Regel verletzt.
"""

from __future__ import annotations

import argparse
import math
import random
import re
import sys
import tempfile
import time
import types
from pathlib import Path

HIER = Path(__file__).resolve().parent
sys.path.insert(0, str(HIER.parent / "backend"))

from agripilot import config as config_module  # noqa: E402
from agripilot import geo, nmea  # noqa: E402
from agripilot.actuators import NullOutput  # noqa: E402
from agripilot.engine import Engine  # noqa: E402
from agripilot.steering import SteeringController  # noqa: E402
from agripilot.storage import Storage  # noqa: E402

HZ = 10.0
DT = 1.0 / HZ
DATUM = (48.1372, 11.5756)
FELD_BREITE_M = 300.0
FELD_LAENGE_M = 400.0
ARBEITSBREITE_M = 6.0
TEMPO_MS = 2.8
WENDE_TEMPO_MS = 1.6
LENKRATE_GRAD_S = 25.0            # so schnell stellt die Mechanik
GYRO_FEHLER_GRAD_S = 0.8          # ein ehrlicher, nicht perfekter Sensor


class Uhr:
    """Virtuelle Zeit - ersetzt time.time, damit Stunden in Minuten laufen."""

    def __init__(self, start: float) -> None:
        self.jetzt = start

    def __call__(self) -> float:
        return self.jetzt


class Fahrzeug:
    """Einspurmodell mit träger Lenkung."""

    def __init__(self, east: float, north: float, heading: float) -> None:
        self.east, self.north, self.heading = east, north, heading
        self.lenk_ist = 0.0
        self.lenk_soll = 0.0
        self.tempo = TEMPO_MS          # negativ = rückwärts
        self.gierrate = 0.0

    def schritt(self) -> None:
        delta = self.lenk_soll - self.lenk_ist
        grenze = LENKRATE_GRAD_S * DT
        self.lenk_ist += max(-grenze, min(grenze, delta))
        self.gierrate = math.degrees(
            self.tempo / 2.6 * math.tan(math.radians(self.lenk_ist)))
        self.heading = (self.heading + self.gierrate * DT) % 360.0
        h = math.radians(self.heading)
        self.east += math.sin(h) * self.tempo * DT
        self.north += math.cos(h) * self.tempo * DT


class Sensor:
    """Was der Neigungssensor meldet: Drehrate mit Fehler, sonst nichts."""

    def __init__(self) -> None:
        self.attitude = types.SimpleNamespace(
            fresh=True, yaw_rate_deg_s=0.0, roll_deg=0.0, pitch_deg=0.0)


class Verstoss(Exception):
    pass


class Lauf:
    def __init__(self, args) -> None:
        self.args = args
        self.zufall = random.Random(args.seed)
        self.ordner = tempfile.TemporaryDirectory()
        self.db = str(Path(self.ordner.name) / "dauerlauf.db")
        self.uhr = Uhr(time.time())
        self.echte_zeit = time.time
        time.time = self.uhr
        self.ebene = geo.LocalPlane(*DATUM)
        self.verstoesse: list[str] = []
        self.zaehler = {"rtk_float": 0, "rtk_aus": 0, "schweigen": 0, "sprung": 0,
                        "rueckwaerts": 0, "eingriff": 0, "neustart": 0, "wenden": 0,
                        "position": 0}
        self.spurfehler: list[float] = []
        self.laengste_position_s = 0.0
        self.letzte_flaeche = 0.0
        self.bahnen = 0
        self.wanderrichtung = -1       # -1: die Bahnen wandern nach Westen
        self.umkehrungen = 0
        self.von_hand = 0
        self.langsamster = ""
        self._rueck_zuletzt = False
        self.verlauf: list[str] = []

        self.fahrzeug = Fahrzeug(FELD_BREITE_M - 6.0, 30.0, 0.0)
        self.sensor = Sensor()
        self.rtk = 4
        self.stumm_bis = 0.0
        self.rueckwaerts_bis = 0.0
        self.sprung_offset = (0.0, 0.0)
        self.ausfall_seit: float | None = None
        self.naechster_eingriff = self._naechster(120.0)
        self.neustart_faellig = False
        self.nach_neustart = False
        self.store = None
        self.motor = None
        self.lenkung = None
        self.rearm_ab = 0.0
        self.rueck_seit = None
        self.rad_frei_seit = None
        self.fahrer_hat_geschaerft = False
        self.zuletzt_gemeldet: dict[str, float] = {}

    # -- Aufbau ---------------------------------------------------------

    def _naechster(self, mittel: float) -> float:
        return self.uhr() + self.zufall.uniform(0.5, 1.5) * mittel

    def starten(self, neu: bool = True) -> None:
        self.store = Storage(self.db)
        config = config_module.load("/kein-solcher-pfad.yaml")
        config.network.device_id = "dauerlauf"
        config.steering.enabled = True
        config.steering.output = "none"
        self.motor = Engine(config, self.store)
        self.motor.imu = self.sensor
        ausgang = NullOutput()
        ausgang.ready = True
        self.lenkung = SteeringController(config.steering, ausgang)
        self.motor.steering = self.lenkung
        self.motor.simulator = types.SimpleNamespace(
            set_steer=lambda grad: setattr(self.fahrzeug, "lenk_soll", grad))
        self.config = config
        if neu:
            rand = [[0.0, 0.0], [FELD_BREITE_M, 0.0], [FELD_BREITE_M, FELD_LAENGE_M],
                    [0.0, FELD_LAENGE_M]]
            feld = self.store.save_field({
                "name": "Dauerlauf", "datum_lat": DATUM[0], "datum_lon": DATUM[1],
                "boundary": rand})
            self.motor.load_field(feld["id"])
            self.motor.update_profile({"width_m": ARBEITSBREITE_M,
                                       "antenna_forward_m": 0.0, "tool_trailing_m": 0.0,
                                       "wheelbase_m": 2.6})
            # Wendekreis knapp über dem, was die Maschine schafft: 2,6 m Radstand
            # und 35 Grad Einschlag geben 3,7 m.
            self.motor.update_headland({"aktiv": True, "muster": "omega",
                                        "richtung": "links", "radius_m": 4.2})
            self.motor.on_fix(self._fix(self.uhr()))
            self.motor.set_ab_from_heading(0.0, "Nord")
            self.motor.start_job("Dauerlauf")
        else:
            meldung = self.motor.resume_session()
            if meldung is None:
                self.melde("Neustart: nichts fortgesetzt")
        self.nach_neustart = not neu
        self.fahrer_hat_geschaerft = False
        # Das Rechnen mit dem Geräteabstand: ein Neustart dauert hier eine Sekunde.

    def log(self, text: str) -> None:
        self.verlauf.append(f"{(self.uhr() - self.start_zeit) / 60:.2f} min {text}")
        del self.verlauf[:-4]
        if self.args.ausfuehrlich:
            print(f"  [{(self.uhr() - self.start_zeit) / 60:6.1f} min] {text}")

    def melde(self, text: str) -> None:
        # Derselbe Verstoß in jedem Schritt wäre nur Lärm: einmal je fünf Sekunden.
        schluessel = re.sub(r"[-+]?\d+(\.\d+)?", "#", text.split(";")[0])
        if self.uhr() - self.zuletzt_gemeldet.get(schluessel, -1e9) < 5.0:
            return
        self.zuletzt_gemeldet[schluessel] = self.uhr()
        stunde = (self.uhr() - self.start_zeit) / 3600.0
        self.verstoesse.append(f"[{stunde:5.2f} h] {text}")
        self.hart = True

    # -- Messwerte ------------------------------------------------------

    def _fix(self, jetzt: float) -> nmea.Fix:
        f = self.fahrzeug
        rauschen = 0.015
        ost = f.east + self.zufall.gauss(0, rauschen) + self.sprung_offset[0]
        nord = f.north + self.zufall.gauss(0, rauschen) + self.sprung_offset[1]
        lat, lon = self.ebene.to_wgs(ost, nord)
        kurs = f.heading if f.tempo >= 0 else (f.heading + 180.0) % 360.0
        fix = nmea.Fix(lat=lat, lon=lon, altitude=520.0, speed_ms=abs(f.tempo),
                       course_deg=kurs, fix_quality=self.rtk, satellites=22, hdop=0.6)
        fix.received_at = jetzt
        return fix

    # -- Störungen ------------------------------------------------------

    def stoerung_planen(self) -> None:
        if self.args.ohne_stoerungen or self.uhr() < self.naechste_stoerung:
            return
        art = self.zufall.choice(["rtk_float", "rtk_aus", "schweigen", "sprung",
                                  "rueckwaerts", "eingriff", "neustart"])
        jetzt = self.uhr()
        self.naechste_stoerung = self._naechster(150.0)
        self.zaehler[art] += 1
        self.log(f"Störung: {art}  (Pos {self.fahrzeug.east:.0f}/{self.fahrzeug.north:.0f}, "
                 f"Kurs {self.fahrzeug.heading:.0f})")
        if art == "rtk_float":
            self.rtk = 5
            self.rtk_wieder = jetzt + self.zufall.uniform(5, 20)
        elif art == "rtk_aus":
            self.rtk = 1
            self.rtk_wieder = jetzt + self.zufall.uniform(5, 25)
        elif art == "schweigen":
            self.stumm_bis = jetzt + self.zufall.uniform(2.5, 6.0)
        elif art == "sprung":
            self.sprung_offset = (self.zufall.uniform(-30, 30), self.zufall.uniform(-30, 30))
            self.sprung_ende = jetzt + 0.1       # eine einzelne falsche Position
        elif art == "rueckwaerts":
            self.rueckwaerts_bis = jetzt + self.zufall.uniform(5, 9)
        elif art == "eingriff":
            self.lenkung.disarm("vom Fahrer ausgeschaltet")
        elif art == "neustart":
            self.neustart_faellig = True

    def stoerung_ende(self) -> None:
        jetzt = self.uhr()
        if self.rtk != 4 and jetzt >= self.rtk_wieder:
            self.rtk = 4
        if self.sprung_offset != (0.0, 0.0) and jetzt >= self.sprung_ende:
            self.sprung_offset = (0.0, 0.0)

    # -- Bahnführung des Fahrers ---------------------------------------

    def fahrer(self) -> None:
        """Der Mensch im Sitz: schaltet nach Störungen wieder scharf, wendet."""
        jetzt = self.uhr()
        f = self.fahrzeug
        if jetzt < self.rueckwaerts_bis:
            if f.tempo >= 0:
                self.kurs_pruefen_ab = jetzt + 1.5      # Umkehr: der Kurs braucht drei Positionen
            f.tempo = -1.2
            return
        if f.tempo < 0:
            self.kurs_pruefen_ab = jetzt + 1.5
        m = self.motor
        # Aus dem Feld hinausgerollt (Wende verpasst, weil RTK gerade weg war):
        # der Fahrer bremst, setzt zurück und wendet von Hand.
        if f.north > FELD_LAENGE_M + 2.0 or f.north < -2.0:
            self.log("aus dem Feld gerollt")
            self.von_hand_wenden(f.north > 0)
            return
        # Die Lenkung hält ihren Einschlag nicht von selbst: wird sie abgeschaltet,
        # steht das Rad, wie es stand. Der Fahrer greift nach gut einer Sekunde zu
        # und lenkt geradeaus - dazu ist der Warnton da.
        if self.lenkung.armed:
            self.rad_frei_seit = None
        else:
            if self.rad_frei_seit is None:
                self.rad_frei_seit = jetzt
            if jetzt - self.rad_frei_seit > 1.2 and m.turn is None:
                f.lenk_soll = 0.0
        # Im Vorgewende und in der Wende nimmt jeder Fahrer das Gas weg.
        langsam = m.turn is not None or m.headland_status.im_vorgewende
        f.tempo = WENDE_TEMPO_MS if langsam else TEMPO_MS
        # Wende am Vorgewende: planen, ansehen, fahren.
        if (m.turn is None and m.headland_status.im_vorgewende and not self.wende_gesperrt
                and self.rtk == 4):
            try:
                # Nach Norden links herum heißt: die Bahnen wandern nach Westen.
                nach_norden = abs(geo.angle_difference(m.heading, 0.0)) < 90.0
                links = nach_norden == (self.wanderrichtung < 0)
                plan = m.plan_turn(richtung="links" if links else "rechts")
                if plan.get("im_feld"):
                    m.start_turn()
                    self.zaehler["wenden"] += 1
                    self.wende_gesperrt = True
                else:
                    self.von_hand_wenden(nach_norden)
            except Exception as exc:       # noqa: BLE001
                self.melde(f"Wende planen/starten scheiterte: {exc}")
        if not m.headland_status.im_vorgewende and m.turn is None:
            self.wende_gesperrt = False
        # Wieder scharf schalten, sobald die Anlage dafür bereit ist.
        if not self.lenkung.armed and jetzt >= self.rearm_ab and self.rtk == 4 \
                and jetzt > self.stumm_bis + 1.0 and f.tempo > 0:
            self.motor.arm_steering()
            self.fahrer_hat_geschaerft = True
        if self.lenkung.armed:
            self.rearm_ab = jetzt + 3.0
        elif self.rearm_ab < jetzt:
            self.rearm_ab = jetzt + 3.0

    def von_hand_wenden(self, nach_norden: bool) -> None:
        """Die Automatik-Wende passt nicht mehr (zu spät gedrückt, Feldrand): der
        Fahrer wendet von Hand. Das Fahrzeug steht danach auf der nächsten Bahn,
        gegenläufig, am Beginn des Vorgewendes."""
        f = self.fahrzeug
        schritt = ARBEITSBREITE_M * self.wanderrichtung
        if not (6.0 <= f.east + schritt <= FELD_BREITE_M - 6.0):
            self.wanderrichtung = -self.wanderrichtung
            self.umkehrungen += 1
            schritt = -schritt
        f.east += schritt
        f.north = FELD_LAENGE_M - 14.0 if nach_norden else 14.0
        f.heading = 180.0 if nach_norden else 0.0
        f.lenk_ist = f.lenk_soll = 0.0
        self.lenkung.disarm("vom Fahrer ausgeschaltet")
        self.von_hand += 1
        self.wende_gesperrt = True
        self.kurs_pruefen_ab = self.uhr() + 20.0
        self.eingeschwungen_ab = self.uhr() + 20.0
        self.log(f"Fahrer wendet von Hand -> Spur bei {f.east:.0f}")

    # -- Prüfungen ------------------------------------------------------

    def pruefen(self, fix: nmea.Fix | None) -> None:
        m, s = self.motor, self.lenkung
        jetzt = self.uhr()
        befehl = s.command
        # Kurzer RTK-Ausfall darf überbrückt werden (rtkbruecke.py) - aber nur so lange,
        # wie eingestellt, und nur, solange überhaupt eine Position kommt.
        frist = self.config.steering.rtk_ueberbrueckung_s
        ueberbrueckt = m.rtk_bruecke.aktiv and jetzt >= self.stumm_bis
        if befehl.engaged:
            if fix is None or jetzt - fix.received_at > 1.0:
                self.melde("lenkt mit veralteten Daten")
            elif fix.rank < 4 and not ueberbrueckt:
                self.melde(f"lenkt ohne RTK-fix ({fix.fix_label})")
            if abs(befehl.cross_track_m) > self.config.steering.max_cross_track_m + 1e-6:
                self.melde(f"lenkt {befehl.cross_track_m:.2f} m neben der Spur")
            if self.fahrzeug.tempo < 0:
                # Die Umkehr wird nach drei Positionen erkannt (0,3 s); dazwischen
                # lenkt die Automatik noch mit dem alten Kurs.
                if self.rueck_seit is None:
                    self.rueck_seit = jetzt
                if jetzt - self.rueck_seit > 0.6:
                    self.melde("lenkt rückwärts")
        if self.fahrzeug.tempo >= 0:
            self.rueck_seit = None
        # Ausfall: höchstens eine Sekunde danach darf nichts mehr gelenkt werden.
        ausfall = self.rtk != 4 or jetzt < self.stumm_bis
        if ausfall:
            if self.ausfall_seit is None:
                self.ausfall_seit = jetzt
            erlaubt = frist + 1.25 if ueberbrueckt else 1.25
            if befehl.engaged and jetzt - self.ausfall_seit > erlaubt:
                self.melde("lenkt weiter trotz Ausfall der Daten/RTK")
        else:
            self.ausfall_seit = None
        if self.nach_neustart and s.armed and not self.fahrer_hat_geschaerft:
            self.melde("Lenkung nach Neustart scharf, ohne dass der Fahrer es wollte")
        # Kurs zeigt in Fahrzeugrichtung (auch rückwärts), sobald eingeschwungen.
        if (m.heading is not None and abs(self.fahrzeug.tempo) > 1.0
                and jetzt > self.kurs_pruefen_ab and jetzt > self.stumm_bis + 2.0
                and self.lenkung.armed):
            fehler = abs(geo.angle_difference(m.heading, self.fahrzeug.heading))
            if fehler > 25.0 and self.fahrzeug.tempo > 0:
                hf = m.heading_filter
                self.melde(f"Kurs {fehler:.0f}° neben der Fahrzeugrichtung (vorwärts; Filter: "
                           f"rückwärts={hf.rueckwaerts}, Lenkung scharf={s.armed}, "
                           f"Tempo {self.fahrzeug.tempo:.1f}, Neustart {self.nach_neustart}, "
                           f"wahr {self.fahrzeug.heading:.0f}, Filter {m.heading:.0f}, "
                           f"gegen {hf._gegen}, Fix {self.rtk}, stumm {jetzt < self.stumm_bis}; "
                           f"zuletzt: {' | '.join(self.verlauf)})")
        if m.heading_filter.rueckwaerts != self._rueck_zuletzt:
            self._rueck_zuletzt = m.heading_filter.rueckwaerts
            self.log(f"Filter rückwärts={self._rueck_zuletzt} (wahr {self.fahrzeug.heading:.0f}, "
                     f"Filter {m.heading:.0f}, Tempo {self.fahrzeug.tempo:.1f}, "
                     f"Fix {fix.course_deg if fix else None})")
        # Spurfehler auf der Geraden, im lenkenden Zustand.
        if befehl.engaged and m.turn is None and abs(self.fahrzeug.gierrate) < 1.0 \
                and jetzt > self.eingeschwungen_ab:
            self.spurfehler.append(abs(befehl.cross_track_m))
        elif not befehl.engaged or m.turn is not None:
            self.eingeschwungen_ab = jetzt + 12.0
        # Fläche: darf nicht schrumpfen.
        flaeche = m.coverage.area_ha
        if flaeche + 1e-6 < self.letzte_flaeche - 0.02 and not self.neustart_gerade:
            self.melde(f"bearbeitete Fläche geschrumpft: {self.letzte_flaeche:.3f} -> {flaeche:.3f} ha")
        self.letzte_flaeche = max(self.letzte_flaeche, flaeche) if not self.neustart_gerade else flaeche
        self.neustart_gerade = False

    # -- Hauptschleife --------------------------------------------------

    def laufen(self) -> int:
        self.start_zeit = self.uhr()
        self.naechste_stoerung = self._naechster(150.0)
        self.rtk_wieder = self.sprung_ende = 0.0
        self.kurs_pruefen_ab = self.uhr() + 20.0
        self.eingeschwungen_ab = self.uhr() + 20.0
        self.wende_gesperrt = False
        self.neustart_gerade = False
        self.hart = False
        ende = self.start_zeit + self.args.stunden * 3600.0
        self.starten(neu=True)
        letzte_meldung = self.uhr()
        schritte = 0
        try:
            while self.uhr() < ende:
                self.uhr.jetzt += DT
                self.fahrzeug.schritt()
                self.sensor.attitude.yaw_rate_deg_s = (
                    self.fahrzeug.gierrate + GYRO_FEHLER_GRAD_S + self.zufall.gauss(0, 0.2))
                self.stoerung_planen()
                self.stoerung_ende()
                self.fahrer()
                fix = None
                if self.uhr() >= self.stumm_bis:
                    fix = self._fix(self.uhr())
                    t0 = self.echte_zeit()
                    self.motor.on_fix(fix)
                    dauer = self.echte_zeit() - t0
                    if dauer > self.laengste_position_s:
                        self.langsamster = f"on_fix bei {(self.uhr() - self.start_zeit) / 60:.1f} min"
                    self.laengste_position_s = max(self.laengste_position_s, dauer)
                    self.zaehler["position"] += 1
                t0 = self.echte_zeit()
                self.motor.tick()
                dauer = self.echte_zeit() - t0
                if dauer > self.laengste_position_s:
                    self.langsamster = (f"tick bei {(self.uhr() - self.start_zeit) / 60:.1f} min "
                                        f"(Fläche {self.motor.coverage.area_ha:.1f} ha)")
                self.laengste_position_s = max(self.laengste_position_s, dauer)
                if fix is None:
                    self.pruefen(self.motor.fix)
                else:
                    self.pruefen(fix)
                if self.neustart_faellig:
                    self.neustart_faellig = False
                    self.neu_starten()
                if self.hart and len(self.verstoesse) > 25:
                    break
                schritte += 1
                if self.args.ausfuehrlich and self.uhr() - letzte_meldung > 300:
                    letzte_meldung = self.uhr()
                    f = self.fahrzeug
                    self.log(f"Stand: {self.motor.coverage.area_ha:.2f} ha, Pos {f.east:.0f}/{f.north:.0f}, "
                             f"Kurs {f.heading:.0f}, Spur {self.motor.guidance.pass_number}, "
                             f"scharf {self.lenkung.armed}, Auftrag {'ja' if self.motor.job else 'nein'}")
                if self.args.fortschritt and self.uhr() - letzte_meldung > 600:
                    letzte_meldung = self.uhr()
                    print(f"  {(self.uhr() - self.start_zeit) / 3600:4.1f} h  "
                          f"{self.motor.coverage.area_ha:6.2f} ha  "
                          f"Wenden {self.zaehler['wenden']}  Neustarts {self.zaehler['neustart']}",
                          flush=True)
        finally:
            time.time = self.echte_zeit
        return self.bericht()

    def neu_starten(self) -> None:
        """Zündung aus: Programm endet (wie beim Beenden), Datenbank wird neu geöffnet."""
        vorher = self.motor.coverage.area_ha
        self.motor.suspend_job()
        self.store.close()
        self.uhr.jetzt += 20.0                # so lange dauert es, bis der Rechner wieder da ist
        self.fahrzeug.tempo = TEMPO_MS
        self.starten(neu=False)
        self.zaehler["neustart"] += 1
        self.neustart_gerade = True
        self.letzte_flaeche = vorher
        nachher = self.motor.coverage.area_ha
        # Höchstens die letzten 30 s Arbeit dürfen fehlen.
        verloren = vorher - nachher
        erlaubt = 30.0 * TEMPO_MS * ARBEITSBREITE_M / 10_000.0 + 0.01
        if self.motor.job is None:
            self.melde("Arbeit nach Neustart nicht fortgesetzt")
        elif verloren > erlaubt:
            self.melde(f"Neustart verlor {verloren:.3f} ha (erlaubt {erlaubt:.3f})")
        self.letzte_flaeche = nachher
        self.kurs_pruefen_ab = self.uhr() + 20.0
        self.eingeschwungen_ab = self.uhr() + 20.0

    def bericht(self) -> int:
        stunden = (self.uhr() - self.start_zeit) / 3600.0
        m = self.motor
        print(f"\nDauerlauf: {stunden:.2f} h virtuell, {self.zaehler['position']} Positionen")
        print(f"  Fläche {m.coverage.area_ha:.2f} ha, Überlappung {m.coverage.overlap_percent:.1f} %, "
              f"Strecke {m.distance_m / 1000:.1f} km")
        print(f"  Wenden von Hand (Automatik passte nicht): {self.von_hand}x, "
              f"Feldrand erreicht: {self.umkehrungen}x")
        print("  Störungen: " + ", ".join(f"{k} {v}" for k, v in self.zaehler.items()
                                          if k not in ("position",)))
        if self.spurfehler:
            fehler = sorted(self.spurfehler)
            mittel = sum(fehler) / len(fehler)
            p95 = fehler[int(len(fehler) * 0.95)]
            print(f"  Spurfehler auf der Geraden: Mittel {mittel * 100:.1f} cm, "
                  f"95 % unter {p95 * 100:.1f} cm, Spitze {fehler[-1] * 100:.1f} cm "
                  f"({len(fehler)} Messungen)")
            if mittel > 0.05:
                self.verstoesse.append(f"Spurfehler im Mittel {mittel * 100:.1f} cm (Grenze 5 cm)")
        else:
            self.verstoesse.append("nie auf der Geraden gelenkt - Lauf aussagelos")
        print(f"  Längste Berechnung einer Position: {self.laengste_position_s * 1000:.0f} ms (echte Zeit) - {self.langsamster}")
        # Der Sicherungspunkt alle 30 s kostet auf diesem PC 10 bis 60 ms; auf einem
        # Pi das Mehrfache. Eine ganze Sekunde wäre ein Fehler, nicht Hardware.
        if self.laengste_position_s > 1.0:
            self.verstoesse.append(
                f"eine Position brauchte {self.laengste_position_s * 1000:.0f} ms (Grenze 1000 ms)")
        self.store.close()
        self.ordner.cleanup()
        if self.verstoesse:
            print("\nVERLETZT:")
            for zeile in self.verstoesse[:40]:
                print("  - " + zeile)
            return 1
        print("\nAlle Regeln eingehalten.")
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--stunden", type=float, default=3.0)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--ohne-stoerungen", action="store_true")
    parser.add_argument("--fortschritt", action="store_true")
    parser.add_argument("--ausfuehrlich", action="store_true", help="Störungen und Wenden einzeln auflisten")
    return Lauf(parser.parse_args()).laufen()


if __name__ == "__main__":
    raise SystemExit(main())
