"""Arbeitsberichte für MyTerra.

Eine fertige Arbeit wird zu einem Bericht, den MyTerra in seinen Prüfeingang legen
kann: Schlag, Tätigkeit, Datum, Dauer, bearbeitete Fläche, Maschine, Fahrspur. Der
Bericht ist ein **Vorschlag** - gebucht wird erst, wenn der Betrieb ihn in MyTerra
freigibt. So gibt es keine Doppelbuchungen, und ein falsch erkannter Schlag fällt
auf, bevor Kosten und Lagerverbrauch daran hängen.

Das Format ist absichtlich flach und selbsterklärend (JSON, deutsche Schlüssel,
SI-nahe Einheiten), damit es sich in MyTerra ohne AgriPilot-Kenntnis lesen lässt:

    {"format": "agripilot-arbeitsbericht", "version": 1, "erstellt": "...",
     "von": "Tablet Fendt", "berichte": [ {...}, ... ]}

Jeder Bericht trägt eine stabile Kennung (``agripilot-<Arbeit>``). Wird dieselbe
Arbeit ein zweites Mal exportiert, erkennt MyTerra sie daran wieder.
"""

from __future__ import annotations

import json
import time
import unicodedata
from typing import Any, Optional

from .storage import Storage

FORMAT = "agripilot-arbeitsbericht"
VERSION = 1
MAX_SPURPUNKTE = 400
SETTING = "myterra_uebergeben"       # {Arbeits-Kennung: Zeitpunkt der Übergabe}

# Die Tätigkeiten, die MyTerra kennt (Schlagkartei PRO).
TAETIGKEITEN = {
    "boden": "Bodenbearbeitung",
    "saat": "Saat",
    "walzen": "Walzen",
    "duengung": "Düngung",
    "psm": "Pflanzenschutz",
    "ernte": "Ernte",
    "sonstiges": "Sonstiges",
}

# Reihenfolge zählt: "Gülle ausbringen" ist Düngung, nicht Bodenbearbeitung.
_STICHWORTE = (
    ("psm", ("spritz", "pflanzenschutz", "psm", "herbizid", "fungizid", "insektizid", "wachstumsregler")),
    ("duengung", ("dung", "streu", "gulle", "mist", "kalk", "nitro", "harnstoff", "kas ")),
    ("saat", ("saat", "saen", "drill", "einsaat", "mais legen", "legen")),
    ("walzen", ("walz",)),
    ("ernte", ("ernte", "mah", "dresch", "hack", "pressen", "schwad", "silier", "ballen")),
    ("boden", ("grubber", "pflug", "pfluge", "egge", "egg", "frase", "stoppel", "kreisel",
               "bodenbearbeit", "kultivator", "scheibe", "striegel", "schleppe", "mulch")),
)


def _glatt(text: str) -> str:
    """Kleinbuchstaben, ohne Umlaute und Akzente: 'Pflügen' -> 'pflugen' -> vergleichbar."""
    zerlegt = unicodedata.normalize("NFD", (text or "").lower().replace("ß", "ss"))
    return "".join(c for c in zerlegt if not unicodedata.combining(c))


def taetigkeit_aus(text: str) -> str:
    """Welche MyTerra-Tätigkeit meint der freie Text der Arbeit? ``sonstiges``, wenn unklar."""
    glatt = _glatt(text) + " "
    for kennung, stichworte in _STICHWORTE:
        if any(s in glatt for s in stichworte):
            return kennung
    return "sonstiges"


def _iso(zeit: Optional[float]) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(zeit)) if zeit else ""


def _spur(store: Storage, job_id: str) -> Optional[dict]:
    punkte = [(p["lon"], p["lat"]) for p in store.track_points(job_id)
              if p.get("lat") is not None and p.get("lon") is not None]
    if len(punkte) < 2:
        return None
    if len(punkte) > MAX_SPURPUNKTE:
        schritt = len(punkte) / MAX_SPURPUNKTE
        punkte = [punkte[int(i * schritt)] for i in range(MAX_SPURPUNKTE - 1)] + [punkte[-1]]
    return {"type": "LineString", "coordinates": [[round(x, 7), round(y, 7)] for x, y in punkte]}


