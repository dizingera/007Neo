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

Misst der Drehgeber, wie lange das Rad dem Befehl hinterherläuft (nachlauf.py),
kommt das vor allem anderen: Bei einem trägen Motor (ab ``NACHLAUF_VORAUS_S``)
schaukelt sich Stanley auf, ganz gleich wie klein die Verstärkung ist - dann
stellt der Assistent auf die Regelart „Vorausschauend“ um, die den Nachlauf
einrechnet, und hält deren Trägheit beim gemessenen Wert.

Was dabei nie passiert:

* Geändert wird nur, wenn die Maschine ruhig geradeaus fährt (Tempo im Bereich,
  keine Wende, kein Spurwechsel, kein Versatz, keine Unterbrechung).
* Jeder Schritt ist klein (-15 % / +15 %), höchstens einer je Fenster, die
  Werte bleiben in festen Grenzen und es gibt eine Obergrenze an Schritten je
  Einschalten.
* Es gibt immer den Weg zurück auf die Ausgangswerte vom Einschalten.
* An der Hardware (Strom, Failsafe, Anschläge) ändert er nichts - nur an den
  Reglerwerten ``steer_gain``, ``actuator_latency_ms``, ``regelart`` und
  ``lenk_traegheit_s``.

Die Auswertung (``bewerte``) ist eine reine Funktion ohne Zustand und lässt sich
deshalb mit nachgestellten Messreihen prüfen; ``Lenkabstimmung`` hält den
Zustand und entscheidet.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

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

NACHLAUF_VORAUS_S = 0.6      # ab so viel Nachlauf kommt Stanley nicht mehr mit
TRAEGHEIT_BAND = (0.6, 1.4)  # die Trägheit passt, solange sie so weit beim Nachlauf liegt
TRAEGHEIT_MIN_S, TRAEGHEIT_MAX_S = 0.1, 2.5


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
    von: Any                  # Zahl, bei der Regelart ihr Name
    nach: Any
    grund: str

    def to_dict(self) -> dict:
        def runden(x):
            return round(x, 3) if isinstance(x, float) else x
        return {"zeit": self.zeit, "was": self.was, "von": runden(self.von),
                "nach": runden(self.nach), "grund": self.grund}


@dataclass
class Lenkabstimmung:
    """Zustand und Entscheidung. ``lesen``/``setzen`` verbinden mit dem Profil."""

    lesen: Callable[[], dict]                 # {"steer_gain": ..., "actuator_latency_ms": ..., "regelart": ..., "lenk_traegheit_s": ...}
    setzen: Callable[[dict], None]
    nachlauf: Optional[Callable[[], Optional[float]]] = None   # gemessener Nachlauf (s), None = unsicher
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

        # Ein träger Motor ist eine Frage der Regelart, nicht der Verstärkung.
        if self._an_den_nachlauf(befund, werte, zeit):
            return

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

    def _ausgang_merken(self, werte: dict) -> None:
        """Was zum ersten Mal verstellt wird, mit dem alten Wert in die Ausgangswerte.

        Gespeicherte Ausgangswerte älterer Fassungen kennen nur Verstärkung und
        Vorhalt - ohne das bliebe nach „zurück“ die umgestellte Regelart stehen.
        """
        if self.ausgangswerte is None:
            return
        for name, wert in werte.items():
            if wert is not None and name not in self.ausgangswerte:
                self.ausgangswerte[name] = wert

    def gemessener_nachlauf(self) -> Optional[float]:
        if self.nachlauf is None:
            return None
        try:
            return self.nachlauf()
        except Exception:  # noqa: BLE001 - ohne Messung arbeitet der Assistent wie bisher
            return None

    def _an_den_nachlauf(self, befund: Befund, werte: dict, zeit: float) -> bool:
        """Regelart und Trägheit zum gemessenen Nachlauf passend machen. True, wenn geändert."""
        n = self.gemessener_nachlauf()
        regelart = werte.get("regelart")
        if n is None or regelart is None:
            return False
        ziel = round(min(TRAEGHEIT_MAX_S, max(TRAEGHEIT_MIN_S, n)), 2)
        if regelart == "voraus":
            traegheit = float(werte.get("lenk_traegheit_s", 0.3))
            if TRAEGHEIT_BAND[0] * n <= traegheit <= TRAEGHEIT_BAND[1] * n:
                return False
            self.hinweis = (f"Das Rad folgt dem Befehl {n:.1f} s später - die Trägheit der Lenkung "
                            f"stand auf {traegheit:.2f} s.")
            if self._aendern("lenk_traegheit_s", traegheit, ziel, zeit, "an den gemessenen Nachlauf angepasst",
                             force=self.modus == "automatisch"):
                self._versuch = None
                return True
            return False
        if n < NACHLAUF_VORAUS_S or befund.art != "schwingt":
            return False
        if any(s.was == "regelart" for s in self.schritte):
            return False                     # schon einmal umgestellt: nicht hin und her
        self.hinweis = (f"Das Rad folgt dem Befehl erst {n:.1f} s später. Damit schaukelt sich Stanley auf - "
                        f"„Vorausschauend“ rechnet den Nachlauf ein.")
        self.schritte.append(Schritt(zeit, "regelart", regelart, "voraus", f"träger Motor, Trägheit {ziel:.2f} s"))
        self._ausgang_merken({"regelart": regelart, "lenk_traegheit_s": werte.get("lenk_traegheit_s")})
        if self.modus == "automatisch":
            self.setzen({"regelart": "voraus", "lenk_traegheit_s": ziel})
            self._warte = 1
        else:
            self.hinweis += f" Vorschlag: Regelart Vorausschauend, Trägheit der Lenkung {ziel:.2f} s."
        self._versuch = None
        return True

    def _aendern(self, was: str, von: float, nach: float, zeit: float, grund: str,
                 force: bool = False) -> bool:
        if abs(nach - von) < 1e-9:
            return False
        if not force and len(self.schritte) >= MAX_SCHRITTE:
            self.hinweis = "Höchstzahl an Schritten erreicht - Werte bleiben stehen."
            return False
        self.schritte.append(Schritt(zeit, was, von, nach, grund))
        self._ausgang_merken({was: von})
        if self.modus == "automatisch":
            self.setzen({was: int(round(nach)) if was == "actuator_latency_ms" else round(nach, 4)})
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
            "regelart": werte.get("regelart"),
            "lenk_traegheit_s": werte.get("lenk_traegheit_s"),
            "nachlauf_s": self.gemessener_nachlauf(),
            "ausgangswerte": self.ausgangswerte,
            "befund": self.befund.to_dict() if self.befund else None,
            "hinweis": self.hinweis,
            "schritte": [s.to_dict() for s in self.schritte[-8:]],
            "schritte_gesamt": len(self.schritte),
            "fenster_s": FENSTER_S,
            "sammelt_s": round(self.zeiten[-1] - self.zeiten[0], 1) if len(self.zeiten) > 1 else 0.0,
            "fenster_gesamt": self.fenster_gesamt,
        }
