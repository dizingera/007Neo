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

from .guidance import VehicleProfile
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


def _maschinen(store: Storage) -> dict[str, dict]:
    """Die Maschinen nach Namen - für Traktor und Gerät, wie MyTerra sie nennt."""
    return {p.get("name", ""): p for p in (store.get_setting("vehicle_profiles") or [])}


def bericht_aus_arbeit(store: Storage, job: dict, felder: dict[str, dict],
                       geraete: dict[str, str],
                       maschinen: Optional[dict[str, dict]] = None) -> dict:
    """Einen Bericht aus einer fertigen Arbeit.

    ``maschine`` ist der Traktor so, wie MyTerra ihn nennt, ``anbaugeraet`` das Gerät.
    Ist bei der AgriPilot-Maschine kein MyTerra-Traktor eingetragen, geht ihr eigener
    Name - dann ordnet MyTerra nur bei gleichem Namen zu.
    """
    feld = felder.get(job["field_id"]) or {}
    maschine = (maschinen if maschinen is not None else _maschinen(store)).get(
        job.get("vehicle", "")) or {}
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
        # Der Traktor, mit dem gefahren wurde (seit 1.5.7 an der Arbeit); bei älteren
        # Arbeiten der, der bei der Maschine eingetragen war.
        "maschine": ((job.get("tractor") or "").strip() or (maschine.get("traktor") or "").strip()
                     or job.get("vehicle", "")),
        "anbaugeraet": (maschine.get("anbaugeraet") or "").strip(),
        "agripilot_maschine": job.get("vehicle", ""),
        "geraet": geraete.get(job.get("device_id", ""), job.get("device_id", "")),
        "spur": _spur(store, job["id"]),
    }


# ------------------------------------------------ Maschinen aus MyTerra

KATALOG = "myterra_maschinen"      # {"traktoren": [...], "geraete": [...]} für die Auswahl


def _ist_traktor(eintrag: dict) -> bool:
    return _glatt(eintrag.get("type") or "") in ("traktor", "schlepper")


def maschinen_uebernehmen(engine, liste: list[dict], traktor: str = "") -> dict:
    """Die Maschinenliste von MyTerra übernehmen.

    Wie in MyTerra sind Traktoren und Geräte getrennt: jeder MyTerra-Traktor wird ein
    AgriPilot-Traktor (Antenne und Lenkung vom gewählten als Vorlage), jedes Gerät und
    jeder Mähdrescher eine Maschine. Was es schon gibt, bleibt unverändert - was dort
    eingestellt ist, hat jemand gemessen. Gewählter Traktor und gewählte Maschine bleiben
    gewählt. ``traktor`` ist seit 1.5.7 ohne Wirkung (nur noch geprüft).
    """
    aktive = [e for e in liste if isinstance(e, dict) and (e.get("name") or "").strip()
              and e.get("active", True) is not False]
    traktoren = sorted({e["name"].strip() for e in aktive if _ist_traktor(e)})
    geraete = [e for e in aktive if not _ist_traktor(e)]
    if traktor and traktor not in traktoren:
        raise ValueError(f"Traktor „{traktor}“ gibt es in MyTerra nicht")
    engine.store.set_setting(KATALOG, {
        "traktoren": traktoren,
        "geraete": sorted({e["name"].strip() for e in geraete}),
    })
    vorhanden = engine.list_profiles()
    belegt = {_glatt(p.get("anbaugeraet") or p.get("name") or "") for p in vorhanden}
    zuvor = next((p for p in vorhanden if p.get("aktiv")), None)
    # Jede neue Maschine geht von der gewählten aus (Antenne, Radstand des Traktors) -
    # nicht von der zuletzt angelegten, sonst erbt ein Gerät ohne Breite die des Vorgängers.
    felder = VehicleProfile.__dataclass_fields__
    basis = {k: v for k, v in (zuvor or {}).items() if k in felder}
    ergebnis: dict[str, list[str]] = {"neu": [], "vorhanden": [], "ohne_breite": []}
    for eintrag in geraete:
        name = eintrag["name"].strip()
        if _glatt(name) in belegt:
            ergebnis["vorhanden"].append(name)
            continue
        werte ={**basis, "anbaugeraet": name, "traktor": ""}
        try:
            breite = float(eintrag.get("widthM") or 0.0)
        except (TypeError, ValueError):
            breite = 0.0
        if breite > 0:
            werte["width_m"] = breite
        else:
            ergebnis["ohne_breite"].append(name)
        engine.save_profile_as(name, werte)
        belegt.add(_glatt(name))
        ergebnis["neu"].append(name)
    if zuvor and ergebnis["neu"]:
        engine.select_profile(zuvor["id"])
    # Die Traktoren: was es unter diesem Namen noch nicht gibt, kommt dazu.
    traktor_zuvor = engine.aktiver_traktor()["id"]
    bekannt = {_glatt(t["name"]) for t in engine.list_traktoren()}
    traktoren_neu = []
    for name in traktoren:
        if _glatt(name) not in bekannt:
            engine.save_traktor_as(name)
            bekannt.add(_glatt(name))
            traktoren_neu.append(name)
    if traktoren_neu:
        engine.select_traktor(traktor_zuvor)
    engine.note(f"Aus MyTerra: {len(ergebnis['neu'])} Maschinen und {len(traktoren_neu)} "
                f"Traktoren neu, {len(ergebnis['vorhanden'])} Maschinen schon da")
    return {**ergebnis, "traktoren": traktoren, "traktoren_neu": traktoren_neu}


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
    maschinen = _maschinen(store)
    berichte, genommen = [], []
    for job in store.list_jobs(limit=500):
        if not job.get("ended_at") or float(job.get("area_ha") or 0.0) <= 0.0:
            continue
        if ids is not None and job["id"] not in ids:
            continue
        if ids is None and nur_neue and job["id"] in uebergeben:
            continue
        berichte.append(bericht_aus_arbeit(store, job, felder, geraete, maschinen))
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
