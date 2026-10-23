"""Fernansicht: wo jede Maschine gerade steht und was sie tut.

Jeder Arbeiter meldet alle paar Sekunden seine Lage an den Master
(``/api/sync/live``). Der Master hält die Meldungen nur im Speicher - eine
Position von gestern ist keine Information, sondern eine Irreführung - und gibt
jedem Arbeiter die Meldungen der anderen zurück. So sieht das Büro alle
Traktoren, und jeder Traktor die anderen auf seiner Karte.

Was gemeldet wird, steht in ``Engine.live_meldung``; hier nur die Ablage.
"""

from __future__ import annotations

import time
from typing import Optional

VERGESSEN_S = 15 * 60.0     # wer so lange nichts meldet, verschwindet aus der Ansicht
FELDER = ("name", "lat", "lon", "kurs", "tempo_ms", "feld", "feld_id", "arbeit", "flaeche_ha",
          "lenkt", "markiert", "fix", "version", "simulator", "rolle")


class Flotte:
    def __init__(self) -> None:
        self._meldungen: dict[str, dict] = {}

    def melden(self, geraet_id: str, meldung: dict, jetzt: Optional[float] = None) -> None:
        if not geraet_id or not isinstance(meldung, dict):
            return
        try:
            lat, lon = float(meldung["lat"]), float(meldung["lon"])
        except (KeyError, TypeError, ValueError):
            return
        if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
            return
        sauber = {k: meldung[k] for k in FELDER if k in meldung}
        sauber.update(id=geraet_id, lat=lat, lon=lon, zeit=jetzt or time.time())
        self._meldungen[geraet_id] = sauber

    def uebernehmen(self, liste: list, jetzt: Optional[float] = None) -> None:
        """Die Liste vom Master: Alter mitgerechnet, damit ihre Uhr hier nicht zählt."""
        jetzt = jetzt or time.time()
        neu = {}
        for m in liste or []:
            if isinstance(m, dict) and m.get("id"):
                eintrag = dict(m)
                eintrag["zeit"] = jetzt - float(m.get("alter_s") or 0.0)
                neu[m["id"]] = eintrag
        self._meldungen = neu

    def liste(self, ohne: Optional[str] = None, jetzt: Optional[float] = None) -> list[dict]:
        jetzt = jetzt or time.time()
        for k in [k for k, m in self._meldungen.items() if jetzt - m["zeit"] > VERGESSEN_S]:
            del self._meldungen[k]
        aus = []
        for k, m in self._meldungen.items():
            if k == ohne:
                continue
            eintrag = {key: v for key, v in m.items() if key != "zeit"}
            eintrag["alter_s"] = round(jetzt - m["zeit"], 1)
            aus.append(eintrag)
        return sorted(aus, key=lambda m: m.get("name") or m["id"])
