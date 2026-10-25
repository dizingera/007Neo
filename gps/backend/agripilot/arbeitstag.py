"""Tagesstart, Gespannprüfung und Saisonarchiv; keine Motorfreigabe."""
from __future__ import annotations
import csv
import io
import math
import time
from dataclasses import asdict
from datetime import datetime
from . import geo, gespann
from .coverage import CoverageMap


def im_stand(engine):
    if engine.steering and engine.steering.armed:
        raise RuntimeError("Erst die Lenkung ausschalten")
    fix = engine.fix
    if fix is None or not fix.valid or not fix.received_at or time.time()-fix.received_at > 2:
        raise RuntimeError("Frische Position zum Prüfen des Stillstands erforderlich")
    if abs(fix.speed_ms) > .15:
        raise RuntimeError("Die Maschine muss stehen")


def gespann_status(engine):
    p = asdict(engine.profile)
    return {"traktor": engine.aktiver_traktor()["name"], "geraet": p["name"],
            "bauform": p["bauform"], "geraet_art": p["geraet_art"],
            "anbau": "gezogen" if p["trailed"] else "vorn" if p["tool_trailing_m"] < 0 else "hinten / an der Achse",
            "breite_m": p["width_m"], "versatz_m": p["tool_offset_m"],
            "antenne": {k: p[k] for k in ("antenna_height_m", "antenna_forward_m", "antenna_right_m")},
            "masse": {k: p[k] for k in gespann.MASSE},
            "vollstaendig": all(p[k] > 0 for k in gespann.MASSE),
            "kalibrierung": "Einbau und Radwinkel vor Gespannwechsel prüfen; Maße bleiben beim gewählten Traktor."}


def einfahrt_lokal(einfahrt, plane):
    if not isinstance(einfahrt,dict) or einfahrt.get("geloescht") or not plane: return None
    if all(isinstance(einfahrt.get(k),(int,float)) and math.isfinite(einfahrt[k]) for k in ("lat","lon")):
        return list(plane.to_local(einfahrt["lat"],einfahrt["lon"]))
    for keys in (("x","y"),("localx","localy")):
        if all(isinstance(einfahrt.get(k),(int,float)) and math.isfinite(einfahrt[k]) for k in keys):
            return [einfahrt[k] for k in keys]
    return None


def status(app):
    e, st = app.engine, app.engine.state()
    system = app.system_status()
    fix = st.get("fix") or {}
    gesund = system.get("gnss", {}).get("healthy", False)
    frisch = bool(e.fix and e.fix.received_at and time.time()-e.fix.received_at < 2)
    uebung = e.config.gnss.source in ("simulator", "replay")
    checks = []
    def add(titel, gut, text, ziel, warn=False):
        checks.append(dict(titel=titel, status="bereit" if gut else "hinweis" if warn else "offen", text=text, ziel=ziel))
    add("GPS", gesund and frisch and bool(fix.get("fix_quality")),
        "Position aktuell" if gesund and frisch else "Empfänger und Verbindung prüfen", "settings")
    add("RTK", frisch and fix.get("fix_quality") == 4, "RTK Fix" if frisch and fix.get("fix_quality") == 4 else "Korrekturdaten prüfen – Internet und GPS sind getrennte Verbindungen", "settings")
    imu = st.get("imu") or {}
    add("IMU", bool(imu.get("healthy")), "Sensor meldet aktuelle Lage" if imu.get("healthy") else "Sensor und Nullung prüfen", "lenkwerkstatt")
    out = (st.get("steering") or {}).get("output") or {}
    add("Lenkmotor", uebung or (bool(out.get("bereit")) and out.get("typ") not in ("none","aus") and e.config.steering.enabled), "Übung: kein echter Motor" if uebung else str(out.get("status") or "Motorverbindung in der Werkstatt prüfen"), "lenkung")
    add("Feld und Spur", bool(e.field and e.line), "Geladen" if e.field and e.line else "Feld und passende Spur laden", "fields")
    g = gespann_status(e)
    add("Gespann", g["vollstaendig"], f"{g['traktor']} · {g['geraet']} · {g['breite_m']:g} m · {g['anbau']}", "machine", True)
    field_id = e.field["id"] if e.field else ""
    einfahrt = e.store.get_setting("feld_einfahrt:"+field_id) if field_id else None
    start = e.startpunkt_wgs() if e.field and e.line else None
    abgleich = system.get("sync") or {}
    return dict(checks=checks, gespann=g, uebung=uebung,
                offline=dict(feld=bool(e.field), spur=bool(e.line), gespann=True,
                             text="Feld und Spur liegen lokal vor. Luftbilder können fehlen; RTK braucht seine eigene Korrekturverbindung." if e.field and e.line else "Vor Abfahrt Feld und Spur auf diesem Gerät laden."),
                arbeit=st.get("job"), sicherung=app.backup_status(), abgleich=abgleich,
                einstieg=dict(einfahrt=einfahrt, start=list(start[:2]) if start else None,
                              spur=e.line.name if e.line else "", richtung=start[2] if start else None,
                              grenze=e.field.get("boundary",[]) if e.field else [],
                              start_xy=list(e.plane.to_local(*start[:2])) if start and e.plane else None,
                              einfahrt_xy=einfahrt_lokal(einfahrt,e.plane),
                              text="Markierung und Spuranfang sind Orientierung, keine Straßenführung."),
                probefahrt=st.get("probefahrt"), rohdatenerfassung=app.aufzeichnung.status(),
                hinweis="Statusübersicht ersetzt keine Einbauprüfung oder Motorfreigabe.")


