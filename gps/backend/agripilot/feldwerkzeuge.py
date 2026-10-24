"""Geometrie und Auswahl für Arbeitsgrenzen und wiederverwendbare Arbeitslinien."""
from __future__ import annotations
import hashlib
import json
import math
from . import geo


def arbeitsgrenze(feld):
    return (feld or {}).get("working_boundary") or (feld or {}).get("boundary") or []


def _abstand(p, a, b):
    dx, dy = b[0]-a[0], b[1]-a[1]
    t = max(0., min(1., ((p[0]-a[0])*dx+(p[1]-a[1])*dy)/(dx*dx+dy*dy))) if dx or dy else 0.
    return math.hypot(p[0]-a[0]-t*dx, p[1]-a[1]-t*dy)


def _kreuz(a,b,c):
    return (b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0])


def _schneiden(a,b,c,d):
    q=(_kreuz(a,b,c),_kreuz(a,b,d),_kreuz(c,d,a),_kreuz(c,d,b))
    if q[0]*q[1]<-1e-12 and q[2]*q[3]<-1e-12: return True
    return any(abs(v)<1e-8 and _abstand(p,x,y)<1e-7 for v,p,x,y in
               ((q[0],c,a,b),(q[1],d,a,b),(q[2],a,c,d),(q[3],b,c,d)))


def _innen(p,ring):
    return geo.point_in_polygon(p,ring) or any(_abstand(p,a,ring[(i+1)%len(ring)])<1e-7 for i,a in enumerate(ring))


def strecke_enthalten(a,b,ring,abstand=0.):
    if len(ring)<3 or not _innen(a,ring) or not _innen(b,ring): return False
    # Schnittparameter teilen die Strecke auch an sehr schmalen Buchten. Nur
    # Endpunkte zu prüfen übersieht eine Grenze, die zwischen ihnen hineinragt.
    ts=[0.,1.]; dx,dy=b[0]-a[0],b[1]-a[1]
    for i,c in enumerate(ring):
        d=ring[(i+1)%len(ring)]; ex,ey=d[0]-c[0],d[1]-c[1]
        den=dx*ey-dy*ex
        if abs(den)>1e-10:
            t=((c[0]-a[0])*ey-(c[1]-a[1])*ex)/den
            u=((c[0]-a[0])*dy-(c[1]-a[1])*dx)/den
            if -1e-9<=t<=1+1e-9 and -1e-9<=u<=1+1e-9: ts.append(max(0.,min(1.,t)))
        if abstand>0:
            if _schneiden(a,b,c,d): return False
            if min(_abstand(a,c,d),_abstand(b,c,d),_abstand(c,a,b),_abstand(d,a,b))<abstand-1e-7: return False
    ts=sorted(set(ts))
    return all(_innen((a[0]+dx*(s+t)/2,a[1]+dy*(s+t)/2),ring) for s,t in zip(ts,ts[1:]))


GRENZ_TOLERANZ_M = 0.5   # so weit darf eine Arbeitsgrenze über die Feldgrenze hinaus


def aussen_hoechstens(punkte, ring, schritt_m=0.25):
    """Wie weit ``punkte`` (geschlossen) höchstens außerhalb von ``ring`` liegt, und bei
    welchem Punkt (Anfang der Kante). Abgetastet alle ``schritt_m`` Meter."""
    if len(punkte) < 3 or len(ring) < 3:
        return 0.0, 0
    weitest, wo = 0.0, 0
    for i, a in enumerate(punkte):
        b = punkte[(i + 1) % len(punkte)]
        n = max(1, int(math.dist(a, b) / schritt_m))
        for k in range(n + 1):
            p = (a[0] + (b[0] - a[0]) * k / n, a[1] + (b[1] - a[1]) * k / n)
            if geo.point_in_polygon(p, ring):
                continue
            d = min(_abstand(p, c, ring[(j + 1) % len(ring)]) for j, c in enumerate(ring))
            if d > weitest:
                weitest, wo = d, (i if k < n else (i + 1) % len(punkte))
    return weitest, wo


def flaeche_enthalten(punkte,ring,abstand=0.):
    if len(punkte)<3 or len(ring)<3: return False
    return all(strecke_enthalten(a,punkte[(i+1)%len(punkte)],ring,abstand) for i,a in enumerate(punkte))


def grenze_pruefen(punkte,original,innerhalb=True):
    if not isinstance(punkte,list) or not 3<=len(punkte)<=2000:
        raise ValueError("Eine Arbeitsgrenze braucht 3 bis 2000 Punkte")
    try:
        if any(not isinstance(p,(list,tuple)) or len(p)!=2 for p in punkte): raise ValueError()
        ring=[[float(x),float(y)] for x,y in punkte]
        if any(not math.isfinite(v) or abs(v)>1e7 for p in ring for v in p): raise ValueError()
    except (ValueError,TypeError): raise ValueError("Grenzpunkte müssen gültige Koordinaten sein") from None
    if ring[0]==ring[-1]: ring.pop()
    if len(ring)<3 or geo.polygon_area(ring)<100:
        raise ValueError("Die Arbeitsgrenze muss mindestens 100 m² umfassen")
    for i,a in enumerate(ring):
        b=ring[(i+1)%len(ring)]
        if math.dist(a,b)<0.05: raise ValueError("Grenzpunkte liegen zu dicht beieinander")
        for j in range(i+1,len(ring)):
            if j==i+1 or (i==0 and j==len(ring)-1): continue
            if _schneiden(a,b,ring[j],ring[(j+1)%len(ring)]):
                raise ValueError("Die Arbeitsgrenze überschneidet oder berührt sich selbst")
    # Eine geänderte Feldgrenze selbst (innerhalb=False) darf über die alte hinaus. Die
    # Arbeitsgrenze darf um GRENZ_TOLERANZ_M hinaus: eine abgefahrene Grenze hat Zacken von
    # ein paar Zentimetern, und eine darüber gezogene Gerade lag sonst "außerhalb".
    if innerhalb:
        aussen, punkt = aussen_hoechstens(ring, original)
        if aussen > GRENZ_TOLERANZ_M:
            raise ValueError(
                f"Die Arbeitsgrenze liegt bis zu {aussen:.1f} m außerhalb der Feldgrenze (bei Punkt {punkt + 1})"
                .replace(".", ",", 1) + f" – erlaubt sind {GRENZ_TOLERANZ_M:.1f} m".replace(".", ",")
                + ". Soll die Feldgrenze selbst so verlaufen: Häkchen „Feldgrenze selbst ändern“ setzen.")
    return ring


