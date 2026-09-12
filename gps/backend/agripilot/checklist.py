"""Inbetriebnahme: die acht Schritte, und was das Programm davon selbst prüft.

Der Einbau ist die Stelle, an der dieses System teuer schiefgehen kann - nicht
im Betrieb. Fast jeder Fehler, der später als "die Lenkung fährt daneben"
auffällt, ist in Wahrheit hier entstanden: eine Basis im Survey-in, ein
Antennenmaß geschätzt statt gemessen, ein Vorzeichen verkehrt herum. Alle drei
melden sich nie von selbst - das Bild in der Kabine sieht in jedem dieser Fälle
vollkommen gesund aus.

Deshalb steht hier die Reihenfolge aus ``docs/INSTALL.md`` nicht als Text,
sondern als Liste, die mitliest. Was das Programm messen kann, misst es:
ob überhaupt Sätze hereinkommen, ob "RTK fix" *dauerhaft* steht statt nur
gerade eben, ob der Neigungssensor genullt wurde, wie lange schon als reine
Lenkhilfe gefahren wurde. Ein Schritt mit einer solchen Prüfung lässt sich
nicht abhaken, solange die Prüfung nicht trägt - eine abgehakte Liste, die
niemand geprüft hat, wäre schlimmer als gar keine.

Was das Programm *nicht* wissen kann, bleibt ausdrücklich eine Aussage des
Menschen: dass der Not-Aus in der Motorleitung sitzt, dass mit dem Maßband
gemessen wurde, dass der Sensor verschraubt ist. Diese Schritte fragen nach
einer Bestätigung und sagen dazu, wofür sie geradesteht.

Der Stand liegt in den Einstellungen der Datenbank und übersteht damit einen
Neustand - der Einbau zieht sich über Tage.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

SETTING = "inbetriebnahme"

# Wie lange "RTK fix" ohne Unterbrechung stehen muss, bevor der Schritt trägt.
# Zwei Minuten sind kurz genug für die Werkstatt und lang genug, dass ein
# Fix, der nur beim Hinsehen kurz einrastet, nicht durchgeht.
RTK_DAUER_S = 120.0

# Wie lange als reine Lenkhilfe gefahren sein muss, bevor der Motor drankommt.
LENKHILFE_S = 2 * 3600.0


@dataclass
class Pruefung:
    """Ergebnis einer automatischen Prüfung.

    ``erfuellt`` ist ``None``, wenn das Programm es nicht wissen kann - dann
    zählt allein die Bestätigung des Menschen.
    """

    erfuellt: Optional[bool]
    meldung: str


@dataclass
class Schritt:
    id: str
    titel: str
    warum: str
    quittung: str = ""          # Text der Bestätigung; leer = rein automatisch
    pruefen: Optional[Callable[["Beobachtung", dict], Pruefung]] = None
    nur_wenn: Optional[Callable[[dict], bool]] = None  # sonst entfällt der Schritt


@dataclass
class Beobachtung:
    """Was sich erst über die Zeit zeigt, nicht in einem einzelnen Bild."""

    rtk_seit: Optional[float] = None      # seit wann ununterbrochen "RTK fix"
    rtk_bestzeit_s: float = 0.0           # längste Strecke am Stück
    rtk_verloren: int = 0                 # wie oft der Fix abgerissen ist
    hang_gesehen_deg: float = 0.0         # größter Seitenhang seit dem Start
    letzter_blick: float = field(default_factory=time.time)


def _fix(live: dict) -> dict:
    return live.get("fix") or {}


def _system(live: dict) -> dict:
    return live.get("system") or {}


# -- die einzelnen Prüfungen ------------------------------------------------

def _pruefe_empfaenger(_beobachtung: Beobachtung, live: dict) -> Pruefung:
    gnss = _system(live).get("gnss") or {}
    fix = _fix(live)
    if not gnss.get("lines"):
        return Pruefung(False, f"keine Sätze vom Empfänger ({gnss.get('status', '')})")
    if not fix or not fix.get("fix_quality"):
        return Pruefung(False, "Sätze kommen an, aber noch keine Position")
    teile = [f"{fix.get('satellites', 0)} Satelliten", fix.get("fix_label", "")]
    if fix.get("accuracy_m") is not None:
        # Die Genauigkeitsschätzung steht nur im GST-Satz. Fehlt sie, ist der
        # Satz im Empfänger nicht eingeschaltet - später fehlt sie im Protokoll.
        teile.append(f"± {fix['accuracy_m'] * 100:.0f} cm")
    else:
        return Pruefung(False, "GST-Satz fehlt - im Empfänger einschalten, "
                               "sonst gibt es keine Genauigkeitsangabe")
    return Pruefung(True, " · ".join(t for t in teile if t))


def _pruefe_treiber(_beobachtung: Beobachtung, live: dict) -> Pruefung:
    system = _system(live)
    fehlt = []
    ausgang = system.get("steering_output") or {}
    if ausgang.get("typ") == "phidget" and not ausgang.get("bereit"):
        fehlt.append(f"Lenkmotor: {ausgang.get('status', 'nicht bereit')}")
    imu = system.get("imu") or {}
    if imu.get("source") not in ("aus", None) and not imu.get("healthy"):
        fehlt.append(f"Neigungssensor: {imu.get('status', 'nicht bereit')}")
    if fehlt:
        return Pruefung(False, " · ".join(fehlt))
    return Pruefung(True, "alle eingerichteten Geräte melden sich")


def _pruefe_maschine(_beobachtung: Beobachtung, live: dict) -> Pruefung:
    profil = live.get("profile") or {}
    if not profil.get("width_m"):
        return Pruefung(False, "Arbeitsbreite fehlt")
    return Pruefung(None,
                    f"Arbeitsbreite {profil['width_m']:.2f} m · "
                    f"Antenne {profil.get('antenna_forward_m', 0):.2f} m vor der Achse, "
                    f"{profil.get('antenna_right_m', 0):.2f} m rechts, "
                    f"{profil.get('antenna_height_m', 0):.2f} m hoch")


def _pruefe_korrekturen(beobachtung: Beobachtung, live: dict) -> Pruefung:
    fix = _fix(live)
    qualitaet = fix.get("fix_quality", 0)
    alter = fix.get("age_of_corrections")
    if qualitaet != 4:
        gesehen = (f", längste Strecke bisher {beobachtung.rtk_bestzeit_s / 60:.0f} min"
                   if beobachtung.rtk_bestzeit_s else "")
        return Pruefung(False, f"aktuell {fix.get('fix_label', 'kein Fix')}{gesehen}")
    steht_s = time.time() - (beobachtung.rtk_seit or time.time())
    # Abgerundet auf Minute und Sekunde: aufgerundet stünde bei 1:59 "2 min"
    # neben einem roten Befund, und die Anzeige widerspräche sich selbst.
    text = (f"RTK fix steht seit {int(steht_s // 60)}:{int(steht_s % 60):02d} min"
            + (f" · Korrekturen {alter:.0f} s alt" if alter is not None else ""))
    if beobachtung.rtk_verloren:
        text += f" · {beobachtung.rtk_verloren}× abgerissen"
    if steht_s < RTK_DAUER_S:
        return Pruefung(False, text + f" (mindestens {int(RTK_DAUER_S // 60)} min)")
    return Pruefung(True, text)


def _pruefe_neigungssensor(beobachtung: Beobachtung, live: dict) -> Pruefung:
    imu = live.get("imu")
    if not imu:
        return Pruefung(None, "kein Neigungssensor eingerichtet")
    if not imu.get("healthy"):
        return Pruefung(False, imu.get("status", "meldet sich nicht"))
    versatz = abs((imu.get("terrain_offset_cm") or [0.0, 0.0])[0])
    text = (f"Hang {imu.get('roll_deg', 0.0):+.1f}° · Ausgleich {versatz:.0f} cm"
            f" · größter Hang bisher {beobachtung.hang_gesehen_deg:.1f}°")
    if beobachtung.hang_gesehen_deg < 2.0:
        # Ohne Schräglage sagt die Probe nichts: auf ebenem Boden zeigt auch
        # ein verkehrtes Vorzeichen einen sauberen Ausgleich.
        return Pruefung(False, text + " - für die Vorzeichenprobe über eine "
                                      "Furche fahren (mindestens 2° Schräglage)")
    return Pruefung(None, text)


def _pruefe_lenkhilfe(_beobachtung: Beobachtung, live: dict) -> Pruefung:
    gefahren = live.get("fahrzeit_s", 0.0)
    text = f"{gefahren / 3600:.1f} h aufgezeichnet"
    if gefahren < LENKHILFE_S:
        return Pruefung(False, text + f" von {LENKHILFE_S / 3600:.0f} h")
    return Pruefung(True, text)


def _pruefe_lenkmotor(_beobachtung: Beobachtung, live: dict) -> Pruefung:
    ausgang = (_system(live).get("steering_output") or {})
    if ausgang.get("typ") in (None, "none"):
        return Pruefung(None, "kein Lenkausgang eingerichtet")
    if not ausgang.get("bereit"):
        return Pruefung(False, ausgang.get("status", "Ausgang nicht bereit"))
    if ausgang.get("mitte_gelernt") is False:
        return Pruefung(False, "Mitte noch nicht gelernt - bei geraden Rädern lernen")
    zaehlwerte = ausgang.get("zaehlwerte_je_grad")
    return Pruefung(None, f"{ausgang.get('status', '')}"
                          + (f" · {zaehlwerte} Zählwerte je Grad" if zaehlwerte else ""))


def _lenkung_vorgesehen(live: dict) -> bool:
    ausgang = (_system(live).get("steering_output") or {})
    return ausgang.get("typ") not in (None, "none")


SCHRITTE: list[Schritt] = [
    Schritt(
        id="simulator",
        titel="Am Schreibtisch mit dem Simulator vertraut machen",
        warum="Wer die Oberfläche erst auf dem Feld kennenlernt, sucht dort zwei "
              "Fehler gleichzeitig: den eigenen und den der Anlage.",
        quittung="Spur angelegt, Grenze umfahren, Arbeit gestartet - alles im Simulator",
    ),
    Schritt(
        id="treiber",
        titel="Treiber installieren (Phidgets-Installer, Brick Daemon)",
        warum="Die Treiber stecken nicht in den Python-Paketen. Die Phidget-"
              "Bibliothek lädt den nativen Treiber erst beim ersten Objekt - "
              "der Fehler kommt also nicht beim Start, sondern beim Losfahren.",
        pruefen=_pruefe_treiber,
    ),
    Schritt(
        id="empfaenger",
        titel="Empfänger anschließen und einstellen",
        warum="10 Hz, die Sätze GGA, RMC, VTG und GST, 115200 Baud, RTCM3-Eingang "
              "frei. Ohne GST fehlt die Genauigkeitsangabe, und damit später "
              "jeder Beleg dafür, wie gut eine Fahrt wirklich lag.",
        pruefen=_pruefe_empfaenger,
    ),
    Schritt(
        id="maschine",
        titel="Maschine einmessen - Antennenmaße und Arbeitsbreite",
        warum="Gemessen wird ab Mitte Hinterachse, mit dem Maßband. 20 cm Fehler "
              "sind 20 cm Versatz in jeder Spur - die häufigste Ursache für "
              "\"das System fährt daneben\". Die Arbeitsbreite legt den Spurabstand "
              "fest und gehört vor die erste Arbeit, sonst passt die bearbeitete "
              "Fläche später nicht mehr dazu.",
        quittung="Maße mit dem Maßband genommen, nicht geschätzt",
        pruefen=_pruefe_maschine,
    ),
    Schritt(
        id="korrekturen",
        titel="Basis anbinden - RTK fix muss dauerhaft stehen",
        warum="Die Basis gehört auf feste Koordinaten, nicht auf Survey-in: sonst "
              "mittelt sie sich nach jedem Stromausfall neu ein, alle Spuren "
              "wandern mit, und nirgends erscheint ein Fehler. \"RTK float\" ist "
              "nicht \"RTK fix\" - Float springt um Dezimeter, sichtbar erst "
              "abends an den Streifen im Feld.",
        quittung="Basis läuft im Fixed Mode auf notierten Koordinaten (kein Survey-in)",
        pruefen=_pruefe_korrekturen,
    ),
    Schritt(
        id="neigungssensor",
        titel="Neigungssensor montieren, nullen, Vorzeichen prüfen",
        warum="Verschraubt montieren, auf ebenem Boden nullen, dann über eine "
              "Furche fahren: bleibt die Abweichung ruhig, stimmt das Vorzeichen. "
              "Wird sie beim Kippeln größer, gehört roll_sign auf -1.0. Ein "
              "falsches Vorzeichen verdoppelt den Fehler, statt ihn aufzuheben.",
        quittung="Über eine Furche gefahren, Abweichung blieb ruhig",
        pruefen=_pruefe_neigungssensor,
    ),
    Schritt(
        id="lenkhilfe",
        titel="Ein paar Stunden als reine Lenkhilfe fahren",
        warum="Erst wenn die Spuren ohne Motor sauber liegen, stimmt die "
              "Grundlage. Wer hier abkürzt, sucht später Lenkfehler, die in "
              "Wahrheit Messfehler sind.",
        pruefen=_pruefe_lenkhilfe,
    ),
    Schritt(
        id="lenkmotor",
        titel="Lenkmotor anbauen, Not-Aus einbauen, Zählwerte je Grad messen",
        warum="Erste Fahrt auf freier Fläche, Schritttempo, Hand am Lenkrad, "
              "Räder gerade beim Scharfschalten. Die Stromgrenze bleibt niedrig, "
              "damit das Lenkrad von Hand übersteuerbar ist.",
        quittung="Not-Aus sitzt in der Motorleitung und wurde ausgelöst getestet",
        pruefen=_pruefe_lenkmotor,
        nur_wenn=_lenkung_vorgesehen,
    ),
]


class Checkliste:
    """Führt Buch über die Inbetriebnahme - beobachtend, nicht eingreifend."""

    def __init__(self, store) -> None:
        self.store = store
        self.beobachtung = Beobachtung()

    # -- Beobachtung ------------------------------------------------------

    def beobachten(self, live: dict) -> None:
        """Mitlesen, was sich nur über die Zeit zeigt. Läuft im Takt der Anzeige."""
        jetzt = time.time()
        self.beobachtung.letzter_blick = jetzt

        if _fix(live).get("fix_quality") == 4:
            if self.beobachtung.rtk_seit is None:
                self.beobachtung.rtk_seit = jetzt
            self.beobachtung.rtk_bestzeit_s = max(
                self.beobachtung.rtk_bestzeit_s, jetzt - self.beobachtung.rtk_seit)
        elif self.beobachtung.rtk_seit is not None:
            self.beobachtung.rtk_seit = None
            self.beobachtung.rtk_verloren += 1

        imu = live.get("imu")
        if imu and imu.get("healthy"):
            self.beobachtung.hang_gesehen_deg = max(
                self.beobachtung.hang_gesehen_deg, abs(imu.get("roll_deg", 0.0)))

    # -- Stand ------------------------------------------------------------

    def _quittungen(self) -> dict[str, Any]:
        gespeichert = self.store.get_setting(SETTING) or {}
        return gespeichert if isinstance(gespeichert, dict) else {}

    def schritte(self, live: dict) -> list[dict]:
        quittungen = self._quittungen()
        ausgabe = []
        offen_gefunden = False
        for nummer, schritt in enumerate(SCHRITTE, start=1):
            if schritt.nur_wenn is not None and not schritt.nur_wenn(live):
                continue
            pruefung = (schritt.pruefen(self.beobachtung, live)
                        if schritt.pruefen else Pruefung(None, ""))
            quittung = quittungen.get(schritt.id) or {}
            erledigt = bool(quittung.get("erledigt"))
            # Ein Schritt ist fertig, wenn er bestätigt ist - und, wo es eine
            # Bestätigung gar nicht braucht, sobald die Prüfung trägt.
            fertig = erledigt or (not schritt.quittung and pruefung.erfuellt is True)
            eintrag = {
                "id": schritt.id,
                "nummer": nummer,
                "titel": schritt.titel,
                "warum": schritt.warum,
                "quittung": schritt.quittung,
                "pruefung": pruefung.meldung,
                "erfuellt": pruefung.erfuellt,
                "fertig": fertig,
                "bestaetigt_am": quittung.get("zeit"),
                "bestaetigt_von": quittung.get("geraet"),
                "dran": False,
            }
            if not fertig and not offen_gefunden:
                eintrag["dran"] = True
                offen_gefunden = True
            ausgabe.append(eintrag)
        return ausgabe

    def zusammenfassung(self, live: dict) -> dict:
        schritte = self.schritte(live)
        fertig = [s for s in schritte if s["fertig"]]
        dran = next((s for s in schritte if s["dran"]), None)
        return {
            "schritte": schritte,
            "fertig": len(fertig),
            "gesamt": len(schritte),
            "abgeschlossen": len(fertig) == len(schritte),
            "naechster": dran["titel"] if dran else "",
        }

    # -- Abhaken ----------------------------------------------------------

    def abhaken(self, schritt_id: str, live: dict, erledigt: bool = True,
                geraet: str = "") -> dict:
        schritt = next((s for s in SCHRITTE if s.id == schritt_id), None)
        if schritt is None:
            raise KeyError(f"Schritt {schritt_id} gibt es nicht")

        quittungen = dict(self._quittungen())
        if not erledigt:
            quittungen.pop(schritt_id, None)
            self.store.set_setting(SETTING, quittungen)
            return self.zusammenfassung(live)

        if schritt.pruefen is not None:
            pruefung = schritt.pruefen(self.beobachtung, live)
            if pruefung.erfuellt is False:
                # Nicht abhaken lassen, was das Programm gerade widerlegt.
                # Eine Liste, die man gegen die Anzeige abhaken kann, wäre
                # genau die Sorte Papier, die im Feld nichts wert ist.
                raise ValueError(f"{schritt.titel}: {pruefung.meldung}")

        quittungen[schritt_id] = {"erledigt": True, "zeit": time.time(),
                                  "geraet": geraet}
        self.store.set_setting(SETTING, quittungen)
        return self.zusammenfassung(live)

    def zuruecksetzen(self, live: dict) -> dict:
        """Nach einem Umbau fängt die Inbetriebnahme wieder von vorn an."""
        self.store.set_setting(SETTING, {})
        self.beobachtung = Beobachtung()
        return self.zusammenfassung(live)


def fahrzeit_s(store) -> float:
    """Aufgezeichnete Arbeitszeit über alle Aufträge - für Schritt 7."""
    return sum(float(job.get("working_time_s") or 0.0) for job in store.list_jobs())
