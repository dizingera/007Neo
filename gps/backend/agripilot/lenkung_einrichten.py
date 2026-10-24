"""Lenkung einrichten: der geführte Ablauf von der Verbindung bis zur Probefahrt.

Die Bausteine gibt es einzeln (Gerätesuche, Motortest, Motor einmessen,
Anschläge messen, Lenk-Assistent). Hier stehen sie in der Reihenfolge, in der
sie gebraucht werden, und jeder Schritt weiß, ob er erledigt ist.

Der eine Schritt, der hier neu gerechnet wird, ist die **Drehrichtung**: der
Regelkreis braucht zweierlei in derselben Richtung - wenn er "nach rechts"
will, müssen die Räder nach rechts gehen (``invert_motor``), und dann muss der
Drehgeber hochzählen (``encoder_invert``). Stimmt das zweite nicht, misst der
Regler die Bewegung verkehrt herum und lenkt vom Ziel weg statt hin. Das ist
kein Feinschliff, sondern Voraussetzung - deshalb kommt es vor allem anderen.
"""

from __future__ import annotations

from typing import Optional

FREI_NACH_RICHTUNG = ("mindestleistung", "zaehlwerte", "feinabstimmung")

SCHRITTE = [
    ("verbindung", "Lenkmotor verbinden"),
    ("richtung", "Drehrichtung prüfen"),
    ("mindestleistung", "Mindestleistung einmessen"),
    ("zaehlwerte", "Zählwerte je Grad messen"),
    ("feinabstimmung", "Feinabstimmung des Lenkmotors"),
    ("notaus", "Not-Aus und Einbau"),
    ("freigabe", "Lenkung freigeben"),
    ("probefahrt", "Probefahrt mit dem Lenk-Assistenten"),
]

MINDEST_BEWEGUNG = 10        # Zählwerte, damit ein Richtungstest zählt


def richtung_auswerten(gesehen: str, bewegt: Optional[int], invert_motor: bool,
                       encoder_invert: bool) -> dict:
    """Aus einem Impuls "nach rechts" und dem, was der Fahrer gesehen hat, die
    beiden Schalter bestimmen.

    ``gesehen``: "rechts" | "links" | "nichts" - wohin die Räder gingen.
    ``bewegt``:  wie weit der Drehgeber dabei gezählt hat (roh, ohne Umkehr).
    Zurück: die Änderungen an den Einstellungen und ein Satz für den Fahrer.
    """
    if gesehen not in ("rechts", "links", "nichts"):
        raise ValueError("Gesehen: rechts, links oder nichts")
    if gesehen == "nichts":
        return {"aenderungen": {}, "erledigt": False,
                "text": "Die Räder haben sich nicht bewegt. Leistung beim Motortest erhöhen oder "
                        "Kupplung, Motor und Stromversorgung prüfen - dann noch einmal."}
    if bewegt is None or abs(bewegt) < MINDEST_BEWEGUNG:
        return {"aenderungen": {}, "erledigt": False,
                "text": "Die Räder haben sich bewegt, der Drehgeber aber nicht (oder kaum). "
                        "Ist der Drehgeber am Motor angeschlossen? Ohne ihn kann die Lenkung "
                        "nicht geregelt werden."}
    neu_motor = invert_motor if gesehen == "rechts" else not invert_motor
    # Wie hätte der Drehgeber bei einer Bewegung nach rechts gezählt?
    nach_rechts = bewegt if gesehen == "rechts" else -bewegt
    gezaehlt_rechts = nach_rechts if not encoder_invert else -nach_rechts
    neu_geber = encoder_invert if gezaehlt_rechts > 0 else not encoder_invert
    aenderungen = {}
    if neu_motor != invert_motor:
        aenderungen["phidget.invert_motor"] = neu_motor
    if neu_geber != encoder_invert:
        aenderungen["phidget.encoder_invert"] = neu_geber
    teile = []
    if "phidget.invert_motor" in aenderungen:
        teile.append("Motor-Drehrichtung umgekehrt")
    if "phidget.encoder_invert" in aenderungen:
        teile.append("Drehgeber umgekehrt")
    text = ("Richtung stimmt schon." if not teile else ", ".join(teile) + ".") + \
        " Zur Kontrolle noch einmal nach rechts testen - dann müssen die Räder nach rechts gehen."
    return {"aenderungen": aenderungen, "erledigt": not aenderungen, "text": text}


