"""Lokales Betriebsbuch: begrenzte Verläufe, keine Steuerung und keine Geheimnisse.

Integration: Betriebsbuch(store) einmal erzeugen; observation(engine.state(),
now=time.time()) nach der Lenkentscheidung aufrufen. Gespann-IDs kann der
Aufrufer als traktor_id/geraet_id ergänzen. einstellung_aendern erhält flache
Profilwerte (asdict(profile)), Quelle und einen separaten ID-Kontext.
verlauf() liefert neue Kopien, jüngste Einträge zuerst. Laufende Dauer wird
nur beim Lesen berechnet, nicht bei jedem Tick gespeichert.
"""
from __future__ import annotations

from copy import deepcopy
import math
import re
import threading
import time
import uuid

from .guidance import REGLER_FELDER, TRAKTOR_FELDER, VehicleProfile

SCHLUESSEL = "betriebsbuch"
MAX_EINTRAEGE = 1000
# Ausschließlich bekannte Werte; freie Fehlertexte können Zugangsdaten enthalten.
GRUENDE = frozenset({
    "vom Fahrer ausgeschaltet", "zu langsam", "zu schnell", "keine Spur aktiv",
    "Rückwärtsfahrt", "System wird beendet", "nicht scharf", "Aktualisierung",
    "Fahrer hat eingegriffen", "in der Konfiguration deaktiviert", "kein GPS-Fix",
    "GPS-Daten veraltet", "keine GPS-Daten", "keine GPS-Daten mehr", "RTK nötig", "zu weit von der Spur", "Lenkausgang gestört",
    "Lenkausgang nicht bereit", "Werkstattlauf aktiv", "unbekannt",
})
AUS_GRUENDE = frozenset({
    "vom Fahrer ausgeschaltet", "Fahrer hat eingegriffen", "System wird beendet",
    "nicht scharf", "Aktualisierung", "in der Konfiguration deaktiviert",
})
STOER_GRUENDE = frozenset({"Lenkausgang gestört", "Lenkausgang nicht bereit", "unbekannt"})
QUELLEN = frozenset({"fahrer", "automatik", "kalibrierung", "sync", "master",
                     "tablet", "wiederherstellung", "system", "unbekannt"})
TEXTWERTE = {
    "regelart": {"stanley", "voraus", "cerea"},
    "bauform": {"traktor", "maehdrescher", "mähdrescher", "knicklenker", "raupe"},
    "marke": {"standard", "fendt", "john_deere", "claas", "case", "new_holland",
              "deutz", "deutz_fahr", "massey_ferguson", "valtra", "steyr", "jcb"},
}
ID_FELDER = ("traktor_id", "geraet_id", "feld_id", "job_id", "linie_id")
ZAHLEN = {
    "fix": ("speed_ms", "speed_kmh", "fix_quality", "satellites", "hdop",
            "accuracy_m", "age_of_corrections"),
    "imu": ("roll_deg", "pitch_deg", "yaw_rate_deg_s", "calibration"),
    "guidance": ("cross_track_m", "heading_error_deg", "pass_number"),
    "steering": ("angle_deg", "rad_ist_deg", "rad_soll_deg"),
}
BOOLS = {"imu": ("fresh", "healthy", "compensation"),
         "steering": ("armed", "engaged", "rueckmeldung_frisch", "simulator")}


def _zahl(wert):
    if type(wert) not in (int, float):
        return False
    try:
        return math.isfinite(wert)
    except OverflowError:
        return False


def _dict(wert):
    return wert if isinstance(wert, dict) else {}


def _ids(kontext):
    """Keine Namen/freien Beschreibungen oder verschachtelten Einstellungen kopieren."""
    kontext = _dict(kontext)
    daten = {}
    for key in ID_FELDER:
        wert = kontext.get(key)
        if isinstance(wert, str) and re.fullmatch(r"[\w.:-]{1,100}", wert, re.ASCII):
            daten[key] = wert
    for key, quelle in (("feld_id", "field"), ("job_id", "job"), ("linie_id", "line")):
        if key not in daten:
            wert = _dict(kontext.get(quelle)).get("id")
            if isinstance(wert, str) and re.fullmatch(r"[\w.:-]{1,100}", wert, re.ASCII):
                daten[key] = wert
    return daten


