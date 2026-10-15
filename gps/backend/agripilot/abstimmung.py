"""Lenk-Assistent: merkt, wie die Lenkung die Spur hält, und stellt sie nach.

Beobachtet wird, solange die Lenkung wirklich lenkt: die Querabweichung zur
Spur, in Fenstern von rund 20 Sekunden Geradeausfahrt. Daraus ergibt sich, was
die Regelung gerade tut:

* **Schwingen** - die Maschine pendelt um die Spur, mit gleichbleibender
  Amplitude. Der Regler zieht zu hart (oder zu spät): Verstärkung runter. Hilft
  das an der Untergrenze nicht, wird der Vorhalt für die Totzeit der Lenkung
  vergrößert.
* **Träge** - die Abweichung ist groß und schwingt nicht: der Regler zieht zu
  lasch. Verstärkung hoch - aber als Versuch. Wird es dadurch nicht merklich
  besser, ist es keine Frage der Verstärkung (Lenkmitte falsch, Hang, Versatz):
  der Schritt geht zurück, und der Assistent sagt es, statt weiter zu drehen.
* **Gut** - nichts ändern.

Was dabei nie passiert:

* Geändert wird nur, wenn die Maschine ruhig geradeaus fährt (Tempo im Bereich,
  keine Wende, kein Spurwechsel, kein Versatz, keine Unterbrechung).
* Jeder Schritt ist klein (-15 % / +15 %), höchstens einer je Fenster, die
  Werte bleiben in festen Grenzen und es gibt eine Obergrenze an Schritten je
  Einschalten.
* Es gibt immer den Weg zurück auf die Ausgangswerte vom Einschalten.
* An der Hardware (Strom, Failsafe, Anschläge) ändert er nichts - nur an den
  beiden Reglerwerten ``steer_gain`` und ``actuator_latency_ms``.

Die Auswertung (``bewerte``) ist eine reine Funktion ohne Zustand und lässt sich
deshalb mit nachgestellten Messreihen prüfen; ``Lenkabstimmung`` hält den
Zustand und entscheidet.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

FENSTER_S = 20.0            # so lange Geradeausfahrt braucht eine Bewertung
LUECKE_S = 1.5              # länger ohne gültige Messung: Fenster verwerfen
TEMPO_MIN_MS = 0.8          # 2,9 km/h
TEMPO_MAX_MS = 6.0          # 21,6 km/h
QUER_MAX_M = 1.0            # weiter weg: noch beim Einfangen, nicht beim Halten

GAIN_MIN, GAIN_MAX = 0.3, 2.0
LATENZ_MAX_MS = 300
SCHRITT_RUNTER = 0.85
SCHRITT_HOCH = 1.15
LATENZ_SCHRITT_MS = 40
MAX_SCHRITTE = 12
PAUSE_NACH_FEHLVERSUCH = 5   # Fenster, in denen kein Hochdrehen mehr versucht wird

SCHWING_AMPLITUDE_M = 0.05   # ab dieser Amplitude zählt Pendeln
SCHWING_NULLDURCHGAENGE = 6  # im Fenster
TRAEGE_EFFEKTIV_M = 0.10     # ab dieser Effektivabweichung ohne Pendeln ist es "träge"
BESSER_FAKTOR = 0.90         # so viel besser muss ein Hochdrehen machen
HYSTERESE_M = 0.015
NAH_M = 0.4                  # so nah war die Maschine an der Spur, bevor sie sie verlor
VERLUST_FRIST_S = 20.0       # und das ist höchstens so lange her
VERLUST_PAUSE_S = 10.0       # zwischen zwei Schritten wegen Spurverlust
WACHSEN_FAKTOR = 1.4         # letzter Ausschlag gegenüber dem ersten
WACHSEN_MIN_M = 0.12
FRUEH_S = 6.0                # ab so viel Messung wird auf Aufschaukeln geprüft


@dataclass
class Befund:
    art: str                  # "schwingt" | "traege" | "gut"
    effektiv_m: float         # Effektivwert der Querabweichung
    mittel_m: float           # Dauerabweichung
    amplitude_m: float        # typische Schwingweite
    nulldurchgaenge: int
    periode_s: float          # 0, wenn nicht schwingend
    abklingend: bool          # zweite Hälfte deutlich kleiner als die erste
    wachsend: bool = False    # Ausschläge werden größer: schaukelt sich auf

    def to_dict(self) -> dict:
        return {k: (round(v, 3) if isinstance(v, float) else v) for k, v in self.__dict__.items()}


def bewerte(zeiten: list[float], quer: list[float]) -> Befund:
    """Eine Messreihe der Querabweichung (m) beurteilen."""
    n = len(quer)
    if n < 10 or len(zeiten) != n:
        return Befund("gut", 0.0, 0.0, 0.0, 0, 0.0, False, False)
    mittel = sum(quer) / n
    rest = [q - mittel for q in quer]
    effektiv = math.sqrt(sum(q * q for q in quer) / n)

    # Nulldurchgänge um die Dauerabweichung, mit Hysterese: Rauschen um null
    # soll kein Pendeln sein.
    durchgaenge, seite = 0, 0
    spitzen: list[float] = []
    spitze = 0.0
    for wert in rest:
        if seite >= 0 and wert < -HYSTERESE_M:
            if seite == 1:
                durchgaenge += 1
                spitzen.append(spitze)
            seite, spitze = -1, abs(wert)
        elif seite <= 0 and wert > HYSTERESE_M:
            if seite == -1:
                durchgaenge += 1
                spitzen.append(spitze)
            seite, spitze = 1, abs(wert)
        else:
            spitze = max(spitze, abs(wert))
    amplitude = sorted(spitzen)[len(spitzen) // 2] if spitzen else 0.0
    dauer = zeiten[-1] - zeiten[0]
    periode = (2.0 * dauer / durchgaenge) if durchgaenge >= 2 else 0.0

    haelfte = n // 2
    erste = math.sqrt(sum(q * q for q in rest[:haelfte]) / max(1, haelfte))
    zweite = math.sqrt(sum(q * q for q in rest[haelfte:]) / max(1, n - haelfte))
    abklingend = erste > 0 and zweite < 0.6 * erste

    # Aufschaukeln: die Ausschläge wachsen von Schwung zu Schwung. Das darf nicht
    # auf ein volles Fenster warten - bis dahin ist die Maschine längst
    # außerhalb des Messbereichs (und die Lenkung hat abgegeben).
    wachsend = (len(spitzen) >= 3 and spitzen[-1] >= WACHSEN_FAKTOR * max(spitzen[0], 0.02)
                and spitzen[-1] >= WACHSEN_MIN_M)
    if wachsend:
        art = "schwingt"
    elif durchgaenge >= SCHWING_NULLDURCHGAENGE and amplitude >= SCHWING_AMPLITUDE_M and not abklingend:
        art = "schwingt"
    elif effektiv >= TRAEGE_EFFEKTIV_M and durchgaenge <= 2:
        art = "traege"
    else:
        art = "gut"
    return Befund(art, effektiv, mittel, amplitude, durchgaenge, periode, abklingend, wachsend)


@dataclass
class Schritt:
    zeit: float
    was: str
    von: float
    nach: float
    grund: str

    def to_dict(self) -> dict:
        return {"zeit": self.zeit, "was": self.was, "von": round(self.von, 3),
                "nach": round(self.nach, 3), "grund": self.grund}


@dataclass
class Lenkabstimmung:
    """Zustand und Entscheidung. ``lesen``/``setzen`` verbinden mit dem Profil."""

    lesen: Callable[[], dict]                 # {"steer_gain": ..., "actuator_latency_ms": ...}
    setzen: Callable[[dict], None]
    modus: str = "aus"                        # aus | beobachten | automatisch
    zeiten: list = field(default_factory=list)
    quer: list = field(default_factory=list)
    _letzte: float = 0.0
    ausgangswerte: Optional[dict] = None
    schritte: list = field(default_factory=list)
    befund: Optional[Befund] = None
    hinweis: str = ""
    _pause: int = 0
    _warte: int = 0                           # Fenster, die nach einer Änderung zum Einschwingen ausgelassen werden
    _versuch: Optional[tuple] = None          # (alter Wert, effektiv davor) nach einem Hochdrehen
    fenster_gesamt: int = 0
    _nah_t: float = -1e9
    _verlust_t: float = -1e9

    # -- Bedienung --------------------------------------------------------

    def einschalten(self, modus: str) -> None:
        if modus not in ("aus", "beobachten", "automatisch"):
            raise ValueError("Modus: aus, beobachten oder automatisch")
        if modus != "aus" and self.modus == "aus":
            self.ausgangswerte = dict(self.lesen())
            self.schritte.clear()
            self._pause = 0
            self._versuch = None
            self.hinweis = ""
            self.befund = None
        self.modus = modus
        self._leeren()

    def zuruecksetzen(self) -> None:
        if self.ausgangswerte:
            self.setzen(dict(self.ausgangswerte))
            self.schritte.append(Schritt(time.time(), "zurück", 0.0, 0.0, "Ausgangswerte wiederhergestellt"))
        self._versuch = None
        self._leeren()

    def _leeren(self) -> None:
        self.zeiten.clear()
        self.quer.clear()

    # -- Messung ----------------------------------------------------------

    def beobachten(self, t: float, quer_m: float, tempo_ms: float, lenkt: bool, ruhig: bool) -> None:
        """Pro Position aufrufen. ``ruhig``: keine Wende, kein Spurwechsel, kein Versatz."""
        if self.modus == "aus":
            return
        fahrt = lenkt and ruhig and TEMPO_MIN_MS <= abs(tempo_ms) <= TEMPO_MAX_MS
        if fahrt and abs(quer_m) <= NAH_M:
            self._nah_t = t
        if (fahrt and abs(quer_m) > QUER_MAX_M and t - self._nah_t <= VERLUST_FRIST_S
                and t - self._verlust_t >= VERLUST_PAUSE_S):
            # Die Maschine hielt die Spur und ist dann weggelaufen: aufgeschaukelt.
            # Messfenster gibt es dafür nicht mehr, aber der Grund ist eindeutig.
            self._verlust_t = t
            self.zeiten.append(t)
            self.quer.append(quer_m)
            self._auswerten(Befund("schwingt", abs(quer_m), 0.0, abs(quer_m), 0, 0.0, False, True),
                            "Die Maschine hat die Spur nach gutem Halten verlassen.")
            self._leeren()
            return
        gueltig = fahrt and abs(quer_m) <= QUER_MAX_M
        if not gueltig:
            if self.zeiten and t - self._letzte > LUECKE_S:
                self._leeren()
            return
        if self.zeiten and t - self._letzte > LUECKE_S:
            self._leeren()
        self.zeiten.append(t)
        self.quer.append(quer_m)
        self._letzte = t
        dauer = self.zeiten[-1] - self.zeiten[0]
        if dauer >= FENSTER_S:
            self._auswerten(bewerte(self.zeiten, self.quer))
            self._leeren()
        elif dauer >= FRUEH_S:
            frueh = bewerte(self.zeiten, self.quer)
            if frueh.wachsend:
                self._auswerten(frueh)
                self._leeren()

    # -- Entscheidung -----------------------------------------------------

    def _auswerten(self, befund: Befund, grund: str = "") -> None:
        self.befund = befund
        self.fenster_gesamt += 1
        werte = self.lesen()
        gain, latenz = float(werte["steer_gain"]), float(werte["actuator_latency_ms"])
        zeit = self.zeiten[-1]
        if befund.wachsend:
            self._versuch = None             # was auch hochgedreht wurde: es war zu viel
            self._warte = 0
        # Direkt nach einer Änderung stimmt der Befund noch nicht: der Regler
        # schwingt erst ein, und ein träger braucht dafür länger als ein Fenster.
        # Pendeln wird trotzdem sofort beantwortet - Warten hilft dabei nicht.
        if self._warte > 0 and befund.art != "schwingt":
            self._warte -= 1
            self.hinweis = "Wartet, bis sich die Änderung eingeschwungen hat."
            return
        self._warte = 0

        # Ein Hochdrehen war ein Versuch: hat es geholfen?
        if self._versuch is not None:
            alt, effektiv_davor = self._versuch
            self._versuch = None
            if befund.effektiv_m > BESSER_FAKTOR * effektiv_davor and befund.art != "schwingt":
                self._aendern("steer_gain", gain, alt, zeit,
                              "Mehr Verstärkung hat nicht geholfen - zurück", force=True)
                self.hinweis = ("Die Abweichung bleibt trotz mehr Verstärkung: Lenkmitte, Hang oder "
                                "Spurversatz prüfen - das ist keine Frage der Reglerstärke.")
                self._pause = PAUSE_NACH_FEHLVERSUCH
                return
        if self._pause > 0:
            self._pause -= 1

        if befund.art == "schwingt":
            self.hinweis = grund or (f"Die Maschine pendelt ({befund.amplitude_m * 100:.0f} cm, "
                                     f"alle {befund.periode_s:.1f} s).")
            if gain > GAIN_MIN + 1e-9:
                self._aendern("steer_gain", gain, max(GAIN_MIN, gain * SCHRITT_RUNTER), zeit, "pendelt")
            elif latenz < LATENZ_MAX_MS:
                self._aendern("actuator_latency_ms", latenz, min(LATENZ_MAX_MS, latenz + LATENZ_SCHRITT_MS),
                              zeit, "pendelt trotz kleinster Verstärkung - Vorhalt für die Lenkverzögerung")
            else:
                self.hinweis += " Alle Stellgrößen am Ende: Mindestleistung, Spiel in der Lenkung oder GPS prüfen."
        elif befund.art == "traege":
            self.hinweis = f"Die Abweichung bleibt groß ({befund.effektiv_m * 100:.0f} cm) ohne zu pendeln."
            if self._pause == 0 and gain < GAIN_MAX - 1e-9:
                neu = min(GAIN_MAX, gain * SCHRITT_HOCH)
                if self._aendern("steer_gain", gain, neu, zeit, "zu träge - Versuch mit mehr Verstärkung"):
                    self._versuch = (gain, befund.effektiv_m)
        else:
            self.hinweis = f"Gut: {befund.effektiv_m * 100:.0f} cm Abweichung."

    def _aendern(self, was: str, von: float, nach: float, zeit: float, grund: str,
                 force: bool = False) -> bool:
        if abs(nach - von) < 1e-9:
            return False
        if not force and len(self.schritte) >= MAX_SCHRITTE:
            self.hinweis = "Höchstzahl an Schritten erreicht - Werte bleiben stehen."
            return False
        self.schritte.append(Schritt(zeit, was, von, nach, grund))
        if self.modus == "automatisch":
            self.setzen({was: round(nach, 4) if was == "steer_gain" else int(round(nach))})
            self._warte = 1
        else:
            self.hinweis += f" Vorschlag: {was} {von:g} → {nach:g}."
        return True

    # -- Auskunft ---------------------------------------------------------

    def status(self) -> dict:
        werte = self.lesen()
        return {
            "modus": self.modus,
            "steer_gain": round(float(werte["steer_gain"]), 3),
            "actuator_latency_ms": int(werte["actuator_latency_ms"]),
            "ausgangswerte": self.ausgangswerte,
            "befund": self.befund.to_dict() if self.befund else None,
            "hinweis": self.hinweis,
            "schritte": [s.to_dict() for s in self.schritte[-8:]],
            "schritte_gesamt": len(self.schritte),
            "fenster_s": FENSTER_S,
            "sammelt_s": round(self.zeiten[-1] - self.zeiten[0], 1) if len(self.zeiten) > 1 else 0.0,
            "fenster_gesamt": self.fenster_gesamt,
        }
