"""The live system: one fix in, one complete cab picture out.

Everything else in this package is a pure piece - parse, project, compute,
store.  This module is the one place where they meet and where the state of the
current work lives: which field, which line, what has been covered, whether a
job is running, whether steering may engage.

The flow for every position update is always the same, and the order matters:

    fix -> local metres -> tool position -> heading -> guidance
        -> coverage & section control -> autosteer -> broadcast

Guidance is computed for the tool, not the antenna, and coverage is recorded
where the tool actually is, so what the screen shows is what the field gets.
"""

from __future__ import annotations

import asyncio
import json
import math
import time
from dataclasses import asdict
from typing import Any, Optional

from . import (applikation as applikation_module, feldplan as feldplan_module,
               geo, headland as headland_module, storage)
from .actuators import SteerContext
from .config import Config
from .coverage import CoverageMap, Section, build_sections
from .guidance import (
    GuidanceLine,
    GuidanceState,
    HeadingFilter,
    ImplementHeading,
    VehicleProfile,
)
from .imu import terrain_offset
from .nmea import Fix

TRACK_MIN_DISTANCE_M = 1.0
TRACK_FLUSH_COUNT = 25


# Unter dieser Strecke gilt eine Arbeit als nicht begonnen: Markieren an und
# gleich wieder aus, ohne zu fahren. Fünf Meter ist eine Traktorlänge.
MINDESTSTRECKE_ARBEIT_M = 5.0


