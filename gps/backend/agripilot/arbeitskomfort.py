"""Arbeitsstart und lokale Bedienvorlieben; keine automatische Lenkfreigabe."""
from __future__ import annotations
import hashlib
import json
import math
import time
from dataclasses import asdict
from . import arbeitstag, settings

ZIELE = {'fields','lines','jobs','arbeitstag','machine','lenkung','lenkregler','saatplan','headland'}
VORLAGEN = [{'id': k,'name':v,'operation':v,'traktor_id':'','profile_id':''} for k,v in [('grubbern','Grubbern'),('saeen','Säen'),('maehen','Mähen'),('ernten','Ernten')]]

def vorlieben(store):
    d=store.get_setting('arbeitskomfort') or {}
    return {'favoriten':d.get('favoriten',[]),'zuletzt':d.get('zuletzt',[]),'vorlagen':d.get('vorlagen',VORLAGEN),'schnellzugriffe':d.get('schnellzugriffe',['fields','lines','jobs'])}

def speichern(app,payload):
    e,s=app.engine,app.store
    if not isinstance(payload,dict):raise ValueError('Ungültige Bedienvorlieben')
    d=vorlieben(s)
    if 'favoriten' in payload:
        v=payload['favoriten']
        if not isinstance(v,list) or len(v)>500 or any(not isinstance(k,str) or len(k)>128 for k in v):raise ValueError('Ungültige Favoriten')
        ids={f['id'] for f in s.list_fields()};d['favoriten']=list(dict.fromkeys(k for k in v if k in ids))
    if 'schnellzugriffe' in payload:
        arbeitstag.im_stand(e)
        v=payload['schnellzugriffe']
        if not isinstance(v,list) or not 1<=len(v)<=3 or any(k not in ZIELE for k in v) or len(set(v))!=len(v):raise ValueError('Ein bis drei unterschiedliche Schnellzugriffe auswählen')
        d['schnellzugriffe']=v
    if 'vorlagen' in payload:
        arbeitstag.im_stand(e)
        v=payload['vorlagen']
        if not isinstance(v,list) or len(v)>30:raise ValueError('Höchstens 30 Vorlagen')
        neu=[]
        for x in v:
            if not isinstance(x,dict) or any(not isinstance(x.get(k,''),str) or len(x.get(k,''))>120 for k in ('id','name','operation','traktor_id','profile_id')):raise ValueError('Ungültige Vorlage')
            if not x.get('id') or not x.get('name','').strip() or not x.get('operation','').strip():raise ValueError('Vorlage braucht Namen und Tätigkeit')
            neu.append({k:x.get(k,'').strip() for k in ('id','name','operation','traktor_id','profile_id')})
        if len({x['id'] for x in neu})!=len(neu):raise ValueError('Doppelte Vorlagenkennung')
        d['vorlagen']=neu
    s.set_setting('arbeitskomfort',d)
    return d

def besucht(store,field_id):
    d=vorlieben(store);d['zuletzt']=[field_id]+[x for x in d['zuletzt'] if x!=field_id];d['zuletzt']=d['zuletzt'][:12];store.set_setting('arbeitskomfort',d)

