"""Auftragsbezogene Flächen, manuelle Kosten und druckbare PDF-Berichte.

Keine API-/Engine-Nebenwirkungen. Aufrufer führen schwere Leseoperationen im
Hintergrund aus. Flächen sind Rastermessungen am gespeicherten Jobcheckpoint,
keine Aussage über die Genauigkeit des tatsächlichen Arbeitsstreifens.
"""
from __future__ import annotations

import math
import sqlite3
import struct
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone

from .storage import Storage

MAX_ZELLEN = 2000000
MAX_KARTENZELLEN = 20000
MAX_KANTEN = 512
MAX_AUFWAND = 12000000
MAX_JOBS = 1000
_BERECHNUNG = threading.BoundedSemaphore(2)
_KOSTEN = threading.Lock()
KOSTEN_GRENZEN = {"diesel_liter": 100000, "diesel_preis_eur_l": 100,
                  "betriebsmittel_eur": 10000000, "stundensatz_eur": 10000}


@contextmanager
def _snapshot(store):
    # Eigene Verbindung: kein BEGIN/COMMIT auf der vom Lenktakt benutzten DB.
    db = sqlite3.connect(store.path.resolve().as_uri() + "?mode=ro", uri=True, timeout=2)
    db.row_factory = sqlite3.Row
    reader = object.__new__(Storage)
    reader.db = db
    try:
        db.execute("BEGIN")
        yield reader
    finally:
        db.close()


def _job(store, job_id):
    job = store.get_job(job_id)
    if not job or job.get("deleted"):
        raise ValueError("Auftrag nicht gefunden")
    return job


def _zahl(value, maximum):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("Endliche Zahl erforderlich")
    if not 0 <= value <= maximum:
        raise ValueError("Wert außerhalb des erlaubten Bereichs")
    value = float(value)
    if not math.isfinite(value):
        raise ValueError("Wert außerhalb des erlaubten Bereichs")
    return value


def _flaeche(poly):
    if len(poly) < 3:
        return 0.0
    x, y = poly[0]
    return abs(sum((a[0]-x)*(b[1]-y)-(b[0]-x)*(a[1]-y)
                   for a, b in zip(poly, poly[1:] + poly[:1]))) / 2


def _clip(poly, x0, y0, cs):
    # Sutherland-Hodgman gegen die konvexe Rasterzelle. Auch bei konkaven
    # Feldringen ergeben verbindende Nullflächen den richtigen Flächeninhalt.
    for axis, edge, sign in ((0,x0,1),(0,x0+cs,-1),(1,y0,1),(1,y0+cs,-1)):
        out = []
        if not poly:
            break
        prev = poly[-1]
        for cur in poly:
            pin = sign*(prev[axis]-edge) >= 0
            cin = sign*(cur[axis]-edge) >= 0
            if pin != cin:
                t = (edge-prev[axis])/(cur[axis]-prev[axis])
                out.append([prev[0]+t*(cur[0]-prev[0]), prev[1]+t*(cur[1]-prev[1])])
            if cin:
                out.append(list(cur))
            prev = cur
        poly = out
    return poly


def _grenze(raw):
    if not isinstance(raw, list) or not 3 <= len(raw) <= MAX_KANTEN:
        raise ValueError("Feldgrenze fehlt oder ist zu umfangreich")
    poly = []
    for p in raw:
        if not isinstance(p, (list, tuple)) or len(p) != 2:
            raise ValueError("Ungültige Feldgrenze")
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) or
               not math.isfinite(v) or abs(v)>10000000 for v in p):
            raise ValueError("Ungültige Feldkoordinaten")
        poly.append(list(p))
    if poly[0] == poly[-1]:
        poly.pop()
    # Selbstschneidende Grenzen sind keine belastbare Flächenbasis.
    def cross(a,b,c):
        return (b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0])
    for i,a in enumerate(poly):
        b=poly[(i+1)%len(poly)]
        if a == b:
            raise ValueError("Doppelte Grenzpunkte")
        for j in range(i+2,len(poly)):
            if i==0 and j==len(poly)-1:
                continue
            c,d=poly[j],poly[(j+1)%len(poly)]
            if (cross(a,b,c)*cross(a,b,d)<=0 and cross(c,d,a)*cross(c,d,b)<=0
                and max(min(a[0],b[0]),min(c[0],d[0]))<=min(max(a[0],b[0]),max(c[0],d[0]))
                and max(min(a[1],b[1]),min(c[1],d[1]))<=min(max(a[1],b[1]),max(c[1],d[1]))):
                raise ValueError("Feldgrenze schneidet sich")
    if _flaeche(poly)<=1e-8:
        raise ValueError("Feldgrenze ohne Fläche")
    return poly


