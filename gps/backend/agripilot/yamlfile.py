"""Konfigurationsdateien lesen und schreiben, auch ohne PyYAML.

Die Konfiguration ist die einzige Datei, die von Hand geschrieben wird - in der
Werkstatt, oft am frisch aufgesetzten Rechner. Genau dort fehlt PyYAML am
ehesten: der Installer lief nicht durch, jemand startet mit dem System-Python
statt aus dem venv, oder auf dem Tablet wurde das venv beim Update ersetzt.
Vorher endete das in einem ``JSONDecodeError`` in Zeile 1 - eine Meldung, die
mit der Ursache nichts zu tun hat und in der Kabine niemandem weiterhilft.

Deshalb liest der Kern seine Konfiguration selbst, so wie er auch Projektion,
NMEA, Führung und Fläche selbst rechnet. Abgedeckt ist der Umfang, den eine
Konfigurationsdatei braucht:

* verschachtelte Abschnitte über Einrückung (``gnss:`` / ``  port: COM3``)
* Zeichenketten, Zahlen, Wahrheitswerte, leere Werte
* Anführungszeichen und Kommentare

Alles darüber hinaus - Listen, Anker, mehrzeilige Blöcke - wird **nicht** still
verschluckt, sondern mit Zeilennummer abgelehnt. Eine halb verstandene
Konfiguration ist im Feld teurer als eine, die sich klar weigert. Ist PyYAML
vorhanden, liest ohnehin PyYAML: siehe ``config.py``.
"""

from __future__ import annotations

from typing import Any

_TRUE = ("true", "yes", "on")
_FALSE = ("false", "no", "off")
_LEER = ("", "~", "null", "none")


class ConfigSyntaxError(ValueError):
    """Die Konfigurationsdatei ist nicht lesbar - mit Zeilennummer im Text."""


def loads(text: str) -> dict:
    """Eine Konfigurationsdatei einlesen.

    Rückgabe ist immer ein Wörterbuch; eine leere Datei ergibt ``{}``.
    """
    wurzel: dict[str, Any] = {}
    # Jede Ebene mit ihrer Einrücktiefe, damit ein Abschnittsende erkannt wird.
    ebenen: list[tuple[int, dict[str, Any]]] = [(-1, wurzel)]

    for nummer, rohzeile in enumerate(text.splitlines(), start=1):
        zeile = _ohne_kommentar(rohzeile)
        if not zeile.strip():
            continue
        if zeile.lstrip().startswith("- "):
            raise ConfigSyntaxError(
                f"Zeile {nummer}: Listen kommen in der Konfiguration nicht vor "
                f"({rohzeile.strip()!r})")

        vorspann = zeile[:len(zeile) - len(zeile.lstrip())]
        if "\t" in vorspann:
            # Ein Tabulator sieht im Editor aus wie eine Einrückung, zählt hier
            # aber nicht - die Zeile landete sonst still im falschen Abschnitt.
            raise ConfigSyntaxError(
                f"Zeile {nummer}: mit Leerzeichen einrücken, nicht mit Tabulator")
        tiefe = len(vorspann)

        while ebenen and tiefe <= ebenen[-1][0]:
            ebenen.pop()
        if not ebenen:
            raise ConfigSyntaxError(f"Zeile {nummer}: Einrückung passt zu keinem Abschnitt")

        schluessel, trenner, rest = zeile.strip().partition(":")
        if not trenner:
            raise ConfigSyntaxError(
                f"Zeile {nummer}: kein Doppelpunkt gefunden ({rohzeile.strip()!r})")
        schluessel = _entkleiden(schluessel.strip())
        if not schluessel:
            raise ConfigSyntaxError(f"Zeile {nummer}: leerer Schlüssel")

        ziel = ebenen[-1][1]
        rest = rest.strip()
        if rest:
            ziel[schluessel] = _wert(rest)
        else:
            # Abschnittskopf: der eingerückte Block darunter gehört hier hinein.
            abschnitt: dict[str, Any] = {}
            ziel[schluessel] = abschnitt
            ebenen.append((tiefe, abschnitt))

    return wurzel


def dumps(data: dict) -> str:
    """Ein Wörterbuch als Konfigurationsdatei schreiben (Gegenstück zu ``loads``)."""
    return "".join(_zeilen(data, 0))


def _zeilen(data: dict, tiefe: int) -> list[str]:
    einzug = "  " * tiefe
    ausgabe: list[str] = []
    for schluessel, wert in data.items():
        if isinstance(wert, dict):
            ausgabe.append(f"{einzug}{schluessel}:\n")
            ausgabe.extend(_zeilen(wert, tiefe + 1))
        else:
            ausgabe.append(f"{einzug}{schluessel}: {_als_text(wert)}\n")
    return ausgabe


def _ohne_kommentar(zeile: str) -> str:
    """Ein ``#`` beendet die Zeile - außer es steht in Anführungszeichen."""
    hochkomma = ""
    for i, zeichen in enumerate(zeile):
        if hochkomma:
            if zeichen == hochkomma:
                hochkomma = ""
        elif zeichen in "\"'":
            hochkomma = zeichen
        elif zeichen == "#" and (i == 0 or zeile[i - 1] in " \t"):
            return zeile[:i]
    return zeile


def _entkleiden(text: str) -> str:
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        return text[1:-1]
    return text


def _wert(text: str) -> Any:
    text = text.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        # In Anführungszeichen bleibt alles Zeichenkette - auch "true" und "8080".
        inhalt = text[1:-1]
        if text[0] == '"':
            inhalt = inhalt.replace('\\"', '"').replace("\\\\", "\\")
        return inhalt
    klein = text.lower()
    if klein in _LEER:
        return None
    if klein in _TRUE:
        return True
    if klein in _FALSE:
        return False
    try:
        return int(text)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError:
        return text


def _als_text(wert: Any) -> str:
    if wert is None:
        return "''"
    if wert is True:
        return "true"
    if wert is False:
        return "false"
    if isinstance(wert, (int, float)):
        return str(wert)
    text = str(wert)
    if not _braucht_hochkommas(text):
        return text
    if "'" not in text:
        return f"'{text}'"
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _braucht_hochkommas(text: str) -> bool:
    """Alles, was beim Zurücklesen nicht mehr dieselbe Zeichenkette wäre."""
    if text == "" or text.strip() != text:
        return True
    if text.lower() in _LEER + _TRUE + _FALSE:
        return True
    if any(zeichen in text for zeichen in ":#\"'\n"):
        return True
    return not isinstance(_wert(text), str)
