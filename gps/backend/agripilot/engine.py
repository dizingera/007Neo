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
from . import feldwerkzeuge, gespann

import asyncio
import math
import time
from dataclasses import asdict, replace
from typing import Any, Optional

from . import abstimmung as abstimmung_module
from . import geo, headland as headland_module, randspuren as randspuren_module, saatplan as saatplan_module, storage
from .actuators import SteerContext
from .config import Config
from .coverage import CoverageMap, Section, build_sections
from .guidance import (
    GuidanceLine,
    GuidanceState,
    HeadingFilter,
    ImplementHeading,
    TRAKTOR_FELDER,
    VehicleProfile,
    rand_seite,
)
from .imu import terrain_offset
from .nmea import Fix
from .kalman import Positionsfilter
from . import hindernisse as hindernisse_module
from .flotte import Flotte
from .rtkbruecke import RtkBruecke
from .writer import HintergrundSchreiber

TRACK_MIN_DISTANCE_M = 1.0
TRACK_FLUSH_COUNT = 25


# Unter dieser Strecke gilt eine Arbeit als nicht begonnen: Markieren an und
# gleich wieder aus, ohne zu fahren. Fünf Meter ist eine Traktorlänge.
MINDESTSTRECKE_ARBEIT_M = 5.0

# Fortsetzen nach Stromausfall. Alle halbe Minute wird die bearbeitete Fläche
# mitgesichert - sonst bliebe von einem halben Tag Arbeit nach dem Absturz nur
# die Fahrspur, aber keine Fläche. Was länger als eine Schicht liegt, wird nicht
# mehr fortgesetzt, sondern abgeschlossen.
# Feld erkennen: erst wenn der Traktor ein paar Sekunden in einem anderen Feld
# steht, wird es vorgeschlagen - nicht beim Überfahren der Ecke.
FELD_VORSCHLAG_S = 3.0
FELDER_NEU_LADEN_S = 30.0

# Wie lange der Empfänger schweigen darf, bevor die Lenkung abgeschaltet wird -
# dieselbe Sekunde, ab der der Lenkregler veraltete Daten selbst ablehnt. Der
# Dauerlauf zeigte: mit zwei Sekunden stand die Anzeige noch doppelt so lange
# auf "lenkt", obwohl keine neue Position mehr kam.
GPS_STILLSTAND_S = 1.0

SESSION_KEY = "session"
CHECKPOINT_S = 30.0
RESUME_MAX_AGE_S = 3 * 3600.0

# Fahrgassen: Spritze und Streuer auf dem Betrieb arbeiten mit 21 m.
FAHRGASSEN_WEITE_VORGABE = 21.0
FAHRGASSEN_WEITE_MIN = 6.0
FAHRGASSEN_WEITE_MAX = 60.0
SAATPLAN_PRAEFIX = "Saatplan "
SAATPLAN_ECKE_M = 40.0      # so weit darf die Antenne von der Startecke weg sein


