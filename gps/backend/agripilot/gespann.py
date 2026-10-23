"""Konservative Gespannhülle entlang einer vorwärts geplanten Wenderoute.

Rechtecke aus gemessenen Ausdehnungen, starres oder nachlaufendes Gerät.
Zwischen zwei Stützlagen wird die konvexe Hülle geprüft, einschließlich einer
Reserve für den Kreisbogen zwischen den Stützlagen. Keine Hinderniserkennung.
"""
from __future__ import annotations
import math
from . import geo
from .feldwerkzeuge import flaeche_enthalten

MASSE=("geraet_breite_m","traktor_breite_m","traktor_vorn_m","traktor_hinten_m","geraet_vorn_m","geraet_hinten_m")


def masse_pruefen(werte):
    for key in MASSE:
        if key not in werte: continue
        try: wert=float(werte[key])
        except (ValueError,TypeError): raise ValueError("Gespannmaße müssen Zahlen sein") from None
        if not math.isfinite(wert) or not 0<=wert<=30: raise ValueError("Gespannmaße müssen zwischen 0 und 30 m liegen")
        werte[key]=wert


def _rechteck(mitte,kurs,breite,vorn,hinten):
    h=math.radians(kurs); f=(math.sin(h),math.cos(h));r=(math.cos(h),-math.sin(h))
    return [(mitte[0]+r[0]*x+f[0]*y,mitte[1]+r[1]*x+f[1]*y)
            for x,y in [(-breite/2,-hinten),(breite/2,-hinten),(breite/2,vorn),(-breite/2,vorn)]]


def _hulle(punkte):
    pts=sorted(set(punkte))
    def kreuz(a,b,c): return (b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0])
    unten=[]; oben=[]
    for seq,out in ((pts,unten),(reversed(pts),oben)):
        for p in seq:
            while len(out)>=2 and kreuz(out[-2],out[-1],p)<=0: out.pop()
            out.append(p)
    return unten[:-1]+oben[:-1]