def starten(app,payload):
    e,s=app.engine,app.store
    arbeitstag.im_stand(e)
    if e.job:raise RuntimeError('Erst die laufende Arbeit pausieren oder abschließen')
    field_id=payload.get('field_id');traktor_id=payload.get('traktor_id');profile_id=payload.get('profile_id')
    feld=s.get_field(field_id) if isinstance(field_id,str) else None
    if not feld or feld.get('deleted'):raise ValueError('Feld nicht gefunden')
    t=next((x for x in e.list_traktoren() if x['id']==traktor_id),None)
    p=next((x for x in e.list_profiles() if x['id']==profile_id),None)
    if not t or not p:raise ValueError('Traktor und Gerät auswählen')
    operation=payload.get('operation')
    if not isinstance(operation,str) or not 1<=len(operation.strip())<=120:raise ValueError('Tätigkeit mit höchstens120 Zeichen angeben')
    # Die Auswahl verweist auf vorhandene Maße. Keine Breite aus einer Tätigkeitsvorlage setzen.
    breite=p.get('width_m')
    if not isinstance(breite,(int,float)) or not math.isfinite(breite) or breite<=0:raise ValueError('Arbeitsbreite zuerst beim Gerät prüfen')
    erwartet=payload.get('breite_m')
    if not isinstance(erwartet,(int,float)) or not math.isfinite(erwartet) or abs(erwartet-breite)>.00001:raise ValueError('Gerätebreite wurde geändert – Zusammenfassung erneut prüfen')
    if payload.get('feld_stand')!=feld['updated_at']:raise ValueError('Feld wurde geändert – Arbeitsstart neu öffnen')
    e.traktor_waehlen(traktor_id);e.select_profile(profile_id);e.load_field(field_id)
    job=e.start_job(operation.strip());besucht(s,field_id)
    return {'job':job,'text':'Arbeit gestartet; Lenkung bleibt aus'}

NAMEN={'width_m':'Arbeitsbreite (m)','overlap_m':'Überlappung (m)','antenna_height_m':'Antennenhöhe (m)','antenna_forward_m':'Antenne vor Achse (m)','antenna_right_m':'Antenne rechts (m)','wheelbase_m':'Radstand (m)','name':'Bezeichnung','max_steer_deg':'Maximaler Lenkwinkel (°)','steer_gain':'Lenkschärfe','cerea_p':'Spurkorrektur','cerea_d':'Lenkberuhigung','cerea_i':'Dauerversatz ausgleichen','cerea_kp':'Spurkorrektur','cerea_kd':'Lenkberuhigung','lookahead_m':'Vorausschau (m)','actuator_latency_ms':'Motorvorhalt (ms)','lenk_traegheit_s':'Lenkträgheit (s)','tool_offset_m':'Geräteversatz (m)','tool_trailing_m':'Gerät hinter der Achse (m)'}
def vorschau(app,ziel,payload):
    if ziel not in ('/api/profile','/api/traktor','/api/settings'):raise ValueError('Keine Vorschau für diesen Bedienweg')
    if not isinstance(payload,dict):raise ValueError('Ungültige Änderungen')
    if ziel=='/api/settings':
        vorher=settings.werte(app.config);neu=payload.get('aenderungen',{});labels={k:v.label for k,v in settings.ALLE_FELDER.items()};geheim={k for k,v in settings.ALLE_FELDER.items() if v.typ=='passwort' or any(w in k.lower() for w in ('token','password','passwort'))}
    else:
        vorher=asdict(app.engine.profile) if ziel=='/api/profile' else app.engine.aktiver_traktor()
        if ziel=='/api/traktor':vorher={**asdict(app.engine.profile),**vorher}
        neu=payload;labels=NAMEN;geheim=set()
    if not isinstance(neu,dict) or len(neu)>300:raise ValueError('Ungültige Änderungen')
    diff=[]
    for k,v in neu.items():
        if k not in vorher or vorher[k]==v:continue
        if k in geheim:
            if v=='***':continue
            alt,neu_wert='Geschützt','Wird geändert'
        else:alt,neu_wert=vorher[k],v
        diff.append({'schluessel':k,'label':labels.get(k,k),'vorher':alt,'nachher':neu_wert})
    basis={'ziel':ziel,'stand':vorher,'traktor_id':app.engine.aktiver_traktor()['id'],'profile_id':app.store.get_setting('vehicle_profile_active')}
    stand=hashlib.sha256(json.dumps(basis,sort_keys=True,default=str,allow_nan=False).encode()).hexdigest()
    return {'aenderungen':diff,'stand':stand,'text':'Die Werte werden erst nach Übernehmen gespeichert.'}

def vorschau_pruefen(app,ziel,payload):
    if '_vorschau' in payload and vorschau(app,ziel,payload)['stand']!=payload['_vorschau']:raise ValueError('Einstellungen wurden inzwischen geändert – Vorschau erneut öffnen')