def stand(daten):
    return hashlib.sha256(json.dumps(daten,sort_keys=True,separators=(",",":"),allow_nan=False).encode()).hexdigest()


def _ohne_wendeboegen(punkte):
    if len(punkte)<4: return [punkte]
    kurse=[geo.heading_deg(a,b) for a,b in zip(punkte,punkte[1:])]
    mask=[False]*len(punkte); anfang=None; drehung=0.; richtung=0
    def abschliessen(ende):
        if anfang is not None and abs(drehung)>=65:
            for n in range(max(0,anfang-1),min(len(mask),ende+2)): mask[n]=True
    for i in range(1,len(kurse)):
        delta=geo.angle_difference(kurse[i],kurse[i-1])
        dist=max(.01,math.dist(punkte[i],punkte[i+1]))
        sign=1 if delta>=0 else -1
        if abs(delta)/dist>=0.3:
            if anfang is not None and sign!=richtung: abschliessen(i);anfang=None
            if anfang is None: anfang=i;drehung=0.;richtung=sign
            drehung+=delta
        else: abschliessen(i);anfang=None
    abschliessen(len(punkte)-1)
    aus=[];teil=[]
    for p,weg in zip(punkte,mask):
        if weg:
            if teil: aus.append(teil);teil=[]
        else: teil.append(p)
    if teil: aus.append(teil)
    return aus


def arbeitslinien(punkte,ebene):
    """Abschnitte ab 20 m; keine automatische Aussaat-/Lenkfreigabe.

    Ein Richtungswechsel über 35° innerhalb 12 m trennt das Wendemanöver.
    Sanfte Kurven bleiben erhalten. Der Fahrer prüft die Vorschau und kann die
    Endpunkte eingrenzen, bevor genau dieser Abschnitt gespeichert wird.
    """
    result=[]; lauf=[]; alt=None
    def abschliessen():
        if len(lauf)<3: return
        pts=[p[1] for p in lauf]
        for teil in _ohne_wendeboegen(pts):
            laenge=sum(math.dist(a,b) for a,b in zip(teil,teil[1:]))
            if laenge<20: continue
            result.append({"punkte":[list(p) for p in geo.simplify(teil,0.05)],
                           "laenge_m":round(laenge,1),"altaufzeichnung":bool(alt)})
    for p in punkte:
        legacy=p.get("tool_lat") is None or p.get("tool_lon") is None
        try:
            lat,lon=(p['lat'],p['lon']) if legacy else (p['tool_lat'],p['tool_lon'])
            t=float(p['t']); pos=ebene.to_local(float(lat),float(lon))
            gueltig=(all(math.isfinite(x) for x in (*pos,t)) and -90<=lat<=90 and -180<=lon<=180
                     and int(p.get('fix_quality') or 0)>0 and float(p.get('speed_ms') or 0)>=0.2
                     and (legacy or (p.get('working')==1 and not p.get('turning'))))
        except (TypeError,ValueError,KeyError): gueltig=False
        if not gueltig:
            abschliessen();lauf=[];continue
        if lauf:
            dt=t-lauf[-1][0]; dist=math.dist(pos,lauf[-1][1])
            sprunghaft=dt<=0 or dt>8 or dist>max(5.,dt*12) or legacy!=alt
            if not sprunghaft and len(lauf)>2 and dist>0.15:
                hinten=len(lauf)-2
                while hinten>0 and math.dist(lauf[hinten][1],lauf[-1][1])<12: hinten-=1
                richtung=geo.heading_deg(lauf[hinten][1],lauf[-1][1])
                jetzt=geo.heading_deg(lauf[-1][1],pos)
                sprunghaft=abs(geo.angle_difference(jetzt,richtung))>35
            if sprunghaft: abschliessen();lauf=[]
        alt=legacy;lauf.append((t,pos))
    abschliessen()
    return result


def abschnitt_kuerzen(punkte,anfang_m=0.,ende_m=0.):
    try: anfang_m=float(anfang_m);ende_m=float(ende_m)
    except (TypeError,ValueError): raise ValueError("Kürzung muss eine Zahl sein") from None
    gesamt=sum(math.dist(a,b) for a,b in zip(punkte,punkte[1:]))
    if not all(math.isfinite(v) and v>=0 for v in (anfang_m,ende_m)) or gesamt-anfang_m-ende_m<20:
        raise ValueError("Nach dem Kürzen müssen mindestens 20 m übrig bleiben")
    aus=[];weg=0.;bis=gesamt-ende_m
    for a,b in zip(punkte,punkte[1:]):
        d=math.dist(a,b)
        if d>1e-9 and weg+d>=anfang_m and weg<=bis:
            for f in (max(0.,(anfang_m-weg)/d),min(1.,(bis-weg)/d)):
                p=[a[0]+(b[0]-a[0])*f,a[1]+(b[1]-a[1])*f]
                if not aus or math.dist(aus[-1],p)>1e-8: aus.append(p)
        weg+=d
    return aus