def status(erledigt: dict, ausgang: dict, lenkung_freigegeben: bool,
           notaus_abgehakt: bool, abstimmung: dict) -> list[dict]:
    """Der Stand jedes Schritts. ``erledigt`` ist das gespeicherte Protokoll."""
    verbunden = ausgang.get("typ") == "phidget" and bool(ausgang.get("bereit"))
    fertig = {
        "verbindung": verbunden,
        "richtung": bool(erledigt.get("richtung")),
        "mindestleistung": bool(erledigt.get("mindestleistung")),
        "zaehlwerte": bool(erledigt.get("zaehlwerte")),
        "feinabstimmung": bool(erledigt.get("feinabstimmung")),
        "notaus": notaus_abgehakt,
        "freigabe": lenkung_freigegeben,
        "probefahrt": bool(erledigt.get("probefahrt"))
                      or ((abstimmung or {}).get("befund") or {}).get("art") == "gut",
    }
    # Mindestleistung, Zählwerte und Feinabstimmung brauchen nur einen verbundenen
    # Motor mit richtiger Drehrichtung - in welcher Reihenfolge, ist egal. Wer sie
    # hintereinander erzwingt, sperrt die Feinabstimmung, obwohl Mindestleistung
    # und Zählwerte längst eingetragen sind. Die Freigabe wartet weiter auf alles.
    grundlage = fertig["verbindung"] and fertig["richtung"]
    ergebnis, davor_fertig = [], True
    for kennung, titel in SCHRITTE:
        if kennung == "notaus":
            moeglich = True
        elif kennung in FREI_NACH_RICHTUNG:
            moeglich = grundlage
        else:
            moeglich = davor_fertig
        ergebnis.append({"id": kennung, "titel": titel, "fertig": fertig[kennung], "moeglich": moeglich,
                         "werte": erledigt.get(kennung) if isinstance(erledigt.get(kennung), dict) else None})
        if kennung != "notaus":
            davor_fertig = davor_fertig and fertig[kennung]
    return ergebnis


# -- Feinabstimmung des Motorreglers -------------------------------------------
# Am Stand fährt der Regler einen kleinen Sprung (6 Grad hin, zurück auf 0) und
# zeichnet auf, wie der Radwinkel folgt. Daraus wird P/I/D in kleinen Schritten
# nachgestellt, bis der Sprung schnell, ohne Überschwingen und ohne Rest sitzt.

FEIN_P = (0.02, 0.6)
FEIN_I = (0.0, 0.2)
FEIN_D = (0.0, 0.15)
FEIN_ANSTIEG_MAX_S = 1.2       # so schnell soll der Sprung zu 90 % erreicht sein
FEIN_UEBERSCHWINGEN_MAX = 0.25 # Anteil des Sprungs
FEIN_REST_MAX_DEG = 0.6        # bleibende Abweichung am Ende
FEIN_HYSTERESE_DEG = 0.3
FEIN_STEHT_DEG = 0.25          # so wenig darf sich der Winkel in der letzten halben Sekunde ändern