def bericht_aus_arbeit(store: Storage, job: dict, felder: dict[str, dict],
                       geraete: dict[str, str]) -> dict:
    """Einen Bericht aus einer fertigen Arbeit."""
    feld = felder.get(job["field_id"]) or {}
    beginn, ende = job["started_at"], job.get("ended_at") or job["started_at"]
    flaeche, doppelt = float(job.get("area_ha") or 0.0), float(job.get("overlap_ha") or 0.0)
    return {
        "id": f"agripilot-{job['id']}",
        "schlag": {
            "agripilot_id": job["field_id"],
            "name": feld.get("name", ""),
            "flaeche_ha": round(float(feld.get("area_ha") or 0.0), 2),
            "hinweis": feld.get("note", ""),
        },
        "datum": time.strftime("%Y-%m-%d", time.localtime(beginn)),
        "beginn": _iso(beginn),
        "ende": _iso(ende),
        "dauer_h": round(max(0.0, ende - beginn) / 3600.0, 2),
        "arbeitszeit_h": round(float(job.get("working_time_s") or 0.0) / 3600.0, 2),
        "flaeche_ha": round(flaeche, 2),
        "doppelt_ha": round(doppelt, 2),
        "ueberlappung_prozent": round(100.0 * doppelt / (flaeche + doppelt), 1) if flaeche + doppelt > 0 else 0.0,
        "strecke_km": round(float(job.get("distance_m") or 0.0) / 1000.0, 2),
        "taetigkeit": taetigkeit_aus(job.get("operation", "")),
        "taetigkeit_text": job.get("operation", ""),
        "maschine": job.get("vehicle", ""),
        "geraet": geraete.get(job.get("device_id", ""), job.get("device_id", "")),
        "spur": _spur(store, job["id"]),
    }


def arbeiten(store: Storage) -> list[dict]:
    """Alle fertigen Arbeiten mit Fläche, neueste zuerst, mit Übergabestand und erkannter Tätigkeit."""
    uebergeben = store.get_setting(SETTING) or {}
    liste = []
    for job in store.list_jobs(limit=500):
        if not job.get("ended_at") or float(job.get("area_ha") or 0.0) <= 0.0:
            continue
        feld = store.get_field(job["field_id"]) or {}
        liste.append({
            "id": job["id"], "schlag": feld.get("name", ""), "datum": time.strftime(
                "%Y-%m-%d", time.localtime(job["started_at"])),
            "taetigkeit": taetigkeit_aus(job.get("operation", "")),
            "taetigkeit_text": job.get("operation", ""),
            "flaeche_ha": round(float(job["area_ha"]), 2),
            "uebergeben": uebergeben.get(job["id"]),
        })
    return liste


def paket_bauen(store: Storage, von: str, ids: Optional[list[str]] = None,
                nur_neue: bool = True, markieren: bool = True,
                jetzt: Optional[float] = None) -> tuple[bytes, list[str]]:
    """Die Berichte als JSON-Datei. ``ids`` wählt bestimmte Arbeiten, sonst alle (neuen)."""
    uebergeben = dict(store.get_setting(SETTING) or {})
    felder = {f["id"]: f for f in store.list_fields()}
    geraete = {d["id"]: d.get("name") or d["id"] for d in store.list_devices()}
    berichte, genommen = [], []
    for job in store.list_jobs(limit=500):
        if not job.get("ended_at") or float(job.get("area_ha") or 0.0) <= 0.0:
            continue
        if ids is not None and job["id"] not in ids:
            continue
        if ids is None and nur_neue and job["id"] in uebergeben:
            continue
        berichte.append(bericht_aus_arbeit(store, job, felder, geraete))
        genommen.append(job["id"])
    berichte.sort(key=lambda b: b["beginn"])
    zeit = jetzt if jetzt is not None else time.time()
    inhalt: dict[str, Any] = {"format": FORMAT, "version": VERSION, "erstellt": _iso(zeit),
                              "von": von, "berichte": berichte}
    if markieren:
        for kennung in genommen:
            uebergeben[kennung] = zeit
        store.set_setting(SETTING, uebergeben)
    return json.dumps(inhalt, ensure_ascii=False, indent=1).encode("utf-8"), genommen
