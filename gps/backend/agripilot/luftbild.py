"""Luftbild unter der Karte: ein Bild je Feld, einmal geholt, dann ohne Netz.

Quelle ist ein WMS-Dienst mit Orthophotos. Vorgabe ist das freie DOP20 des
Landesamts für Geoinformation Baden-Württemberg (20 cm, Open Data,
Datenlizenz Deutschland – Namensnennung 2.0); in den Einstellungen lässt sich
ein anderer Dienst eintragen (in Bayern z.B. das DOP40 der Vermessungsverwaltung).

Geholt wird ein Ausschnitt um das ganze Feld plus ``RAND_M`` in WGS84
(EPSG:4326). In der Feldebene (geo.LocalPlane) ist so ein Ausschnitt auf
Zentimeter ein achsparalleles Rechteck - die Oberfläche legt das Bild einfach
in dieses Rechteck. Gespeichert wird je Feld eine JPEG-Datei und eine kleine
Beschreibung; draußen auf dem Feld braucht es dann kein Netz mehr.

Ein Arbeiter fragt zuerst den Master (der hat das Bild meist schon und hängt im
Hof-Netz), erst dann den Dienst selbst.
"""

from __future__ import annotations

import hashlib
import json
import math
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable, Optional

from . import geo

STANDARD_WMS = "https://owsproxy.lgl-bw.de/owsproxy/ows/WMS_LGL-BW_ATKIS_DOP_20_C"
STANDARD_EBENE = "IMAGES_DOP_20_RGB"
STANDARD_QUELLE = "Luftbild © LGL, www.lgl-bw.de (dl-de/by-2-0)"
RAND_M = 150.0
M_JE_PIXEL = 0.3
MAX_PIXEL = 4096
KOPF = {"User-Agent": "AgriPilot-Luftbild"}


class LuftbildFehler(RuntimeError):
    pass


def ausschnitt(feld: dict, rand_m: float = RAND_M) -> Optional[tuple[float, float, float, float]]:
    """(lat_s, lon_w, lat_n, lon_o) um die Feldgrenze, oder None ohne Grenze."""
    grenze = feld.get("boundary") or []
    if len(grenze) < 3:
        return None
    ebene = geo.LocalPlane(feld["datum_lat"], feld["datum_lon"])
    xs = [p[0] for p in grenze]
    ys = [p[1] for p in grenze]
    sw = ebene.to_wgs(min(xs) - rand_m, min(ys) - rand_m)
    no = ebene.to_wgs(max(xs) + rand_m, max(ys) + rand_m)
    return (sw[0], sw[1], no[0], no[1])


def bildgroesse(feld: dict, box) -> tuple[int, int]:
    ebene = geo.LocalPlane(feld["datum_lat"], feld["datum_lon"])
    x0, y0 = ebene.to_local(box[0], box[1])
    x1, y1 = ebene.to_local(box[2], box[3])
    breite, hoehe = (x1 - x0) / M_JE_PIXEL, (y1 - y0) / M_JE_PIXEL
    faktor = min(1.0, MAX_PIXEL / max(breite, hoehe))
    return max(64, int(breite * faktor)), max(64, int(hoehe * faktor))


def getmap_url(wms: str, ebene: str, box, breite: int, hoehe: int) -> str:
    werte = {
        "SERVICE": "WMS", "VERSION": "1.1.1", "REQUEST": "GetMap", "LAYERS": ebene, "STYLES": "",
        "SRS": "EPSG:4326", "BBOX": f"{box[1]:.7f},{box[0]:.7f},{box[3]:.7f},{box[2]:.7f}",
        "WIDTH": str(breite), "HEIGHT": str(hoehe), "FORMAT": "image/jpeg",
    }
    trenner = "&" if "?" in wms else "?"
    return wms.rstrip("?&") + trenner + urllib.parse.urlencode(werte)