def _rest(reader, job, budget=None, grenze=None, zeige_karte=True):
    field = reader.get_field(job["field_id"])
    if not field or field.get("deleted"):
        raise ValueError("Feld nicht gefunden")
    result = dict(field_id=field["id"], job_id=job["id"], gesamt_ha=None,
                  bearbeitet_ha=None, rest_ha=None, prozent=None, restzeit_min=None,
                  leistung_ha_h=None, grenze=[], kartenZellen=[], ueberlappt=None,
                  ueberlappung_raeumlich_bekannt=False, bekannt=False,
                  stand=job.get("updated_at"), herkunft="gespeicherter_jobcheckpoint",
                  hinweis="", raster_m=None, karten_vollstaendig=False,
                  karten_limit=MAX_KARTENZELLEN, karten_grundflaeche="unbekannt",
                  karten_darstellung="feldgrenze_mit_bearbeiteten_zellen")
    try:
        poly = grenze if grenze is not None else _grenze(field.get("boundary"))
        result["grenze"] = poly
        total = _flaeche(poly)
        result["gesamt_ha"] = total/10000
        if budget is not None and budget[0]<=0:
            raise ValueError("Berichtslimit erreicht; Auftrag einzeln abrufen")
        blob = reader.get_job_coverage(job["id"])
        if blob is None:
            raise ValueError("Keine gespeicherte Rasterkarte vorhanden")
        if len(blob)<4 or (len(blob)-4)%8 or len(blob)>4+MAX_ZELLEN*8:
            raise ValueError("Rasterkarte ungültig oder Berechnungsgrenze erreicht")
        cs, = struct.unpack_from("<f",blob)
        if not math.isfinite(cs) or not 0.05<=cs<=100:
            raise ValueError("Ungültige Rastergröße")
        count=(len(blob)-4)//8
        aufwand=count*(len(poly)+1)
        if aufwand>MAX_AUFWAND:
            raise ValueError("Belegtes Raster überschreitet die Berechnungsgrenze")
        if budget is not None:
            if aufwand>budget[0]:
                budget[0]=0
                raise ValueError("Berichtslimit erreicht; Auftrag einzeln abrufen")
            budget[0]-=aufwand
        # Nur wirklich belegte Zellen besuchen, nie das unbefahrene Feldraster.
        # Kompakte 64-Bit-Schlüssel statt zusätzlicher Index-Tupelmengen.
        seen=set()
        worked=0.0
        maps=[]
        map_count=0
        xmin=min(p[0] for p in poly); xmax=max(p[0] for p in poly)
        ymin=min(p[1] for p in poly); ymax=max(p[1] for p in poly)
        rechteck=(len(poly)==4 and {tuple(p) for p in poly}==
                  {(xmin,ymin),(xmax,ymin),(xmax,ymax),(xmin,ymax)})
        for ix,iy in struct.iter_unpack("<ii",memoryview(blob)[4:]):
            x,y=ix*cs,iy*cs
            if x>=xmax or x+cs<=xmin or y>=ymax or y+cs<=ymin:
                continue
            key=((ix & 0xffffffff)<<32)|(iy & 0xffffffff)
            if key in seen:
                continue
            seen.add(key)
            if rechteck:
                left,right=max(x,xmin),min(x+cs,xmax)
                bottom,top=max(y,ymin),min(y+cs,ymax)
                area=(right-left)*(top-bottom)
                clipped=([[left,bottom],[right,bottom],[right,top],[left,top]]
                         if zeige_karte and len(maps)<MAX_KARTENZELLEN else [])
            else:
                clipped=_clip(poly,x,y,cs)
                area=_flaeche(clipped)
            if area<=1e-10:
                continue
            worked+=area
            map_count+=1
            if zeige_karte and len(maps)<MAX_KARTENZELLEN:
                maps.append({"ix":ix,"iy":iy,"status":"bearbeitet",
                             "polygon":clipped,"flaeche_m2":area})
        worked=min(total,max(0.,worked))
        rest=max(0.,total-worked)
        result.update(bekannt=True,bearbeitet_ha=worked/10000,rest_ha=rest/10000,
                      prozent=100*worked/total,kartenZellen=maps,raster_m=cs,
                      karten_vollstaendig=map_count<=MAX_KARTENZELLEN,
                      karten_grundflaeche="verbleibend" if map_count<=MAX_KARTENZELLEN else "unbekannt",
                      hinweis="Rastermessung; räumliche Überlappung im Packformat nicht enthalten")
        if map_count>MAX_KARTENZELLEN:
            result["hinweis"]+="; bearbeitete Kartenzellen begrenzt, Flächenberechnung vollständig"
        seconds=job.get("working_time_s")
        if isinstance(seconds,(int,float)) and math.isfinite(seconds) and 120<=seconds<=366*86400 and worked>=10:
            result["leistung_ha_h"]=worked/10000*3600/seconds
            estimate=rest/worked*seconds/60
            if estimate<=10080:
                result["restzeit_min"]=estimate
        return result
    except (ValueError, TypeError, OverflowError, struct.error) as exc:
        result["hinweis"]=str(exc)
        return result