def bewerte_sprung(aufzeichnung: list, start_deg: float, ziel_deg: float) -> dict:
    """Wie gut ist der Radwinkel einem Sprung gefolgt?

    ``aufzeichnung``: [(Sekunden seit Sprungbeginn, gemessener Winkel), ...]
    """
    hub = ziel_deg - start_deg
    punkte = [(t, w) for t, w in aufzeichnung if w is not None]
    if abs(hub) < 0.5 or len(punkte) < 5:
        return {"gueltig": False, "anstieg_s": None, "ueberschwingen": 0.0, "rest_deg": 0.0,
                "schwingt": False, "bewegt": False, "steht": True}
    anteil = [(t, (w - start_deg) / hub) for t, w in punkte]
    anstieg = next((t for t, p in anteil if p >= 0.9), None)
    hoechst = max(p for _, p in anteil)
    ueber = max(0.0, hoechst - 1.0)
    ende = punkte[-1][0]
    letzte = [w for t, w in punkte if t >= ende - 0.5] or [punkte[-1][1]]
    rest = ziel_deg - sum(letzte) / len(letzte)
    steht = (max(letzte) - min(letzte)) < FEIN_STEHT_DEG     # am Ende keine Bewegung mehr
    wechsel, seite = 0, 0
    if anstieg is not None:
        for t, w in punkte:
            if t < anstieg:
                continue
            fehler = ziel_deg - w
            if fehler > FEIN_HYSTERESE_DEG and seite <= 0:
                wechsel += seite != 0
                seite = 1
            elif fehler < -FEIN_HYSTERESE_DEG and seite >= 0:
                wechsel += seite != 0
                seite = -1
    return {"gueltig": True, "anstieg_s": anstieg, "ueberschwingen": round(ueber, 3),
            "rest_deg": round(rest, 2), "schwingt": wechsel >= 3, "bewegt": hoechst >= 0.1,
            "steht": steht}


def feinabstimmung_schritt(befunde: list[dict], p: float, i: float, d: float) -> dict:
    """Aus den Befunden eines Durchgangs (hin und zurück) die nächsten Werte."""
    def grenze(wert, bereich):
        return round(max(bereich[0], min(bereich[1], wert)), 4)

    if not all(b["gueltig"] for b in befunde):
        return {"p": p, "i": i, "d": d, "fertig": True, "gut": False,
                "text": "Kein auswertbarer Sprung - Rückmeldung (Drehgeber) prüfen."}
    if not all(b["bewegt"] for b in befunde):
        return {"p": p, "i": i, "d": d, "fertig": True, "gut": False,
                "text": "Die Räder folgen dem Sprung nicht - Mindestleistung, Kupplung oder Motor prüfen."}
    if any(b["schwingt"] or b["ueberschwingen"] > FEIN_UEBERSCHWINGEN_MAX for b in befunde):
        neu_p, neu_d = grenze(p * 0.75, FEIN_P), grenze(max(d * 1.4, 0.005), FEIN_D)
        return {"p": neu_p, "i": i, "d": neu_d, "fertig": False, "gut": False,
                "text": "Überschwingt - weniger P, mehr Dämpfung (D)."}
    # Zu langsam: das Ziel nicht rechtzeitig erreicht - oder am Ende noch unterwegs
    # (dann ist ein Rest kein Stehenbleiben, sondern Langsamkeit).
    # Steht er dagegen vor dem Ziel still, hängt er an der Reibung: dafür ist I da.
    langsam = [b for b in befunde
               if (b["anstieg_s"] is None and not b["steht"])
               or (b["anstieg_s"] is not None and b["anstieg_s"] > FEIN_ANSTIEG_MAX_S)
               or (abs(b["rest_deg"]) > FEIN_REST_MAX_DEG and not b["steht"])]
    if langsam:
        if p >= FEIN_P[1] - 1e-9:
            return {"p": p, "i": i, "d": d, "fertig": True, "gut": False,
                    "text": "Auch mit größtem P zu langsam - Größte Leistung (max_duty) oder "
                            "Beschleunigung erhöhen, Motor/Übersetzung prüfen."}
        return {"p": grenze(p * 1.35, FEIN_P), "i": i, "d": d, "fertig": False, "gut": False,
                "text": "Zu langsam - mehr P."}
    if any(abs(b["rest_deg"]) > FEIN_REST_MAX_DEG for b in befunde):
        if i >= FEIN_I[1] - 1e-9:
            return {"p": p, "i": i, "d": d, "fertig": True, "gut": False,
                    "text": "Bleibt vor dem Ziel stehen - Mindestleistung erhöhen."}
        return {"p": p, "i": grenze(i + 0.03, FEIN_I), "d": d, "fertig": False, "gut": False,
                "text": "Bleibt vor dem Ziel stehen - etwas I dazu."}
    return {"p": p, "i": i, "d": d, "fertig": True, "gut": True,
            "text": "Sitzt: schnell, ohne Überschwingen, ohne Rest."}