class Engine:
    def __init__(self, config: Config, store: storage.Storage) -> None:
        self.config = config
        self.store = store

        self.profile = self._load_profile()
        self.sections: list[Section] = build_sections(
            self.profile.width_m, self.profile.sections
        )
        self.heading_filter = HeadingFilter()

        self.field: Optional[dict] = None
        self.plane: Optional[geo.LocalPlane] = None
        self.line: Optional[GuidanceLine] = None
        self.coverage = CoverageMap(cell_size=0.5)
        self.job: Optional[dict] = None

        # Arbeitsplan des Feldes: Bahnen, Ringe, Reihenfolge.
        self.plan: Optional[feldplan_module.Feldplan] = None
        self.plan_id: Optional[str] = None
        self.plan_bahn: Optional[int] = None     # welche Bahn gerade geführt wird
        self._fortschritt: Optional[feldplan_module.Planfortschritt] = None
        self._fortschritt_zeit = 0.0

        # Applikationskarte und die Mitschrift dessen, was ausgebracht wurde.
        self.karte: Optional[applikation_module.Applikationskarte] = None
        self.karte_id: Optional[str] = None
        self.karte_lokal: Optional[applikation_module.LokaleKarte] = None
        self.sollwert: Optional[float] = None
        self.ausbringung = applikation_module.Ausbringung()
        self._karte_anhaengen()

        self.fix: Optional[Fix] = None
        self.position: Optional[geo.Point] = None      # antenna, local metres
        self.tool_position: Optional[geo.Point] = None
        self.steer_position: Optional[geo.Point] = None   # worauf gelenkt wird: die Achse
        # Beim starren Anbau dasselbe wie tool_position; beim gezogenen Gerät
        # nicht - dort wird hier markiert, und in der Kurve liegt das spürbar
        # innerhalb der Fahrspur.
        self.implement_position: Optional[geo.Point] = None
        self.implement_heading = ImplementHeading()
        self.heading: Optional[float] = None
        self.guidance = GuidanceState()

        # Vorgewende: Tiefe, Alarm und Wendemuster. Steht in der Datenbank, weil
        # es zur Maschine gehört und einen Neustart überleben soll.
        self.headland = headland_module.HeadlandSettings.from_dict(
            self.store.get_setting("headland")
        )
        self.headland_status = headland_module.HeadlandStatus()
        self.turn: Optional[headland_module.TurnFollower] = None
        self.turn_preview: list[geo.Point] = []
        self.turn_preview_ok = False
        self._turn_planned_at: Optional[geo.Point] = None
        self._ring_key: Optional[tuple] = None
        self._ring_cache: list[list[float]] = []
        self._abdeckung_at = 0.0          # zuletzt abgetastet (Sekunden)
        self._abdeckung: Optional[float] = None
        self._steering_was_engaged = False

        self.record_mode: Optional[str] = None          # "boundary" | "curve"
        self._recording: list[geo.Point] = []
        self._pending_a: Optional[geo.Point] = None

        self.distance_m = 0.0
        self.working_time_s = 0.0
        self.session_started_at = time.time()
        self.messages: list[str] = []
        # Was beim letzten Mal offen blieb (Zündung aus statt „Arbeit beenden"),
        # wird jetzt abgeschlossen - sonst steht es ewig als „läuft" in der Liste.
        verwaist = store.close_orphan_jobs(config.network.device_id)
        if verwaist:
            self.note(f"{verwaist} unterbrochene Arbeit(en) vom letzten Mal abgeschlossen")
        self.auto_sections = True

        self._track_buffer: list[tuple] = []
        self._last_track_point: Optional[geo.Point] = None
        self._last_update_at: Optional[float] = None
        self.steering = None    # set by the server once the controller exists
        self.simulator = None   # set when running on the simulator source
        self.imu = None         # set when a tilt sensor is configured
        self.terrain_offset_m = (0.0, 0.0)   # (rechts, vorn) - nur zur Anzeige

    # -- profile ----------------------------------------------------------

    def _load_profile(self) -> VehicleProfile:
        stored = self.store.get_setting("vehicle_profile")
        if not stored:
            return VehicleProfile()
        fields = VehicleProfile.__dataclass_fields__
        return VehicleProfile(**{k: v for k, v in stored.items() if k in fields})

    # Mehrere Maschinen: jede ein vollständiges Profil unter einer Kennung.
    # "vehicle_profile" bleibt das aktive - so lesen es alle anderen Stellen,
    # und eine Datenbank vom Frühjahr hat genau dieses eine, das dann zur
    # ersten Maschine der Liste wird.

    def list_profiles(self) -> list[dict]:
        profile = self.store.get_setting("vehicle_profiles") or []
        aktiv = self.store.get_setting("vehicle_profile_active") or ""
        if not profile:
            eintrag = {"id": storage.new_id(), **asdict(self.profile)}
            profile = [eintrag]
            aktiv = eintrag["id"]
            self.store.set_setting("vehicle_profiles", profile)
            self.store.set_setting("vehicle_profile_active", aktiv)
        return [{**p, "aktiv": p["id"] == aktiv} for p in profile]

    def save_profile_as(self, name: str, values: dict | None = None) -> dict:
        """Eine neue Maschine anlegen - aus den aktuellen Werten, mit neuem Namen."""
        name = (name or "").strip()
        if not name:
            raise ValueError("Die Maschine braucht eine Bezeichnung")
        fields = VehicleProfile.__dataclass_fields__
        werte = asdict(self.profile)
        werte.update({k: v for k, v in (values or {}).items() if k in fields})
        werte["name"] = name
        neu = {"id": storage.new_id(), **asdict(VehicleProfile(**werte))}
        profile = [p for p in self.list_profiles()]
        for p in profile:
            p.pop("aktiv", None)
        profile.append(neu)
        self.store.set_setting("vehicle_profiles", profile)
        self.select_profile(neu["id"])
        return neu

    def select_profile(self, profile_id: str) -> VehicleProfile:
        profile = self.list_profiles()
        eintrag = next((p for p in profile if p["id"] == profile_id), None)
        if eintrag is None:
            raise KeyError("Maschine nicht gefunden")
        self.store.set_setting("vehicle_profile_active", profile_id)
        werte = {k: v for k, v in eintrag.items() if k in VehicleProfile.__dataclass_fields__}
        self.update_profile(werte)
        self.note(f"Maschine: {self.profile.name}")
        return self.profile

    def delete_profile(self, profile_id: str) -> None:
        profile = self.list_profiles()
        if len(profile) <= 1:
            raise ValueError("Die letzte Maschine bleibt - eine muss es geben")
        rest = [p for p in profile if p["id"] != profile_id]
        if len(rest) == len(profile):
            raise KeyError("Maschine nicht gefunden")
        war_aktiv = any(p["id"] == profile_id and p["aktiv"] for p in profile)
        for p in rest:
            p.pop("aktiv", None)
        self.store.set_setting("vehicle_profiles", rest)
        if war_aktiv:
            self.select_profile(rest[0]["id"])

    def update_profile(self, values: dict) -> VehicleProfile:
        fields = VehicleProfile.__dataclass_fields__
        current = asdict(self.profile)
        current.update({k: v for k, v in values.items() if k in fields})
        self.profile = VehicleProfile(**current)
        self.store.set_setting("vehicle_profile", current)
        # ... und in der Maschinenliste die aktive mitziehen.
        profile = self.store.get_setting("vehicle_profiles") or []
        aktiv = self.store.get_setting("vehicle_profile_active") or ""
        if profile and any(p["id"] == aktiv for p in profile):
            self.store.set_setting("vehicle_profiles", [
                {**current, "id": aktiv} if p["id"] == aktiv else p for p in profile])
        self.sections = build_sections(self.profile.width_m, self.profile.sections)
        if self.line is not None:
            # Changing the working width changes the pass spacing, and with it
            # which pass the machine is on - but not the reference line itself.
            self.line.spacing = self.profile.spacing_m
        # Aus einem angebauten Gerät ist gerade ein gezogenes geworden (oder
        # umgekehrt). Die nachlaufende Ausrichtung des alten Geräts weiterzu-
        # schleppen hieße, mit der Geometrie des vorigen zu markieren.
        self.implement_heading.reset(self.heading)
        return self.profile

    def update_headland(self, values: dict) -> headland_module.HeadlandSettings:
        """Vorgewende und Wendemuster ändern.

        Eine laufende Wende wird dabei abgebrochen: sie wurde nach den alten
        Werten geplant, und eine Route halb nach neuen Zahlen weiterzufahren
        wäre die schlechteste von beiden.
        """
        felder = headland_module.HeadlandSettings.__dataclass_fields__
        aktuell = self.headland.to_dict()
        aktuell.update({k: v for k, v in values.items() if k in felder})
        self.headland = headland_module.HeadlandSettings.from_dict(aktuell)
        self.store.set_setting("headland", self.headland.to_dict())
        if self.turn is not None:
            self.stop_turn("Einstellungen geändert")
        self.turn_preview, self.turn_preview_ok = [], False
        return self.headland

    # -- field and line ---------------------------------------------------

    def load_field(self, field_id: str) -> dict:
        field = self.store.get_field(field_id)
        if field is None:
            raise KeyError("Feld nicht gefunden")
        self.field = field
        self.plane = geo.LocalPlane(field["datum_lat"], field["datum_lon"])
        self.coverage = CoverageMap(cell_size=0.5)
        self._karte_anhaengen()
        self.line = None
        self.plan = None
        self.plan_id = None
        self.plan_bahn = None
        self._fortschritt = None
        self.karte = None
        self.karte_id = None
        self.karte_lokal = None
        self.sollwert = None
        self.ausbringung = applikation_module.Ausbringung()
        # Plan und Karte des Feldes kommen mit - auch die vom Traktor nebenan,
        # denn beide stehen in der abgeglichenen Datenbank.
        gespeicherter_plan = self.store.field_plan(field_id)
        if gespeicherter_plan is not None and gespeicherter_plan.get("plan"):
            try:
                self._plan_uebernehmen(gespeicherter_plan)
            except (KeyError, TypeError, ValueError):
                self.note("Der abgelegte Plan ist unlesbar - bitte neu rechnen")
        karten = self.store.list_maps(field_id)
        if karten:
            try:
                self.karte_waehlen(karten[0]["id"])
            except (KeyError, RuntimeError, TypeError, ValueError):
                self.note("Die abgelegte Applikationskarte ist unlesbar")
        # Die Saisonspur zuerst: die Fahrgassen vom Säen sollen beim Düngen und
        # Spritzen wieder unter den Rädern liegen - das ganze Jahr, ohne dass
        # jemand daran denken muss. Sonst die zuletzt benutzte Spur.
        saison = self.store.season_line(field_id, time.localtime().tm_year)
        lines = self.store.list_lines(field_id)
        if saison is not None:
            self.load_line(saison["id"])
        elif lines:
            self.load_line(lines[0]["id"])
        self.note(f"Feld geladen: {field['name']}")
        return field

    # -- Arbeitsplan --------------------------------------------------------

    def _plan_grenze(self) -> list[geo.Point]:
        if not self.field or len(self.field.get("boundary") or []) < 3:
            raise RuntimeError("Ohne Feldgrenze lässt sich nichts planen - "
                               "das Feld einmal umfahren oder ein Shapefile einlesen")
        return [tuple(p) for p in self.field["boundary"]]

    def plan_einstellungen(self, werte: Optional[dict] = None
                           ) -> feldplan_module.PlanEinstellungen:
        """Die Vorgaben für den Plan, aufgefüllt aus Maschine und Vorgewende.

        Was der Fahrer nicht angibt, steht schon im System: die Arbeitsbreite im
        Maschinenprofil, die Vorgewendetiefe in den Vorgewende-Einstellungen.
        Zweimal dasselbe eintippen zu müssen ist die zuverlässigste Art, zwei
        verschiedene Werte zu bekommen.
        """
        breite = max(0.5, self.profile.spacing_m)
        grund = {
            "arbeitsbreite_m": breite,
            # In Arbeitsbreiten gerechnet, aber über tiefe_m - so gilt auch ein
            # in Metern übersteuertes Vorgewende.
            "vorgewende_breiten": self.headland.tiefe_m(breite) / breite,
            "wenderadius_m": self.headland.radius_m,
            "geschwindigkeit_kmh": 8.0,
        }
        grund.update(werte or {})
        return feldplan_module.PlanEinstellungen.from_dict(grund)

    def plan_rechnen(self, werte: Optional[dict] = None) -> dict:
        """Einen Arbeitsplan rechnen, ablegen und laden."""
        grenze = self._plan_grenze()
        einstellungen = self.plan_einstellungen(werte)
        self.plan = feldplan_module.planen(grenze, einstellungen)
        self.plan_bahn = None
        self._fortschritt = None
        datensatz = self.store.save_plan({
            "id": self.plan_id or storage.new_id(),
            "field_id": self.field["id"],
            "name": (f"{einstellungen.arbeitsbreite_m:g} m, "
                     f"{self.plan.richtung_grad:.0f}°"),
            "einstellungen": einstellungen.to_dict(),
            "plan": self.plan.to_dict(),
        })
        self.plan_id = datensatz["id"]
        self.note(f"Plan: {len(self.plan.bahnen)} Bahnen, "
                  f"{self.plan.richtung_grad:.0f}°, "
                  f"{self.plan.dauer_min:.0f} min")
        return self.plan.to_dict()

    def plan_laden(self, plan_id: str) -> dict:
        datensatz = self.store.get_plan(plan_id)
        if datensatz is None:
            raise KeyError("Plan nicht gefunden")
        self._plan_uebernehmen(datensatz)
        return self.plan.to_dict()

    def _plan_uebernehmen(self, datensatz: dict) -> None:
        """Einen abgelegten Plan wieder zu Bahnen machen.

        Gerechnet wird nicht neu: der Plan aus der Ablage ist derselbe, den der
        Traktor nebenan vor sich hat, mit denselben Bahnnummern. Neu rechnen
        würde bei einer inzwischen nachgemessenen Feldgrenze eine andere
        Einteilung ergeben - und dann meinen zwei Fahrer mit "Bahn 12"
        verschiedene Stellen im Feld.
        """
        roh = datensatz.get("plan") or {}
        einstellungen = feldplan_module.PlanEinstellungen.from_dict(
            roh.get("einstellungen") or datensatz.get("einstellungen"))
        bahnen = [feldplan_module.Bahn(
            nummer=int(b["nummer"]), spur=int(b["spur"]),
            start=tuple(b["start"]), ende=tuple(b["ende"]),
            richtung=float(b["richtung"]), laenge_m=float(b["laenge_m"]))
            for b in roh.get("bahnen") or []]
        self.plan = feldplan_module.Feldplan(
            einstellungen=einstellungen,
            richtung_grad=float(roh.get("richtung_grad", 0.0)),
            automatisch=bool(roh.get("automatisch", False)),
            bahnen=bahnen,
            ringe=[[tuple(p) for p in ring] for ring in roh.get("ringe") or []],
            feld_flaeche_ha=float(roh.get("feld_flaeche_ha", 0.0)),
            bahnen_flaeche_ha=float(roh.get("bahnen_flaeche_ha", 0.0)),
            vorgewende_flaeche_ha=float(roh.get("vorgewende_flaeche_ha", 0.0)),
            arbeitsstrecke_m=float(roh.get("arbeitsstrecke_m", 0.0)),
            wendestrecke_m=float(roh.get("wendestrecke_m", 0.0)),
            ringstrecke_m=float(roh.get("ringstrecke_m", 0.0)),
        )
        self.plan_id = datensatz["id"]
        self.plan_bahn = None
        self._fortschritt = None

    def plan_loeschen(self) -> None:
        if self.plan_id:
            self.store.delete_plan(self.plan_id)
        self.plan = None
        self.plan_id = None
        self.plan_bahn = None
        self._fortschritt = None
        self.note("Plan verworfen")

    def bahn_waehlen(self, nummer: int) -> dict:
        """Eine geplante Bahn als Führungslinie übernehmen.

        Die Bahn wird zur AB-Linie zwischen ihren beiden Enden - dieselbe
        Führung wie bei einer selbst gesetzten Spur, nur dass A und B aus dem
        Plan kommen. Gespeichert wird sie nicht: sie steht schon im Plan, und
        eine zweite Kopie liefe ihm davon.
        """
        if self.plan is None:
            raise RuntimeError("Kein Plan geladen")
        bahn = self.plan.bahn(int(nummer))
        if bahn is None:
            raise KeyError(f"Bahn {nummer} steht nicht im Plan")
        self.line = GuidanceLine(
            "ab", [bahn.start, bahn.ende], self.profile.spacing_m,
            name=f"Bahn {bahn.nummer}", line_id="", derived=True)
        self.plan_bahn = bahn.nummer
        self.note(f"Bahn {bahn.nummer} von {len(self.plan.bahnen)}")
        return self.line.to_dict()

    def naechste_bahn(self) -> dict:
        """Die nächstgelegene offene Bahn übernehmen."""
        stand = self.plan_fortschritt(neu=True)
        if stand is None or stand.naechste is None:
            raise RuntimeError("Keine offene Bahn mehr - das Feld ist durch")
        return self.bahn_waehlen(stand.naechste)

    def plan_fortschritt(self, neu: bool = False
                         ) -> Optional[feldplan_module.Planfortschritt]:
        """Wie weit der Plan abgearbeitet ist - höchstens alle zwei Sekunden neu.

        Das Auszählen kostet je Bahn ein paar Dutzend Abfragen an die
        bearbeitete Fläche. Bei achtzig Bahnen ist das nichts, was zehnmal je
        Sekunde passieren muss: der Fortschritt ändert sich im Takt der
        Traktorgeschwindigkeit, nicht im Takt des Empfängers.
        """
        if self.plan is None:
            return None
        jetzt = time.time()
        if neu or self._fortschritt is None or jetzt - self._fortschritt_zeit > 2.0:
            self._fortschritt = feldplan_module.fortschritt(
                self.plan, self.coverage.is_covered,
                ab_position=self.tool_position)
            self._fortschritt_zeit = jetzt
        return self._fortschritt

    # -- Applikationskarte --------------------------------------------------

    def _karte_anhaengen(self) -> None:
        """Die Buchung der Ausbringung an die Flächenkarte hängen."""
        self.coverage.on_new_cell = self._zelle_gebucht

    def _zelle_gebucht(self, zelle: tuple[int, int]) -> None:
        if self.karte_lokal is None or self.job is None:
            return
        kante = self.coverage.cell_size
        mitte = ((zelle[0] + 0.5) * kante, (zelle[1] + 0.5) * kante)
        self.ausbringung.buchen(self.karte_lokal.wert_bei(mitte), kante * kante)

    def karte_waehlen(self, map_id: str) -> dict:
        datensatz = self.store.get_map(map_id)
        if datensatz is None:
            raise KeyError("Applikationskarte nicht gefunden")
        if self.plane is None:
            raise RuntimeError("Erst ein Feld laden")
        self.karte = applikation_module.Applikationskarte.from_dict(datensatz["daten"])
        self.karte_id = datensatz["id"]
        self.karte_lokal = self.karte.binden(self.plane)
        self.ausbringung = applikation_module.Ausbringung(einheit=self.karte.einheit)
        self.note(f"Applikationskarte: {self.karte.name}")
        return self.karte_uebersicht()

    def karte_entfernen(self) -> None:
        self.karte = None
        self.karte_id = None
        self.karte_lokal = None
        self.sollwert = None
        self.note("Applikationskarte abgewählt")

    def karte_speichern(self, karte: "applikation_module.Applikationskarte") -> dict:
        """Eine frisch eingelesene Karte ablegen und gleich verwenden."""
        if self.field is None:
            raise RuntimeError("Erst ein Feld laden")
        datensatz = self.store.save_map({
            "field_id": self.field["id"],
            "name": karte.name,
            "einheit": karte.einheit,
            "quelle": karte.quelle,
            "daten": karte.to_dict(),
        })
        self.karte_waehlen(datensatz["id"])
        return datensatz

    def karte_uebersicht(self) -> dict:
        """Karte, geplante Mengen fürs Feld und der Stand der Ausbringung."""
        if self.karte is None or self.karte_lokal is None:
            return {"aktiv": False}
        geplant = {}
        if self.field and len(self.field.get("boundary") or []) >= 3:
            geplant = applikation_module.kennzahlen(
                self.karte_lokal, [tuple(p) for p in self.field["boundary"]])
        return {
            "aktiv": True,
            "id": self.karte_id,
            "name": self.karte.name,
            "einheit": self.karte.einheit,
            "quelle": self.karte.quelle,
            "art": self.karte.art,
            "hinweise": list(self.karte.hinweise),
            "geplant": geplant,
            "ausgebracht": self.ausbringung.to_dict(),
            "abgleich": (applikation_module.abgleich(geplant, self.ausbringung)
                         if geplant else None),
        }

    def import_fields(self, umrisse: list) -> list[dict]:
        """Felder aus einem Shapefile anlegen - eines je Fläche.

        Ein Feld gleichen Namens wird nicht doppelt angelegt, sondern bekommt
        die neue Grenze: die Datei aus dem Antrag ist die Wahrheit, nicht die
        alte Umfahrung. Gespeicherte Spuren hängen an der Kennung und bleiben.
        """
        vorhandene = {f["name"]: f for f in self.store.list_fields()}
        angelegt = []
        for umriss in umrisse:
            datensatz = umriss.als_feld()
            alt = vorhandene.get(datensatz["name"])
            if alt is not None:
                # Bezug bleibt: die Spuren des Feldes sind in dessen Metern
                # gespeichert. Die neue Grenze wird auf den alten Bezug gelegt.
                ebene = geo.LocalPlane(alt["datum_lat"], alt["datum_lon"])
                datensatz["boundary"] = [
                    [round(x, 3), round(y, 3)]
                    for x, y in (ebene.to_local(lat, lon) for lat, lon in umriss.ring)]
                datensatz["datum_lat"], datensatz["datum_lon"] = alt["datum_lat"], alt["datum_lon"]
                datensatz["id"] = alt["id"]
                datensatz["note"] = alt.get("note") or datensatz["note"]
            angelegt.append(self.store.save_field(datensatz))
        self.note(f"{len(angelegt)} Felder aus Shapefile übernommen")
        return angelegt

    def update_line(self, line_id: str, values: dict) -> dict:
        """Spur umbenennen oder als Saisonspur mit Fahrgassen festlegen."""
        werte = {}
        if "name" in values:
            werte["name"] = str(values["name"]).strip() or "Spur"
        if "fahrgasse_m" in values:
            fahrgasse = float(values["fahrgasse_m"] or 0.0)
            if fahrgasse < 0:
                raise ValueError("Der Fahrgassenabstand kann nicht negativ sein")
            werte["fahrgasse_m"] = fahrgasse
            # Fahrgassen gehören zu einer Saison. Ohne Angabe ist es diese.
            werte["saison"] = int(values.get("saison") or time.localtime().tm_year) if fahrgasse > 0 else 0
        elif "saison" in values:
            werte["saison"] = int(values["saison"] or 0)
        record = self.store.update_line(line_id, **werte)
        if record is None:
            raise KeyError("Spurlinie nicht gefunden")
        if self.line is not None and self.line.id == line_id:
            self.line.name = record["name"]
            self.line.fahrgasse_m = record["fahrgasse_m"]
            self.line.saison = record["saison"]
        return record

    def create_field(self, name: str) -> dict:
        """Anchor a new field at the current position.

        The datum is what makes coverage grids from different machines line up,
        so it is fixed once at creation and never moved afterwards.
        """
        if self.fix is None or not self.fix.valid:
            raise RuntimeError("Ohne GPS-Fix lässt sich kein Feld anlegen")
        field = self.store.save_field({
            "name": name,
            "datum_lat": self.fix.lat,
            "datum_lon": self.fix.lon,
            "boundary": [],
            "area_ha": 0.0,
        })
        return self.load_field(field["id"])

    def load_line(self, line_id: str) -> dict:
        record = self.store.get_line(line_id)
        if record is None:
            raise KeyError("Spurlinie nicht gefunden")
        self.line = GuidanceLine(
            record["mode"], [tuple(p) for p in record["points"]],
            self.profile.spacing_m, record["name"], record["id"],
        )
        self.line.nudge_m = record.get("nudge_m", 0.0)
        self.line.fahrgasse_m = float(record.get("fahrgasse_m") or 0.0)
        self.line.saison = int(record.get("saison") or 0)
        zusatz = f" · Fahrgassen alle {self.line.fahrgasse_m:g} m" if self.line.fahrgasse_m else ""
        self.note(f"Spur aktiv: {record['name']}{zusatz}")
        return record

    def clear_line(self) -> None:
        self.line = None
        self.guidance = GuidanceState()

    def use_contour(self) -> dict:
        """Die Feldgrenze selbst als Spurmuster nehmen.

        Ring 0 ist die Grenze, jeder weitere Ring liegt eine Arbeitsbreite
        weiter innen. Kein A- und kein B-Punkt nötig - was für das Vorgewende
        und für krumme Schläge der kürzere Weg ist als eine Kurve abzufahren,
        die man ohnehin schon einmal abgefahren hat.

        Die Kontur wird **nicht gespeichert**. Sie entsteht bei jedem Aufruf neu
        aus der aktuellen Grenze; eine gespeicherte Kopie würde nach dem nächsten
        Abfahren der Grenze still danebenliegen.
        """
        if self.field is None:
            raise RuntimeError("Kein Feld ausgewählt")
        grenze = [tuple(p) for p in (self.field.get("boundary") or [])]
        if len(grenze) < 3:
            raise RuntimeError("Ohne Feldgrenze gibt es keine Kontur - erst die "
                               "Grenze abfahren (⬠)")
        self.line = GuidanceLine(
            "contour", grenze, self.profile.spacing_m, "Kontur", "contour",
        )
        self.note("Kontur aktiv: Ring 0 ist die Feldgrenze")
        return self.line.to_dict()

    def set_a(self) -> dict:
        self._require_position()
        self._pending_a = self.tool_position
        self.note("Punkt A gesetzt - jetzt bis zum Ende fahren und B setzen")
        return {"a": list(self._pending_a)}

    def set_b(self, name: str = "") -> dict:
        self._require_position()
        if self._pending_a is None:
            raise RuntimeError("Erst Punkt A setzen")
        if geo.distance(self._pending_a, self.tool_position) < 3.0:
            raise RuntimeError("A und B liegen zu dicht beieinander (mind. 3 m)")
        line = self._save_line(
            "ab", [self._pending_a, self.tool_position],
            name or f"AB {time.strftime('%H:%M')}",
        )
        self._pending_a = None
        return line

    def set_ab_from_heading(self, heading: float, name: str = "") -> dict:
        """A+ line: one point plus a compass bearing.

        Useful when the neighbouring field or a previous year's pass already
        defines the direction and there is no room to drive out a B point.
        """
        self._require_position()
        a = self._pending_a or self.tool_position
        h = math.radians(heading)
        b = (a[0] + math.sin(h) * 200.0, a[1] + math.cos(h) * 200.0)
        self._pending_a = None
        # A+ noch einmal gedrückt heißt: die Richtung neu nehmen - nicht eine
        # weitere Spur in die Liste stellen. Die eben angelegte A+-Spur wird
        # überschrieben, solange sie keine Saisonspur ist (die hat jemand
        # bewusst so festgelegt; sie bleibt).
        ersetzen = ""
        if (self.line is not None and not name and self.line.mode == "ab"
                and self.line.name.startswith("A+") and not self.line.fahrgasse_m
                and self.field is not None):
            alt = self.store.get_line(self.line.id)
            if alt is not None and alt["field_id"] == self.field["id"]:
                ersetzen = self.line.id
        return self._save_line("ab", [a, b], name or f"A+ {heading:.0f}°", line_id=ersetzen)

    def start_recording(self, mode: str) -> None:
        if mode not in ("boundary", "curve"):
            raise ValueError("Aufzeichnung: 'boundary' oder 'curve'")
        self._require_position()
        self.record_mode = mode
        self._recording = [self.tool_position]
        self.note("Grenze wird aufgezeichnet" if mode == "boundary"
                  else "Kurve wird aufgezeichnet")

    def stop_recording(self, name: str = "") -> dict:
        if self.record_mode is None:
            raise RuntimeError("Es läuft keine Aufzeichnung")
        mode, points = self.record_mode, self._recording
        self.record_mode, self._recording = None, []
        if len(points) < 3:
            raise RuntimeError("Zu wenige Punkte aufgezeichnet")
        if mode == "curve":
            return self._save_line("curve", points, name or f"Kurve {time.strftime('%H:%M')}")
        return self.save_boundary(points)

    def save_boundary(self, points: list[geo.Point]) -> dict:
        """Close the recorded loop and store it as the field boundary."""
        if self.field is None:
            raise RuntimeError("Kein Feld ausgewählt")
        simplified = geo.simplify(points, 0.3)
        area_ha = geo.polygon_area(simplified) / 10_000.0
        if len(simplified) < 3 or area_ha < 0.01:
            # Eine Gerade oder ein Kringel von hundert Quadratmetern ist keine
            # Feldgrenze. Sie zu speichern würde eine vorhandene Grenze durch
            # Unsinn ersetzen - und die Kontur, das Vorgewende, die Sektionen
            # gleich mit. Die alte Grenze bleibt, der Fahrer bekommt es gesagt.
            raise RuntimeError(f"Grenze zu klein ({area_ha:.2f} ha) - einmal ganz um das "
                               "Feld fahren, die alte Grenze bleibt")
        field = self.store.save_field({
            **{k: self.field[k] for k in
               ("id", "name", "datum_lat", "datum_lon", "note")},
            "boundary": [list(p) for p in simplified],
            "area_ha": area_ha,
        })
        self.field = field
        self.note(f"Feldgrenze gespeichert: {area_ha:.2f} ha")
        if self.line is not None and self.line.mode == "contour":
            # Die Kontur hängt an der Grenze. Wird die Grenze neu abgefahren,
            # muss die Ringspur mitkommen - sonst führt sie ab jetzt gegen eine
            # Grenze, die es nicht mehr gibt.
            nudge = self.line.nudge_m
            self.use_contour()
            self.line.nudge_m = nudge
        return field

    def _save_line(self, mode: str, points: list[geo.Point], name: str,
                   line_id: str = "") -> dict:
        if self.field is None:
            raise RuntimeError("Kein Feld ausgewählt")
        record = self.store.save_line({
            **({"id": line_id} if line_id else {}),
            "field_id": self.field["id"],
            "name": name,
            "mode": mode,
            "points": [list(p) for p in points],
            "spacing_m": self.profile.spacing_m,
        })
        self.load_line(record["id"])
        return record

    def startpunkt_im_feld(self) -> Optional[tuple[float, float, float]]:
        """Ost, Nord, Kurs für einen Neuanfang im Feld - für den Simulator.

        Auf der aktiven AB-Spur: an ihrem Anfang, in ihrer Richtung. Sonst in
        der Mitte der Grenze, nach Norden.
        """
        if self.field is None:
            return None
        if self.line is not None and self.line.mode == "ab" and len(self.line.points) >= 2:
            a, b = self.line.points[0], self.line.points[-1]
            kurs = geo.heading_deg(a, b)
            rx, ry = math.cos(math.radians(kurs)), -math.sin(math.radians(kurs))
            versatz = self.line.nudge_m
            return a[0] + rx * versatz, a[1] + ry * versatz, kurs
        grenze = self.field.get("boundary") or []
        if len(grenze) >= 3:
            return (sum(p[0] for p in grenze) / len(grenze),
                    sum(p[1] for p in grenze) / len(grenze), 0.0)
        return 0.0, 0.0, 0.0

    def nudge(self, metres: float) -> float:
        """Trim the whole pattern sideways.

        Real fields drift: a slightly different antenna position between
        machines, or a headland that was not quite square.  Nudging moves every
        pass rather than the one you are on, so the pattern stays consistent.
        """
        if self.line is None:
            raise RuntimeError("Keine Spur aktiv")
        # "Rechts" heißt rechts vom Sitz aus. Auf einer rückwärts gefahrenen
        # AB-Spur und auf dem Ring gegen den Uhrzeigersinn liegt rechts vom
        # Fahrer links vom Muster - die Führung weiß das (right_sign), also
        # wird der Wunsch des Fahrers hier in die Sprache des Musters übersetzt.
        if self.guidance.active:
            metres *= self.guidance.right_sign
        self.line.nudge_m += metres
        if self.line.derived:
            # Die Kontur ist keine gespeicherte Spur. Sie hier anzulegen würde
            # bei jedem Versatz eine neue Spur in die Liste schreiben.
            return self.line.nudge_m
        self.store.save_line({
            "id": self.line.id,
            "field_id": self.field["id"],
            "name": self.line.name,
            "mode": self.line.mode,
            "points": [list(p) for p in self.line.points],
            "spacing_m": self.line.spacing,
            "nudge_m": self.line.nudge_m,
        })
        return self.line.nudge_m

    # -- Vorgewende und Wende ---------------------------------------------

    def headland_ring(self) -> list[list[float]]:
        """Die Vorgewendelinie zum Zeichnen - gerechnet nur, wenn nötig.

        Der Versatz kostet für jeden Eckpunkt einen Blick auf die ganze Grenze.
        Das ist einmal je Änderung nichts und zehnmal je Sekunde zu viel, also
        wird das Ergebnis behalten, bis sich Grenze oder Tiefe ändern.
        """
        if (self.field is None or not self.field.get("boundary")
                or not self.headland.aktiv):
            return []
        tiefe = self.headland.tiefe_m(self.profile.width_m)
        if tiefe <= 0.0:
            return []
        schluessel = (self.field["id"], self.field.get("updated_at"), round(tiefe, 3))
        if self._ring_key != schluessel:
            self._ring_key = schluessel
            self._ring_cache = [
                list(p) for p in headland_module.ring(
                    [tuple(p) for p in self.field["boundary"]], tiefe)
            ]
        return self._ring_cache

    def plan_turn(self, muster: str = "", richtung: str = "") -> dict:
        """Eine Wende planen und vollständig prüfen - gefahren wird noch nichts.

        Zwei Schritte, absichtlich getrennt: erst sieht der Fahrer die Route auf
        der Karte liegen, dann startet er sie. Eine Wende, die auf Knopfdruck
        sofort losfährt, hat niemand vorher angesehen.

        Geprüft wird gegen die Feldgrenze, Punkt für Punkt. Ragt auch nur ein
        Stück hinaus, bleibt ``im_feld`` falsch - und mit der
        Sicherheitsprüfung an lässt sich die Route dann nicht starten.
        """
        self._require_position()
        if self.heading is None:
            raise RuntimeError("Noch kein Kurs - ein Stück geradeaus fahren")
        einstellung = self.headland
        if muster or richtung:
            werte = einstellung.to_dict()
            if muster:
                werte["muster"] = muster
            if richtung:
                werte["richtung"] = richtung
            einstellung = headland_module.HeadlandSettings.from_dict(werte)

        grenze = [tuple(p) for p in (self.field.get("boundary") or [])] \
            if self.field else []
        # Geplant wird ab dem Punkt, auf den auch gelenkt wird - der Achse.
        # Ab dem Gerät geplant, läge die Route sieben Meter hinter der
        # Maschine, und der Folger bräche beim ersten Bogen ab.
        start = self._lenkpunkt()
        pfad = headland_module.plan(
            start, self.heading, einstellung,
            self.profile.spacing_m, einstellung.tiefe_m(self.profile.width_m),
        )
        im_feld = headland_module.route_im_feld(pfad, grenze)
        self.turn_preview = pfad
        self.turn_preview_ok = im_feld
        self._turn_planned_at = start
        return {
            "punkte": [list(p) for p in pfad],
            "im_feld": im_feld,
            "muster": einstellung.muster,
            "richtung": einstellung.richtung,
            "laenge_m": sum(geo.distance(pfad[i], pfad[i + 1])
                            for i in range(len(pfad) - 1)),
            "grenze_vorhanden": len(grenze) >= 3,
        }

    def start_turn(self) -> dict:
        """Die geplante Route übernehmen und ihr folgen."""
        if not self.turn_preview:
            raise RuntimeError("Erst eine Wende planen")
        if self.headland.nur_im_feld and not self.turn_preview_ok:
            raise RuntimeError(
                "Die Route liegt nicht vollständig im Feld. Wenderichtung, "
                "Wendekreis oder Vorgewendetiefe ändern - oder die "
                "Sicherheitsprüfung bewusst abschalten.")
        self._require_position()
        losgefahren = getattr(self, "_turn_planned_at", None)
        if losgefahren is not None and \
                geo.distance(losgefahren, self._lenkpunkt()) > 5.0:
            # Die Route beginnt dort, wo sie geplant wurde. Von hier aus wäre
            # ihr erster Bogen ein Sprung quer über das Feld.
            raise RuntimeError("Die Maschine steht nicht mehr am Planungspunkt - "
                               "Wende neu planen")
        # Der Folger gibt spätestens dort auf, wo auch die Lenkung aufgäbe -
        # sonst zeigte die Anzeige "Wende läuft", während der Motor längst
        # abgeschaltet hat und der Fahrer allein lenkt.
        abbruch = 3.0
        if self.steering is not None and self.steering.config.enabled:
            abbruch = min(abbruch, float(self.steering.config.max_cross_track_m))
        self.turn = headland_module.TurnFollower(pfad=list(self.turn_preview),
                                                 abbruch_abstand_m=abbruch)
        self.note(f"Wende gestartet ({self.headland.muster.upper()}, "
                  f"{self.headland.richtung})")
        return self.turn.to_dict()

    def stop_turn(self, grund: str = "vom Fahrer beendet") -> None:
        if self.turn is None:
            return
        self.turn = None
        # Die Route ist gefahren (oder verworfen). Sie stehen zu lassen hieße:
        # der Knopf sagt weiter „Ω los" und ein Druck schickte die Maschine auf
        # eine Route, die an einer anderen Stelle des Feldes beginnt.
        self.turn_preview, self.turn_preview_ok = [], False
        self.note(f"Wende beendet: {grund}")

    # -- jobs -------------------------------------------------------------

    def start_job(self, operation: str = "") -> dict:
        if self.field is None:
            raise RuntimeError("Kein Feld ausgewählt")
        if self.job is not None:
            return self.job
        self.job = self.store.start_job(
            self.field["id"], self.config.network.device_id,
            self.profile.name, operation,
            self.line.id if self.line else None,
        )
        self.distance_m = 0.0
        self.working_time_s = 0.0
        self.ausbringung = applikation_module.Ausbringung(
            einheit=self.karte.einheit if self.karte else "")
        self.note(f"Arbeit gestartet{': ' + operation if operation else ''}")
        return self.job

    def stop_job(self) -> Optional[dict]:
        if self.job is None:
            return None
        self._flush_track(force=True)
        self.store.update_job(
            self.job["id"],
            ended_at=time.time(),
            distance_m=self.distance_m,
            area_ha=self.coverage.area_ha,
            overlap_ha=self.coverage.overlap_m2 / 10_000.0,
            working_time_s=self.working_time_s,
            coverage=self.coverage.pack(),
            map_id=self.karte_id,
            plan_id=self.plan_id,
            ausbringung=(json.dumps(self.ausbringung.to_dict())
                         if self.ausbringung.nach_wert or self.ausbringung.ohne_wert_m2
                         else ""),
        )
        job = self.store.get_job(self.job["id"])
        self.job = None
        if self.distance_m < MINDESTSTRECKE_ARBEIT_M:
            # Markieren an, Markieren aus, nichts gefahren: das war ein Versehen
            # oder ein Versuch, keine Arbeit. In der Liste und im CSV fürs Büro
            # wäre es eine Zeile mit 0,00 ha, die niemand haben will.
            self.store.delete_job(job["id"])
            self.note("Arbeit verworfen: nichts gefahren")
            return {**job, "verworfen": True}
        self.note(f"Arbeit beendet: {job['area_ha']:.2f} ha")
        return job

    # -- the position pipeline -------------------------------------------

    def on_fix(self, fix: Fix) -> None:
        if not fix.valid:
            return
        now = fix.received_at or time.time()
        self.fix = fix

        if self.plane is None:
            # No field chosen yet: anchor a working plane at the first fix so
            # the display is useful immediately.
            self.plane = geo.LocalPlane(fix.lat, fix.lon)

        self.position = self.plane.to_local(fix.lat, fix.lon)
        dt = (now - self._last_update_at) if self._last_update_at else 0.0
        self._last_update_at = now

        attitude = self.imu.attitude if self.imu is not None else None
        yaw_rate = attitude.yaw_rate_deg_s if (attitude and attitude.fresh) else None
        use_yaw = yaw_rate if (self.imu is not None
                               and self.config.imu.use_for_heading) else None
        heading = self.heading_filter.update(
            fix.course_deg, fix.heading_deg, fix.speed_ms, use_yaw, dt
        )
        self.heading = heading
        if heading is None:
            return

        self.position = self._compensate_terrain(self.position, heading, attitude)

        previous_tool = self.tool_position
        previous_implement = self.implement_position
        self.tool_position = self.profile.tool_position(self.position, heading)
        self.steer_position = self.profile.steer_position(self.position, heading)

        # Das gezogene Gerät schwenkt dem Fahrzeug nach, statt sich mit ihm zu
        # drehen. Erst die Ausrichtung fortschreiben, dann daraus die Lage - in
        # dieser Reihenfolge, sonst markiert man mit der Ausrichtung von vorhin.
        self.implement_heading.update(heading, fix.speed_ms, dt, self.profile)
        self.implement_position = self.profile.implement_position(
            self.position, heading, self.implement_heading.value
        )

        # A position that moved further than the machine could have travelled is
        # a receiver artefact - a re-acquired fix after a gap under trees, or a
        # jump between RTK float and fix.  Believing it would add phantom metres
        # to the record and paint a swath of worked ground straight across the
        # field, so the step is dropped and the trail simply picks up again.
        plausible = True
        if previous_tool is not None and 0 < dt < 5.0:
            step = geo.distance(previous_tool, self.tool_position)
            plausible = step < fix.speed_ms * dt * 3.0 + 5.0
            if plausible:
                self.distance_m += step
                if fix.speed_ms > 0.3:
                    self.working_time_s += dt
        elif previous_tool is not None:
            plausible = False

        if not plausible:
            # Nach einem Positionssprung ist auch die nachlaufende Ausrichtung
            # des Geräts nichts mehr wert - sie wurde aus dem Sprung gerechnet.
            self.implement_heading.reset(heading)

        self._update_headland()
        self._update_guidance(fix)
        # Erst nachschlagen, dann markieren: dann zeigt die Kabine denselben
        # Sollwert, den die Buchung für diesen Schritt verwendet.
        self._update_sollwert()
        self._update_coverage(previous_implement if plausible else None)
        self._update_steering(fix)
        self._record_track(fix, now)

        if self.record_mode is not None:
            last = self._recording[-1] if self._recording else None
            if last is None or geo.distance(last, self.tool_position) > 0.5:
                self._recording.append(self.tool_position)

    def _compensate_terrain(self, antenna: geo.Point, heading: float,
                            attitude) -> geo.Point:
        """Von der Antenne auf den Punkt am Boden rechnen.

        Bei drei Metern Antennenhöhe sind sechs Grad Seitenhang gut 30 cm - die
        Spur wandert genau um diesen Betrag, ohne dass der Empfänger irgendetwas
        Falsches misst. Deshalb wird die Neigung herausgerechnet, bevor
        Führung und Fläche daraus etwas machen.
        """
        if (attitude is None or not attitude.fresh
                or not self.config.imu.terrain_compensation):
            self.terrain_offset_m = (0.0, 0.0)
            return antenna
        right_off, forward_off = terrain_offset(
            attitude, self.profile.antenna_height_m, self.config.imu.roll_sign
        )
        self.terrain_offset_m = (right_off, forward_off)
        h = math.radians(heading)
        forward = (math.sin(h), math.cos(h))
        right = (math.cos(h), -math.sin(h))
        return (antenna[0] - right[0] * right_off - forward[0] * forward_off,
                antenna[1] - right[1] * right_off - forward[1] * forward_off)

    def _update_headland(self) -> None:
        """Restdistanz, Vorgewendelage und Annäherungsalarm nachführen."""
        grenze = [tuple(p) for p in (self.field.get("boundary") or [])] \
            if self.field else []
        vorher = self.headland_status.alarm
        self.headland_status = headland_module.status(
            self.tool_position, self.heading, grenze, self.headland,
            self.profile.width_m,
        )
        if self.headland_status.alarm and not vorher:
            self.note(f"Vorgewende in {self.headland_status.rest_m:.0f} m")
        # Ob das Vorgewende schon dran war, sagt die Fläche - einmal je Sekunde
        # gefragt, nicht zehnmal: die Antwort ändert sich langsamer als die Position.
        jetzt = time.time()
        if grenze and jetzt - self._abdeckung_at > 1.0:
            self._abdeckung_at = jetzt
            self._abdeckung = headland_module.abdeckung(
                self.coverage.is_covered, grenze, self.headland_status.tiefe_m)
        self.headland_status.abdeckung = self._abdeckung
        self.headland_status.hinweis = headland_module.reihenfolge_hinweis(
            self.headland, self._abdeckung,
            self.line.mode if self.line else None,
            self.guidance.pass_number if self.guidance.active else None)

    def _update_guidance(self, fix: Fix) -> None:
        if self.turn is not None and self.tool_position is not None \
                and self.heading is not None:
            # Während einer geplanten Wende führt die Route, nicht die Spur.
            # Gemeldet wird die Abweichung von der Route - damit prüft die
            # Lenkung weiter gegen etwas Sinnvolles statt gegen die verlassene
            # Spur, von der man in einer Wende zwangsläufig weit weg ist.
            zustand = self.turn.solve(
                self._lenkpunkt(), self.heading, fix.speed_ms, self.profile
            )
            if self.turn.abgebrochen:
                # Die Maschine folgt der Route nicht mehr. Jetzt übernimmt der
                # Fahrer - und zwar wirklich: die Lenkung geht aus, und für diesen
                # Zyklus gibt es keine Spur. Sonst fiele die Führung im selben
                # Atemzug auf die nächste Spur zurück, deren Abweichung modulo
                # Spurabstand immer klein aussieht, und die Lenkung zöge mitten in
                # der gescheiterten Wende auf irgendeine Spur ein.
                grund = self.turn.grund or "Wende abgebrochen"
                self.stop_turn(grund)
                if self.steering is not None:
                    self.steering.disarm(f"Wende: {grund}")
                self.guidance = GuidanceState(message=f"Wende abgebrochen: {grund}")
                return
            if self.turn.fertig:
                # Regulär am Ziel: die Route endete geprüft auf der Nachbarspur,
                # dort darf die Spur sofort wieder führen.
                self.stop_turn(self.turn.grund or "Wende beendet")
            else:
                self.guidance = zustand
                return

        if self.line is None or self.tool_position is None or self.heading is None:
            self.guidance = GuidanceState(message="Keine Spur aktiv")
            return
        # Zwischen der Position vom Empfänger und der tatsächlichen Radbewegung
        # liegt Zeit: Empfänger, Programm, Platine, Motor, Lenkgestänge. Wer sie
        # nicht ausgleicht, lenkt immer auf die Stelle, an der die Maschine vor
        # einem Augenblick war - in schnellen Kurven läuft sie deshalb hinterher.
        # Geführt wird deshalb auf den Punkt, an dem sie sein wird; markiert
        # wird weiterhin dort, wo sie wirklich war.
        fuehrungspunkt = self._lenkpunkt()
        vorhalt_m = self.profile.actuator_latency_ms / 1000.0 * max(0.0, fix.speed_ms)
        if vorhalt_m > 0.01:
            h = math.radians(self.heading)
            fuehrungspunkt = (fuehrungspunkt[0] + math.sin(h) * vorhalt_m,
                              fuehrungspunkt[1] + math.cos(h) * vorhalt_m)
        self.guidance = self.line.solve(
            fuehrungspunkt, self.heading, fix.speed_ms, self.profile
        )
        if self.line.mode == "contour":
            ecke = self.line.naechste_ecke(fuehrungspunkt, self.heading)
            if ecke is not None and ecke < 40.0:
                self.guidance.message = (f"Ecke in {ecke:.0f} m - von Hand um die Ecke, "
                                         "danach greift die Lenkung wieder")

    def _lenkpunkt(self) -> Optional[geo.Point]:
        """Worauf geführt wird: die Hinterachse (siehe VehicleProfile.steer_position).

        Ohne Antennenposition - in Tests, die den Werkzeugpunkt direkt setzen -
        bleibt es der Werkzeugpunkt.
        """
        if self.steer_position is not None and self.position is not None:
            return self.steer_position
        return self.tool_position

    def _update_sollwert(self) -> None:
        """Den Sollwert an der Stelle des Geräts nachschlagen.

        Am Gerät, nicht an der Antenne: ausgebracht wird dort, wo der Streuer
        ist, und bei einem gezogenen Gerät liegen zwischen beiden in der Kurve
        mehrere Meter - genug, um an der Zonengrenze die falsche Menge zu
        nehmen.
        """
        if self.karte_lokal is None or self.implement_position is None:
            self.sollwert = None
            return
        self.sollwert = self.karte_lokal.wert_bei(self.implement_position)

    def _update_coverage(self, previous_implement: Optional[geo.Point]) -> None:
        """Mark the ground swept since the previous position.

        `previous_implement` is None when the last step was not believable; the
        anchor is then simply moved without painting, so a dropout leaves a gap
        in the map rather than a false stripe.

        Markiert wird an der Lage **des Geräts** und mit **seiner** Ausrichtung.
        Beim starren Anbau ist das der Werkzeugpunkt wie bisher; beim gezogenen
        Gerät liegt es in der Kurve innerhalb der Fahrspur, und genau das soll
        in der Karte stehen.
        """
        if self.job is None or self.implement_position is None \
                or self.heading is None:
            return
        kurs = self.implement_heading.value if self.profile.trailed else self.heading
        if kurs is None:
            kurs = self.heading
        boundary = None
        if self.field and self.field.get("boundary"):
            boundary = [tuple(p) for p in self.field["boundary"]]
        if self.auto_sections:
            self.coverage.update_auto_sections(
                self.implement_position, kurs, self.sections,
                speed_ms=self.fix.speed_ms if self.fix else 0.0,
                boundary=boundary,
                pcc=self.profile.section_pcc,
            )
        if previous_implement is not None:
            self.coverage.add_swath(
                previous_implement, self.implement_position, kurs, self.sections
            )

    def _update_steering(self, fix: Fix) -> None:
        if self.steering is None:
            return
        attitude = self.imu.attitude if self.imu is not None else None
        context = SteerContext(
            speed_ms=fix.speed_ms,
            wheelbase_m=self.profile.wheelbase_m,
            yaw_rate_deg_s=(attitude.yaw_rate_deg_s
                            if attitude and attitude.fresh else None),
            cross_track_m=self.guidance.cross_track_m,
        )
        command = self.steering.update(self.guidance, fix, context)
        # On the simulator, close the loop so autosteer can be demonstrated and
        # tuned without a machine.
        #
        # Solange die Automatik nicht greift, gehört das Lenkrad dem Fahrer -
        # am Schreibtisch also dem Regler unter Menü → System. Hier bei jeder
        # Position eine Null hineinzuschreiben hieß: der Regler stand zehnmal
        # je Sekunde wieder auf gerade und war damit wirkungslos. Genullt wird
        # nur im Augenblick des Abschaltens, so wie es die echte Anlage tut.
        if self.simulator is not None:
            if command.engaged:
                self.simulator.set_steer(command.angle_deg)
            elif self._steering_was_engaged:
                self.simulator.set_steer(0.0)
        self._steering_was_engaged = command.engaged

    def _record_track(self, fix: Fix, now: float) -> None:
        if self.job is None or self.tool_position is None:
            return
        if (self._last_track_point is not None
                and geo.distance(self._last_track_point, self.tool_position)
                < TRACK_MIN_DISTANCE_M):
            return
        self._last_track_point = self.tool_position
        self._track_buffer.append((
            now, fix.lat, fix.lon, fix.altitude, fix.speed_ms,
            self.heading, fix.fix_quality, self.guidance.cross_track_m,
        ))
        self._flush_track()

    def _flush_track(self, force: bool = False) -> None:
        if not self._track_buffer or self.job is None:
            return
        if force or len(self._track_buffer) >= TRACK_FLUSH_COUNT:
            self.store.add_track_points(self.job["id"], self._track_buffer)
            self._track_buffer = []

    def tick(self, now: Optional[float] = None) -> None:
        """Regelmäßiger Herzschlag, unabhängig von eintreffenden Positionen.

        Die ganze Kette hängt sonst daran, dass Positionen kommen: bleibt der
        Empfänger stehen, wird auch nie neu entschieden, ob noch gelenkt werden
        darf. Die Hardware fängt das über ihren Failsafe ab, aber die Anzeige
        stünde weiter auf "lenkt". Deshalb wird hier von außen nachgesehen.
        """
        now = now or time.time()
        age = now - self.fix.received_at if (self.fix and self.fix.received_at) else 99.0
        if age > 2.0 and self.turn is not None:
            # Eine Wende ohne Positionsdaten weiterzuführen hieße, blind auf
            # einem Bogen zu lenken. Sie endet hier, nicht erst beim nächsten Fix.
            self.stop_turn("keine GPS-Daten")
        if self.steering is None or not self.steering.armed:
            return
        if age > 2.0:
            self.steering.disarm("keine GPS-Daten mehr")
            self.note("Lenkung abgeschaltet: keine GPS-Daten")

    # -- output -----------------------------------------------------------

    def note(self, message: str) -> None:
        self.messages.append(f"{time.strftime('%H:%M:%S')}  {message}")
        del self.messages[:-30]

    def state(self) -> dict[str, Any]:
        fix = self.fix
        return {
            "time": time.time(),
            "device": {
                "id": self.config.network.device_id,
                "name": self.config.network.device_name,
                "role": self.config.network.role,
            },
            "fix": fix.to_dict() if fix else None,
            "position": list(self.position) if self.position else None,
            "tool_position": list(self.tool_position) if self.tool_position else None,
            "implement": {
                "position": (list(self.implement_position)
                             if self.implement_position else None),
                "heading": self.implement_heading.value,
                "trailed": self.profile.trailed,
                "hitch_length_m": self.profile.hitch_length_m,
            },
            "heading": self.heading,
            "guidance": self.guidance.to_dict(),
            "headland": {
                **self.headland.to_dict(),
                **self.headland_status.to_dict(),
                "ring": self.headland_ring(),
            },
            "turn": {
                "aktiv": self.turn is not None,
                "punkte": ([list(p) for p in self.turn.pfad] if self.turn
                           else [list(p) for p in self.turn_preview]),
                "geplant": bool(self.turn_preview) and self.turn is None,
                "im_feld": self.turn_preview_ok,
            },
            "line": self.line.to_dict() if self.line else None,
            "field": {
                "id": self.field["id"],
                "name": self.field["name"],
                "area_ha": self.field["area_ha"],
                "boundary": self.field["boundary"],
                "datum": [self.field["datum_lat"], self.field["datum_lon"]],
                # Steht die Maschine im Feld? None ohne Grenze oder Position.
                "im_feld": (geo.point_in_polygon(self.position, [tuple(p) for p in self.field["boundary"]])
                            if self.position is not None and len(self.field["boundary"]) >= 3 else None),
            } if self.field else None,
            "profile": asdict(self.profile),
            "sections": [asdict(s) for s in self.sections],
            "auto_sections": self.auto_sections,
            "job": {
                **self.job,
                "area_ha": self.coverage.area_ha,
                "overlap_ha": self.coverage.overlap_m2 / 10_000.0,
                "overlap_percent": self.coverage.overlap_percent,
                "distance_m": self.distance_m,
                "working_time_s": self.working_time_s,
            } if self.job else None,
            "coverage": {
                "cell_size": self.coverage.cell_size,
                "area_ha": self.coverage.area_ha,
                "overlap_percent": self.coverage.overlap_percent,
                "cell_count": len(self.coverage.cells),
            },
            "recording": {
                "mode": self.record_mode,
                "points": len(self._recording),
                "area_ha": (geo.polygon_area(self._recording) / 10_000.0
                            if self.record_mode == "boundary"
                            and len(self._recording) > 2 else 0.0),
                "pending_a": list(self._pending_a) if self._pending_a else None,
            },
            "imu": ({
                **self.imu.attitude.to_dict(),
                "status": self.imu.status,
                "healthy": self.imu.healthy,
                "terrain_offset_cm": [self.terrain_offset_m[0] * 100.0,
                                      self.terrain_offset_m[1] * 100.0],
                "compensation": self.config.imu.terrain_compensation,
            } if self.imu is not None else None),
            "steering": self.steering.status() if self.steering else None,
            "plan": self._plan_state(),
            "applikation": self._applikation_state(),
            "messages": self.messages[-6:],
        }

    def _plan_state(self) -> Optional[dict]:
        """Der Plan fürs Bild: Bahnen, Ringe, Fortschritt, aktuelle Bahn."""
        if self.plan is None:
            return None
        stand = self.plan_fortschritt()
        return {
            **self.plan.to_dict(),
            "id": self.plan_id,
            "bahn": self.plan_bahn,
            "fortschritt": stand.to_dict() if stand else None,
        }

    def _applikation_state(self) -> dict:
        """Der Sollwert fürs Bild - jede Position neu, ohne die großen Zahlen.

        Die Mengenrechnung fürs ganze Feld steht bewusst nicht hier: sie tastet
        das Feld ab und hat in einem Bild, das zehnmal je Sekunde über die
        Leitung geht, nichts zu suchen. Die holt sich die Oberfläche einzeln
        über ``karte_uebersicht``.
        """
        if self.karte is None:
            return {"aktiv": False}
        return {
            "aktiv": True,
            "id": self.karte_id,
            "name": self.karte.name,
            "einheit": self.karte.einheit,
            "art": self.karte.art,
            "sollwert": self.sollwert,
            "ausgebracht": self.ausbringung.to_dict(),
        }

    def _require_position(self) -> None:
        if self.tool_position is None:
            raise RuntimeError("Noch keine Position - warte auf GPS")
