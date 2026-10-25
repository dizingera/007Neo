"""Begrenzter Zusatzdatenabgleich und lokales Konfliktarchiv, ohne Schemaänderung."""
from __future__ import annotations

import base64
import hashlib
import json
import math
import re
import time
import uuid

_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


def _json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def _zahl(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _punkte(value):
    return (isinstance(value, list) and len(value) <= 20000 and
            all(isinstance(p, (list, tuple)) and len(p) == 2 and all(_zahl(x) for x in p) for p in value))


def _wert(key, value):
    """Nur bekannte Datentypen; unbekannte Zusatzfelder werden nicht weitergegeben."""
    if key.startswith("grenze_verlauf:") and _ID.fullmatch(key.split(":", 1)[1]):
        if not isinstance(value, list) or len(value) > 100:
            raise ValueError("Grenzverlauf ungültig")
        out = []
        for v in value:
            if not isinstance(v, dict) or not _zahl(v.get("zeit")) or not _punkte(v.get("boundary")):
                raise ValueError("Grenzversion ungültig")
            w = {"zeit": v["zeit"], "boundary": v["boundary"]}
            if "working_boundary" in v:
                if not _punkte(v["working_boundary"]):
                    raise ValueError("Arbeitsgrenze ungültig")
                w["working_boundary"] = v["working_boundary"]
            if _zahl(v.get("area_ha")):
                w["area_ha"] = v["area_ha"]
            out.append(w)
        return out
    if key.startswith("feld_einfahrt:") and _ID.fullmatch(key.split(":", 1)[1]):
        if not isinstance(value, dict):
            raise ValueError("Einfahrt ungültig")
        if "zeit" in value and (not _zahl(value["zeit"]) or value["zeit"] < 0):
            raise ValueError("Einfahrtszeit ungültig")
        zeit = {"zeit": value["zeit"]} if "zeit" in value else {}
        if "geloescht" in value and not isinstance(value["geloescht"], bool):
            raise ValueError("Löschmarkierung ungültig")
        if value.get("geloescht") is True:
            if not zeit:
                raise ValueError("Löschmarkierung braucht Versionszeit")
            return {"geloescht": True, **zeit}
        if all(_zahl(value.get(k)) for k in ("lat", "lon")) and abs(value["lat"]) <= 90 and abs(value["lon"]) <= 180:
            return {**{k: value[k] for k in ("lat", "lon")}, **zeit}
        for pair in (("x", "y"), ("localx", "localy")):
            if all(_zahl(value.get(k)) for k in pair):
                return {**{k: value[k] for k in pair}, **zeit}
        raise ValueError("Einfahrtkoordinaten ungültig")
    if key == "probefahrt_bericht":
        if not isinstance(value, dict) or not all(isinstance(value.get(k), dict) for k in ("vorher", "nachher")):
            raise ValueError("Bericht ungültig")
        out = {k: value[k] for k in ("uebung", "vergleichbar", "besser", "rms_aenderung_prozent", "urteil") if k in value and isinstance(value[k], (bool, float, int, str, type(None)))}
        # Einstellungen enthalten ausschließlich Profil-/Messwerte, niemals Netzwerkdaten.
        from .guidance import VehicleProfile
        from dataclasses import fields
        profil_keys = {f.name for f in fields(VehicleProfile)}
        konfig_keys = {"traktor", "geraet", "uebung", "max_duty", "min_duty", "kp", "ki", "kd", "encoder_counts_per_degree", "invert", "imu_enabled", "imu_roll_offset_deg", "imu_pitch_offset_deg"}
        for phase in ("vorher", "nachher"):
            v = value[phase]
            werte = v.get("werte", {})
            konfig = v.get("einstellungen", {})
            if not isinstance(werte, dict) or not isinstance(konfig, dict) or not _zahl(v.get("zeit")):
                raise ValueError("Berichtmessung ungültig")
            # Kennzahlen sind numerisch/bool/null, keine frei verschachtelten Daten.
            clean = {k: x for k, x in werte.items() if k in {"imu_anteil", "dauer_s", "rms_cm", "p95_cm", "mittel_cm", "tempo_kmh", "gps_cm", "roll_deg", "motor_grenze_anteil", "radfehler_deg", "nachlauf_s", "proben", "rad_proben", "motor_proben"} and (x is None or isinstance(x, bool) or _zahl(x))}
            cfg = {k: x for k, x in konfig.items() if k in konfig_keys and isinstance(x, (str, int, float, bool, type(None)))}
            for group, keys in (("imu", {"source", "axis_map", "roll_sign", "terrain_compensation", "use_for_heading", "aktiv"}),
                                ("motor", {"max_duty", "min_duty", "gain_p", "gain_i", "gain_d", "acceleration", "current_limit_a", "dead_band_deg", "feedback", "encoder_counts_per_deg", "encoder_invert", "invert_motor", "mitte_versatz_deg"})):
                obj = konfig.get(group, {})
                if isinstance(obj, dict):
                    cfg[group] = {k: x for k, x in obj.items() if k in keys and isinstance(x, (str, int, float, bool, type(None)))}
            profil = konfig.get("profil", {})
            if isinstance(profil, dict):
                cfg["profil"] = {k: x for k, x in profil.items() if k in profil_keys and isinstance(x, (str, int, float, bool, type(None)))}
            out[phase] = {"zeit": v["zeit"], "werte": clean, "einstellungen": cfg}
        return json.loads(_json(out))
    raise ValueError("Zusatzschlüssel nicht freigegeben")


def _device(store, device_id=None):
    if device_id and _ID.fullmatch(str(device_id)):
        return str(device_id)
    value = store.get_setting("zusatzsync_device_id")
    if not isinstance(value, str) or not _ID.fullmatch(value):
        value = uuid.uuid4().hex
        store.set_setting("zusatzsync_device_id", value)
    return value


def exportieren(store, since=0, device_id=None):
    """Wire: [{key,value,updated_at,device_id}], streng begrenzte Settings-Auswahl."""
    device = _device(store, device_id)
    rows = store.db.execute("SELECT key,value,updated_at FROM settings WHERE updated_at>? AND (key LIKE 'grenze_verlauf:%' OR key LIKE 'feld_einfahrt:%' OR key='probefahrt_bericht' OR key LIKE 'probefahrt_fremd:%')", (since,)).fetchall()
    out = []
    for row in rows:
        key = row["key"]
        try:
            value = json.loads(row["value"])
            origin = device
            stamp = row["updated_at"]
            if key.startswith("feld_einfahrt:"):
                meta = store.get_setting("zusatzsync_meta", {}).get(key, {})
                if row["updated_at"] == meta.get("empfang"):
                    stamp = meta["zeit"]
                if isinstance(value, dict) and _zahl(value.get("zeit")):
                    stamp = value["zeit"]
            if key.startswith("probefahrt_fremd:"):
                origin = key.split(":", 1)[1]
                key, stamp, value = "probefahrt_bericht", value["updated_at"], value["bericht"]
            out.append(dict(key=key, value=_wert(key, value), updated_at=stamp, device_id=origin))
        except (ValueError, TypeError, KeyError):
            continue
    return out


def uebernehmen(store, items, device_id=None):
    """Fremdberichte bleiben getrennt. Grenzversionen: deterministische Union, letzte zehn."""
    result = {"angewendet": 0, "abgelehnt": 0}
    if not isinstance(items, list) or len(items) > 2000:
        return {"angewendet": 0, "abgelehnt": 1}
    own = _device(store, device_id)
    for item in items:
        try:
            if not isinstance(item, dict):
                raise ValueError("Eintrag ungültig")
            key, stamp, origin = item["key"], item["updated_at"], item["device_id"]
            if not isinstance(key, str) or not _zahl(stamp) or stamp < 0 or not isinstance(origin, str) or not _ID.fullmatch(origin):
                raise ValueError("Metadaten ungültig")
            if len(_json(item)) > 2000000:
                raise ValueError("Eintrag zu groß")
            value = _wert(key, item["value"])
            if key == "probefahrt_bericht":
                if origin == own:
                    continue
                key = "probefahrt_fremd:" + origin
                old = store.get_setting(key) or {}
                if old.get("updated_at", -1) >= stamp:
                    continue
                value = {"device_id": origin, "updated_at": stamp, "bericht": value}
            elif key.startswith("grenze_verlauf:"):
                old = _wert(key, store.get_setting(key, []))
                union = {_json(v): v for v in old + value}
                value = sorted(union.values(), key=lambda v: (v["zeit"], _json(v)), reverse=True)[:10]
            else:
                meta = store.get_setting("zusatzsync_meta", {})
                row = store.db.execute("SELECT updated_at FROM settings WHERE key=?", (key,)).fetchone()
                # Empfangszeit ist kein Bearbeitungszeitpunkt: Original-Zeit separat erhalten.
                local = meta.get(key, {})
                local_stamp = local.get("zeit", 0) if row and row["updated_at"] == local.get("empfang") else (row["updated_at"] if row else -1)
                existing = store.get_setting(key)
                if "zeit" in value:
                    stamp = value["zeit"]
                if isinstance(existing, dict) and _zahl(existing.get("zeit")):
                    local_stamp = existing["zeit"]
                # Bei gleicher Versionszeit gewinnt die Löschung; alte Geräte
                # dürfen durch erneutes Senden keinen gelöschten Punkt beleben.
                deletion_wins_tie = (stamp == local_stamp and value.get("geloescht") is True
                                     and isinstance(existing, dict) and existing.get("geloescht") is not True)
                if stamp < local_stamp or (stamp == local_stamp and not deletion_wins_tie):
                    continue
            if store.get_setting(key) != value:
                store.set_setting(key, value)
                result["angewendet"] += 1
                if key.startswith("feld_einfahrt:"):
                    meta = store.get_setting("zusatzsync_meta", {})
                    row = store.db.execute("SELECT updated_at FROM settings WHERE key=?", (key,)).fetchone()
                    meta[key] = {"zeit": stamp, "empfang": row["updated_at"]}
                    store.set_setting("zusatzsync_meta", meta)
        except (ValueError, TypeError, KeyError, OverflowError):
            result["abgelehnt"] += 1
    return result


def exchange(store, items=None, since=0, device_id=None):
    """Server: nach erfolgreicher Authentifizierung aufrufen; Antwort direkt ergänzen."""
    applied = uebernehmen(store, [] if items is None else items, device_id=device_id)
    return {"zusatzdaten": exportieren(store, since, device_id=device_id), "zusatzdaten_status": applied}


def konflikt_sichern(store, table, incoming):
    if table not in ("fields", "lines", "jobs"):
        return False
    row = store.db.execute(f"SELECT * FROM {table} WHERE id=?", (incoming.get("id"),)).fetchone()
    if not row:
        return False
    local = dict(row)
    push = float(store.get_setting("sync_push_cursor", 0))
    remote_stamp = store.get_setting(f"sync_remote_stand:{table}:{local['id']}")
    received = store.db.execute("SELECT received_at FROM sync_received WHERE table_name=? AND record_id=?", (table, local["id"])).fetchone()
    if (incoming.get("updated_at", 0) <= local["updated_at"] or local["updated_at"] <= push
            or local["updated_at"] == remote_stamp
            or (received and local["updated_at"] <= received["received_at"])):
        return False
    if isinstance(local.get("coverage"), bytes):
        local["coverage"] = base64.b64encode(local["coverage"]).decode()
    def nutzdaten(record):
        result = {}
        for k, v in record.items():
            if k not in local or k in ("updated_at", "coverage"):
                continue
            if isinstance(v, str) and v[:1] in ("[", "{"):
                try:
                    v = json.loads(v)
                except ValueError:
                    pass
            result[k] = v
        return result
    if nutzdaten(local) == {**nutzdaten(local), **nutzdaten(incoming)}:
        return False
    incoming = {k: v for k, v in incoming.items() if k in local}
    ident = hashlib.sha256(_json([table, local, incoming]).encode()).hexdigest()[:24]
    archiv = store.get_setting("sync_konflikte", [])
    if any(c["id"] == ident for c in archiv):
        return False
    archiv.append({"id": ident, "tabelle": table, "datensatz_id": local["id"], "zeit": time.time(),
                   "lokal": local, "eingehend": incoming, "status": "offen", "neuerer_uebernommen": True})
    store.set_setting("sync_konflikte", archiv[-50:])
    return True


def loesen(store, id, auswahl):
    """Auswahl 'lokal' oder 'eingehend'; neuer Zeitstempel macht Auflösung synchronisierbar."""
    if auswahl not in ("lokal", "eingehend"):
        raise ValueError("Auswahl muss lokal oder eingehend sein")
    archiv = store.get_setting("sync_konflikte", [])
    conflict = next((c for c in archiv if c["id"] == id), None)
    if not conflict or conflict.get("status") != "offen":
        raise ValueError("Offener Konflikt nicht gefunden")
    table = conflict["tabelle"]
    if table not in ("fields", "lines", "jobs"):
        raise ValueError("Konflikttabelle ungültig")
    current = store.db.execute(f"SELECT * FROM {table} WHERE id=?", (conflict["datensatz_id"],)).fetchone()
    record = dict(conflict[auswahl])
    if not current or current["updated_at"] != conflict["eingehend"]["updated_at"]:
        raise ValueError("Datensatz inzwischen geändert; Konflikt neu prüfen")
    # Aktuelle vereinigte Arbeitsfläche bei einer Metadatenauflösung erhalten.
    if table == "jobs" and current["coverage"]:
        record["coverage"] = base64.b64encode(current["coverage"]).decode()
    record["updated_at"] = max(time.time(), current["updated_at"] + .000001)
    store.apply_remote(table, [record])
    store.db.execute("DELETE FROM sync_received WHERE table_name=? AND record_id=?", (table, record["id"]))
    store.db.commit()
    conflict.update(status="geloest", auswahl=auswahl, geloest_at=time.time())
    store.set_setting("sync_konflikte", archiv)
    return {"ok": True, "id": id, "auswahl": auswahl, "tabelle": table, "datensatz_id": record["id"]}
