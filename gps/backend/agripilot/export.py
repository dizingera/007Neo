"""Exports.

Records only count if they leave the tractor.  Three formats, each for a
different reader: GPX for anything that maps tracks, GeoJSON for a GIS or a farm
management program, CSV for a spreadsheet and for proof of work.
"""

from __future__ import annotations

import csv
import io
from datetime import datetime, timezone
from typing import Optional

from .coverage import CoverageMap
from .geo import LocalPlane
from .storage import Storage


def _iso(t: float) -> str:
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def job_gpx(store: Storage, job_id: str) -> str:
    job = store.get_job(job_id)
    if job is None:
        raise KeyError("Auftrag nicht gefunden")
    field = store.get_field(job["field_id"])
    points = store.track_points(job_id)
    name = f"{field['name'] if field else 'Feld'} - {job['operation'] or 'Arbeit'}"
    out = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<gpx version="1.1" creator="AgriPilot" xmlns="http://www.topografix.com/GPX/1/1">',
        f"  <metadata><name>{_xml(name)}</name>"
        f"<time>{_iso(job['started_at'])}</time></metadata>",
        f"  <trk><name>{_xml(name)}</name><trkseg>",
    ]
    for p in points:
        out.append(
            f'    <trkpt lat="{p["lat"]:.8f}" lon="{p["lon"]:.8f}">'
            f'<ele>{p["altitude"] or 0:.1f}</ele>'
            f'<time>{_iso(p["t"])}</time>'
            f'<fix>{"3d" if (p["fix_quality"] or 0) >= 1 else "none"}</fix>'
            "</trkpt>"
        )
    out += ["  </trkseg></trk>", "</gpx>"]
    return "\n".join(out)


def job_geojson(store: Storage, job_id: str, include_coverage: bool = True) -> dict:
    """Track, field boundary and worked area as one FeatureCollection."""
    job = store.get_job(job_id)
    if job is None:
        raise KeyError("Auftrag nicht gefunden")
    field = store.get_field(job["field_id"])
    points = store.track_points(job_id)
    features = []

    if points:
        features.append({
            "type": "Feature",
            "properties": {
                "typ": "Fahrspur",
                "auftrag": job["id"],
                "arbeit": job["operation"],
                "fahrzeug": job["vehicle"],
                "start": _iso(job["started_at"]),
                "strecke_m": round(job["distance_m"], 1),
            },
            "geometry": {
                "type": "LineString",
                "coordinates": [[p["lon"], p["lat"]] for p in points],
            },
        })

    if field and field["boundary"]:
        plane = LocalPlane(field["datum_lat"], field["datum_lon"])
        ring = [list(plane.to_wgs(e, n))[::-1] for e, n in field["boundary"]]
        if ring and ring[0] != ring[-1]:
            ring.append(ring[0])
        features.append({
            "type": "Feature",
            "properties": {"typ": "Feldgrenze", "name": field["name"],
                           "flaeche_ha": field["area_ha"]},
            "geometry": {"type": "Polygon", "coordinates": [ring]},
        })

    if include_coverage and field:
        blob = store.get_job_coverage(job_id)
        if blob:
            plane = LocalPlane(field["datum_lat"], field["datum_lon"])
            coverage = CoverageMap.unpack(blob)
            features.append({
                "type": "Feature",
                "properties": {
                    "typ": "Bearbeitete Fläche",
                    "flaeche_ha": round(coverage.area_ha, 4),
                    "ueberlappung_prozent": round(coverage.overlap_percent, 1),
                },
                "geometry": {
                    "type": "MultiPolygon",
                    "coordinates": _coverage_polygons(coverage, plane),
                },
            })

    return {"type": "FeatureCollection", "features": features}


def _coverage_polygons(coverage: CoverageMap, plane: LocalPlane,
                       limit: int = 60_000) -> list:
    """Each worked cell as its own square.

    Merging cells into few large polygons would produce a smaller file, but a
    grid of squares is exactly what was recorded, and every GIS handles it.
    """
    size = coverage.cell_size
    polygons = []
    for ix, iy in sorted(coverage.cells)[:limit]:
        x0, y0 = ix * size, iy * size
        x1, y1 = x0 + size, y0 + size
        corners = [(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)]
        ring = []
        for e, n in corners:
            lat, lon = plane.to_wgs(e, n)
            ring.append([lon, lat])
        polygons.append([ring])
    return polygons


def job_csv(store: Storage, job_id: str) -> str:
    points = store.track_points(job_id)
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";")
    writer.writerow([
        "zeit_utc", "breite", "laenge", "hoehe_m", "geschwindigkeit_kmh",
        "kurs_grad", "fix", "abweichung_cm",
    ])
    for p in points:
        writer.writerow([
            _iso(p["t"]), f'{p["lat"]:.8f}', f'{p["lon"]:.8f}',
            f'{p["altitude"] or 0:.1f}', f'{(p["speed_ms"] or 0) * 3.6:.2f}',
            f'{p["heading"] or 0:.1f}', p["fix_quality"],
            f'{(p["cross_track_m"] or 0) * 100:.1f}',
        ])
    return buffer.getvalue()


def jobs_summary_csv(store: Storage, field_id: Optional[str] = None) -> str:
    """One row per job - the sheet you hand to the office."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";")
    writer.writerow([
        "datum", "feld", "fahrzeug", "arbeit", "dauer_h", "strecke_km",
        "flaeche_ha", "ueberlappung_ha", "geraet",
    ])
    fields = {f["id"]: f["name"] for f in store.list_fields()}
    for job in store.list_jobs(field_id=field_id, limit=2000):
        duration_h = ((job["ended_at"] or job["started_at"]) - job["started_at"]) / 3600
        writer.writerow([
            datetime.fromtimestamp(job["started_at"]).strftime("%d.%m.%Y %H:%M"),
            fields.get(job["field_id"], job["field_id"]),
            job["vehicle"], job["operation"], f"{duration_h:.2f}",
            f'{job["distance_m"] / 1000:.2f}', f'{job["area_ha"]:.3f}',
            f'{job["overlap_ha"]:.3f}', job["device_id"],
        ])
    return buffer.getvalue()


def _xml(text: str) -> str:
    return (text.replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))