def restflaeche(store, job_id):
    """JSON-fähig; unbekannt=None. kartenZellen enthalten beschnittene Polygone.

    Maximal zwei Berechnungen; bei Überlast RuntimeError (API: 503).
    Fehlende/gelöschte IDs: ValueError. Kein Live-Engine-Zugriff.
    """
    if not _BERECHNUNG.acquire(blocking=False):
        raise RuntimeError("Berichtsberechnung ausgelastet; bitte erneut versuchen")
    try:
        with _snapshot(store) as reader:
            return _rest(reader,_job(reader,job_id))
    finally:
        _BERECHNUNG.release()


def _kosten(reader,job):
    raw=reader.get_setting("arbeitskosten:"+job["id"])
    values={k:None for k in KOSTEN_GRENZEN}
    stamp=None
    if raw is not None:
        if not isinstance(raw,dict):
            raise ValueError("Gespeicherte Kosten ungültig")
        for key,maximum in KOSTEN_GRENZEN.items():
            if raw.get(key) is not None:
                values[key]=_zahl(raw[key],maximum)
        stamp=raw.get("aktualisiert_am")
    seconds=job.get("working_time_s")
    hours=seconds/3600 if isinstance(seconds,(int,float)) and math.isfinite(seconds) and seconds>=0 else None
    fuel=values["diesel_liter"]*values["diesel_preis_eur_l"] if all(values[k] is not None for k in ("diesel_liter","diesel_preis_eur_l")) else None
    labour=hours*values["stundensatz_eur"] if hours is not None and values["stundensatz_eur"] is not None else None
    parts=[fuel,values["betriebsmittel_eur"],labour]
    complete=all(p is not None for p in parts)
    total=sum(parts) if complete else None
    area=job.get("area_ha")
    perha=total/area if total is not None and isinstance(area,(int,float)) and math.isfinite(area) and area>0 else None
    return dict(job_id=job["id"],**values,herkunft="manuell",berechnung_herkunft="schaetzung",
                aktualisiert_am=stamp,diesel_eur=fuel,arbeit_eur=labour,gesamt_eur=total,
                kosten_eur_ha=perha,vollstaendig=complete,
                hinweis="Manuelle Angaben; Kosten aus aufgezeichneter Arbeitszeit berechnet")


def kosten_lesen(store,job_id):
    with _snapshot(store) as reader:
        return _kosten(reader,_job(reader,job_id))