def einfahrt_setzen(engine, aktion):
    if not engine.field:
        raise RuntimeError("Erst ein Feld laden")
    im_stand(engine)
    key = "feld_einfahrt:"+engine.field["id"]
    if aktion == "loeschen":
        wert = {"geloescht": True, "zeit": time.time()}
    elif aktion == "setzen":
        wert = dict(lat=engine.fix.lat, lon=engine.fix.lon, zeit=time.time())
    else:
        raise ValueError("Unbekannte Aktion")
    engine.store.set_setting(key, wert)
    return wert


def archiv(store, jahr=None, device_id=None):
    # Keine 200-Zeilen-Standardgrenze: Saisonbericht enthält alle vorhandenen Arbeiten.
    jobs = store.list_jobs(limit=100000)
    felder = store.list_fields()
    namen = {f["id"]: f["name"] for f in felder}
    saisons = sorted({datetime.fromtimestamp(j["started_at"]).year for j in jobs}, reverse=True)
    if jahr is not None:
        jobs = [j for j in jobs if datetime.fromtimestamp(j["started_at"]).year == int(jahr)]
    for j in jobs:
        j["feld"] = namen.get(j["field_id"], "Gelöschtes Feld")
        j["fortsetzbar"] = j.get("ended_at") is None and (device_id is None or j["device_id"] == device_id)
    return dict(felder=[{k:f[k] for k in ("id","name","area_ha")} for f in felder], jobs=jobs,
                saisons=saisons, jahr=jahr, flaeche_ha=sum(j.get("area_ha") or 0 for j in jobs),
                hinweis="Flächensumme aller Arbeiten; mehrere Arbeitsgänge zählen mehrfach. Keine ungemessenen Fehlstellen ausgewiesen.")


def bericht_csv(store, jahr=None):
    buf = io.StringIO(newline="")
    w = csv.writer(buf, delimiter=";")
    w.writerow(["Feld","Arbeit","Beginn","Ende","Traktor","Gerät","Fläche ha","Überlappung ha","Strecke m","Arbeitszeit s","Spur-ID"])
    def text(v):
        s = str(v or "")
        return "'"+s if s.lstrip().startswith(("=","+","-","@")) else s
    for j in archiv(store,jahr)["jobs"]:
        w.writerow([text(j["feld"]),text(j.get("operation")),datetime.fromtimestamp(j["started_at"]).isoformat(timespec="seconds"),
                    datetime.fromtimestamp(j["ended_at"]).isoformat(timespec="seconds") if j.get("ended_at") else "offen",
                    text(j.get("tractor")),text(j.get("vehicle")),j.get("area_ha",0),j.get("overlap_ha",0),
                    j.get("distance_m",0),j.get("working_time_s",0),text(j.get("line_id"))])
    return "\ufeff"+buf.getvalue()


def fortsetzen(engine, job_id):
    im_stand(engine)
    if engine.job:
        raise RuntimeError("Erst die laufende Arbeit beenden")
    j = engine.store.get_job(job_id)
    if not j or j.get("deleted") or j.get("ended_at") is not None:
        raise RuntimeError("Nur eine offene Arbeit kann fortgesetzt werden")
    if j["device_id"] != engine.config.network.device_id:
        raise RuntimeError("Diese Arbeit gehört zu einem anderen Gerät")
    if j.get("tractor") and j["tractor"] != engine.aktiver_traktor()["name"]:
        raise RuntimeError("Erst den passenden Traktor auswählen")
    if j.get("vehicle") and j["vehicle"] != engine.profile.name:
        raise RuntimeError("Erst das passende Gerät auswählen")
    feld = engine.store.get_field(j["field_id"])
    if not feld or feld.get("deleted"):
        raise RuntimeError("Feld dieser Arbeit fehlt")
    if j.get("line_id"):
        line = engine.store.get_line(j["line_id"])
        if not line or line.get("deleted") or line["field_id"] != j["field_id"]:
            raise RuntimeError("Spur dieser Arbeit fehlt")
    if j.get("reference_spacing_m") and abs(j["reference_spacing_m"]-engine.profile.spacing_m) > .001:
        raise RuntimeError("Arbeitsbreite passt nicht zur offenen Arbeit – zuerst das richtige Gespann wählen")
    blob = engine.store.get_job_coverage(job_id)
    coverage = CoverageMap.unpack(blob) if blob else CoverageMap(cell_size=.5)
    engine.load_field(j["field_id"])
    if j.get("line_id"): engine.load_line(j["line_id"])
    engine._last_track_point = None
    engine._last_update_at = None
    engine._tb_ventil = []
    engine.genauigkeit.zuruecksetzen(engine.steering.abgaben if engine.steering else 0,
                                   engine.steering.abgabe_gruende if engine.steering else None)
    engine.job = j
    engine.coverage = coverage
    engine.coverage.applied_area_m2 += (j.get("overlap_ha") or 0)*10000
    engine.distance_m = j.get("distance_m") or 0
    engine.working_time_s = j.get("working_time_s") or 0
    engine._checkpoint_at = time.time()
    engine._remember_session()
    engine.note("Offene Arbeit fortgesetzt; Lenkung bleibt aus")
    return j
