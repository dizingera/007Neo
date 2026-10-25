"""Daten vom Master an die Arbeiter schicken - als Datei, wenn kein Netz da ist.

Der Master (der Rechner im Büro) erfasst Felder, Spuren, Umrisse aus dem
Flächenantrag und Maschinen. Die Arbeiter (die Tablets in den Traktoren) bekommen
das auf zwei Wegen:

* **über das Netz**, sobald beide im selben WLAN sind oder per VPN verbunden:
  der laufende Abgleich (sync.py) trägt dann auch die Maschinen,
* **als Datenpaket**, wenn das Tablet draußen kein Netz zum Büro hat: eine
  Zip-Datei mit einer JSON-Datei, per USB-Stick, Messenger oder Mail zum Tablet
  und dort unter Menü → Daten & Geräte eingespielt.

Beide Wege nehmen dieselbe Zusammenführung: je Datensatz gilt der neuere, die
bearbeitete Fläche zweier Geräte wird vereinigt statt überschrieben (sync.py).
Ein Paket kann deshalb gefahrlos mehrmals oder in beliebiger Reihenfolge
eingespielt werden, und es überschreibt nichts, was auf dem Tablet neuer ist.
"""

from __future__ import annotations

import io
import json
import time
import zipfile
from typing import Any

from . import sync
from .storage import SYNCED_TABLES, Storage

FORMAT = "agripilot-daten"
VERSION = 1
DATEI = "daten.json"
MAX_BYTES = 100 * 1024 * 1024          # ein Paket in dieser Größe ist kein Datenpaket mehr
# Geräte gehören dem Gerät, das sie meldet; Arbeiten (mit bearbeiteter Fläche)
# nur auf Wunsch - sie machen das Paket groß.
TABELLEN = tuple(t for t in SYNCED_TABLES if t != "devices")


class PaketFehler(ValueError):
    """Das Paket ist nicht das, was es sein soll - der Text sagt dem Fahrer, warum."""


def paket_bauen(store: Storage, engine, von: str = "", mit_arbeiten: bool = False,
                jetzt: float | None = None) -> bytes:
    """Alles, was die Arbeiter brauchen, in einer Zip-Datei."""
    tabellen = {t: store.changes_since(t, 0.0) for t in TABELLEN
                if mit_arbeiten or t != "jobs"}
    inhalt: dict[str, Any] = {
        "format": FORMAT,
        "version": VERSION,
        "erstellt": jetzt if jetzt is not None else time.time(),
        "von": von,
        "mit_arbeiten": mit_arbeiten,
        "tabellen": tabellen,
        "maschinen": engine.profile_export(),
    }
    puffer = io.BytesIO()
    with zipfile.ZipFile(puffer, "w", zipfile.ZIP_DEFLATED) as archiv:
        archiv.writestr(DATEI, json.dumps(inhalt, ensure_ascii=False, separators=(",", ":")))
    return puffer.getvalue()


def paket_lesen(daten: bytes) -> dict:
    """Ein Paket öffnen und prüfen. Wirft ``PaketFehler`` mit verständlichem Text."""
    if not daten:
        raise PaketFehler("Leere Datei")
    if len(daten) > MAX_BYTES:
        raise PaketFehler("Die Datei ist zu groß für ein Datenpaket")
    try:
        with zipfile.ZipFile(io.BytesIO(daten)) as archiv:
            if DATEI not in archiv.namelist():
                raise PaketFehler("Das ist kein AgriPilot-Datenpaket (daten.json fehlt)")
            info = archiv.getinfo(DATEI)
            if info.file_size > 5 * MAX_BYTES:
                raise PaketFehler("Das Paket ist beschädigt (zu groß entpackt)")
            inhalt = json.loads(archiv.read(DATEI))
    except zipfile.BadZipFile as exc:
        raise PaketFehler("Das ist keine Zip-Datei oder sie ist beschädigt") from exc
    except ValueError as exc:
        if isinstance(exc, PaketFehler):
            raise
        raise PaketFehler("Der Inhalt des Pakets ist nicht lesbar") from exc
    if not isinstance(inhalt, dict) or inhalt.get("format") != FORMAT:
        raise PaketFehler("Das ist kein AgriPilot-Datenpaket")
    if int(inhalt.get("version") or 0) > VERSION:
        raise PaketFehler("Das Paket stammt von einer neueren Version - AgriPilot hier zuerst aktualisieren")
    return inhalt


def paket_einspielen(store: Storage, engine, daten: bytes) -> dict:
    """Ein Paket zusammenführen. Gibt zurück, was dazukam, für die Anzeige."""
    inhalt = paket_lesen(daten)
    tabellen = {t: rows for t, rows in (inhalt.get("tabellen") or {}).items() if t in TABELLEN}
    angewandt = sync.apply_changes(store, tabellen)
    maschinen = engine.profile_merge(inhalt.get("maschinen") or {})
    return {
        "von": inhalt.get("von", ""),
        "erstellt": inhalt.get("erstellt"),
        "felder": angewandt.get("fields", 0),
        "spuren": angewandt.get("lines", 0),
        "arbeiten": angewandt.get("jobs", 0),
        "maschinen": maschinen,
    }
