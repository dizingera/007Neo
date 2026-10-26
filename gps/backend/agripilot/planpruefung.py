"""Begrenzte, rein lesende Plausibilitätsprüfung lokaler Saatplan-XY-Geometrie.

Keine Freigabe zum autonomen Fahren. Es werden weder Bedeckungsflächen noch
Maschinenhüllen oder automatisch angenommene Verbindungen zwischen Spuren geprüft.
"""
import math

MAX_SEGMENTE = 1800
MAX_VERGLEICHE = 120000
MAX_PUNKTE = 4000
MAX_BEFUNDE = 100


def _zahl(wert):
    if isinstance(wert, bool):
        return None
    try:
        zahl = float(wert)
        return zahl if math.isfinite(zahl) else None
    except (TypeError, ValueError, OverflowError):
        return None


def _punkt(wert):
    if not isinstance(wert, (list, tuple)) or len(wert) != 2:
        return None
    x, y = (_zahl(v) for v in wert)
    return (x, y) if x is not None and y is not None and max(abs(x), abs(y)) <= 1e9 else None


def _abstand(p, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    q = dx * dx + dy * dy
    t = max(0., min(1., ((p[0]-a[0])*dx + (p[1]-a[1])*dy)/q)) if q else 0.
    return math.hypot(p[0]-a[0]-t*dx, p[1]-a[1]-t*dy)


def _innen(p, ring):
    innen = False
    for a, b in zip(ring, ring[1:] + ring[:1]):
        if _abstand(p, a, b) <= .02:
            return True
        if (a[1] > p[1]) != (b[1] > p[1]):
            if p[0] < a[0] + (p[1]-a[1])*(b[0]-a[0])/(b[1]-a[1]):
                innen = not innen
    return innen


def _kreuzung(a, b, c, d):
    ux, uy, vx, vy = b[0]-a[0], b[1]-a[1], d[0]-c[0], d[1]-c[1]
    det = ux*vy-uy*vx
    if abs(det) < 1e-10:
        return None
    t = ((c[0]-a[0])*vy-(c[1]-a[1])*vx)/det
    s = ((c[0]-a[0])*uy-(c[1]-a[1])*ux)/det
    # Berührung an einem Spurende ist keine durchgehende Kreuzung.
    ta = .02/max(math.hypot(ux, uy), .02)
    tb = .02/max(math.hypot(vx, vy), .02)
    if ta < t < 1-ta and tb < s < 1-tb:
        return (a[0]+t*ux, a[1]+t*uy)
    return None


def _eckkreuzung(a, b, p, nachbarn):
    """Eine echte Durchquerung an einer Ecke, keine tangentiale Berührung."""
    if nachbarn is None or _abstand(p, a, b) > 1e-7:
        return None
    if min(math.dist(p, a), math.dist(p, b)) <= .02:
        return None
    dx, dy = b[0]-a[0], b[1]-a[1]
    seiten = [dx*(q[1]-p[1])-dy*(q[0]-p[0]) for q in nachbarn]
    return p if seiten[0]*seiten[1] < -1e-10 else None


def _parallel(a, b, c, d, breite):
    la, lb = math.dist(a, b), math.dist(c, d)
    if min(la, lb) < max(1., breite):
        return None
    u = ((b[0]-a[0])/la, (b[1]-a[1])/la)
    v = ((d[0]-c[0])/lb, (d[1]-c[1])/lb)
    if abs(u[0]*v[1]-u[1]*v[0]) > math.sin(math.radians(3)):
        return None
    proj = [(p[0]-a[0])*u[0]+(p[1]-a[1])*u[1] for p in (c, d)]
    lo, hi = max(0., min(proj)), min(la, max(proj))
    if hi-lo < max(1., breite):
        return None
    # Beide Enden des überlappenden Abschnitts müssen deutlich zu eng sein.
    punkte = [(a[0]+t*u[0], a[1]+t*u[1]) for t in (lo, hi)]
    abstand = max(_abstand(p, c, d) for p in punkte)
    if abstand < max(.05, breite-max(.15, breite*.05)):
        return punkte, abstand
    return None


def pruefen(plan: dict, feld: dict, breite_m: float) -> dict:
    """Liefert ok, befunde, hinweis, vollstaendig; verändert Eingaben nicht.

    ok bedeutet nur: keine Befunde UND Prüfung im angegebenen Umfang vollständig.
    severity: 'warnung' oder 'fehler'; punkte sind lokale XY-Meterpaare.
    Optionale Maße: plan['radius_m'] bzw. feld['headland']['radius_m'].
    Saatplan enthält keine Wenderouten: kurze Hauptfeldstücke sind Hinweise,
    keine Behauptung, dass eine bestimmte Wende unmöglich wäre.
    """
    befunde, hinweise, gesehen = [], [], set()
    vollstaendig = True
    def melden(art, text, punkte=(), severity='warnung'):
        nonlocal vollstaendig
        marker = tuple((round(p[0], 2), round(p[1], 2)) for p in punkte)
        key = (art, marker)
        if key in gesehen:
            return
        gesehen.add(key)
        if len(befunde) >= MAX_BEFUNDE:
            vollstaendig = False
            return
        befunde.append(dict(art=art, text=text, punkte=[list(p) for p in marker], severity=severity))
    if not isinstance(plan, dict) or not isinstance(feld, dict):
        return dict(ok=False, befunde=[dict(art='daten', text='Plan und Feld müssen Datensätze sein.', punkte=[], severity='fehler')], hinweis='Keine Sicherheitsgarantie.', vollstaendig=False)
    breite = _zahl(breite_m)
    if breite is None or breite <= 0:
        breite = None
        hinweise.append('Arbeitsbreite fehlt oder ist ungültig; Abstandsprüfung entfällt.')
        vollstaendig = False
    raw = feld.get('boundary', [])
    ring = []
    if isinstance(raw, (list, tuple)) and len(raw) <= MAX_PUNKTE:
        ring = [_punkt(p) for p in raw]
        if any(p is None for p in ring):
            ring = []
        else:
            ring = [p for i, p in enumerate(ring) if not i or p != ring[i-1]]
            if len(ring) > 1 and ring[-1] == ring[0]:
                ring.pop()
    if len(set(ring)) < 3 or abs(sum(a[0]*b[1]-b[0]*a[1] for a,b in zip(ring, ring[1:]+ring[:1]))) < 1e-8:
        ring = []
        vollstaendig = False
        hinweise.append('Feldpolygon fehlt, ist ungültig oder überschreitet das Prüflimit.')
    hd = feld.get('headland')
    radius = _zahl(plan.get('radius_m', hd.get('radius_m') if isinstance(hd, dict) else None))
    radius = radius if radius and radius > 0 else None
    segmente, gepruefte_punkte, ecken = [], 0, {}
    def segment(a, b, art, nr):
        nonlocal vollstaendig
        if len(segmente) >= MAX_SEGMENTE:
            vollstaendig = False
            return
        pa, pb = _punkt(a), _punkt(b)
        if pa is None or pb is None:
            melden('daten', 'Spur enthält ungültige Koordinaten.', severity='fehler')
            vollstaendig = False
            return
        if math.dist(pa, pb) < 1e-6:
            return
        segmente.append((pa, pb, art, nr))
        if art == 'H' and math.dist(pa, pb) < (2*radius if radius else (breite*.5 if breite else 0)):
            melden('kurzes_stueck', 'Kurzes Hauptfeldstück: Einfahrt und Übergang vor Ort prüfen.', [pa, pb])
    for art, key in (('H', 'spuren'), ('V', 'ringe')):
        eintraege = plan.get(key, [])
        if not isinstance(eintraege, (list, tuple)):
            melden('daten', 'Spurliste ist ungültig.', severity='fehler')
            vollstaendig = False
            continue
        for nr, spur in enumerate(eintraege[:MAX_SEGMENTE]):
            if not isinstance(spur, dict):
                melden('daten', 'Spurdatensatz ist ungültig.', severity='fehler')
                vollstaendig = False
                continue
            teile = spur.get('stuecke', []) if art == 'H' else spur.get('punkte', [])
            if not isinstance(teile, (list, tuple)) or len(teile) > MAX_PUNKTE:
                vollstaendig = False
                continue
            if art == 'H':
                if len(teile) > MAX_SEGMENTE:
                    vollstaendig = False
                for paar in teile[:MAX_SEGMENTE]:
                    if isinstance(paar, (list, tuple)) and len(paar) == 2:
                        segment(*paar, art, nr)
                    else:
                        melden('daten', 'Hauptfeldstück benötigt zwei Punkte.', severity='fehler')
                        vollstaendig = False
            else:
                punkte = [_punkt(p) for p in teile]
                if any(p is None for p in punkte) or len(set(punkte)) < 3:
                    melden('daten', 'Vorgewendering ist ungültig.', severity='fehler')
                    vollstaendig = False
                    continue
                # Explizit geschlossene Ringe nicht ein zweites Mal schließen.
                punkte = [p for i,p in enumerate(punkte) if not i or p != punkte[i-1]]
                if punkte[-1] == punkte[0]:
                    punkte.pop()
                for i,p in enumerate(punkte):
                    ecken[(nr,p)] = (punkte[i-1], punkte[(i+1)%len(punkte)])
                for a,b in zip(punkte, punkte[1:]+punkte[:1]):
                    segment(a,b,art,nr)
            if len(segmente) >= MAX_SEGMENTE:
                vollstaendig = False
                break
        if len(eintraege) > MAX_SEGMENTE:
            vollstaendig = False
    if not segmente:
        melden('daten', 'Keine prüfbaren Fahrspuren vorhanden.', severity='fehler')
    # Begrenzte Stichprobe: Enden und Mitte; keine erfundenen Verbindungssegmente.
    for a,b,art,nr in segmente:
        if not ring:
            break
        if gepruefte_punkte + 3*len(ring) > MAX_VERGLEICHE:
            vollstaendig = False
            break
        for p in (a, ((a[0]+b[0])/2, (a[1]+b[1])/2), b):
            if not _innen(p, ring):
                melden('ausserhalb', 'Geprüfter Spurpunkt liegt außerhalb der Feldgrenze.', [p], 'fehler')
        gepruefte_punkte += 3*len(ring)
    vergleiche = 0
    for i,(a,b,art,nr) in enumerate(segmente):
        for c,d,art2,nr2 in segmente[i+1:]:
            vergleiche += 1
            if vergleiche > MAX_VERGLEICHE:
                vollstaendig = False
                break
            if art != art2:
                p = _kreuzung(a,b,c,d) or _eckkreuzung(a,b,c,ecken.get((nr2,c)))
                if p is not None:
                    melden('kreuzung', 'Hauptfeldspur kreuzt eine Vorgewenderunde.', [p], 'fehler')
            elif art == 'V' and nr != nr2 and breite:
                nah = _parallel(a,b,c,d,breite)
                if nah:
                    punkte, abstand = nah
                    melden('vorgewende_abstand', f'Parallele Vorgewenderunden liegen nur {abstand:.2f} m auseinander (Arbeitsbreite {breite:g} m).', punkte)
        if vergleiche > MAX_VERGLEICHE:
            break
    # Bereits berechnete Rand-/Reststreifenhinweise behalten; keine zweite Planung.
    konflikte = plan.get('konflikte', [])
    if konflikte is None:
        konflikte = []
    if not isinstance(konflikte, (list, tuple)):
        melden('daten', 'Konfliktliste ist ungültig.', severity='fehler')
        vollstaendig = False
    else:
        if len(konflikte) > MAX_BEFUNDE:
            vollstaendig = False
        for k in konflikte[:MAX_BEFUNDE]:
            if not isinstance(k, dict) or not isinstance(k.get('text'), str):
                melden('daten', 'Plankonflikt ist ungültig.', severity='fehler')
                vollstaendig = False
                continue
            raw_punkte = k.get('punkte', [])
            if raw_punkte is None:
                raw_punkte = []
            if not isinstance(raw_punkte, (list, tuple)):
                pts = []
                vollstaendig = False
            else:
                pts = [_punkt(p) for p in raw_punkte[:100]]
                if len(raw_punkte) > 100 or any(p is None for p in pts):
                    vollstaendig = False
            melden('plan_'+str(k.get('art') or 'hinweis')[:30], k['text'][:1000], [p for p in pts if p is not None])
    planhinweise = plan.get('hinweise', [])
    if planhinweise is None:
        planhinweise = []
    if not isinstance(planhinweise, (list, tuple)):
        melden('daten', 'Hinweisliste ist ungültig.', severity='fehler')
        vollstaendig = False
    else:
        if len(planhinweise) > 30:
            vollstaendig = False
        for h in planhinweise[:30]:
            if isinstance(h, str):
                hinweise.append(h[:1000])
            else:
                vollstaendig = False
    if not vollstaendig:
        hinweise.append('Prüfung unvollständig: fehlende Daten oder Aufwands-/Befundlimit erreicht.')
    hinweise.append('Grenzprüfung als Stichprobe (Enden/Mitten); Restflächen und Maschinenhülle nicht berechnet. Keine Sicherheitsgarantie.')
    hinweise.append('Keine vollständigen Wendeverbindungen im Saatplan; Übergänge nicht auf Fahrbarkeit freigegeben.' + (' Mindestwendemaß fehlt.' if radius is None else ' Kurze Stücke anhand des angegebenen Radius markiert.'))
    return dict(ok=not befunde and vollstaendig, befunde=befunde, hinweis=' '.join(hinweise), vollstaendig=vollstaendig)
