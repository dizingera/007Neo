"""Vorwärts fahrbare Dubins-Verbindungen zwischen zwei Posen in Feldmetern.

Die sechs analytischen Standardfamilien nach PythonRobotics (MIT),
Atsushi Sakai und Mitwirkende; siehe docs/DRITTLIZENZEN.md.
Kurse an der öffentlichen Grenze: wie GNSS, 0° Nord, 90° Ost.
"""
from __future__ import annotations
from dataclasses import dataclass
import math

TAU = 2 * math.pi

@dataclass
class DubinsPfad:
    familie: str
    laenge_m: float
    punkte: list[tuple[float, float]]
    kurse: list[float]
    segmente_m: tuple[float, float, float]


def _familien(a, b, d):
    sa, sb, ca, cb, cab = math.sin(a), math.sin(b), math.cos(a), math.cos(b), math.cos(a-b)
    wurzel = lambda x: math.sqrt(max(0.0, x))
    p2 = 2+d*d-2*cab+2*d*(sa-sb)
    if p2 >= -1e-10:
        t=math.atan2(cb-ca,d+sa-sb)
        yield "LSL", ((-a+t)%TAU, wurzel(p2), (b-t)%TAU)
    p2 = 2+d*d-2*cab+2*d*(sb-sa)
    if p2 >= -1e-10:
        t=math.atan2(ca-cb,d-sa+sb)
        yield "RSR", ((a-t)%TAU, wurzel(p2), (-b+t)%TAU)
    p2 = -2+d*d+2*cab+2*d*(sa+sb)
    if p2 >= -1e-10:
        p=wurzel(p2);t=math.atan2(-ca-cb,d+sa+sb)-math.atan2(-2,p)
        yield "LSR", ((-a+t)%TAU, p, (-b+t)%TAU)
    p2 = d*d-2+2*cab-2*d*(sa+sb)
    if p2 >= -1e-10:
        p=wurzel(p2);t=math.atan2(ca+cb,d-sa-sb)-math.atan2(2,p)
        yield "RSL", ((a-t)%TAU, p, (b-t)%TAU)
    t=(6-d*d+2*cab+2*d*(sa-sb))/8
    if abs(t)<=1+1e-10:
        p=(TAU-math.acos(max(-1,min(1,t))))%TAU
        q=(a-math.atan2(ca-cb,d-sa+sb)+p/2)%TAU
        yield "RLR", (q,p,(a-b-q+p)%TAU)
    t=(6-d*d+2*cab+2*d*(-sa+sb))/8
    if abs(t)<=1+1e-10:
        p=(TAU-math.acos(max(-1,min(1,t))))%TAU
        q=(-a-math.atan2(ca-cb,d+sa-sb)+p/2)%TAU
        yield "LRL", (q,p,(b-a-q+p)%TAU)


def kandidaten(start, ziel, radius_m: float, schritt_m: float = 0.25) -> list[DubinsPfad]:
    """Alle möglichen Familien, nach wirklicher Länge sortiert, ohne Rückwärtsgang."""
    if (len(start)!=3 or len(ziel)!=3
            or not all(math.isfinite(x) for x in (*start,*ziel,radius_m,schritt_m))
            or radius_m<=0 or schritt_m<=0):
        raise ValueError("Endliche Start-/Zielpose, positiver Wendekreis und Schritt nötig")
    dx,dy=ziel[0]-start[0],ziel[1]-start[1]
    theta=math.atan2(dy,dx)
    anfang=math.radians(90-start[2]);ende=math.radians(90-ziel[2])
    ergebnis=[]
    for familie, laengen in _familien((anfang-theta)%TAU,(ende-theta)%TAU,math.hypot(dx,dy)/radius_m):
        segmente=tuple(x*radius_m for x in laengen)
        if sum(math.ceil(x/schritt_m) for x in segmente)>50000:
            raise ValueError("Wenderoute zu groß für die gewählte Auflösung")
        x,y,kurs=start[0],start[1],anfang
        punkte=[(x,y)];kurse=[start[2]%360]
        for art,laenge in zip(familie,segmente):
            n=max(1,math.ceil(laenge/schritt_m));ds=laenge/n
            if laenge<1e-10:continue
            for _ in range(n):
                if art=="S":
                    x+=math.cos(kurs)*ds;y+=math.sin(kurs)*ds
                else:
                    k=(1 if art=="L" else -1)/radius_m
                    neu=kurs+k*ds
                    x+=(math.sin(neu)-math.sin(kurs))/k
                    y+=(math.cos(kurs)-math.cos(neu))/k
                    kurs=neu
                punkte.append((x,y));kurse.append((90-math.degrees(kurs))%360)
        if len(punkte)==1:punkte.append((x,y));kurse.append(start[2]%360)
        ergebnis.append(DubinsPfad(familie,sum(segmente),punkte,kurse,segmente))
    return sorted(ergebnis,key=lambda p:p.laenge_m)