def _kennung(feld: dict, wms: str, ebene: str, box) -> str:
    roh = json.dumps([wms, ebene, [round(v, 6) for v in box]])
    return f"{feld['id']}-{hashlib.sha1(roh.encode()).hexdigest()[:10]}"


def _lesen(url: str, kopf: dict, timeout: float) -> bytes:
    anfrage = urllib.request.Request(url, headers={**KOPF, **kopf})
    with urllib.request.urlopen(anfrage, timeout=timeout) as antwort:
        return antwort.read()


def ist_jpeg(daten: bytes) -> bool:
    return daten[:3] == b"\xff\xd8\xff"


def beschreibung(feld: dict, box, quelle: str) -> dict:
    """Wo das Bild in der Feldebene liegt: [x0, y0, x1, y1] (Süd-West, Nord-Ost)."""
    ebene = geo.LocalPlane(feld["datum_lat"], feld["datum_lon"])
    x0, y0 = ebene.to_local(box[0], box[1])
    x1, y1 = ebene.to_local(box[2], box[3])
    return {"feld_id": feld["id"], "rechteck": [round(x0, 2), round(y0, 2), round(x1, 2), round(y1, 2)],
            "quelle": quelle}


def holen(feld: dict, ordner: Path, wms: str = STANDARD_WMS, ebene: str = STANDARD_EBENE,
          quelle: str = STANDARD_QUELLE, master: Optional[tuple[str, dict]] = None,
          lesen: Callable[[str, dict, float], bytes] = _lesen, timeout: float = 30.0) -> tuple[Path, dict]:
    """Das Bild des Feldes - aus dem Zwischenspeicher, vom Master oder vom Dienst.

    ``master``: (Basisadresse, Kopfzeilen) eines Masters, der zuerst gefragt wird.
    """
    box = ausschnitt(feld)
    if box is None:
        raise LuftbildFehler("Das Feld hat keine Grenze - ohne Grenze kein Ausschnitt")
    ordner = Path(ordner)
    ordner.mkdir(parents=True, exist_ok=True)
    kennung = _kennung(feld, wms, ebene, box)
    bild, meta = ordner / f"{kennung}.jpg", ordner / f"{kennung}.json"
    if bild.exists() and meta.exists():
        return bild, json.loads(meta.read_text(encoding="utf-8"))
    daten = b""
    fehler = []
    if master is not None:
        basis, kopf = master
        try:
            daten = lesen(f"{basis.rstrip('/')}/api/sync/luftbild/{feld['id']}", kopf, timeout)
        except Exception as exc:  # noqa: BLE001 - dann eben direkt beim Dienst
            fehler.append(f"Master: {exc}")
            daten = b""
    if not ist_jpeg(daten):
        breite, hoehe = bildgroesse(feld, box)
        try:
            daten = lesen(getmap_url(wms, ebene, box, breite, hoehe), {}, timeout)
        except Exception as exc:  # noqa: BLE001
            fehler.append(f"Dienst: {exc}")
            daten = b""
    if not ist_jpeg(daten):
        raise LuftbildFehler("Luftbild nicht erreichbar" + (f" ({'; '.join(fehler)})" if fehler else
                             " - der Dienst lieferte kein Bild"))
    for alt in ordner.glob(f"{feld['id']}-*"):
        alt.unlink(missing_ok=True)          # nach Grenzänderung: altes Bild weg
    bild.write_bytes(daten)
    info = beschreibung(feld, box, quelle)
    meta.write_text(json.dumps(info), encoding="utf-8")
    return bild, info


def vorhanden(feld: dict, ordner: Path, wms: str = STANDARD_WMS,
              ebene: str = STANDARD_EBENE) -> Optional[tuple[Path, dict]]:
    box = ausschnitt(feld)
    if box is None:
        return None
    kennung = _kennung(feld, wms, ebene, box)
    bild, meta = Path(ordner) / f"{kennung}.jpg", Path(ordner) / f"{kennung}.json"
    if bild.exists() and meta.exists():
        return bild, json.loads(meta.read_text(encoding="utf-8"))
    return None
