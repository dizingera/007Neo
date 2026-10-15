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

SCHRITTE = [
    ("verbindung", "Lenkmotor verbinden"),
    ("richtung", "Drehrichtung prüfen"),
    ("mindestleistung", "Mindestleistung einmessen"),
    ("zaehlwerte", "Zählwerte je Grad messen"),
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
        "notaus": notaus_abgehakt,
        "freigabe": lenkung_freigegeben,
        "probefahrt": bool(erledigt.get("probefahrt"))
                      or ((abstimmung or {}).get("befund") or {}).get("art") == "gut",
    }
    ergebnis, davor_fertig = [], True
    for kennung, titel in SCHRITTE:
        ergebnis.append({"id": kennung, "titel": titel, "fertig": fertig[kennung],
                         "moeglich": davor_fertig or kennung == "notaus",
                         "werte": erledigt.get(kennung) if isinstance(erledigt.get(kennung), dict) else None})
        if kennung != "notaus":
            davor_fertig = davor_fertig and fertig[kennung]
    return ergebnis