def _profil(daten):
    basis = VehicleProfile()
    sauber = {}
    for key in set(REGLER_FELDER) | set(TRAKTOR_FELDER):
        wert = _dict(daten).get(key)
        standard = getattr(basis, key, None)
        if key in TEXTWERTE:
            if isinstance(wert, str) and wert in TEXTWERTE[key]:
                sauber[key] = wert
        elif type(standard) is bool:
            if type(wert) is bool:
                sauber[key] = wert
        elif _zahl(standard) and _zahl(wert):
            sauber[key] = wert
    for key in ("phidget.max_duty", "phidget.min_duty", "phidget.gain_p", "phidget.gain_i", "phidget.gain_d", "phidget.encoder_counts_per_deg", "phidget.acceleration"):
        wert = _dict(daten).get(key)
        if _zahl(wert): sauber[key] = wert
    return sauber


def _grund(wert):
    if not isinstance(wert, str):
        return "unbekannt"
    kurz = wert.split(" (")[0]
    for vorne in ("RTK nötig", "Lenkausgang gestört", "Lenkausgang nicht bereit",
                  "Werkstattlauf aktiv"):
        if kurz == vorne or kurz.startswith(vorne + ":") or kurz.startswith(vorne + " -") or kurz.startswith(vorne + ","):
            return vorne
    return kurz if kurz in GRUENDE else "unbekannt"


def _snapshot(state):
    daten = _ids(state)
    for bereich in set(ZAHLEN) | set(BOOLS):
        quelle = _dict(state.get(bereich))
        werte = {k: quelle[k] for k in ZAHLEN.get(bereich, ()) if _zahl(quelle.get(k))}
        werte.update({k: quelle[k] for k in BOOLS.get(bereich, ()) if type(quelle.get(k)) is bool})
        if werte:
            daten[bereich] = werte
    return daten


