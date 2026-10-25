import json
import tempfile
import time
import unittest
import uuid
from unittest.mock import patch
from test_tablet_lenkung import geraet
from agripilot import geo, gespann
from agripilot.storage import Storage
from agripilot.nmea import Fix

class FeldwerkzeugeAPI(unittest.TestCase):
    def setUp(self):
        self.tmp=self.enterContext(tempfile.TemporaryDirectory())
        self.c=self.enterContext(geraet(self.tmp,'client','a'))
        self.app=self.c.app.state.app;self.e=self.app.engine;self.s=self.app.store
        self.f=self.s.save_field(dict(name='Testfeld',datum_lat=50,datum_lon=10,boundary=[[0,0],[100,0],[100,100],[0,100]]))
        self.e.load_field(self.f['id'])
        self.e.fix=Fix(lat=50,lon=10,fix_quality=4,speed_ms=0,received_at=time.time())
        self.url='/api/fields/'+self.f['id']+'/arbeitsgrenze'
    def test_arbeitsgrenze_speichern_undo_original_und_altstand(self):
        pts=[[5,5],[95,5],[95,95],[5,95]]
        r=self.c.post(self.url,json=dict(stand=self.f['updated_at'],punkte=pts))
        self.assertEqual(r.status_code,200,r.text)
        neu=r.json()['data'];self.assertEqual(neu['boundary'],self.f['boundary']);self.assertEqual(neu['working_boundary'],pts)
        self.assertEqual(self.e.state()['field']['working_boundary'],pts)
        self.assertEqual(self.c.post(self.url,json=dict(stand=self.f['updated_at'],punkte=[])).status_code,409)
        self.assertEqual(self.c.post(self.url,json=dict(stand=neu['updated_at'],punkte=[])).status_code,200)
        self.assertEqual(self.s.get_field(self.f['id'])['boundary'],self.f['boundary'])
    def test_feldgrenze_selbst_aendern_darf_ueber_die_alte_hinaus_und_sichert_sie(self):
        # Erst eine Arbeitsgrenze - sie fällt mit der neuen Feldgrenze weg.
        neu=self.c.post(self.url,json=dict(stand=self.f['updated_at'],punkte=[[5,5],[95,5],[95,95],[5,95]])).json()['data']
        # Die Südostecke 5 m nach außen gezogen und als Feldgrenze gespeichert.
        pts=[[0,0],[105,-5],[100,100],[0,100]]
        r=self.c.post(self.url,json=dict(stand=neu['updated_at'],punkte=pts,ziel='feldgrenze'))
        self.assertEqual(r.status_code,200,r.text)
        feld=r.json()['data']
        self.assertEqual(feld['boundary'],pts)
        self.assertEqual(feld['working_boundary'],[])
        self.assertAlmostEqual(feld['area_ha'],geo.polygon_area([tuple(p) for p in pts])/10000,places=4)
        verlauf=self.c.get('/api/fields/'+self.f['id']+'/grenzverlauf').json()['data']['verlauf']
        self.assertEqual(verlauf[0]['boundary'],self.f['boundary'])          # die alte ist gesichert
        self.assertEqual(verlauf[0]['working_boundary'],[[5,5],[95,5],[95,95],[5,95]])
        self.assertEqual(self.e.state()['field']['boundary'],pts)
        # Selbstschnitt bleibt auch hier verboten, und ohne Punkte geht nichts.
        kaputt=[[0,0],[100,100],[0,100],[100,0]]
        self.assertEqual(self.c.post(self.url,json=dict(stand=feld['updated_at'],punkte=kaputt,ziel='feldgrenze')).status_code,400)
        self.assertEqual(self.c.post(self.url,json=dict(stand=feld['updated_at'],punkte=[],ziel='feldgrenze')).status_code,400)
        self.assertEqual(self.s.get_field(self.f['id'])['boundary'],pts)

    def test_arbeitsgrenze_darf_ueber_die_feldgrenze_hinaus(self):
        # Kein Limit (Andy, 24.10.2026): auch 30 m über die Feldgrenze hinaus wird gespeichert.
        weit=[[0,-30],[100,-30],[100,100],[0,100]]
        r=self.c.post(self.url,json=dict(stand=self.f['updated_at'],punkte=weit))
        self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(r.json()['data']['working_boundary'],weit)
        self.assertEqual(r.json()['data']['boundary'],self.f['boundary'])   # die Feldgrenze bleibt


    def test_neue_feldgrenze_verwirft_den_alten_saatplan_und_der_verlauf_haelt_zehn(self):
        self.s.set_setting('saatplan:'+self.f['id'],{'vorgabe':{'start':'0a','breite_m':3},'plan':{'spuren':[]}})
        stand=self.f['updated_at']
        for k in range(12):
            pts=[[0,0],[100+k,0],[100,100],[0,100]]
            r=self.c.post(self.url,json=dict(stand=stand,punkte=pts,ziel='feldgrenze'))
            self.assertEqual(r.status_code,200,r.text); stand=r.json()['data']['updated_at']
        gespeichert=self.s.get_setting('saatplan:'+self.f['id'])
        self.assertIsNone(gespeichert['plan'])                                  # alte Form, neu planen
        self.assertEqual(gespeichert['vorgabe']['start'],'0a')                  # Vorgaben bleiben
        verlauf=self.c.get('/api/fields/'+self.f['id']+'/grenzverlauf').json()['data']['verlauf']
        self.assertEqual(len(verlauf),10)
        self.assertEqual(verlauf[0]['boundary'],[[0,0],[110,0],[100,100],[0,100]])   # der vorletzte Stand

    def test_ungueltiger_ring_veraendert_nichts(self):
        r=self.c.post(self.url,json=dict(stand=self.f['updated_at'],punkte=[[0,0],[100,100],[0,100],[100,0]]))
        self.assertEqual(r.status_code,400,r.text);self.assertEqual(self.s.get_field(self.f['id']),self.f)
    def test_fahrt_beginnt_waehrend_pruefung_speichert_nicht(self):
        from agripilot import feldwerkzeuge
        original=feldwerkzeuge.grenze_pruefen
        def pruefen(*a):
            p=original(*a);self.e.fix.speed_ms=2;return p
        with patch('agripilot.server.feldwerkzeuge.grenze_pruefen',side_effect=pruefen):
            r=self.c.post(self.url,json=dict(stand=self.f['updated_at'],punkte=[[2,2],[98,2],[98,98],[2,98]]))
        self.assertEqual(r.status_code,400,r.text);self.assertEqual(self.s.get_field(self.f['id']),self.f)
    def test_alter_sync_setzt_unpassende_arbeitsgrenze_zurueck(self):
        self.s.save_field({**self.f,'working_boundary':[[5,5],[95,5],[95,95],[5,95]]})
        alt={**self.f,'boundary':json.dumps([[0,0],[50,0],[50,50],[0,50]]),'updated_at':time.time()+5}
        alt.pop('working_boundary',None)
        self.s.apply_remote('fields',[alt])
        self.assertEqual(self.s.get_field(self.f['id'])['working_boundary'],[])
    def spur(self):
        l=self.s.save_line(dict(field_id=self.f['id'],name='Saat',mode='ab',points=[[10,0],[10,100]],spacing_m=3,nudge_m=.4,saison=2026,fahrgasse_m=24))
        self.e.load_line(l['id']);return l
    def test_originalspur_keine_neue_aussaat_und_kontextbindung(self):
        l=self.spur();payload=dict(field_id=self.f['id'],line_id=l['id'],nudge_m=.4)
        self.assertEqual(self.c.post('/api/guidance/original',json=payload).status_code,200)
        neu=self.s.get_line(l['id']);self.assertEqual(neu['nudge_m'],0);self.assertEqual(neu['aussaat_at'],l['aussaat_at'])
        self.assertEqual(self.c.post('/api/guidance/original',json=payload).status_code,400)
    def test_originalspur_fremde_geometrie_und_fahrt_abgewiesen(self):
        l=self.spur();payload=dict(field_id=self.f['id'],line_id=l['id'],nudge_m=.4)
        self.e.fix.speed_ms=2
        self.assertEqual(self.c.post('/api/guidance/original',json=payload).status_code,400)
        self.e.fix.speed_ms=0;self.s.save_line({**l,'points':[[20,0],[20,100]]})
        self.assertEqual(self.c.post('/api/guidance/original',json=payload).status_code,400)
        self.assertEqual(self.s.get_line(l['id'])['nudge_m'],.4)
    def arbeit(self,alt=False):
        job=self.s.start_job(self.f['id'],'a',operation='Saat')
        plane=geo.LocalPlane(50,10);points=[]
        for i in range(40):
            lat,lon=plane.to_wgs(10,10+i*2)
            p=(i,lat,lon,0,2,0,4,0)
            points.append(p if alt else (*p,lat,lon,1,0))
        self.s.add_track_points(job['id'],points)
        self.s.update_job(job['id'],ended_at=time.time(),reference_spacing_m=3)
        return job
    def test_arbeit_zu_referenz_kuerzen_idempotent_und_sync(self):
        saison=self.spur();j=self.arbeit();url='/api/jobs/'+j['id']+'/referenz'
        r=self.c.get(url);self.assertEqual(r.status_code,200,r.text);p=r.json()['data']
        payload=dict(stand=p['stand'],segment=0,name='Geprüfte Saatlinie',abstand_m=3,anfang_m=5,ende_m=7,request_id=str(uuid.uuid4()))
        r=self.c.post(url,json=payload);self.assertEqual(r.status_code,200,r.text);l=r.json()['data']
        self.assertAlmostEqual(l['points'][0][1],15,places=3);self.assertAlmostEqual(l['points'][-1][1],81,places=3)
        r2=self.c.post(url,json=payload);self.assertEqual(r2.status_code,200,r2.text);self.assertEqual(r2.json()['data']['id'],l['id'])
        self.assertEqual(self.s.season_line(self.f['id'])['id'],saison['id'])
        with tempfile.TemporaryDirectory() as d:
            other=Storage(d+'/b.db')
            try:
                other.apply_remote('fields',self.s.changes_since('fields',0));other.apply_remote('lines',self.s.changes_since('lines',0))
                self.assertEqual(other.get_line(l['id'])['points'],l['points']);self.assertEqual(other.season_line(self.f['id'])['id'],saison['id'])
            finally:other.close()
    def test_altaufzeichnung_braucht_bewusste_bestaetigung(self):
        j=self.arbeit(True);url='/api/jobs/'+j['id']+'/referenz';p=self.c.get(url).json()['data']
        payload=dict(stand=p['stand'],segment=0,name='Altspur',abstand_m=3,request_id=str(uuid.uuid4()))
        self.assertTrue(p['segmente'][0]['altaufzeichnung']);self.assertEqual(self.c.post(url,json=payload).status_code,400)
        payload['antenne_bestaetigt']=True;self.assertEqual(self.c.post(url,json=payload).status_code,200)
    def test_abgelehnte_masse_bleiben_nicht_gespeichert(self):
        self.e.list_profiles();self.e.list_traktoren()
        for fn in (lambda:self.e.update_traktor({'traktor_breite_m':-1}),lambda:self.e.save_traktor_as('Schlecht',{'traktor_breite_m':-1}),lambda:self.e.save_profile_as('Schlecht',{'geraet_breite_m':float('nan')})):
            vorher=list(self.s.db.execute('SELECT key,value FROM settings'))
            with self.assertRaises(ValueError):fn()
            self.assertEqual([tuple(r) for r in self.s.db.execute('SELECT key,value FROM settings')],[tuple(r) for r in vorher])
    def test_wendestart_prueft_aktuelle_aussenkante(self):
        self.e.field=self.s.save_field({**self.f,'boundary':[[-100,-100],[2.2,-100],[2.2,100],[-100,100]]})
        self.e.update_profile(dict(width_m=3,antenna_forward_m=0,tool_trailing_m=0,traktor_breite_m=2,traktor_vorn_m=4,traktor_hinten_m=1,geraet_breite_m=3,geraet_vorn_m=1,geraet_hinten_m=1))
        self.e.update_headland(dict(muster='u',richtung='links',radius_m=6,ueberspringen=4,grenzabstand_m=.5))
        self.e.position=self.e.tool_position=(0.,0.);self.e.heading=0
        self.e.implement_heading.value=0
        plan=self.c.post('/api/turn/plan',json={});self.assertEqual(plan.status_code,200,plan.text)
        self.assertTrue(plan.json()['data']['im_feld'])
        self.e.position=self.e.tool_position=(.9,0.)
        r=self.c.post('/api/turn/start');self.assertEqual(r.status_code,400,r.text);self.assertIsNone(self.e.turn)

    def test_saison_randspur_bleibt_bei_neuer_arbeitsgrenze_und_randspuren_erhalten(self):
        self.e.randspuren_anlegen(self.f['id'])
        rand=next(l for l in self.s.list_lines(self.f['id']) if l['name'].startswith('Rand West'))
        rand=self.e.update_line(rand['id'],dict(saisonspur=True,saison=2026))
        self.e.load_line(rand['id'])
        vorher=(self.e.line.rand_innen,self.e.line.raster_versatz(),self.e.line.fahrgassen_versatz())
        self.assertNotEqual(vorher[0],0)
        r=self.c.post(self.url,json=dict(stand=self.f['updated_at'],punkte=[[10,10],[90,10],[90,90],[10,90]]))
        self.assertEqual(r.status_code,200,r.text)
        self.e.load_line(rand['id']);self.e.randspuren_anlegen(self.f['id'])
        self.assertEqual(self.s.get_line(rand['id']),rand)
        self.assertEqual((self.e.line.rand_innen,self.e.line.raster_versatz(),self.e.line.fahrgassen_versatz()),vorher)
        self.assertTrue(any(l['points']!=rand['points'] and l['name'].startswith('Rand West') for l in self.s.list_lines(self.f['id'])))
        with tempfile.TemporaryDirectory() as d, geraet(d,'client','b') as b:
            other=b.app.state.app.store;motor=b.app.state.app.engine
            other.apply_remote('fields',self.s.changes_since('fields',0));other.apply_remote('lines',self.s.changes_since('lines',0))
            motor.load_field(self.f['id']);motor.load_line(rand['id'])
            self.assertEqual((motor.line.rand_innen,motor.line.raster_versatz(),motor.line.fahrgassen_versatz()),vorher)

    def test_alte_datenbank_erhaelt_dauerhaften_randbezug_ohne_neue_aussaat(self):
        with tempfile.TemporaryDirectory() as d:
            pfad=d+'/alt.db';s=Storage(pfad)
            try:
                f=s.save_field(self.f)
                l=s.save_line(dict(field_id=f['id'],name='Rand West 100 m',mode='ab',points=[[0,0],[0,100]],spacing_m=3,saison=2026,fahrgasse_m=24))
                s.db.execute('ALTER TABLE lines DROP COLUMN rand_innen');s.db.commit()
            finally:s.close()
            s=Storage(pfad)
            try:
                neu=s.get_line(l['id']);self.assertNotEqual(neu['rand_innen'],0)
                self.assertEqual(neu['updated_at'],l['updated_at']);self.assertEqual(neu['aussaat_at'],l['aussaat_at'])
                alt=s.changes_since('lines',0)[0];alt.pop('rand_innen');alt['updated_at']+=2
                s.apply_remote('lines',[alt]);self.assertEqual(s.get_line(l['id'])['rand_innen'],neu['rand_innen'])
            finally:s.close()

    def turn_vorbereiten(self):
        self.e.field=self.s.save_field({**self.f,'boundary':[[-100,-100],[100,-100],[100,100],[-100,100]]})
        self.e.update_profile(dict(width_m=3,antenna_forward_m=0,tool_trailing_m=0,traktor_breite_m=2,traktor_vorn_m=4,traktor_hinten_m=1,geraet_breite_m=3,geraet_vorn_m=1,geraet_hinten_m=1))
        self.e.update_headland(dict(muster='u',richtung='links',radius_m=6,ueberspringen=4))
        self.e.position=self.e.tool_position=(0.,0.);self.e.heading=90;self.e.implement_heading.value=90

    def test_wende_braucht_unveraenderte_spur_auch_waehrend_berechnung(self):
        self.spur();self.turn_vorbereiten()
        original=gespann.pruefen
        def pruefen(*a,**kw):
            r=original(*a,**kw);self.e.line.nudge_m+=1;return r
        with patch('agripilot.engine.gespann.pruefen',side_effect=pruefen):
            r=self.c.post('/api/turn/plan',json={})
        self.assertEqual(r.status_code,409,r.text)
        r=self.c.post('/api/turn/plan',json={});self.assertEqual(r.status_code,200,r.text)
        self.e.line.nudge_m+=1
        r=self.c.post('/api/turn/start');self.assertEqual(r.status_code,400,r.text);self.assertIsNone(self.e.turn)

    def test_wendestart_erkennt_geaenderten_geraetekurs_nord(self):
        self.turn_vorbereiten()
        r=self.c.post('/api/turn/plan',json={});self.assertEqual(r.status_code,200,r.text)
        self.assertTrue(r.json()['data']['im_feld'])
        self.e.implement_heading.value=0
        r=self.c.post('/api/turn/start');self.assertEqual(r.status_code,400,r.text)
        self.assertIn('Ausrichtung',r.text);self.assertIsNone(self.e.turn)

    def test_profilmasse_liefern_verstaendlichen_eingabefehler(self):
        r=self.c.post('/api/profile',json=dict(geraet_breite_m=-1))
        self.assertEqual(r.status_code,400,r.text)

    def test_alter_master_abgleich_bewahrt_masse_und_explizite_null_setzt_zurueck(self):
        self.e.config.network.role='master'
        werte={k:2. for k in gespann.MASSE};self.e.update_profile(werte)
        paket=self.e.profile_export()
        for gruppe in ('maschinen','traktoren'):
            for eintrag in paket[gruppe]:
                for k in gespann.MASSE:eintrag.pop(k,None)
                eintrag['stand']=time.time()+10
        self.e.profile_merge(paket)
        self.e.select_profile(self.s.get_setting('vehicle_profile_active'))
        self.assertEqual({k:getattr(self.e.profile,k) for k in gespann.MASSE},werte)
        for gruppe in ('maschinen','traktoren'):
            for eintrag in paket[gruppe]:
                eintrag['geraet_breite_m']=0;eintrag['traktor_breite_m']=0;eintrag['stand']+=1
        self.e.profile_merge(paket)
        self.assertEqual(self.e.profile.geraet_breite_m,0);self.assertEqual(self.e.profile.traktor_breite_m,0)

    def test_zweite_wendeplanung_wird_waehrend_berechnung_abgewiesen(self):
        self.e._plan_laeuft=True
        r=self.c.post('/api/turn/plan',json={})
        self.assertEqual(r.status_code,409,r.text)