def pruefen(pfad,grenze,profil,abstand_m=0.,geraetekurs=None,startkurs=None,endkurs=None):
    fehlt=[key for key in MASSE if not isinstance(getattr(profil,key,0),(int,float))
           or not math.isfinite(getattr(profil,key,0)) or getattr(profil,key,0)<=0]
    if len(pfad)<2:
        return dict(vollstaendig=False,im_feld=False,huellen=[],hinweis="Keine Wenderoute vorhanden")
    # Unbekannte Maße werden nie durch erfundene Fahrzeugabmessungen ersetzt.
    if fehlt:
        return dict(vollstaendig=False,im_feld=False,huellen=[],
                    hinweis="Gespannmaße fehlen. Unter Traktor und Gerät die Ausdehnungen für die Wendeprüfung eintragen.")
    if not all(math.isfinite(float(v)) for v in (profil.width_m,profil.tool_offset_m,profil.tool_trailing_m,profil.hitch_length_m,abstand_m)):
        raise ValueError("Ungültige Gespanngeometrie")
    if profil.width_m<=0 or profil.width_m>100 or abs(profil.tool_trailing_m)>30 or abs(profil.tool_offset_m)>30 or (profil.trailed and not 0.5<=profil.hitch_length_m<=30):
        raise ValueError("Gespanngeometrie außerhalb der unterstützten Maße")
    # Hinterachse: der vorhandene Lenkpunkt ist um den seitlichen Geräteversatz verschoben.
    def achse(p,k):
        h=math.radians(k)
        return p[0]-math.cos(h)*profil.tool_offset_m,p[1]+math.sin(h)*profil.tool_offset_m
    sehnen=[geo.heading_deg(a,b) for a,b in zip(pfad,pfad[1:])]
    laengen=[math.dist(a,b) for a,b in zip(pfad,pfad[1:])]
    tangenten=[sehnen[0]]
    for i in range(1,len(pfad)-1):
        gewicht=laengen[i-1]/max(1e-9,laengen[i-1]+laengen[i])
        tangenten.append(sehnen[i-1]+geo.angle_difference(sehnen[i],sehnen[i-1])*gewicht)
    tangenten.append(sehnen[-1])
    if len(sehnen)>1:
        tangenten[0]-=geo.angle_difference(sehnen[1],sehnen[0])*laengen[0]/max(1e-9,sum(laengen[:2]))
        tangenten[-1]+=geo.angle_difference(sehnen[-1],sehnen[-2])*laengen[-1]/max(1e-9,sum(laengen[-2:]))
    if startkurs is not None:tangenten[0]=startkurs
    if endkurs is not None:tangenten[-1]=endkurs
    kurs0=tangenten[0]
    tk=kurs0 if geraetekurs is None else geraetekurs
    lagen=[]; vorige_achse=None
    for i in range(len(pfad)-1):
        a,b=pfad[i],pfad[i+1]
        k0,k1=tangenten[i],tangenten[i+1];dk=geo.angle_difference(k1,k0)
        n=max(1,math.ceil(math.dist(a,b)/0.2),math.ceil(abs(dk)/1))
        for j in range(0 if i==0 else 1,n+1):
            f=j/n; p=(a[0]+(b[0]-a[0])*f,a[1]+(b[1]-a[1])*f); k=k0+dk*f
            rear=achse(p,k)
            if profil.trailed and vorige_achse is not None:
                ds=math.dist(rear,vorige_achse)
                bewegung=geo.heading_deg(vorige_achse,rear) if ds>1e-8 else k
                # Exakte Nachlauf-Lösung für dieses kurze gerade Zugpunktstück.
                differenz=math.radians(geo.angle_difference(bewegung,tk))
                rest=2*math.atan(math.tan(differenz/2)*math.exp(-ds/profil.hitch_length_m))
                tk=bewegung-math.degrees(rest)
            elif not profil.trailed: tk=k
            vorige_achse=rear
            back=profil.hitch_length_m if profil.trailed else profil.tool_trailing_m
            h=math.radians(tk); r=(math.cos(h),-math.sin(h));fw=(math.sin(h),math.cos(h))
            tool=(rear[0]-fw[0]*back+r[0]*profil.tool_offset_m,rear[1]-fw[1]*back+r[1]*profil.tool_offset_m)
            lagen.append((_rechteck(rear,k,profil.traktor_breite_m,profil.traktor_vorn_m,profil.traktor_hinten_m),
                          _rechteck(tool,tk,max(profil.width_m,profil.geraet_breite_m),profil.geraet_vorn_m,profil.geraet_hinten_m),k,tk))
    ok=len(grenze)>=3; huellen=[]; puffer=[[],[]]
    # 10 cm Modell-/Abtastreserve zusätzlich zum vom Fahrer eingetragenen Abstand.
    # Die Rotationsreserve steigt bei langen Deichseln oder großen Geräten mit.
    ausladung=max(profil.traktor_vorn_m,profil.traktor_hinten_m,profil.traktor_breite_m,
                   profil.hitch_length_m+max(profil.width_m,profil.geraet_breite_m)+profil.geraet_vorn_m+profil.geraet_hinten_m,
                   abs(profil.tool_trailing_m)+abs(profil.tool_offset_m))
    for i,lage in enumerate(lagen):
        alt=lagen[max(0,i-1)]
        winkel=math.radians(max(abs(geo.angle_difference(lage[2],alt[2])),abs(geo.angle_difference(lage[3],alt[3]))))
        reserve=0.10+ausladung*(1-math.cos(winkel/2))
        # Gemeinsame Hülle umfasst auch Deichsel/Verbindung, nicht nur die
        # zwei getrennten Körper. Bei starkem Einknicken bewusst konservativ.
        hull=_hulle(alt[0]+lage[0]+alt[1]+lage[1])
        if ok and not flaeche_enthalten(hull,grenze,abstand_m+reserve): ok=False
        puffer[0].extend(hull)
        # Zusammengefasste Hüllen verringern die Datenmenge der Live-Vorschau.
        if i%8==7 or i==len(lagen)-1:
            huellen.extend([[[round(x,3),round(y,3)] for x,y in _hulle(p)] for p in puffer if p])
            puffer=[[],[]]
    return dict(vollstaendig=True,im_feld=ok,huellen=huellen,abstand_m=abstand_m,reserve_m=0.10,
                hinweis="Gespann und Grenzabstand passen im Modell." if ok else "Gespann oder Grenzabstand ragen über die Arbeitsgrenze. Vorgewende vergrößern oder eine andere Route planen.")