class Engine:
    def __init__(self, config: Config, store: storage.Storage) -> None:
        self.config = config
        self.store = store

        self.profile = self._load_profile()
        # Antenne und Lenkung vom gewählten Traktor (legt die Traktorliste beim ersten
        # Start aus dem bisherigen Gespann an).
        self.profile = VehicleProfile(**{**asdict(self.profile), **self._traktor_werte()})
        self.sections: list[Section] = build_sections(
            self.profile.width_m, self.profile.sections
        )
        self.heading_filter = HeadingFilter()

        self.field: Optional[dict] = None
        self.plane: Optional[geo.LocalPlane] = None
        self.line: Optional[GuidanceLine] = None
        self._saison_stand = None
        self.saisonspur_neu = None
        self.coverage = CoverageMap(cell_size=0.5)
        self.job: Optional[dict] = None

        self.positionsfilter = Positionsfilter()
        self.rtk_bruecke = RtkBruecke()
        # Hindernisse des geladenen Feldes in lokalen Metern (hindernisse.py).
        self._hindernisse: list[dict] = []
        self._hindernisse_feld: Optional[str] = None
        self._hindernisse_at = -1e9
        self.hindernis_warnung: Optional[dict] = None
        # Fernansicht: die Lage der anderen Maschinen (flotte.py).
        self.flotte = Flotte()
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
        self.turn_preview_fahrbar = True
        self.turn_preview_radius = (float("inf"), 0.0)
        self._turn_planned_at: Optional[geo.Point] = None
        self._ring_key: Optional[tuple] = None
        self._ring_cache: list[list[float]] = []
        self._abdeckung_at = 0.0          # zuletzt abgetastet (Sekunden)
        self._abdeckung: Optional[float] = None
        self._steering_was_engaged = False

        # Lenk-Assistent: stellt Verstärkung und Vorhalt der Spurregelung in
        # kleinen Schritten nach (abstimmung.py). Der Modus überlebt den Neustart.
        self.abstimmung = abstimmung_module.Lenkabstimmung(
            lesen=lambda: {"steer_gain": self.profile.steer_gain,
                           "actuator_latency_ms": self.profile.actuator_latency_ms},
            setzen=lambda werte: self.update_profile(werte, stempeln=False))
        gespeichert = self.store.get_setting("lenkabstimmung") or {}
        if gespeichert.get("modus") in ("beobachten", "automatisch"):
            self.abstimmung.ausgangswerte = gespeichert.get("ausgangswerte")
            self.abstimmung.modus = gespeichert["modus"]
        self._abst_spur = (None, 0.0)           # (Spurnummer, seit wann)
        self._nudge_zeit = 0.0

        self.record_mode: Optional[str] = None          # "boundary" | "curve"
        self._recording: list[geo.Point] = []
        self._pending_a: Optional[geo.Point] = None

        self.distance_m = 0.0
        self.working_time_s = 0.0
        self.session_started_at = time.time()
        self.messages: list[str] = []
        # Was beim letzten Mal offen blieb (Zündung aus statt „Arbeit beenden"),
        # wird jetzt abgeschlossen - sonst steht es ewig als „läuft" in der Liste.
        # Die jüngste frische Arbeit bleibt offen: resume_session() setzt sie fort
        # oder schließt sie, wenn das Feld nicht mehr da ist.
        self._resumable_job = store.find_resumable_job(
            config.network.device_id, RESUME_MAX_AGE_S)
        verwaist = store.close_orphan_jobs(
            config.network.device_id,
            self._resumable_job["id"] if self._resumable_job else None)
        self._checkpoint_at = 0.0
        self._kurs_warnung = ""
        self.feld_vorschlag: Optional[dict] = None
        self._vorschlag_kandidat: Optional[str] = None
        self._vorschlag_seit = 0.0
        self._vorschlag_geprueft = 0.0
        self._felder_cache: list[tuple] = []
        self._felder_cache_at = -1e9
        if verwaist:
            self.note(f"{verwaist} unterbrochene Arbeit(en) vom letzten Mal abgeschlossen")
        self.auto_sections = True
        self.teilbreiten = None

        # Fahrspur und Zwischenstand schreibt ein eigener Faden (writer.py).
        self._schreiber = HintergrundSchreiber(store.path)
        store.vor_schliessen = self._schreiber.beenden
        self._sicherung_laeuft = False      # ein Zwischenstand ist unterwegs
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
            # Feste Kennung: jedes Gerät legt beim ersten Start so eine Maschine an, und mit
            # zufälligen Kennungen hätte der Master nach dem Abgleich eine je Tablet.
            eintrag = {"id": "standard", **asdict(self.profile)}
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
        gespann.masse_pruefen(werte)
        werte["name"] = name
        neu = {"id": storage.new_id(), **asdict(VehicleProfile(**werte)), "stand": time.time()}
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
        # Von den Grundwerten aus, nicht von der vorher gewählten Maschine: einer Maschine aus
        # einer älteren Version fehlen neuere Felder, und die erbte sie sonst vom Vorgänger
        # (so bekam "SuperMax" das MyTerra-Gerät der zuletzt angelegten Maschine).
        # Antenne und Lenkung kommen vom gewählten Traktor, nicht aus dem Gerät.
        werte = {**asdict(VehicleProfile()),
                 **{k: v for k, v in eintrag.items()
                    if k in VehicleProfile.__dataclass_fields__ and k not in TRAKTOR_FELDER},
                 **self._traktor_werte()}
        self.update_profile(werte, stempeln=False)    # Auswählen ändert die Maschine nicht
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
        # Merken, dass sie gelöscht wurde - sonst bringt der nächste Abgleich sie zurück.
        grab = self.store.get_setting("vehicle_profiles_deleted") or {}
        grab[profile_id] = time.time()
        self.store.set_setting("vehicle_profiles_deleted", grab)
        if war_aktiv:
            self.select_profile(rest[0]["id"])

    def abstimmung_modus(self, modus: str) -> dict:
        """Lenk-Assistent aus / nur Vorschläge / stellt selbst nach."""
        self.abstimmung.einschalten(modus)
        self.store.set_setting("lenkabstimmung", {
            "modus": self.abstimmung.modus, "ausgangswerte": self.abstimmung.ausgangswerte})
        self.note(f"Lenk-Assistent: {modus}")
        return self.abstimmung.status()

    def abstimmung_zuruecksetzen(self) -> dict:
        self.abstimmung.zuruecksetzen()
        self.note("Lenk-Assistent: Ausgangswerte wiederhergestellt")
        return self.abstimmung.status()

    def _abstimmung_beobachten(self, lenkt: bool, fix) -> None:
        """Dem Assistenten die Querabweichung geben - aber nur, wo sie etwas über
        die Regelung sagt: geradeaus, ohne Wende, ohne frischen Spurwechsel."""
        if self.abstimmung.modus == "aus":
            return
        jetzt = time.time()
        nummer = self.guidance.pass_number
        if nummer != self._abst_spur[0]:
            self._abst_spur = (nummer, jetzt)
        ruhig = (self.turn is None and self.line is not None and self.line.mode != "contour"
                 and self.guidance.active and abs(self.guidance.heading_error_deg) < 15.0
                 and jetzt - self._abst_spur[1] > 5.0 and jetzt - self._nudge_zeit > 5.0)
        self.abstimmung.beobachten(jetzt, self.guidance.cross_track_m, fix.speed_ms, lenkt, ruhig)
        if self.abstimmung.modus == "automatisch" and self.abstimmung.schritte:
            self.store.set_setting("lenkabstimmung", {
                "modus": self.abstimmung.modus, "ausgangswerte": self.abstimmung.ausgangswerte})

    def update_profile(self, values: dict, stempeln: bool = True) -> VehicleProfile:
        """Das Gespann ändern. Antenne und Lenkung gehen an den gewählten Traktor, der Rest
        an die gewählte Maschine (das Gerät)."""
        fields = VehicleProfile.__dataclass_fields__
        self.list_profiles()          # legt auf einem frischen Gerät die erste Maschine an
        current = asdict(self.profile)
        values = dict(values)
        gespann.masse_pruefen(values)
        current.update({k: v for k, v in values.items() if k in fields})
        self.profile = VehicleProfile(**current)
        self.store.set_setting("vehicle_profile", current)
        # ... und in der Maschinenliste die aktive mitziehen. Die Traktorwerte stehen dort
        # weiter mit drin, damit ein Tablet mit älterer Version das Gespann noch versteht.
        profile = self.store.get_setting("vehicle_profiles") or []
        aktiv = self.store.get_setting("vehicle_profile_active") or ""
        if profile and any(p["id"] == aktiv for p in profile):
            stand = {"stand": time.time()} if stempeln else {}
            self.store.set_setting("vehicle_profiles", [
                {**p, **current, "id": aktiv, **stand} if p["id"] == aktiv else p
                for p in profile])
        self._traktor_schreiben({k: current[k] for k in TRAKTOR_FELDER}, stempeln)
        self.sections = build_sections(self.profile.width_m, self.profile.sections)
        if self.line is not None:
            # Changing the working width changes the pass spacing, and with it
            # which pass the machine is on - but not the reference line itself.
            self.line.spacing = self.profile.spacing_m
        # Aus einem angebauten Gerät ist gerade ein gezogenes geworden (oder
        # umgekehrt). Die nachlaufende Ausrichtung des alten Geräts weiterzu-
        # schleppen hieße, mit der Geometrie des vorigen zu markieren.
        self.implement_heading.reset(self.heading)
        if self.turn is not None: self.stop_turn("Gespann geändert")
        self.turn_preview=[]
        self.turn_preview_ok=False
        return self.profile

    # -- Traktoren ----------------------------------------------------------
    # Ein Traktor zieht viele Geräte. Wo seine Antenne sitzt und wie er lenkt, stellt
    # man einmal ein (TRAKTOR_FELDER); gewählt wird er getrennt vom Gerät. Gerechnet
    # wird mit dem Gespann: self.profile = Gerät + Traktor.

    @staticmethod
    def traktor_kennung(name: str) -> str:
        """Feste Kennung aus dem Namen: Master und Tablets legen beim ersten Start
        dieselben Traktoren an - mit zufälligen Kennungen gäbe es sie nach dem Abgleich doppelt."""
        import re
        import unicodedata
        glatt = unicodedata.normalize("NFKD", name.lower()).encode("ascii", "ignore").decode()
        return "traktor-" + (re.sub(r"[^a-z0-9]+", "-", glatt).strip("-") or "ohne-name")

    def _erste_traktoren(self) -> list[dict]:
        """Beim ersten Start mit Traktorliste: aus dem, was es schon gibt.

        Die Werte kommen aus dem gerade gewählten Gespann - dort stehen die gemessenen
        Antennenmaße. Die Namen aus den Maschinen (1.5.6) und der Liste aus MyTerra.
        Stand 0: ein Traktor, den irgendwo schon jemand eingestellt hat, gewinnt.
        """
        werte = {k: getattr(self.profile, k) for k in TRAKTOR_FELDER}
        namen: list[str] = []
        katalog = (self.store.get_setting("myterra_maschinen") or {}).get("traktoren") or []
        maschinen = self.store.get_setting("vehicle_profiles") or []
        for name in [self.profile.traktor, *[p.get("traktor") for p in maschinen], *katalog]:
            name = (name or "").strip()
            if name and name not in namen:
                namen.append(name)
        return [{"id": self.traktor_kennung(n), "name": n, **werte, "stand": 0.0}
                for n in (namen or ["Traktor"])]

    def list_traktoren(self) -> list[dict]:
        liste = self.store.get_setting("traktoren") or []
        aktiv = self.store.get_setting("traktor_aktiv") or ""
        if not liste:
            liste = self._erste_traktoren()
            self.store.set_setting("traktoren", liste)
        if not any(t["id"] == aktiv for t in liste):
            aktiv = liste[0]["id"]
            self.store.set_setting("traktor_aktiv", aktiv)
        return [{**t, "aktiv": t["id"] == aktiv} for t in liste]

    def aktiver_traktor(self) -> dict:
        return next(t for t in self.list_traktoren() if t["aktiv"])

    def _traktor_werte(self) -> dict:
        basis = asdict(VehicleProfile())
        traktor = self.aktiver_traktor()
        return {k: traktor.get(k, basis[k]) for k in TRAKTOR_FELDER}

    def _traktor_schreiben(self, werte: dict, stempeln: bool) -> None:
        """Die Traktorwerte des Gespanns in den gewählten Traktor zurückschreiben."""
        liste = [dict(t) for t in self.list_traktoren()]
        for t in liste:
            aktiv = t.pop("aktiv")
            if aktiv and any(t.get(k) != v for k, v in werte.items()):
                t.update(werte)
                if stempeln or not self.config.is_master:
                    # Auch die automatische Abstimmung im Tablet muss als neue
                    # Traktorfassung zum Büro gelangen; das Gerät bleibt unverändert.
                    t["stand"] = max(time.time(), float(t.get("stand") or 0.0) + 0.000001)
        self.store.set_setting("traktoren", liste)

    def select_traktor(self, traktor_id: str) -> dict:
        if not any(t["id"] == traktor_id for t in self.list_traktoren()):
            raise KeyError("Traktor nicht gefunden")
        self.store.set_setting("traktor_aktiv", traktor_id)
        self.update_profile(self._traktor_werte(), stempeln=False)
        traktor = self.aktiver_traktor()
        self.note(f"Traktor: {traktor['name']}")
        return traktor

    def save_traktor_as(self, name: str, values: dict | None = None) -> dict:
        """Einen weiteren Traktor anlegen - mit den Werten des gewählten als Vorlage."""
        name = (name or "").strip()
        if not name:
            raise ValueError("Der Traktor braucht einen Namen")
        liste = [dict(t) for t in self.list_traktoren()]
        if any(t["name"].lower() == name.lower() for t in liste):
            raise ValueError(f"Einen Traktor „{name}“ gibt es schon")
        werte = self._traktor_werte()
        werte.update({k: v for k, v in (values or {}).items() if k in TRAKTOR_FELDER})
        gespann.masse_pruefen(werte)
        kennung = self.traktor_kennung(name)
        if any(t["id"] == kennung for t in liste):
            kennung = storage.new_id()
        for t in liste:
            t.pop("aktiv", None)
        liste.append({"id": kennung, "name": name, **werte, "stand": time.time()})
        self.store.set_setting("traktoren", liste)
        return self.select_traktor(kennung)

    def update_traktor(self, values: dict) -> dict:
        """Den gewählten Traktor ändern: Name, Antenne, Lenkung."""
        values=dict(values)
        gespann.masse_pruefen(values)
        liste = [dict(t) for t in self.list_traktoren()]
        name = (values.get("name") or "").strip() if "name" in values else None
        for t in liste:
            if t.pop("aktiv"):
                if name is not None:
                    if not name:
                        raise ValueError("Der Traktor braucht einen Namen")
                    t["name"] = name
                t.update({k: v for k, v in values.items() if k in TRAKTOR_FELDER})
                t["stand"] = time.time()
        self.store.set_setting("traktoren", liste)
        self.update_profile(self._traktor_werte(), stempeln=False)
        return self.aktiver_traktor()

    def delete_traktor(self, traktor_id: str) -> None:
        liste = self.list_traktoren()
        if len(liste) <= 1:
            raise ValueError("Der letzte Traktor bleibt - einer muss es geben")
        rest = [dict(t) for t in liste if t["id"] != traktor_id]
        if len(rest) == len(liste):
            raise KeyError("Traktor nicht gefunden")
        war_aktiv = any(t["id"] == traktor_id and t["aktiv"] for t in liste)
        for t in rest:
            t.pop("aktiv", None)
        self.store.set_setting("traktoren", rest)
        grab = dict(self.store.get_setting("traktoren_geloescht") or {})
        grab[traktor_id] = time.time()
        self.store.set_setting("traktoren_geloescht", grab)
        if war_aktiv:
            self.select_traktor(rest[0]["id"])

    def _traktoren_mergen(self, eingang: dict) -> None:
        """Traktoren abgleichen; Arbeiter behalten ihre Fahrzeug- und Lenkwerte."""
        if not isinstance(eingang.get("traktoren"), list):
            return                                   # ein Gerät mit älterer Version
        lokal = {t["id"]: {k: v for k, v in t.items() if k != "aktiv"}
                 for t in self.list_traktoren()}
        grab = dict(self.store.get_setting("traktoren_geloescht") or {})
        aktiv_lokal = self.store.get_setting("traktor_aktiv") if not self.config.is_master else None
        for kennung, zeit in (eingang.get("traktoren_geloescht") or {}).items():
            if kennung == aktiv_lokal:
                continue  # Der Büro-Abgleich darf den Traktor in der Kabine nicht umschalten.
            grab[kennung] = max(float(zeit or 0.0), float(grab.get(kennung, 0.0)))
        for t in eingang["traktoren"]:
            if not isinstance(t, dict) or not t.get("id") or not (t.get("name") or "").strip():
                continue
            stand = float(t.get("stand") or 0.0)
            if t["id"] in grab and float(grab[t["id"]]) >= stand:
                continue
            sauber = {"id": str(t["id"]), "name": str(t["name"]).strip(), "stand": stand,
                      **{k: v for k, v in t.items() if k in TRAKTOR_FELDER}}
            alt = lokal.get(t["id"])
            if alt is not None:
                sauber.update({k: alt[k] for k in gespann.MASSE if k in TRAKTOR_FELDER and k not in t and k in alt})
            if alt is not None and not self.config.is_master:
                # Die Kabine bestimmt ihre Fahrzeug- und Lenkabstimmung. Vom Büro
                # kommen Namen und Listen, keine Ersatzwerte für den lokalen Einbau.
                sauber.update({k: alt[k] for k in TRAKTOR_FELDER if k in alt})
            if alt is None or stand > float(alt.get("stand") or 0.0):
                lokal[t["id"]] = sauber
        for kennung, zeit in grab.items():
            if kennung == aktiv_lokal:
                continue
            alt = lokal.get(kennung)
            if alt is not None and float(alt.get("stand") or 0.0) <= float(zeit) and len(lokal) > 1:
                del lokal[kennung]
        self.store.set_setting("traktoren", list(lokal.values()))
        self.store.set_setting("traktoren_geloescht", grab)
        werte = self._traktor_werte()
        if any(getattr(self.profile, k) != v for k, v in werte.items()):
            self.update_profile(werte, stempeln=False)

    # -- Maschinen zwischen Geräten teilen ---------------------------------

    def profile_export(self) -> dict:
        """Maschinen und Traktoren für den Abgleich mit anderen Geräten (Master/Arbeiter, Datenpaket)."""
        maschinen = []
        for p in self.list_profiles():
            p = dict(p)
            p.pop("aktiv", None)
            maschinen.append(p)
        traktoren = []
        for t in self.list_traktoren():
            t = dict(t)
            t.pop("aktiv", None)
            traktoren.append(t)
        return {"maschinen": maschinen,
                "geloescht": dict(self.store.get_setting("vehicle_profiles_deleted") or {}),
                "traktoren": traktoren,
                "traktoren_geloescht": dict(self.store.get_setting("traktoren_geloescht") or {})}

    def profile_merge(self, eingang: dict) -> dict:
        """Maschinen eines anderen Geräts übernehmen: je Maschine gilt die neuere.

        Neue kommen dazu, geänderte ersetzen die ältere Fassung, gelöschte
        verschwinden - aber nie die letzte, und die gerade gewählte Maschine
        bleibt gewählt (ihre Werte werden neu geladen, falls sie sich geändert haben).
        """
        self._traktoren_mergen(eingang)
        felder = VehicleProfile.__dataclass_fields__
        lokal = {}
        for p in self.list_profiles():
            p = dict(p)
            p.pop("aktiv", None)
            lokal[p["id"]] = p
        aktiv = self.store.get_setting("vehicle_profile_active") or ""
        grab = dict(self.store.get_setting("vehicle_profiles_deleted") or {})
        for kennung, zeit in (eingang.get("geloescht") or {}).items():
            grab[kennung] = max(float(zeit or 0.0), float(grab.get(kennung, 0.0)))
        zaehler = {"neu": 0, "aktualisiert": 0, "entfernt": 0}
        for p in eingang.get("maschinen") or []:
            if not isinstance(p, dict) or not p.get("id"):
                continue
            stand = float(p.get("stand") or 0.0)
            if float(grab.get(p["id"], 0.0)) >= stand and p["id"] in grab:
                continue                                   # die Löschung ist neuer
            sauber = {"id": str(p["id"]), "stand": stand,
                      **{k: v for k, v in p.items() if k in felder}}
            alt = lokal.get(p["id"])
            if alt is not None:
                sauber.update({k: alt[k] for k in gespann.MASSE if k not in p and k in alt})
            if alt is None:
                lokal[p["id"]] = sauber
                zaehler["neu"] += 1
            elif stand > float(alt.get("stand") or 0.0):
                lokal[p["id"]] = sauber
                zaehler["aktualisiert"] += 1
        for kennung, zeit in grab.items():
            alt = lokal.get(kennung)
            if alt is not None and float(alt.get("stand") or 0.0) <= float(zeit) and len(lokal) > 1:
                del lokal[kennung]
                zaehler["entfernt"] += 1
        if sum(zaehler.values()) == 0 and grab == (self.store.get_setting("vehicle_profiles_deleted") or {}):
            return zaehler
        liste = list(lokal.values())
        self.store.set_setting("vehicle_profiles", liste)
        self.store.set_setting("vehicle_profiles_deleted", grab)
        if aktiv not in lokal:
            aktiv = liste[0]["id"]
        self.store.set_setting("vehicle_profile_active", aktiv)
        # Die gewählte Maschine neu laden: ihre Werte können sich geändert haben.
        # Antenne und Lenkung bleiben beim gewählten Traktor.
        eintrag = lokal[aktiv]
        werte = {k: v for k, v in eintrag.items() if k in felder and k not in TRAKTOR_FELDER}
        if werte != {k: v for k, v in asdict(self.profile).items() if k in werte}:
            self.update_profile(werte, stempeln=False)
        return zaehler

    def update_headland(self, values: dict) -> headland_module.HeadlandSettings:
        """Vorgewende und Wendemuster ändern.

        Eine laufende Wende wird dabei abgebrochen: sie wurde nach den alten
        Werten geplant, und eine Route halb nach neuen Zahlen weiterzufahren
        wäre die schlechteste von beiden.
        """
        if "grenzabstand_m" in values and (not math.isfinite(float(values["grenzabstand_m"])) or not 0<=float(values["grenzabstand_m"])<=20):
            raise ValueError("Grenzabstand muss zwischen 0 und 20 m liegen")
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
        self.positionsfilter.reset()
        self.coverage = CoverageMap(cell_size=0.5)
        self.line = None
        # Die Saisonspur zuerst: die Fahrgassen vom Säen sollen beim Düngen und
        # Spritzen wieder unter den Rädern liegen – bis zur nächsten Aussaat, ohne dass
        # jemand daran denken muss. Sonst die zuletzt benutzte Spur.
        saison = self.store.season_line(field_id)
        self._saison_stand = self._saison_kennung(saison)
        self.saisonspur_neu = None
        lines = self.store.list_lines(field_id)
        # Randspuren (aus der Grenze erzeugt) gehen nicht vor: sie sind alle
        # gleichzeitig entstanden, "zuletzt benutzt" hieße sonst "zufällig eine
        # davon". Gibt es keine eigene Spur, die längste gerade Kante - an ihr
        # fährt man üblicherweise die erste Spur.
        eigene = [l for l in lines if not randspuren_module.ist_randspur(l["name"])]
        rand_ab = [l for l in lines if randspuren_module.ist_randspur(l["name"]) and l["mode"] == "ab"]
        if saison is not None:
            self.load_line(saison["id"])
        elif eigene:
            self.load_line(eigene[0]["id"])
        elif rand_ab:
            laengste = max(rand_ab, key=lambda l: geo.distance(tuple(l["points"][0]), tuple(l["points"][-1])))
            self.load_line(laengste["id"])
        elif lines:
            self.load_line(lines[0]["id"])
        self.note(f"Feld geladen: {field['name']}")
        self._remember_session()
        return field

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

    def fahrgassen_weite(self) -> float:
        """Abstand der Fahrgassen, mit dem eine Spur zur Saisonspur wird.

        Gehört zum Betrieb (Spritze und Streuer), nicht zum Gerät, das gerade
        dran ist. Eine schon gesetzte Saisonspur behält ihren Abstand, auch wenn
        sich dieser Wert ändert - die Fahrgassen liegen dann schon im Feld.
        """
        wert = self.store.get_setting("fahrgassen_weite")
        return float(wert) if wert else FAHRGASSEN_WEITE_VORGABE

    def set_fahrgassen_weite(self, meter: float) -> float:
        try:
            meter = float(meter)
        except (TypeError, ValueError):
            raise ValueError("Fahrgassenweite bitte in Metern angeben") from None
        if not FAHRGASSEN_WEITE_MIN <= meter <= FAHRGASSEN_WEITE_MAX:
            raise ValueError(f"Fahrgassenweite zwischen {FAHRGASSEN_WEITE_MIN:g} "
                             f"und {FAHRGASSEN_WEITE_MAX:g} m")
        self.store.set_setting("fahrgassen_weite", meter)
        self.note(f"Fahrgassenweite {meter:g} m")
        return meter

    def update_line(self, line_id: str, values: dict) -> dict:
        """Spur umbenennen oder als Saisonspur mit Fahrgassen festlegen."""
        werte = {}
        if "name" in values:
            werte["name"] = str(values["name"]).strip() or "Spur"
        if "saisonspur" in values:
            # Die Taste in der Liste: Saisonspur mit der eingestellten
            # Fahrgassenweite, oder wieder keine.
            values = {**values, "fahrgasse_m": self.fahrgassen_weite() if values["saisonspur"] else 0.0}
        if "fahrgasse_m" in values:
            fahrgasse = float(values["fahrgasse_m"] or 0.0)
            if fahrgasse < 0:
                raise ValueError("Der Fahrgassenabstand kann nicht negativ sein")
            werte["fahrgasse_m"] = fahrgasse
            # Fahrgassen gehören zu einer Saison. Ohne Angabe ist es diese.
            werte["saison"] = self._erntejahr(values.get("saison")) if fahrgasse > 0 else 0
        elif "saison" in values:
            werte["saison"] = self._erntejahr(values["saison"]) if values["saison"] else 0
        vorher = self.store.get_line(line_id)
        if (vorher and vorher["saison"] > 0 and vorher["fahrgasse_m"] > 0
                and werte.get("saison", 0) > 0 and werte["saison"] != vorher["saison"]):
            # Dieselbe AB-Linie kann im nächsten Erntejahr wieder benutzt werden.
            # Das Vorjahr bleibt eine eigene Fassung, statt seine Zuordnung zu verlieren.
            kopie = {k: v for k, v in vorher.items() if k not in ("id", "aussaat_at")}
            record = self.store.save_line({**kopie, **werte})
        else:
            record = self.store.update_line(line_id, **werte)
        if record is None:
            raise KeyError("Spurlinie nicht gefunden")
        if self.line is not None and self.line.id == line_id:
            self.line.id = record["id"]
            self.line.name = record["name"]
            self.line.fahrgasse_m = record["fahrgasse_m"]
            self.line.saison = record["saison"]
            self.line.rand_innen = self._rand_innen(self.line)
            self._remember_session()
        return record

    @staticmethod
    def _erntejahr(wert=None) -> int:
        if wert is None:
            return time.localtime().tm_year
        text = str(wert)
        if not text.isdigit() or not 2000 <= int(text) <= 2200:
            raise ValueError("Erntejahr bitte als ganze Jahreszahl von 2000 bis 2200 angeben")
        return int(text)

    @staticmethod
    def _saison_kennung(spur):
        return (spur["id"], spur.get("aussaat_at", 0)) if spur else None

    def saisonspur_abgleichen(self) -> None:
        """Empfangene Aussaat im Stillstand übernehmen, nie während Arbeit/Lenkung."""
        if self.field is None:
            return
        spur = self.store.season_line(self.field["id"])
        kennung = self._saison_kennung(spur)
        if kennung is None or kennung == self._saison_stand:
            self.saisonspur_neu = None
            return
        if (self.job is not None or getattr(self.steering, "armed", False)
                or self.record_mode is not None or self.turn is not None):
            self.saisonspur_neu = {k: spur[k] for k in ("id", "name", "saison")}
            return
        self.load_line(spur["id"])
        self.note(f"Fahrgassen Erntejahr {spur['saison']} vom Abgleich übernommen")

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
        self.line.fahrgassen_liste = [float(x) for x in (record.get("fahrgassen") or [])]
        self.line.rand_innen = self._rand_innen(self.line)
        if self.field is not None:
            self._saison_stand = self._saison_kennung(self.store.season_line(self.field["id"]))
            self.saisonspur_neu = None
        zusatz =f" · Fahrgassen alle {self.line.fahrgasse_m:g} m" if self.line.fahrgasse_m else ""
        self.note(f"Spur aktiv: {record['name']}{zusatz}")
        self._remember_session()
        return record

    def _rand_innen(self, line: GuidanceLine) -> float:
        """Randspur: auf welcher Seite das Feld liegt, damit eine Saisonspur ihr
        Raster an der Grenze beginnt statt auf ihr (GuidanceLine.am_rand)."""
        record = self.store.get_line(line.id) if line.id else None
        if record is not None and record.get("rand_innen") is not None:
            return float(record["rand_innen"])
        if not randspuren_module.ist_randspur(line.name) or self.field is None:
            return 0.0
        return rand_seite(line, [tuple(p) for p in self.field["boundary"]])

    def clear_line(self) -> None:
        self.line = None
        self.guidance = GuidanceState()
        self._remember_session()

    def randspuren_anlegen(self, field_id: str) -> dict:
        """Aus der Feldgrenze Spuren machen: gerade Kanten ab 20 m als AB-Spur,
        gebogene Ränder ab 20 m als Kurvenspur (siehe randspuren.py).

        Ungebundene Randspuren werden aktualisiert. Saisonspuren bleiben samt
        Geometrie und Randbezug erhalten; geänderte Ränder erhalten eigene IDs.
        Selbst angelegte Spuren werden nie angefasst.
        """
        feld = self.store.get_field(field_id)
        if feld is None:
            raise KeyError("Feld nicht gefunden")
        grenze = [tuple(p) for p in (feldwerkzeuge.arbeitsgrenze(feld))]
        if len(grenze) < 3:
            return {"feld": feld["name"], "angelegt": [], "hinweis": "keine Grenze"}
        randlinien = [l for l in self.store.list_lines(field_id)
                      if randspuren_module.ist_randspur(l["name"])]
        geschuetzt = [l for l in randlinien if l.get("saison", 0) > 0]
        vorhanden = {l["name"]: l for l in randlinien if l.get("saison", 0) <= 0}
        neu: list[str] = []
        namen_gesehen: dict[str, int] = {}
        benannt = []
        for kante in randspuren_module.kanten(grenze):
            name = randspuren_module.benennen(kante, grenze)
            namen_gesehen[name] = namen_gesehen.get(name, 0) + 1
            if namen_gesehen[name] > 1:
                name = f"{name} ({namen_gesehen[name]})"
            punkte = randspuren_module.runden(kante["points"])
            gleiche = next((l for l in geschuetzt if l["mode"] == kante["mode"]
                            and l["points"] == punkte), None)
            if gleiche is not None:
                neu.append(gleiche["name"])
            else:
                benannt.append((name, kante))
        # Ungebundene Vorgänger nach Namen, dann nach Lage zuordnen.
        vorgaenger = {name: vorhanden.pop(name) for name, _ in benannt if name in vorhanden}
        for name, kante in benannt:
            if name in vorgaenger or not vorhanden:
                continue
            beste = max(vorhanden.values(),
                        key=lambda l: randspuren_module.deckung(l["points"], kante["points"]))
            if randspuren_module.deckung(beste["points"], kante["points"]) >= 0.5:
                vorgaenger[name] = vorhanden.pop(beste["name"])
        for name, kante in benannt:
            alt = vorgaenger.get(name)
            self.store.save_line({
                **({"id": alt["id"], "nudge_m": alt.get("nudge_m", 0.0),
                    "saison": alt.get("saison", 0), "fahrgasse_m": alt.get("fahrgasse_m", 0.0)}
                   if alt else {}),
                "field_id": field_id,
                "name": name,
                "mode": kante["mode"],
                "points": randspuren_module.runden(kante["points"]),
                "rand_innen": rand_seite(GuidanceLine(kante["mode"], kante["points"],
                                                      self.profile.spacing_m, name), grenze),
                "spacing_m": self.profile.spacing_m,
            })
            neu.append(name)
            # Die aktive Spur hat jetzt eine neue Form: neu laden.
            if alt is not None and self.line is not None and self.line.id == alt["id"]:
                self.load_line(alt["id"])
        for veraltet in vorhanden.values():
            self.store.delete_line(veraltet["id"])
            if self.line is not None and self.line.id == veraltet["id"]:
                self.clear_line()
        return {"feld": feld["name"], "angelegt": neu}

    # -- Saatplan ---------------------------------------------------------

    def _feld_mit_grenze(self, field_id: str) -> tuple[dict, list]:
        feld = self.store.get_field(field_id)
        if feld is None:
            raise KeyError("Feld nicht gefunden")
        grenze = [tuple(p) for p in (feldwerkzeuge.arbeitsgrenze(feld))]
        if len(grenze) < 3:
            raise ValueError(f"{feld['name']} hat keine Grenze - erst umfahren oder Shapefile einlesen")
        return feld, grenze

    def saatplan(self, field_id: str) -> dict:
        """Startecken des Feldes, Vorgaben und der zuletzt gerechnete Plan."""
        feld, grenze = self._feld_mit_grenze(field_id)
        breite = self.profile.spacing_m
        weite = self.fahrgassen_weite()
        verhaeltnis = weite / breite if breite > 0 else 0.0
        rand = int(round((verhaeltnis - 1) / 2)) if abs(verhaeltnis - round(verhaeltnis)) < 0.01 \
            and round(verhaeltnis) % 2 == 1 else 3
        gespeichert = self.store.get_setting(f"saatplan:{field_id}") or {}
        return {
            "feld": feld["name"],
            "optionen": [{k: o[k] for k in ("id", "text", "kante")}
                         for o in saatplan_module.startkanten(grenze)],
            "vorgabe": gespeichert.get("vorgabe") or {"breite_m": breite, "fahrgasse_m": weite,
                                                       "randspuren": rand},
            "plan": gespeichert.get("plan"),
        }

    def saatplan_planen(self, field_id: str, vorgabe: dict) -> dict:
        """Den Schlag durchplanen und den Plan beim Feld speichern."""
        feld, grenze = self._feld_mit_grenze(field_id)
        optionen = {o["id"]: o for o in saatplan_module.startkanten(grenze)}
        start = optionen.get(str(vorgabe.get("start") or ""))
        if start is None:
            raise ValueError("Bitte Startecke und Richtung wählen")
        try:
            breite = float(vorgabe.get("breite_m"))
            weite = float(vorgabe.get("fahrgasse_m"))
            rand = int(vorgabe.get("randspuren"))
        except (TypeError, ValueError):
            raise ValueError("Breite, Fahrgassenweite und Randspuren als Zahlen angeben") from None
        if not 0 <= rand <= 20:
            raise ValueError("Randspuren zwischen 0 und 20")
        plan = saatplan_module.planen(grenze, tuple(start["von"]), tuple(start["nach"]),
                                      breite, weite, rand)
        plan["startwahl"] = start["text"]
        plan["kante"] = start["kante"]
        self.store.set_setting(f"saatplan:{field_id}", {
            "vorgabe": {"start": start["id"], "breite_m": breite, "fahrgasse_m": weite, "randspuren": rand},
            "plan": plan})
        self.note(f"Saatplan {feld['name']}: {plan['anzahl_spuren']} Spuren, "
                  f"{plan['anzahl_fahrgassen']} Fahrgassen, {len(plan['konflikte'])} Konflikte")
        return plan

    def saatplan_hier(self, werte: Optional[dict] = None) -> dict:
        """Saatplan ab der Ecke, an der der Traktor gerade steht.

        So wird es auf dem Acker gemacht: an die Startecke fahren, in die
        Richtung der ersten Spur ausrichten, Knopf drücken. Die Ecke ist die
        nächste Startecke zur Antenne (höchstens ``SAATPLAN_ECKE_M`` weg), die
        Kante die, die von dort am besten in Fahrtrichtung zeigt.
        """
        if self.field is None:
            raise RuntimeError("Kein Feld geladen")
        if self.position is None:
            raise RuntimeError("Keine Position - auf GPS warten")
        feld, grenze = self._feld_mit_grenze(self.field["id"])
        optionen = saatplan_module.startkanten(grenze)
        if not optionen:
            raise ValueError("Der Schlag hat keine gerade Kante für den Saatplan")
        naechste = min(geo.distance(self.position, tuple(o["von"])) for o in optionen)
        if naechste > SAATPLAN_ECKE_M:
            raise ValueError(f"Keine Ecke in der Nähe - die nächste ist {naechste:.0f} m weg. "
                             f"Erst an die Startecke fahren.")
        an_der_ecke = [o for o in optionen if geo.distance(self.position, tuple(o["von"])) <= naechste + 5.0]
        if self.heading is None:
            raise RuntimeError("Fahrtrichtung unbekannt - ein paar Meter in Richtung der ersten Spur fahren")
        wahl = min(an_der_ecke, key=lambda o: abs(geo.angle_difference(
            geo.heading_deg(tuple(o["von"]), tuple(o["nach"])), self.heading)))
        gespeichert = (self.store.get_setting(f"saatplan:{feld['id']}") or {}).get("vorgabe") or {}
        vorgabe = {**self.saatplan(feld["id"])["vorgabe"], **gespeichert, **(werte or {}), "start": wahl["id"]}
        return self.saatplan_planen(feld["id"], vorgabe)

    def saatplan_uebernehmen(self, field_id: str, saison=None) -> dict:
        """Den Plan als Saisonspur speichern: eine AB-Spur durch eine Fahrgasse,
        Fahrgassen alle fahrgasse_m. Ersetzt den Saatplan desselben Erntejahres."""
        feld, _ = self._feld_mit_grenze(field_id)
        gespeichert = self.store.get_setting(f"saatplan:{field_id}") or {}
        plan = gespeichert.get("plan")
        if not plan:
            raise ValueError("Erst planen, dann übernehmen")
        jahr = self._erntejahr(saison)
        for alt in self.store.list_lines(field_id):
            if alt["name"].startswith(SAATPLAN_PRAEFIX) and alt["saison"] == jahr:
                self.store.delete_line(alt["id"])
        kante = plan.get("kante", "").replace(randspuren_module.NAMENS_PRAEFIX, "", 1)
        # Mit Gegenseite zwei Spuren: B (durch die Fahrgasse von B) für das
        # Säen von B her - dort liegt das Raster anders - und A für alles
        # andere. A wird zuletzt gespeichert und ist damit die Saisonspur, die
        # beim Laden des Feldes kommt. Beide kennen genau die geplanten
        # Fahrgassen; die Liste läuft mit der Spur auf die Tablets.
        if plan.get("saisonspur_b"):
            self.store.save_line({
                "field_id": field_id,
                "name": f"{SAATPLAN_PRAEFIX}{kante} · B",
                "mode": "ab", "points": plan["saisonspur_b"], "spacing_m": plan["breite_m"],
                "saison": jahr, "fahrgasse_m": plan["fahrgasse_m"],
                "fahrgassen": plan.get("saisonspur_b_fahrgassen") or [],
            })
            time.sleep(0.01)                       # A sicher die neuere
        record = self.store.save_line({
            "field_id": field_id,
            "name": f"{SAATPLAN_PRAEFIX}{kante} · " + ("A" if plan.get("saisonspur_b") else
                                                       f"{plan['fahrgasse_m']:g} m"),
            "mode": "ab",
            "points": plan["saisonspur"],
            "spacing_m": plan["breite_m"],
            "saison": jahr,
            "fahrgasse_m": plan["fahrgasse_m"],
            "fahrgassen": plan.get("saisonspur_fahrgassen") or [],
        })
        if self.field is None or self.field["id"] != field_id:
            self.load_field(field_id)
        self.load_line(record["id"])
        self.note(f"Saatplan als Saisonspur übernommen: {feld['name']}")
        return record

    def saatplan_loeschen(self, field_id: str) -> None:
        self.store.set_setting(f"saatplan:{field_id}", {})

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
        grenze = [tuple(p) for p in (feldwerkzeuge.arbeitsgrenze(self.field))]
        if len(grenze) < 3:
            raise RuntimeError("Ohne Feldgrenze gibt es keine Kontur - erst die "
                               "Grenze abfahren (⬠)")
        self.line = GuidanceLine(
            "contour", grenze, self.profile.spacing_m, "Kontur", "contour",
        )
        self.note("Kontur aktiv: Ring 0 ist die Feldgrenze")
        self._remember_session()
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

    SIM_SPUR_MAX_ABSTAND_M = 3000.0

    def startpunkt_im_feld(self) -> Optional[tuple[float, float, float]]:
        """Ost, Nord, Kurs für einen Neuanfang im Feld - für den Simulator.

        Auf der aktiven AB-Spur: an ihrem Anfang, in ihrer Richtung. Sonst in
        der Mitte der Grenze, nach Norden.
        """
        if self.field is None:
            return None
        grenze = feldwerkzeuge.arbeitsgrenze(self.field)
        mitte = ((sum(p[0] for p in grenze) / len(grenze),
                  sum(p[1] for p in grenze) / len(grenze)) if len(grenze) >= 3 else (0.0, 0.0))
        if self.line is not None and self.line.mode == "ab" and len(self.line.points) >= 2:
            a, b = self.line.points[0], self.line.points[-1]
            # Eine Spur, die weit weg vom Feld liegt (im Simulator an anderem Ort
            # angelegt), taugt nicht als Startpunkt - dann die Feldmitte.
            if math.dist(a, mitte) <= self.SIM_SPUR_MAX_ABSTAND_M:
                kurs = geo.heading_deg(a, b)
                rx, ry = math.cos(math.radians(kurs)), -math.sin(math.radians(kurs))
                versatz = self.line.nudge_m + self.line.raster_versatz()
                return a[0] + rx * versatz, a[1] + ry * versatz, kurs
        return mitte[0], mitte[1], 0.0

    def startpunkt_wgs(self) -> Optional[tuple[float, float, float]]:
        """Wie startpunkt_im_feld, aber als Breite, Länge, Kurs."""
        ziel = self.startpunkt_im_feld()
        if ziel is None or self.plane is None:
            return None
        lat, lon = self.plane.to_wgs(ziel[0], ziel[1])
        return lat, lon, ziel[2]

    def _werkzeug_stillstand(self):
        if ((self.steering is not None and self.steering.armed) or self.turn is not None
                or self.job is not None or self.record_mode is not None
                or (self.fix is not None and abs(self.fix.speed_ms or 0)>0.3)):
            raise RuntimeError("Erst anhalten, Lenkung ausschalten und laufende Arbeit/Aufzeichnung beenden")

    def originalspur(self, feld_id, spur_id, erwartet):
        self._werkzeug_stillstand()
        if self.field is None or self.line is None or self.field["id"]!=feld_id or self.line.id!=spur_id:
            raise RuntimeError("Die aktive Spur hat sich geändert – neu auswählen")
        if not isinstance(erwartet,(float,int)) or not math.isfinite(float(erwartet)) or abs(self.line.nudge_m-float(erwartet))>1e-9:
            raise RuntimeError("Der Versatz wurde inzwischen geändert – Anzeige neu laden")
        if not self.line.derived:
            alt=self.store.get_line(spur_id)
            if (alt is None or alt["deleted"] or abs(alt["nudge_m"]-float(erwartet))>1e-9
                    or alt["mode"]!=self.line.mode or alt["points"]!=[list(p) for p in self.line.points]):
                raise RuntimeError("Die gespeicherte Spur wurde inzwischen geändert – neu laden")
            self.store.save_line({**alt,"nudge_m":0.})
        self.line.nudge_m=0.
        self._nudge_zeit=time.time()
        self.turn_preview=[]
        self.note("Seitlicher Versatz auf Originalspur zurückgesetzt")
        return {"nudge_m":0.}

    def arbeitsgrenze_speichern(self, feld_id, werte, geprueft=None):
        feld=self.store.get_field(feld_id)
        if feld is None or feld["deleted"]: raise ValueError("Feld nicht gefunden")
        self._werkzeug_stillstand()
        if werte.get("stand")!=feld["updated_at"]:
            raise RuntimeError("Die Feldgrenze wurde inzwischen geändert. Entwurf verwerfen und neu öffnen.")
        punkte=werte.get("punkte")
        if punkte != []: punkte=geprueft if geprueft is not None else feldwerkzeuge.grenze_pruefen(punkte,feld["boundary"])
        neu=self.store.save_field({**feld,"working_boundary":punkte})
        self.grenze_abgleichen()
        return neu

    def grenze_abgleichen(self):
        if self.field is None: return
        neu=self.store.get_field(self.field["id"])
        if neu is None or neu["deleted"]: return
        if (neu["boundary"],neu.get("working_boundary"))==(self.field["boundary"],self.field.get("working_boundary")): return
        try: self._werkzeug_stillstand()
        except RuntimeError: return
        self.field=neu
        self.turn_preview=[]
        self.turn_preview_ok=False
        if self.line is not None and self.line.derived and self.line.mode=="contour": self.use_contour()
        self._update_headland()
        self.note("Aktuelle Arbeitsgrenze übernommen")

    def arbeitslinien_vorschau(self, job_id):
        job=self.store.get_job(job_id)
        if job is None or job["deleted"]: raise ValueError("Arbeit nicht gefunden")
        if not job["ended_at"]: raise ValueError("Arbeit zuerst beenden")
        feld=self.store.get_field(job["field_id"])
        if feld is None or feld["deleted"]: raise ValueError("Zugehöriges Feld fehlt")
        punkte=self.store.track_points(job_id)
        segmente=feldwerkzeuge.arbeitslinien(punkte,geo.LocalPlane(feld["datum_lat"],feld["datum_lon"]))
        return {"job_id":job_id,"field_id":feld["id"],"feldname":feld["name"],"bezug":[feld["datum_lat"],feld["datum_lon"]],"jobstand":job["updated_at"],
                "grenze":feld["boundary"],"stand":feldwerkzeuge.stand([punkte,feld["datum_lat"],feld["datum_lon"]]),
                "abstand_m":job.get("reference_spacing_m",0),"segmente":segmente,
                "hinweis":"Keine geeigneten lokalen Spurpunkte. Auf dem aufzeichnenden Tablet öffnen; gespeicherte Referenzspuren werden anschließend abgeglichen." if not segmente else ""}

    def arbeitslinie_speichern(self, job_id, werte, plan=None):
        plan=plan if plan is not None else self.arbeitslinien_vorschau(job_id)
        aktuell=self.store.get_job(job_id)
        feld=self.store.get_field(plan["field_id"])
        if (aktuell is None or aktuell["deleted"] or feld is None or feld["deleted"]
                or aktuell["field_id"]!=plan["field_id"] or aktuell["updated_at"]!=plan["jobstand"]
                or [feld["datum_lat"],feld["datum_lon"]]!=plan["bezug"]):
            raise RuntimeError("Arbeit oder Feld nicht mehr verfügbar")
        if werte.get("stand")!=plan["stand"]: raise RuntimeError("Die Aufzeichnung hat sich geändert – Vorschau neu öffnen")
        index=werte.get("segment")
        if type(index) is not int or not 0<=index<len(plan["segmente"]): raise ValueError("Abschnitt auswählen")
        segment=plan["segmente"][index]
        if segment["altaufzeichnung"] and werte.get("antenne_bestaetigt") is not True:
            raise ValueError("Altaufzeichnung enthält die Antennenspur ohne gesicherten Geräteversatz/Arbeitszustand. Bitte ausdrücklich bestätigen.")
        try: abstand=float(werte.get("abstand_m") or 0)
        except (ValueError,TypeError): raise ValueError("Bitte einen gültigen Spurabstand eingeben") from None
        if not math.isfinite(abstand) or not 0.1<=abstand<=100: raise ValueError("Spurabstand muss zwischen 0,1 und 100 m liegen")
        name=str(werte.get("name") or "").strip()
        if not name or len(name)>120: raise ValueError("Bitte einen Spurnamen mit höchstens 120 Zeichen angeben")
        # Neue Kennung, kein load_line: weder die aktuelle Lenkung noch die Aussaat
        # ändert sich durch eine reine Archivaktion.
        punkte=feldwerkzeuge.abschnitt_kuerzen(segment["punkte"],werte.get("anfang_m",0),werte.get("ende_m",0))
        import uuid
        try: kennung="ref-"+uuid.UUID(str(werte.get("request_id"))).hex
        except ValueError: raise ValueError("Ungültiger Speichervorgang – Vorschau neu öffnen") from None
        datensatz={"id":kennung,"field_id":plan["field_id"],"name":name,"mode":"curve",
                   "points":punkte,"spacing_m":abstand}
        alt=self.store.get_line(kennung)
        if alt is not None:
            if alt["deleted"] or any(alt[k]!=v for k,v in datensatz.items()):
                raise RuntimeError("Dieser Speichervorgang wurde bereits abgeschlossen – Vorschau neu öffnen")
            return alt
        return self.store.save_line(datensatz)

    def nudge(self, metres: float, bildschirm_rechts: Optional[list] = None) -> float:
        """Trim the whole pattern sideways.

        Real fields drift: a slightly different antenna position between
        machines, or a headland that was not quite square.  Nudging moves every
        pass rather than the one you are on, so the pattern stays consistent.
        """
        if self.line is None:
            raise RuntimeError("Keine Spur aktiv")
        self._nudge_zeit = time.time()      # der Lenk-Assistent wertet danach eine Weile nicht
        # "Rechts" heißt rechts vom Sitz aus. Auf einer rückwärts gefahrenen
        # AB-Spur und auf dem Ring gegen den Uhrzeigersinn liegt rechts vom
        # Fahrer links vom Muster - die Führung weiß das (right_sign), also
        # wird der Wunsch des Fahrers hier in die Sprache des Musters übersetzt.
        #
        # Kommt die Richtung "rechts auf dem Bildschirm" mit (Weltvektor), gilt
        # die: der Fahrer sieht die Spur, und "›" soll sie dorthin schieben,
        # wohin er schaut - in jeder Ansicht, auch "Norden oben", und auch dann,
        # wenn die Anlage vorwärts und rückwärts verwechselt hat. Vorher hing
        # es am errechneten Kurs; fuhr man in "Norden oben" nach Süden, sprang
        # die Spur auf dem Bildschirm zur falschen Seite.
        punkt = self._lenkpunkt()
        if bildschirm_rechts is not None and punkt is not None and len(bildschirm_rechts) == 2:
            muster = self.line.versatz_richtung(punkt)
            skalar = muster[0] * float(bildschirm_rechts[0]) + muster[1] * float(bildschirm_rechts[1])
            if abs(skalar) > 1e-6:
                metres = metres if skalar > 0 else -metres
        elif self.guidance.active:
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

    def richtung_umkehren(self) -> dict:
        """Vorwärts/rückwärts von Hand umdrehen (Knopf auf dem Hauptbildschirm)."""
        self.heading_filter.umkehren()
        if self.heading_filter.value is not None:
            self.heading = self.heading_filter.value
            self.implement_heading.reset(self.heading)
        richtung = "rückwärts" if self.heading_filter.rueckwaerts else "vorwärts"
        self.note(f"Fahrtrichtung von Hand: {richtung}")
        return {"rueckwaerts": self.heading_filter.rueckwaerts}

    def arm_steering(self) -> str:
        """Lenkung scharf schalten - der Fahrer erklärt damit auch, dass er vorwärts fährt."""
        self.heading_filter.bestaetige_vorwaerts()
        return self.steering.arm()

    # -- Fortsetzen nach Neustart -----------------------------------------

    def _remember_session(self) -> None:
        """Feld, Spur und laufende Arbeit festhalten.

        Steht in der Datenbank und nicht in einer Datei neben ihr, damit beides
        zusammen gesichert, zurückgespielt und gelöscht wird.
        """
        line = self.line
        self.store.set_setting(SESSION_KEY, {
            "field_id": self.field["id"] if self.field else None,
            "line_id": line.id if line is not None and not line.derived else None,
            "contour": bool(line is not None and line.derived),
            "job_id": self.job["id"] if self.job else None,
        })

    def resume_session(self) -> Optional[dict]:
        """Dort weitermachen, wo vor dem Ausschalten Schluss war.

        Gibt es nichts fortzusetzen, ist das Ergebnis ``None``. Die Lenkung
        bleibt dabei in jedem Fall unscharf: wer den Schlüssel dreht, hat nicht
        entschieden, dass der Motor wieder zugreifen darf.
        """
        sitzung = self.store.get_setting(SESSION_KEY) or {}
        job, self._resumable_job = self._resumable_job, None
        feld = self.store.get_field(sitzung["field_id"]) if sitzung.get("field_id") else None
        if feld is None or feld.get("deleted"):
            if job is not None:
                self._close_orphans()
            return None

        self.load_field(feld["id"])
        if sitzung.get("contour"):
            try:
                self.use_contour()
            except RuntimeError:
                pass
        elif sitzung.get("line_id"):
            record = self.store.get_line(sitzung["line_id"])
            if record is not None and not record.get("deleted"):
                self.load_line(record["id"])

        arbeit = False
        if job is not None and job["field_id"] == feld["id"]:
            self.job = job
            blob = self.store.get_job_coverage(job["id"])
            if blob:
                self.coverage = CoverageMap.unpack(blob)
                self.coverage.applied_area_m2 += (job.get("overlap_ha") or 0.0) * 10_000.0
            self.distance_m = job.get("distance_m") or 0.0
            self.working_time_s = job.get("working_time_s") or 0.0
            self._checkpoint_at = time.time()
            arbeit = True
        elif job is not None:
            self._close_orphans()
        self._remember_session()

        text = f"Weiter auf „{feld['name']}“"
        if self.line is not None:
            text += f", Spur {self.line.name}"
        if arbeit:
            text += f" - Arbeit läuft weiter bei {self.coverage.area_ha:.2f} ha"
        self.note(text)
        return {"text": text, "feld": feld["name"], "arbeit": arbeit,
                "flaeche_ha": self.coverage.area_ha}

    # -- Feld erkennen ----------------------------------------------------

    def refresh_field_cache(self) -> None:
        """Alle Felder mit Grenze, je in ihrer eigenen Ebene - das Feld, in dem
        man steht, ist nicht das, dessen Ebene gerade geladen ist."""
        cache = []
        for feld in self.store.list_fields():
            grenze = [tuple(p) for p in (feldwerkzeuge.arbeitsgrenze(feld))]
            if len(grenze) >= 3:
                cache.append((feld["id"], feld["name"],
                              geo.LocalPlane(feld["datum_lat"], feld["datum_lon"]), grenze))
        self._felder_cache = cache
        self._felder_cache_at = time.time()

    def _pruefe_feldvorschlag(self, now: float) -> None:
        """Steht der Traktor in einem bekannten Feld, das nicht geladen ist?

        Vorgeschlagen, nicht geladen: ein Feldwechsel setzt die bearbeitete
        Fläche zurück, und das soll der Fahrer entscheiden, nicht ein Zaun.
        """
        if now - self._vorschlag_geprueft < 1.0:
            return
        self._vorschlag_geprueft = now
        fix = self.fix
        frisch = fix is not None and fix.valid and fix.received_at and now - fix.received_at < 2.0
        if not frisch or self.job is not None:
            self.feld_vorschlag = None
            self._vorschlag_kandidat = None
            return
        if now - self._felder_cache_at > FELDER_NEU_LADEN_S:
            self.refresh_field_cache()
        aktuell = self.field["id"] if self.field else None
        treffer = None
        for feld_id, name, plane, grenze in self._felder_cache:
            if geo.point_in_polygon(plane.to_local(fix.lat, fix.lon), grenze):
                treffer = (feld_id, name)
                break
        if treffer is None or treffer[0] == aktuell:
            self.feld_vorschlag = None
            self._vorschlag_kandidat = None
            return
        if treffer[0] != self._vorschlag_kandidat:
            self._vorschlag_kandidat = treffer[0]
            self._vorschlag_seit = now
        if now - self._vorschlag_seit >= FELD_VORSCHLAG_S:
            self.feld_vorschlag = {"id": treffer[0], "name": treffer[1]}

    def _close_orphans(self) -> None:
        geschlossen = self.store.close_orphan_jobs(self.config.network.device_id)
        if geschlossen:
            self.note(f"{geschlossen} unterbrochene Arbeit(en) vom letzten Mal abgeschlossen")

    def suspend_job(self) -> None:
        """Beim Beenden des Programms: Stand sichern, Arbeit offen lassen.

        Ob Zündung aus, Aktualisierung oder Neustart - die Arbeit ist damit
        nicht zu Ende. Beim nächsten Start wird sie fortgesetzt, nach einer
        Schicht ohne Fortsetzung mit dem gesicherten Stand abgeschlossen.
        """
        self._checkpoint(time.time(), force=True)
        self.warte_auf_sicherung()

    def warte_auf_sicherung(self) -> None:
        """Bis alles, was im Hintergrund geschrieben wird, in der Datenbank steht."""
        self._schreiber.warten()
        self._fehler_melden()

    def close(self) -> None:
        """Beim Beenden des Programms: Schreiber zu Ende laufen lassen und schließen."""
        self._schreiber.beenden()
        self._fehler_melden()

    def _fehler_melden(self) -> None:
        for text in self._schreiber.fehler_holen():
            self.note(text)

    def _checkpoint(self, now: float, force: bool = False) -> None:
        """Bearbeitete Fläche und Strecke zwischendurch sichern - im Hintergrund.

        Die Lenkschleife übergibt nur eine Kopie der Zellen (wenige Millisekunden);
        Verpacken und Schreiben macht der Schreiber. Läuft sein letzter Auftrag
        noch, wird übersprungen und beim nächsten Tick erneut versucht - es hilft
        niemandem, wenn sich Aufträge auf einer langsamen SD-Karte stapeln.
        """
        if self.job is None or (not force and now - self._checkpoint_at < CHECKPOINT_S):
            return
        if not force and self._sicherung_laeuft:
            return
        self._checkpoint_at = now
        self._flush_track(force=True)
        job_id = self.job["id"]
        zellen, zellgroesse = set(self.coverage.cells), self.coverage.cell_size
        werte = {
            "distance_m": self.distance_m,
            "area_ha": self.coverage.area_ha,
            "overlap_ha": self.coverage.overlap_m2 / 10_000.0,
            "working_time_s": self.working_time_s,
        }

        def schreiben(store: storage.Storage) -> None:
            try:
                karte = CoverageMap(cell_size=zellgroesse)
                karte.cells = zellen
                store.update_job(job_id, coverage=karte.pack(), **werte)
            finally:
                self._sicherung_laeuft = False

        self._sicherung_laeuft = True
        self._schreiber.auftrag(schreiben)
        self._fehler_melden()

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
                    [tuple(p) for p in feldwerkzeuge.arbeitsgrenze(self.field)], tiefe)
            ]
        return self._ring_cache

    def plan_turn(self, muster: str = "", richtung: str = "") -> dict:
        """Eine Wende planen und vollständig prüfen - gefahren wird noch nichts.

        Zwei Schritte, absichtlich getrennt: erst sieht der Fahrer die Route auf
        der Karte liegen, dann startet er sie. Eine Wende, die auf Knopfdruck
        sofort losfährt, hat niemand vorher angesehen.

        Fahrzeug, Gerät und ihre Verbindung werden als abgetastete Hülle
        gegen die Arbeitsgrenze samt Abstand geprüft. Ohne vollständige Maße
        oder mit einer Außenkante jenseits der Grenze bleibt ``im_feld`` falsch.
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

        grenze = [tuple(p) for p in (feldwerkzeuge.arbeitsgrenze(self.field))] \
            if self.field else []
        # Geplant wird ab dem Punkt, auf den auch gelenkt wird - der Achse.
        # Ab dem Gerät geplant, läge die Route sieben Meter hinter der
        # Maschine, und der Folger bräche beim ersten Bogen ab.
        start = self._lenkpunkt()
        # Gewendet wird am Vorgewende, nicht dort, wo der Knopf gedrückt wurde.
        # Vorher begann der Bogen sofort - wer 20 m vorher drückte, wendete
        # mitten im Bestand. Jetzt fährt die Route bis zum Vorgewende gerade
        # auf der Spur weiter und wendet erst dort.
        anlauf, kurs = self._wende_anlauf()
        wendepunkt = (start[0] + math.sin(math.radians(kurs)) * anlauf,
                      start[1] + math.cos(math.radians(kurs)) * anlauf)
        gerade = [(start[0] + math.sin(math.radians(kurs)) * s,
                   start[1] + math.cos(math.radians(kurs)) * s)
                  for s in [i * 1.0 for i in range(int(anlauf))]]
        tiefe = einstellung.tiefe_m(self.profile.width_m)
        bogen = headland_module.plan(wendepunkt, kurs, einstellung, self.profile.spacing_m, tiefe, grenze)
        pfad = gerade + bogen
        mitte_im_feld = headland_module.route_im_feld(pfad, grenze)
        huelle = gespann.pruefen(pfad, grenze, self.profile, einstellung.grenzabstand_m,
                                self.implement_heading.value, self.heading, (kurs+180)%360)
        im_feld = mitte_im_feld and huelle["im_feld"]
        hindernis = hindernisse_module.auf_route(pfad, self.profile.width_m, self._hindernisse)
        if hindernis is not None:
            im_feld = False
            huelle = {**huelle, "im_feld": False,
                      "hinweis": f"Hindernis „{hindernis['name']}“ liegt auf der Wenderoute"}
        self.turn_huelle = huelle
        self._turn_kontext = self.wende_kontext()
        self._turn_geraetekurs = self.implement_heading.value
        self._turn_kurs = self.heading
        self._turn_optionen = (muster, richtung)
        vorschlag = huelle["hinweis"] if not im_feld else ""
        if not mitte_im_feld and anlauf > 0.0 and len(grenze) >= 3:
            vorschlag = self._wende_vorschlag(bogen, wendepunkt, kurs, tiefe, einstellung)
        # Kann die Maschine den engsten Bogen überhaupt fahren? Radstand und
        # größter Einschlag geben den kleinsten Kreis; ein enger geplanter Bogen
        # wird nicht "ungefähr" gefahren, sondern verlassen.
        engster = headland_module.engster_radius(pfad)
        # Der Anschlag der Seite, zu der gewendet wird.
        links, rechts = self.profile.einschlag_grenzen()
        anschlag = min(links,rechts) if einstellung.muster == "dubins" else (links if einstellung.richtung == "links" else rechts)
        kleinster = self.profile.wheelbase_m / math.tan(
            math.radians(max(5.0, min(60.0, anschlag))))
        fahrbar = engster >= kleinster * 0.98
        self.turn_preview = pfad
        self.turn_preview_ok = im_feld
        self.turn_preview_fahrbar = fahrbar
        self.turn_preview_radius = (engster, kleinster)
        self._turn_planned_at = start
        return {
            "punkte": [list(p) for p in pfad],
            "im_feld": im_feld,
            "fahrbar": fahrbar,
            "engster_radius_m": None if math.isinf(engster) else round(engster, 2),
            "kleinster_radius_m": round(kleinster, 2),
            "muster": einstellung.muster,
            "richtung": einstellung.richtung,
            "laenge_m": sum(geo.distance(pfad[i], pfad[i + 1])
                            for i in range(len(pfad) - 1)),
            "grenze_vorhanden": len(grenze) >= 3,
            "anlauf_m": round(anlauf, 1),
            "vorschlag": vorschlag,
            "gespann": huelle,
        }

    def _wende_vorschlag(self, bogen, wendepunkt, kurs, tiefe, einstellung) -> str:
        """Warum die Wende nicht ins Vorgewende passt - in Metern, mit dem Ausweg.

        Gewendet wird, wenn das Gerät die Vorgewendelinie erreicht; die Achse
        steht dann schon um den Abstand Achse-Gerät im Vorgewende, und von dort
        holt der Bogen noch nach vorn aus.
        """
        h = math.radians(kurs)
        voraus = max((p[0] - wendepunkt[0]) * math.sin(h) + (p[1] - wendepunkt[1]) * math.cos(h)
                      for p in bogen)
        achse_vor_geraet = 0.0
        if self.tool_position is not None and self._lenkpunkt() is not None:
            lp, tp = self._lenkpunkt(), self.tool_position
            achse_vor_geraet = max(0.0, (lp[0] - tp[0]) * math.sin(h) + (lp[1] - tp[1]) * math.cos(h))
        noetig = achse_vor_geraet + voraus + 0.5
        breite = einstellung.breite_m if einstellung.breite_m > 0 else max(0.5, self.profile.width_m)
        spuren = int(math.ceil(noetig / breite))
        form = {"omega":"Ω-Wende","u":"U-Wende","dubins":"Dubins-Wende"}[einstellung.muster]
        def m(wert: float) -> str:
            return f"{wert:.1f}".replace(".", ",")
        return (f"Die {form} braucht {m(noetig)} m Vorgewende, eingestellt sind {m(tiefe)} m. "
                f"Vorgewende auf {spuren} Spuren stellen (Menü → Vorgewende) oder "
                f"einen größeren Spursprung mit U-Wende wählen.")

    WENDE_VORLAUF_MAX_M = 80.0

    def _wende_anlauf(self) -> tuple[float, float]:
        """Wie weit bis zum Vorgewende geradeaus, und in welche Richtung.

        Ohne Spur oder schon im Vorgewende: kein Anlauf, gewendet wird hier,
        in der Richtung, in die die Maschine zeigt. Auf der Spur geht der
        Anlauf in Spurrichtung - nicht in die augenblickliche Fahrtrichtung,
        die um ein paar Grad pendelt und auf 30 m einen Meter ausmacht.
        """
        rest = self.headland_status.rest_m
        # Auf der Kontur fährt man die Grenze selbst ab - dort liegt kein
        # Vorgewende voraus, gewendet wird wie bisher an Ort und Stelle.
        auf_spur = (self.line is not None and self.guidance.active
                    and self.guidance.mode in ("ab", "curve"))
        if rest is None or rest <= 0.5 or not auf_spur:
            return 0.0, self.heading
        if rest > self.WENDE_VORLAUF_MAX_M:
            raise RuntimeError(f"Noch {rest:.0f} m bis zum Vorgewende - die Wende auf den "
                               f"letzten {self.WENDE_VORLAUF_MAX_M:.0f} m planen")
        return rest, self.guidance.target_heading_deg

    def wende_kontext(self) -> str:
        """Feld, Gespann und tatsächliche Spur der Vorschau unverwechselbar binden."""
        return feldwerkzeuge.stand([self.field, asdict(self.profile), self.headland.to_dict(),
                                   self.line.to_dict() if self.line else None])

    def start_turn(self) -> dict:
        """Die geplante Route übernehmen und ihr folgen."""
        if not self.turn_preview:
            raise RuntimeError("Erst eine Wende planen")
        self.grenze_abgleichen()
        if getattr(self,"_turn_kontext",None)!=self.wende_kontext():
            self.turn_preview=[]
            raise RuntimeError("Feld, Spur oder Gespann geändert – Wende neu planen")
        aktuell=self.store.get_field(self.field["id"]) if self.field else None
        if aktuell and (aktuell["boundary"],aktuell.get("working_boundary"))!=(self.field["boundary"],self.field.get("working_boundary")):
            raise RuntimeError("Neue Arbeitsgrenze liegt vor – anhalten und Wende neu planen")
        if not self.turn_preview_fahrbar:
            engster, kleinster = self.turn_preview_radius
            raise RuntimeError(
                f"Die Maschine kann diese Wende nicht fahren: engster Bogen "
                f"{engster:.1f} m, die Lenkung schafft höchstens {kleinster:.1f} m. "
                "Wendekreis größer einstellen oder mehr Spuren überspringen.")
        if self.headland.nur_im_feld and not self.turn_preview_ok:
            raise RuntimeError(
                getattr(self, "turn_huelle", {}).get("hinweis") or
                "Das Gespann liegt nicht vollständig im Feld. Wenderichtung, "
                "Wendekreis oder Vorgewendetiefe ändern und neu planen.")
        self._require_position()
        losgefahren = getattr(self, "_turn_planned_at", None)
        if losgefahren is not None and \
                (geo.distance(losgefahren, self._lenkpunkt()) > 1.0 or abs(geo.angle_difference(self.heading or 0,getattr(self,"_turn_kurs",0)))>5.0):
            # Eine neue Startlage benötigt eine erneut betrachtete Vorschau.
            self.turn_preview=[]
            raise RuntimeError("Position oder Richtung geändert – Wende neu planen und Vorschau prüfen")
        if self.headland.nur_im_feld:
            jetzt=self._lenkpunkt()
            profil=self.profile
            ausladung=max(profil.width_m,profil.geraet_breite_m,profil.hitch_length_m,
                          profil.traktor_vorn_m,profil.traktor_hinten_m,abs(profil.tool_trailing_m),1.)*3
            geraetekurs = self.implement_heading.value
            if geraetekurs is None:
                geraetekurs = self.heading or 0
            geplant = getattr(self, "_turn_geraetekurs", None)
            if geplant is None:
                geplant = getattr(self, "_turn_kurs", 0)
            winkel=max(abs(geo.angle_difference(self.heading or 0,getattr(self,"_turn_kurs",0))),
                       abs(geo.angle_difference(geraetekurs, geplant)))
            if ausladung*math.radians(winkel)>0.05:
                raise RuntimeError("Ausrichtung des Gespanns geändert – Wende neu planen")
            h=math.radians(self.heading or 0)
            kurz=[jetzt,(jetzt[0]+math.sin(h)*.01,jetzt[1]+math.cos(h)*.01)]
            lage=gespann.pruefen(kurz,feldwerkzeuge.arbeitsgrenze(self.field),profil,self.headland.grenzabstand_m,
                                self.implement_heading.value,self.heading)
            if not lage["im_feld"]:
                raise RuntimeError("Die aktuelle Gespannlage hält den Grenzabstand nicht ein – Wende neu planen")
            alte_huellen=getattr(self,"turn_huelle",{}).get("huellen",[])
            if alte_huellen and lage["huellen"]:
                verbindung=gespann._hulle([tuple(p) for p in alte_huellen[0]+lage["huellen"][0]])
                if not feldwerkzeuge.flaeche_enthalten(verbindung,feldwerkzeuge.arbeitsgrenze(self.field),self.headland.grenzabstand_m+.10):
                    raise RuntimeError("Anfahrt zur Wenderoute verlässt den Grenzabstand – neu planen")
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

    def _wenderichtung_wechseln(self) -> None:
        """Nach einer fertigen Wende geht die nächste andersherum.

        Beim Hin- und Herfahren wendet man am einen Ende links, am anderen
        rechts - sonst fährt die Maschine nach zwei Wenden auf die Spuren
        zurück, von denen sie kam.
        """
        werte = self.headland.to_dict()
        werte["richtung"] = "rechts" if self.headland.richtung == "links" else "links"
        self.headland = headland_module.HeadlandSettings.from_dict(werte)
        self.store.set_setting("headland", self.headland.to_dict())

    def stop_turn(self, grund: str = "vom Fahrer beendet") -> None:
        if self.turn is None:
            return
        self.turn = None
        # Die Route ist gefahren (oder verworfen). Sie stehen zu lassen hieße:
        # der Knopf sagt weiter „Ω los" und ein Druck schickte die Maschine auf
        # eine Route, die an einer anderen Stelle des Feldes beginnt.
        self.turn_preview, self.turn_preview_ok = [], False
        self.note(grund if grund.startswith("Wende") else f"Wende beendet: {grund}")

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
            tractor=self.aktiver_traktor()["name"],
        )
        self.store.update_job(self.job["id"], reference_spacing_m=self.profile.spacing_m)
        self.distance_m = 0.0
        self.working_time_s = 0.0
        self.note(f"Arbeit gestartet{': ' + operation if operation else ''}")
        self._checkpoint_at = time.time()
        self._remember_session()
        return self.job

    def stop_job(self) -> Optional[dict]:
        if self.job is None:
            return None
        if self.teilbreiten is not None:self.teilbreiten.wunsch(0,min(32,len(self.sections)))
        self._flush_track(force=True)
        self.warte_auf_sicherung()          # erst alle Spurpunkte, dann das Ende der Arbeit
        self.store.update_job(
            self.job["id"],
            ended_at=time.time(),
            distance_m=self.distance_m,
            area_ha=self.coverage.area_ha,
            overlap_ha=self.coverage.overlap_m2 / 10_000.0,
            working_time_s=self.working_time_s,
            coverage=self.coverage.pack(),
        )
        job = self.store.get_job(self.job["id"])
        self.job = None
        self._remember_session()
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
            self.fix = fix  # den tatsächlichen Fixverlust auch sofort anzeigen
            self.positionsfilter.reset()
            self.rtk_bruecke.zuruecksetzen()
            self._last_update_at = None
            self.tool_position = None
            self.implement_position = None
            if self.steering is not None:self.steering.disarm("GNSS-Position ungültig")
            if self.turn is not None:self.turn = None
            if self.teilbreiten is not None:self.teilbreiten.wunsch(0,min(32,len(self.sections)))
            return
        now = fix.received_at or time.time()
        self.fix = fix
        if self.simulator is not None and hasattr(self.simulator, "fahrzeug_setzen"):
            self.simulator.fahrzeug_setzen(self.profile)

        if self.plane is None:
            # No field chosen yet: anchor a working plane at the first fix so
            # the display is useful immediately.
            self.plane = geo.LocalPlane(fix.lat, fix.lon)

        self.position = self.plane.to_local(fix.lat, fix.lon)
        # Kurzer RTK-Ausfall: Versatz der Ersatzlösung herausrechnen (rtkbruecke.py).
        self.position = self.rtk_bruecke.update(
            self.position, fix.rank, fix.speed_ms, self.heading, now,
            self.config.steering.rtk_ueberbrueckung_s)
        if self.rtk_bruecke.ereignis:
            self.note(self.rtk_bruecke.ereignis)
        if self.steering is not None:
            self.steering.rtk_ueberbrueckt = self.rtk_bruecke.aktiv
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
        warnung = self.heading_filter.warnung
        if warnung and warnung != self._kurs_warnung:
            self._kurs_warnung = warnung
            self.note(warnung)
        if heading is None:
            return

        if self.config.gnss.kalman_enabled:
            self.position = self.positionsfilter.update(
                self.position,fix.speed_ms,
                fix.course_deg if fix.speed_ms>.3 and fix.course_deg is not None else heading,
                now,fix.fix_quality,fix.accuracy_m,
                self.config.gnss.kalman_acceleration_ms2)
            if not self.positionsfilter.akzeptiert:
                if self.teilbreiten is not None:self.teilbreiten.wunsch(0,min(32,len(self.sections)))
                if self.steering is not None:self.steering.disarm("GNSS-Ausreißer im Positionsfilter")
                return  # keine Fläche oder Lenkbefehle aus verworfenen Messungen
        else:
            self.positionsfilter.reset()
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
        self._hindernisse_pruefen(fix)
        self._update_guidance(fix)
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
        grenze = [tuple(p) for p in (feldwerkzeuge.arbeitsgrenze(self.field))] \
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
                self._wenderichtung_wechseln()
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
            if self.teilbreiten is not None:self.teilbreiten.wunsch(0,min(32,len(self.sections)))
            return
        kurs = self.implement_heading.value if self.profile.trailed else self.heading
        if kurs is None:
            kurs = self.heading
        boundary = None
        if self.field and self.field.get("boundary"):
            boundary = [tuple(p) for p in feldwerkzeuge.arbeitsgrenze(self.field)]
        if self.auto_sections:
            self.coverage.update_auto_sections(
                self.implement_position, kurs, self.sections,
                speed_ms=self.fix.speed_ms if self.fix else 0.0,
                boundary=boundary,
                pcc=self.profile.section_pcc,
            )
        if self.teilbreiten is not None:
            maske=sum(1<<s.index for s in self.sections if s.enabled and not s.forced_off)
            self.teilbreiten.wunsch(maske if len(self.sections)<=32 else 0,min(32,len(self.sections)))
        wirksam=self._wirksame_teilbreiten()
        if previous_implement is not None:
            self.coverage.add_swath(previous_implement,self.implement_position,kurs,wirksam)

    def _wirksame_teilbreiten(self):
        if self.config.teilbreiten.output!="udp":return self.sections
        maske=self.teilbreiten.bestaetigt() if self.teilbreiten is not None else 0
        return [replace(s,enabled=s.enabled and not s.forced_off and bool(maske & (1<<s.index)))
                for s in self.sections]

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
        # Hat der Fahrer eben "vorwärts" bestätigt, wartet das Filter auf den
        # nächsten Kurs - bis dahin nicht gleich wieder abschalten.
        self.steering.rueckwaerts = (self.heading_filter.rueckwaerts
                                     and not self.heading_filter.bestaetigung_offen)
        command = self.steering.update(self.guidance, fix, context)
        self._abstimmung_beobachten(command.engaged, fix)
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
            # Was das virtuelle Rad wirklich tut - wie ein Drehgeber am echten.
            rad = getattr(self.simulator, "rad_deg", None)
            if self.steering is not None and rad is not None:
                self.steering.output.feedback.wheel_angle_deg = rad
        self._steering_was_engaged = command.engaged

    def _record_track(self, fix: Fix, now: float) -> None:
        if self.job is None or self.tool_position is None:
            return
        if (self._last_track_point is not None
                and geo.distance(self._last_track_point, self.tool_position)
                < TRACK_MIN_DISTANCE_M):
            return
        self._last_track_point = self.tool_position
        geraet = self.plane.to_wgs(*self.implement_position) if self.implement_position is not None else (None,None)
        arbeitet = any(s.enabled and not s.forced_off for s in self._wirksame_teilbreiten())
        self._track_buffer.append((
            now, fix.lat, fix.lon, fix.altitude, fix.speed_ms,
            self.heading, fix.fix_quality, self.guidance.cross_track_m,
            *geraet, int(arbeitet), int(self.turn is not None),
        ))
        self._flush_track()

    def _flush_track(self, force: bool = False) -> None:
        if not self._track_buffer or self.job is None:
            return
        if force or len(self._track_buffer) >= TRACK_FLUSH_COUNT:
            job_id, punkte = self.job["id"], self._track_buffer
            self._track_buffer = []
            self._schreiber.auftrag(lambda store: store.add_track_points(job_id, punkte))

    def tick(self, now: Optional[float] = None) -> None:
        """Regelmäßiger Herzschlag, unabhängig von eintreffenden Positionen.

        Die ganze Kette hängt sonst daran, dass Positionen kommen: bleibt der
        Empfänger stehen, wird auch nie neu entschieden, ob noch gelenkt werden
        darf. Die Hardware fängt das über ihren Failsafe ab, aber die Anzeige
        stünde weiter auf "lenkt". Deshalb wird hier von außen nachgesehen.
        """
        now = now or time.time()
        self._checkpoint(now)
        self._hindernisse_laden(now)
        if now-getattr(self,"_grenzabgleich_at",0)>1:
            self._grenzabgleich_at=now
            self.grenze_abgleichen()
        self._pruefe_feldvorschlag(now)
        age = now - self.fix.received_at if (self.fix and self.fix.received_at) else 99.0
        if self.teilbreiten is not None and (self.job is None or age>GPS_STILLSTAND_S):
            self.teilbreiten.wunsch(0,min(32,len(self.sections)))
        if age > GPS_STILLSTAND_S and self.turn is not None:
            # Eine Wende ohne Positionsdaten weiterzuführen hieße, blind auf
            # einem Bogen zu lenken. Sie endet hier, nicht erst beim nächsten Fix.
            self.stop_turn("keine GPS-Daten")
        if self.steering is None or not self.steering.armed:
            return
        if age > GPS_STILLSTAND_S:
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
            "positionsfilter": {"aktiv": self.config.gnss.kalman_enabled, **self.positionsfilter.status()},
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
            "kurs_kopplung": self.heading_filter.status(),
            "feld_vorschlag": self.feld_vorschlag,
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
                "fahrbar": self.turn_preview_fahrbar,
                "gespann": getattr(self,"turn_huelle",{}) if self.turn_preview or self.turn else {},
            },
            "line": self.line.to_dict() if self.line else None,
            "saisonspur_neu": self.saisonspur_neu,
            # Liegt die Maschine gerade auf einer Fahrgasse? Dann muss der Fahrer
            # beim Säen die Fahrgassenschaltung setzen.
            "fahrgasse_aktiv": bool(self.line is not None and self.guidance.active
                                    and self.guidance.mode != "turn"
                                    and self.line.ist_fahrgasse(self.guidance.pass_number)),
            "field": {
                "id": self.field["id"],
                "name": self.field["name"],
                "area_ha": self.field["area_ha"],
                "boundary": self.field["boundary"],
                "working_boundary": self.field.get("working_boundary", []),
                "datum": [self.field["datum_lat"], self.field["datum_lon"]],
                # Steht die Maschine im Feld? None ohne Grenze oder Position.
                "im_feld": (geo.point_in_polygon(self.position, [tuple(p) for p in self.field["boundary"]])
                            if self.position is not None and len(self.field["boundary"]) >= 3 else None),
            } if self.field else None,
            "profile": asdict(self.profile),
            "traktor": self.aktiver_traktor()["name"],
            "sections": [asdict(s) for s in self._wirksame_teilbreiten()],
            "teilbreiten_hardware": self.teilbreiten.status_dict() if self.teilbreiten else None,
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
            "rtk_bruecke": self.rtk_bruecke.status(time.time(), self.config.steering.rtk_ueberbrueckung_s),
            "hindernisse": [{k: h[k] for k in ("id", "name", "x", "y", "radius_m")} for h in self._hindernisse],
            "hindernis_warnung": self.hindernis_warnung,
            "flotte": self.flotte_lokal(),
            "abstimmung": (self.abstimmung.status() if self.abstimmung.modus != "aus"
                           else {"modus": "aus"}),
            "messages": self.messages[-6:],
        }

    # -- Hindernisse ------------------------------------------------------

    def _hindernisse_laden(self, now: Optional[float] = None, erzwingen: bool = False) -> None:
        """Die Hindernisse des geladenen Feldes - alle 5 s neu, damit abgeglichene ankommen."""
        now = now or time.time()
        feld = self.field["id"] if self.field else None
        if not erzwingen and feld == self._hindernisse_feld and now - self._hindernisse_at < 5.0:
            return
        self._hindernisse_feld, self._hindernisse_at = feld, now
        if feld is None or self.plane is None:
            self._hindernisse = []
            return
        lokal = []
        for h in self.store.list_hindernisse(feld):
            x, y = self.plane.to_local(h["lat"], h["lon"])
            lokal.append({**h, "x": x, "y": y})
        self._hindernisse = lokal

    def _hindernisse_pruefen(self, fix: Fix) -> None:
        if self._hindernisse_feld != (self.field["id"] if self.field else None):
            self._hindernisse_laden(erzwingen=True)
        vorher = self.hindernis_warnung
        self.hindernis_warnung = hindernisse_module.warnung(
            self.steer_position or self.position, self.heading, fix.speed_ms,
            self.profile.width_m, self._hindernisse)
        if self.hindernis_warnung and (not vorher or vorher.get("id") != self.hindernis_warnung["id"]):
            self.note(f"Achtung: {self.hindernis_warnung['name']} in "
                      f"{self.hindernis_warnung['abstand_m']:.0f} m")

    def hindernis_setzen(self, name: str = "Hindernis", seite: str = "hier",
                         radius_m: float = 1.0) -> dict:
        """Ein Hindernis an der aktuellen Lage: unter der Maschine oder an einer Gerätekante."""
        self._require_position()
        if self.field is None:
            raise RuntimeError("Erst ein Feld laden - Hindernisse gehören zu einem Feld")
        if self.heading is None:
            raise RuntimeError("Noch kein Kurs - ein Stück fahren")
        punkt = self.steer_position or self.position
        if seite in ("links", "rechts"):
            h = math.radians(self.heading)
            seitlich = (self.profile.width_m / 2.0) * (1 if seite == "rechts" else -1)
            punkt = (punkt[0] + math.cos(h) * seitlich, punkt[1] - math.sin(h) * seitlich)
        lat, lon = self.plane.to_wgs(*punkt)
        name = (name or "Hindernis").strip()[:40] or "Hindernis"
        radius_m = max(0.2, min(25.0, float(radius_m)))
        record = self.store.save_hindernis({"field_id": self.field["id"], "name": name,
                                            "lat": lat, "lon": lon, "radius_m": radius_m})
        self._hindernisse_laden(erzwingen=True)
        self.note(f"Hindernis „{name}“ markiert")
        return record

    def hindernis_loeschen(self, hindernis_id: str) -> None:
        self.store.delete_hindernis(hindernis_id)
        self._hindernisse_laden(erzwingen=True)

    # -- Fernansicht ------------------------------------------------------

    def live_meldung(self) -> Optional[dict]:
        """Was diese Maschine an die anderen meldet - oder None ohne gültige Lage."""
        from . import __version__
        fix = self.fix
        if fix is None or not fix.valid:
            return None
        return {
            "name": self.config.network.device_name or self.config.network.device_id,
            "lat": fix.lat, "lon": fix.lon,
            "kurs": self.heading, "tempo_ms": fix.speed_ms,
            "feld": self.field["name"] if self.field else "",
            "feld_id": self.field["id"] if self.field else "",
            "arbeit": (self.job.get("operation") or "Arbeit") if self.job else "",
            "flaeche_ha": round(self.coverage.area_ha, 2) if self.job else 0.0,
            "lenkt": bool(self.steering and self.steering.command.engaged),
            "markiert": self.job is not None,
            "fix": fix.fix_label,
            "version": __version__,
            "simulator": self.config.gnss.source == "simulator",
            "rolle": self.config.network.role,
        }

    def flotte_lokal(self) -> list[dict]:
        """Die anderen Maschinen, mit Lage in den Metern des geladenen Feldes."""
        aus = []
        for m in self.flotte.liste(ohne=self.config.network.device_id):
            eintrag = dict(m)
            if self.plane is not None:
                x, y = self.plane.to_local(m["lat"], m["lon"])
                eintrag["x"], eintrag["y"] = round(x, 2), round(y, 2)
            aus.append(eintrag)
        return aus

    def _require_position(self) -> None:
        if self.tool_position is None:
            raise RuntimeError("Noch keine Position - warte auf GPS")