class Betriebsbuch:
    def __init__(self, storage, limit=100):
        self.storage = storage
        self.limit = max(1, min(MAX_EINTRAEGE, int(limit)))
        self._lock = threading.RLock()
        self._unterbrechungen = []
        self._einstellungen = []
        self._offen = None
        self._letzter_zustand = None
        self._zuletzt = None
        daten = _dict(storage.get_setting(SCHLUESSEL, {}))
        for ziel, key in ((self._unterbrechungen, "unterbrechungen"),
                          (self._einstellungen, "einstellungen")):
            for eintrag in daten.get(key, []) if isinstance(daten.get(key), list) else []:
                if isinstance(eintrag, dict) and isinstance(eintrag.get("id"), str) and _zahl(eintrag.get("zeitpunkt")):
                    sauber = self._laden(eintrag, key)
                    if sauber is not None:
                        ziel.append(sauber)
            del ziel[:-self.limit]
        # Ein Prozessausfall liefert kein zuverlässiges Ende. Keine Dauer erfinden.
        geaendert = False
        for eintrag in self._unterbrechungen:
            if eintrag.get("offen"):
                eintrag.update(offen=False, ende=None, dauer_s=None, abschluss="Neustart: Ende unbekannt")
                geaendert = True
        if geaendert:
            self._speichern()

    @staticmethod
    def _laden(eintrag, key):
        # Auch persistente Daten nach einem Versionswechsel nicht blind ausgeben.
        if not re.fullmatch(r"[0-9a-f]{32}", eintrag["id"]):
            return None
        sauber = {"id": eintrag["id"], "zeitpunkt": eintrag["zeitpunkt"]}
        if key == "einstellungen":
            alt, neu = {}, {}
            for feld, werte in _dict(eintrag.get("aenderungen")).items():
                if isinstance(werte, dict):
                    alt[feld], neu[feld] = werte.get("vorher"), werte.get("nachher")
            alt, neu = _profil(alt), _profil(neu)
            aenderungen = {k: {"vorher": alt[k], "nachher": neu[k]}
                           for k in sorted(alt.keys() & neu.keys()) if alt[k] != neu[k]}
            if not aenderungen:
                return None
            quelle = eintrag.get("quelle")
            sauber.update(quelle=quelle if isinstance(quelle, str) and quelle in QUELLEN else "unbekannt",
                          kontext=_ids(eintrag.get("kontext")), aenderungen=aenderungen)
        else:
            klasse = eintrag.get("klasse")
            if klasse not in ("Pause", "Aus", "Störung"):
                return None
            ende = eintrag.get("ende")
            ende = ende if _zahl(ende) and ende >= sauber["zeitpunkt"] else None
            sauber.update(klasse=klasse, grund=_grund(eintrag.get("grund")),
                          offen=eintrag.get("offen") is True, ende=ende,
                          dauer_s=ende - sauber["zeitpunkt"] if ende is not None else None,
                          snapshot=_snapshot(_dict(eintrag.get("snapshot"))))
            abschluss = eintrag.get("abschluss")
            if abschluss in ("Zustandswechsel", "Neustart: Ende unbekannt", "Beobachtungslücke: Ende unbekannt"):
                sauber["abschluss"] = abschluss
        return sauber

    def _speichern(self):
        self.storage.set_setting(SCHLUESSEL, deepcopy({
            "version": 1, "unterbrechungen": self._unterbrechungen,
            "einstellungen": self._einstellungen,
        }))

    def _beenden(self, jetzt, abschluss):
        if self._offen is not None:
            self._offen.update(ende=jetzt, dauer_s=max(0.0, jetzt - self._offen["zeitpunkt"]),
                               offen=False, abschluss=abschluss)
            self._offen = None
            return True
        return False

    def observation(self, state, now=None):
        """Zustandskante protokollieren; identische Folgeticks verursachen kein DB-I/O.

        Nach mehr als fünf Sekunden Beobachtungslücke wird das alte Ereignis am
        letzten beobachteten Zeitpunkt beendet (Abschluss benennt die Lücke).
        Die Signatur enthält Grundklasse und Gespann/Feld/Auftrags-IDs, keine
        schwankenden Messwerte. Ungültige Lenkzustände liefern keine neue Kante.
        """
        jetzt = time.time() if now is None else now
        if not _zahl(jetzt):
            raise ValueError("Zeitpunkt muss endlich sein")
        state = _dict(state)
        steering = _dict(state.get("steering"))
        if any(type(steering.get(k)) is not bool for k in ("armed", "engaged")):
            return
        with self._lock:
            if self._zuletzt is not None and jetzt < self._zuletzt:
                return
            geaendert = False
            if self._zuletzt is not None and jetzt - self._zuletzt > 5:
                geaendert = self._beenden(self._zuletzt, "Beobachtungslücke: Ende unbekannt")
                self._letzter_zustand = None
            self._zuletzt = jetzt
            armed, engaged = steering["armed"], steering["engaged"]
            grund = _grund(steering.get("reason"))
            klasse = ("Störung" if grund in STOER_GRUENDE else "Pause") if armed else (
                "Aus" if grund in AUS_GRUENDE else "Störung")
            signatur = (armed, engaged, grund if not engaged else "", tuple(sorted(_ids(state).items())))
            if signatur == self._letzter_zustand:
                return
            vorher = self._letzter_zustand
            self._letzter_zustand = signatur
            geaendert = self._beenden(jetzt, "Zustandswechsel") or geaendert
            # Beim Start nicht jeden abgestellten Traktor als Unterbrechung melden.
            beginnen = not engaged and (armed or (vorher is not None and (vorher[0] or vorher[1])))
            if beginnen:
                self._offen = {
                    "id": uuid.uuid4().hex, "zeitpunkt": jetzt, "ende": None,
                    "dauer_s": None, "offen": True, "klasse": klasse, "grund": grund,
                    "snapshot": _snapshot(state),
                }
                self._unterbrechungen.append(self._offen)
                del self._unterbrechungen[:-self.limit]
                geaendert = True
            if geaendert:
                self._speichern()

    # Deutscher Alias für Aufrufer im bestehenden Programm.
    beobachten = observation

    def einstellung_aendern(self, vorher, nachher, quelle, kontext=None):
        """Nur Änderungen gemeinsamer, erlaubter skalarer Profilwerte speichern.

        Fehlende/ungültige Werte gelten nicht als Löschung. Kein Restore und
        keine Änderung der übergebenen Profile. Unbekannte Quellen werden neutral.
        """
        alt, neu = _profil(vorher), _profil(nachher)
        aenderungen = {k: {"vorher": alt[k], "nachher": neu[k]}
                       for k in sorted(alt.keys() & neu.keys()) if alt[k] != neu[k]}
        if not aenderungen:
            return None
        with self._lock:
            eintrag = {
                "id": uuid.uuid4().hex, "zeitpunkt": time.time(),
                "quelle": quelle if isinstance(quelle, str) and quelle in QUELLEN else "unbekannt",
                "kontext": _ids(kontext), "aenderungen": aenderungen,
            }
            self._einstellungen.append(eintrag)
            del self._einstellungen[:-self.limit]
            self._speichern()
            return deepcopy(eintrag)

    record = einstellung_aendern

    def verlauf(self, limit=100):
        """Beide Verläufe neueste zuerst; offene Dauer nur bis zum letzten Tick."""
        anzahl = max(0, min(self.limit, int(limit)))
        with self._lock:
            ereignisse = deepcopy(list(reversed(self._unterbrechungen))[:anzahl])
            for eintrag in ereignisse:
                if eintrag.get("offen") and self._zuletzt is not None:
                    eintrag["dauer_s"] = max(0.0, self._zuletzt - eintrag["zeitpunkt"])
            return {"unterbrechungen": ereignisse,
                    "einstellungen": deepcopy(list(reversed(self._einstellungen))[:anzahl])}