def kosten_speichern(store,job_id,payload):
    """Vollersatz der vier Eingaben; fehlend/null bleibt unbekannt, 0 ist erlaubt.

    Nur endliche JSON-Zahlen, keine Texte/Boolwerte, keine unbekannten Schlüssel.
    Stand-/Berechtigungsprüfung bleibt beim API-Aufrufer.
    """
    if not isinstance(payload,dict) or set(payload)-set(KOSTEN_GRENZEN):
        raise ValueError("Unbekannte Kostenfelder")
    values={k:None if payload.get(k) is None else _zahl(payload[k],maximum)
            for k,maximum in KOSTEN_GRENZEN.items()}
    if not _KOSTEN.acquire(blocking=False):
        raise RuntimeError("Kostenspeicherung ausgelastet")
    try:
        # Eigene Schreibverbindung verhindert Commit fremder Transaktionen.
        db=sqlite3.connect(store.path,timeout=2)
        db.row_factory=sqlite3.Row
        reader=object.__new__(Storage)
        reader.db=db
        try:
            db.execute("BEGIN IMMEDIATE")
            job=_job(reader,job_id)
            values.update(herkunft="manuell",aktualisiert_am=time.time())
            reader.set_setting("arbeitskosten:"+job_id,values)
            return _kosten(reader,job)
        finally:
            db.close()
    finally:
        _KOSTEN.release()


def _pdf(pages):
    # Kleine, echte PDF-1.4-Datei mit Byteoffsets und WinAnsi-Helvetica.
    objects=[b"<< /Type /Catalog /Pages 2 0 R >>",b"",b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>"]
    kids=[]
    for content in pages:
        number=len(objects)+1
        kids.append(f"{number} 0 R")
        objects.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 3 0 R >> >> /Contents {number+1} 0 R >>".encode())
        stream=content.encode("cp1252",errors="replace")
        objects.append(f"<< /Length {len(stream)} >>\nstream\n".encode()+stream+b"\nendstream")
    objects[1]=f"<< /Type /Pages /Count {len(kids)} /Kids [{' '.join(kids)}] >>".encode()
    output=bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets=[0]
    for i,obj in enumerate(objects,1):
        offsets.append(len(output))
        output.extend(f"{i} 0 obj\n".encode()+obj+b"\nendobj\n")
    start=len(output)
    output.extend(f"xref\n0 {len(offsets)}\n0000000000 65535 f \n".encode())
    for offset in offsets[1:]:
        output.extend(f"{offset:010d} 00000 n \n".encode())
    output.extend(f"trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{start}\n%%EOF\n".encode())
    return bytes(output)


def _text(text,x,y,size=11):
    text=str(text).replace("\\","\\\\").replace("(","\\(").replace(")","\\)")
    text=" ".join(text.split())
    return f"BT /F1 {size} Tf {x} {y} Td ({text}) Tj ET\n"


def _fmt(value,unit):
    if isinstance(value,bool) or not isinstance(value,(int,float)):
        return "unbekannt"
    try:
        if math.isfinite(value) and value>=0:
            number=f"{value:,.2f}".replace(",","#").replace(".",",").replace("#",".")
            return f"{number} {unit}".strip()
    except OverflowError:
        pass
    return "unbekannt"


def _dauer(seconds):
    if isinstance(seconds,bool) or not isinstance(seconds,(int,float)):
        return "unbekannt"
    if not math.isfinite(seconds) or seconds<0:
        return "unbekannt"
    if 0<seconds<60:
        return "unter 1 min"
    minutes=int(seconds//60)
    return f"{minutes//60} h {minutes%60:02d} min" if minutes>=60 else f"{minutes} min"


def _datum(timestamp):
    if timestamp is None:
        return "noch nicht beendet"
    try:
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
        try:
            zone=ZoneInfo("Europe/Berlin")
            label="Europe/Berlin"
        except ZoneInfoNotFoundError:
            zone=timezone.utc
            label="UTC"
        date=datetime.fromtimestamp(timestamp,timezone.utc).astimezone(zone)
        offset=date.strftime("%z")
        offset=offset[:3]+":"+offset[3:]
        return date.strftime("%d.%m.%Y, %H:%M")+f" Uhr ({label}, UTC{offset})"
    except (ValueError,TypeError,OverflowError,OSError):
        return "unbekannt"


def _zeilen(text,breite=500,groesse=11):
    # Konservative Helvetica-Breiten halten auch lange Namen in den Rändern.
    text=" ".join(str(text).split())
    lines=[]
    line=""
    width=0
    for char in text:
        factor=(.98 if char in "MWmw@%" else .32 if char in " ilI.,:;'!|" else
                .8 if char.isupper() else .65)
        delta=factor*groesse
        if line and width+delta>breite:
            head,separator,tail=line.rpartition(" ")
            if separator and head:
                lines.append(head.rstrip())
                line=tail
                width=sum((.98 if c in "MWmw@%" else .32 if c in " ilI.,:;'!|" else
                           .8 if c.isupper() else .65)*groesse for c in line)
            else:
                lines.append(line.rstrip())
                line=""; width=0
        line+=char
        width+=delta
    return lines+[line.rstrip()]


def _kopf(fieldname,subtitle):
    title=_zeilen(fieldname,breite=470,groesse=20)[0]
    if title!=fieldname:
        title=title.rstrip()+" ..."
    sub=_zeilen(subtitle,breite=480,groesse=12)[0]
    if sub!=subtitle:
        sub=sub.rstrip()+" ..."
    return ("0.12 0.25 0.19 rg\n"+_text("AGRIPILOT  /  FELDBERICHT",45,802,9)+
            _text(title,45,770,20)+"0.25 0.30 0.28 rg\n"+_text(sub,45,745,12)+
            "0.75 0.81 0.76 RG 0.7 w 45 727 m 550 727 l S\n0 0 0 rg\n")


def _kennzahlen(items):
    out=""
    for i,(label,value) in enumerate(items):
        x=45+(i%2)*258
        y=647-(i//2)*72
        out+=f"q 0.95 0.97 0.95 rg {x} {y} 247 61 re f Q\n"
        out+="0.30 0.37 0.32 rg\n"+_text(label,x+12,y+42,9)
        out+="0.10 0.24 0.15 rg\n"+_text(value,x+12,y+15,17)
    return out+"0 0 0 rg\n"


def _fuss(ref,number,total):
    return ("0.75 0.81 0.76 RG 0.5 w 45 67 m 550 67 l S\n0.35 0.38 0.35 rg\n"+
            _text(ref,45,49,8)+_text(f"Seite {number} von {total}",465,49,8)+"0 0 0 rg\n")


def bericht_pdf(store,field_id,jahr=None,job_id=None):
    """PDF-bytes: Felddeckblatt mit echtem Umriss, eine Seite pro Auftrag.

    Jahr filtert UTC-Startjahr. Maximal 1000 Aufträge, niemals still abschneiden.
    Jahresbericht aggregiert keine sich überlagernden Arbeitsflächen.
    """
    if jahr is not None and (type(jahr) is not int or not 1970<=jahr<=2200):
        raise ValueError("Ungültiges Jahr")
    if not _BERECHNUNG.acquire(blocking=False):
        raise RuntimeError("Berichtsberechnung ausgelastet")
    try:
        with _snapshot(store) as reader:
            field=reader.get_field(field_id)
            if not field or field.get("deleted"):
                raise ValueError("Feld nicht gefunden")
            poly=_grenze(field.get("boundary"))
            if job_id is not None:
                jobs=[_job(reader,job_id)]
                if jobs[0]["field_id"]!=field_id:
                    raise ValueError("Auftrag gehört nicht zum Feld")
            else:
                jobs=reader.list_jobs(field_id,limit=MAX_JOBS+1)
                if len(jobs)>MAX_JOBS:
                    raise ValueError("Zu viele Aufträge; einzelnen Auftrag auswählen")
            if jahr is not None:
                jobs=[j for j in jobs if datetime.fromtimestamp(j["started_at"],timezone.utc).year==jahr]
            name=str(field.get("name") or "Unbenanntes Feld")
            first=_kopf(name,"Feldübersicht"+(f" · {jahr}" if jahr else " · Alle Arbeitsgänge"))
            first+=_kennzahlen([
                ("FELDFLÄCHE",_fmt(_flaeche(poly)/10000,"ha")),
                ("ARBEITSGÄNGE",str(len(jobs))),
                ("ZEITRAUM",str(jahr) if jahr else "Alle Jahre"),
                ("AUSWAHL","Einzelauftrag" if job_id is not None else "Feldübersicht")])
            first+=_text("Feldkarte",45,542,13)
            first+=_text("Tatsächlicher Feldumriss · maßstäblich",45,525,9)
            first+="q 0.96 0.97 0.95 rg 45 195 505 315 re f Q\n"
            xs=[p[0] for p in poly]; ys=[p[1] for p in poly]
            scale=min(480/max(max(xs)-min(xs),1e-9),285/max(max(ys)-min(ys),1e-9))
            for i,(x,y) in enumerate(poly):
                first+=f"{50+(x-min(xs))*scale:.4f} {210+(y-min(ys))*scale:.4f} {'m' if i==0 else 'l'}\n"
            first+="h q 0.87 0.93 0.86 rg 0.15 0.45 0.2 RG 1.5 w B Q\n"
            first+=_text("Arbeitsgänge werden einzeln ausgewiesen; ihre Flächen werden nicht addiert.",45,170,9)
            first+=_text("Messwerte und manuelle Kosten finden Sie auf den folgenden Arbeitsseiten.",45,153,9)
            pages=[first]
            name_lines=_zeilen(name)
            if len(name_lines)>1:
                for offset in range(0,len(name_lines),36):
                    page=_kopf("Feldname - Fortsetzung","Vollständige Feldbezeichnung")
                    page+=''.join(_text(line,45,695-i*16) for i,line in enumerate(name_lines[offset:offset+36]))
                    pages.append(page)
            budget=[MAX_AUFWAND]
            for job in jobs:
                rest=_rest(reader,job,budget=budget,grenze=poly,zeige_karte=False)
                cost=_kosten(reader,job)
                operation=str(job.get("operation") or "Tätigkeit nicht angegeben")
                lines=["Feld: "+name,"Tätigkeit: "+operation,
                       "Beginn: "+_datum(job["started_at"]),
                       "Ende: "+_datum(job.get("ended_at")),
                       "Traktor: "+str(job.get("tractor") or "nicht angegeben"),
                       "Gerät: "+str(job.get("vehicle") or "nicht angegeben"),
                       "Innerhalb der Feldgrenze bearbeitet: "+_fmt(rest["bearbeitet_ha"],"ha"),
                       "Verbleibende Fläche: "+_fmt(rest["rest_ha"],"ha"),
                       "Restzeit (Schätzung): "+_dauer(rest["restzeit_min"]*60 if rest["restzeit_min"] is not None else None),
                       "Hinweis: Bearbeitete Fläche und Überlappung stammen aus der Aufzeichnung; sie können Anteile außerhalb der Feldgrenze enthalten. Mehrfachbearbeitung ist separat ausgewiesen.",
                       rest["hinweis"], "Manuelle Kosten"]
                labels={"diesel_liter":("Diesel","Liter"),
                        "diesel_preis_eur_l":("Dieselpreis","EUR/Liter"),
                        "betriebsmittel_eur":("Betriebsmittel","EUR"),
                        "stundensatz_eur":("Stundensatz","EUR/h")}
                lines += [label+": "+_fmt(cost[k],unit) for k,(label,unit) in labels.items()]
                lines += ["Gesamtkosten (Schätzung): "+_fmt(cost["gesamt_eur"],"EUR"),cost["hinweis"],
                          "Gespeicherter Arbeitsstand; räumliche Überlappungszellen unbekannt."]
                wrapped=[part for line in lines for part in _zeilen(line)]
                for offset in range(0,len(wrapped),27):
                    page=_kopf(name,operation+(" · Fortsetzung" if offset else ""))
                    page+=_kennzahlen([
                        ("BEARBEITETE FLÄCHE",_fmt(job.get("area_ha"),"ha")),
                        ("ARBEITSZEIT",_dauer(job.get("working_time_s"))),
                        ("STRECKE",_fmt(job.get("distance_m"),"m")),
                        ("MEHRFACHBEARBEITUNG",_fmt(job.get("overlap_ha"),"ha"))])
                    page+=''.join(_text(line,45,545-i*16) for i,line in enumerate(wrapped[offset:offset+27]))
                    pages.append(page)
            # Nur eine kurze Referenz im Fußbereich, keine technischen IDs im Titel.
            ref="Feldbericht"+(" · Referenz "+str(job_id)[:8] if job_id is not None else "")
            pages=[page+_fuss(ref,index+1,len(pages)) for index,page in enumerate(pages)]
            return _pdf(pages)
    finally:
        _BERECHNUNG.release()
