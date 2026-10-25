"""Gegenproben für Grenzabschaltung, Flächenmaß und Gespannhindernisse."""
import math
import unittest
from unittest import mock
from types import SimpleNamespace
from agripilot.coverage import CoverageMap, build_sections
from agripilot.guidance import VehicleProfile
from agripilot import hindernisse, gespann


def profil(**kw):
    return VehicleProfile(**dict(dict(width_m=1, tool_offset_m=3,
        traktor_breite_m=3, traktor_vorn_m=4, traktor_hinten_m=1,
        geraet_breite_m=1, geraet_vorn_m=.5, geraet_hinten_m=.5), **kw))


class GrenzRegression(unittest.TestCase):
    def schalten(self, ring, **kw):
        s=build_sections(10,1)
        CoverageMap().update_auto_sections((0,0),0,s,boundary=ring,
            speed_ms=1,look_ahead_m=0,vorlauf_an_s=0,vorlauf_aus_s=0,**kw)
        return s[0].enabled

    def test_aussenkante_schaltet_ab_beide_seiten(self):
        for links,rechts in ((-20,4.8),(-4.8,20)):
            self.assertFalse(self.schalten([(links,-20),(rechts,-20),(rechts,20),(links,20)]))

    def test_schmale_konkave_bucht_zwischen_proben(self):
        ring=[(-20,-20),(20,-20),(20,20),(1.3,20),(1.3,-1),(.7,-1),(.7,20),(-20,20)]
        self.assertFalse(self.schalten(ring))

    def test_genau_auf_grenze_erlaubt_und_leere_grenze_zu(self):
        self.assertTrue(self.schalten([(-5,-20),(5,-20),(5,20),(-5,20)]))
        self.assertFalse(self.schalten([]))

    def test_vorgewendeabstand_auch_zwischen_proben(self):
        ring=[(-20,-20),(20,-20),(20,20),(1.3,20),(1.3,.1),(.7,.1),(.7,20),(-20,20)]
        self.assertFalse(self.schalten(ring,vorgewende_m=.3))


class FlaechenRegression(unittest.TestCase):
    def test_schrage_bewegung_ist_parallelogramm_ohne_ueberlappung(self):
        for cs in (.5,.05):
            for dx in (-1,1):
                c=CoverageMap(cs)
                self.assertAlmostEqual(c.add_swath((0,0),(dx,1),0,build_sections(3,1)),3)
                self.assertAlmostEqual(c.overlap_m2,0)
                self.assertGreaterEqual(c.overlap_percent,0)

    def test_rein_seitlich_keine_fiktive_flaeche(self):
        c=CoverageMap()
        self.assertAlmostEqual(c.add_swath((0,0),(1,0),0,build_sections(3,1)),0)
        self.assertEqual(c.area_m2,0)

    def test_wiederholung_erkennt_echte_ueberdeckung_symmetrisch(self):
        for dx in (-1,1):
            c=CoverageMap(.05);s=build_sections(3,1)
            c.add_swath((0,0),(dx,1),0,s)
            c.add_swath((0,0),(dx,1),0,s)
            self.assertAlmostEqual(c.applied_area_m2,6)
            self.assertAlmostEqual(c.overlap_m2,3)
            self.assertAlmostEqual(c.overlap_percent,50)

    def test_merge_und_neuladen_erhalten_overlap(self):
        c=CoverageMap(.05);s=build_sections(3,1)
        c.add_swath((0,0),(1,1),0,s)
        other=CoverageMap(.05);other.add_swath((10,0),(11,1),0,s)
        c.merge(other)
        self.assertAlmostEqual(c.overlap_m2,0)
        loaded=CoverageMap.unpack(c.pack());loaded.applied_area_m2+=c.overlap_m2
        self.assertAlmostEqual(loaded.overlap_m2,0)


class HindernisRegression(unittest.TestCase):
    def test_traktor_trotz_geraeteversatz_erkannt_live_und_route(self):
        hd=[dict(id='mast',name='Mast',x=0,y=2,radius_m=.2)]
        p=profil()
        self.assertEqual(hindernisse.warnung((3,0),0,1,1,hd,profil=p)['id'],'mast')
        self.assertEqual(hindernisse.auf_route([(3,0),(3,10)],1,hd,profil=p,startkurs=0)['id'],'mast')

    def test_frontschneidwerk_und_nachlauf_erkannt(self):
        for p,h in ((profil(tool_offset_m=0,tool_trailing_m=-5.4,width_m=6,geraet_breite_m=6),
                      dict(id='front',x=2,y=5.4,radius_m=.1)),
                    (profil(tool_offset_m=0,trailed=True,hitch_length_m=8),
                      dict(id='heck',x=0,y=-8,radius_m=.1))):
            self.assertIsNotNone(hindernisse.warnung((0,0),0,1,p.width_m,[h],profil=p,geraetekurs=0))

    def test_eingeknicktes_geraet_und_fernes_hindernis(self):
        p=profil(tool_offset_m=0,trailed=True,hitch_length_m=8)
        hd=[dict(id='geraet',x=-8,y=0,radius_m=.1)]
        self.assertIsNotNone(hindernisse.warnung((0,0),0,1,1,hd,profil=p,geraetekurs=90))
        self.assertIsNone(hindernisse.warnung((0,0),0,1,1,[dict(id='fern',x=100,y=100,radius_m=.1)],profil=p))

    def test_fehlende_masse_geben_keine_huellenfreigabe(self):
        r=hindernisse.auf_route([(0,0),(0,10)],3,[],profil=VehicleProfile())
        self.assertIsNotNone(r)
        self.assertFalse(r['vollstaendig'])

    def test_engine_live_reicht_echtes_profil_weiter(self):
        from agripilot.engine import Engine
        e=Engine.__new__(Engine);e.field=None;e._hindernisse_feld=None
        e.hindernis_warnung=None;e.steer_position=(3,0);e.position=(0,0);e.heading=0
        e.profile=profil();e._hindernisse=[];e.heading_filter=SimpleNamespace(rueckwaerts=False);e.implement_heading=SimpleNamespace(value=90);e.note=lambda _:None
        with mock.patch('agripilot.engine.hindernisse_module.warnung',return_value=None) as fn:
            e._hindernisse_pruefen(SimpleNamespace(speed_ms=1))
        self.assertIs(fn.call_args.kwargs.get('profil'),e.profile)
        self.assertEqual(fn.call_args.kwargs.get('geraetekurs'),90)


class WeitereRegression(unittest.TestCase):
    def test_resume_mit_gespeicherter_ueberlappung_und_weiterfahrt(self):
        c=CoverageMap(.05);sec=build_sections(3,1)
        for _ in range(2):c.add_swath((0,0),(1,1),0,sec)
        loaded=CoverageMap.unpack(c.pack())
        loaded.applied_area_m2+=c.overlap_m2  # bestehender Storage-/Engine-Wiederaufnahmeweg
        self.assertAlmostEqual(loaded.overlap_m2,3)
        loaded.add_swath((10,0),(11,1),0,sec)
        self.assertAlmostEqual(loaded.overlap_m2,3)
        loaded.add_swath((10,0),(11,1),0,sec)
        self.assertAlmostEqual(loaded.overlap_m2,6)

    def test_merge_eingelesenes_fremdraster_ohne_erfundene_history(self):
        a=CoverageMap(.05);sec=build_sections(3,1)
        a.add_swath((0,0),(1,1),0,sec)
        b=CoverageMap.unpack(a.pack())  # Paket hat keine Ausbringungshistorie
        a.merge(b)
        self.assertAlmostEqual(a.overlap_m2,b.area_m2)
        self.assertGreaterEqual(a.overlap_percent,0)
        self.assertLessEqual(a.overlap_percent,100)

    def test_leere_hindernisliste_rechnet_keine_huelle(self):
        with mock.patch('agripilot.gespann.pruefen',side_effect=AssertionError('unnoetig')):
            self.assertIsNone(hindernisse.warnung((0,0),0,1,3,[],profil=VehicleProfile()))

    def test_rueckwaerts_warnung_negatives_tempo_und_filter(self):
        p=profil(tool_offset_m=0)
        hd=[dict(id='hinten',x=0,y=-12,radius_m=.2)]
        self.assertIsNone(hindernisse.warnung((0,0),0,1,1,hd,profil=p))
        for kw in (dict(tempo_ms=-1),dict(tempo_ms=1,rueckwaerts=True)):
            self.assertEqual(hindernisse.warnung((0,0),0,breite_m=1,hindernisse=hd,profil=p,**kw)['id'],'hinten')

    def test_engine_wendestart_prueft_hindernisse_unabhaengig_von_grenzschalter(self):
        from agripilot.engine import Engine
        e=Engine.__new__(Engine)
        e.turn_preview=[(3,0),(3,10)];e.turn_preview_fahrbar=True;e.turn_preview_ok=True
        e.grenze_abgleichen=lambda:None;e.wende_kontext=lambda:'stand';e._turn_kontext='stand'
        e.field=None;e._require_position=lambda:None;e._turn_planned_at=None
        e.headland=SimpleNamespace(nur_im_feld=False,muster='u',richtung='links')
        e.profile=profil();e.heading=0;e._lenkpunkt=lambda:(3,0)
        e.implement_heading=SimpleNamespace(value=0);e.steering=None;e.note=lambda _:None
        e._hindernisse=[dict(id='neu',name='Neuer Mast',x=0,y=2,radius_m=.2)]
        e._hindernisse_laden=mock.Mock()
        with self.assertRaisesRegex(RuntimeError,'Hindernis'):
            e.start_turn()
        e._hindernisse_laden.assert_called_once_with(erzwingen=True)


class AbschlussRegression(unittest.TestCase):
    def test_rotationen_ohne_raster_rundungsueberlappung(self):
        for h in (0,30,45,90,135,180,225,270,315):
            r=math.radians(h)
            ziel=(math.cos(r)+math.sin(r),-math.sin(r)+math.cos(r))
            c=CoverageMap(.05)
            c.add_swath((0,0),ziel,h,build_sections(3,1))
            self.assertAlmostEqual(c.applied_area_m2,3)
            self.assertAlmostEqual(c.overlap_m2,0)

    def test_engine_live_meldet_tatsaechlichen_traktortreffer(self):
        from agripilot.engine import Engine
        e=Engine.__new__(Engine);e.field=None;e._hindernisse_feld=None
        e.hindernis_warnung=None;e.steer_position=(3,0);e.position=(0,0);e.heading=0
        e.profile=profil();e.implement_heading=SimpleNamespace(value=0)
        e.heading_filter=SimpleNamespace(rueckwaerts=False);e.note=lambda _:None
        e._hindernisse=[dict(id='mast',name='Mast',x=0,y=2,radius_m=.2)]
        e._hindernisse_pruefen(SimpleNamespace(speed_ms=1))
        self.assertEqual(e.hindernis_warnung['id'],'mast')
        self.assertTrue(e.hindernis_warnung['vollstaendig'])

    def test_live_mit_hindernissen_und_unbekannten_massen_bleibt_unvollstaendig(self):
        w=hindernisse.warnung((0,0),0,1,3,[dict(id='mast',x=0,y=2)],profil=VehicleProfile())
        self.assertFalse(w['vollstaendig'])


class WiederaufnahmeIntegration(unittest.TestCase):
    def test_echte_engine_checkpoint_sqlite_neustart(self):
        import tempfile
        from pathlib import Path
        from agripilot.config import Config
        from agripilot.storage import Storage
        from agripilot.engine import Engine
        with tempfile.TemporaryDirectory() as tmp:
            pfad=Path(tmp)/'nur-test.db';config=Config()
            store=Storage(pfad);e=Engine(config,store)
            try:
                f=store.save_field(dict(name='Kunstfeld',datum_lat=50,datum_lon=10,
                    boundary=[[-20,-20],[20,-20],[20,20],[-20,20]]))
                e.load_field(f['id']);j=e.start_job('Kunsttest')
                e.coverage=CoverageMap(.05)
                for _ in range(2):e.coverage.add_swath((0,0),(1,1),0,build_sections(3,1))
                e.suspend_job()
                self.assertAlmostEqual(store.get_job(j['id'])['overlap_ha']*10000,3)
            finally:e.close();store.close()
            store=Storage(pfad);e=Engine(config,store)
            try:
                e.resume_session()
                self.assertEqual(e.job['id'],j['id'])
                self.assertAlmostEqual(e.coverage.overlap_m2,3)
                e.coverage.add_swath((10,0),(11,1),0,build_sections(3,1))
                self.assertAlmostEqual(e.coverage.overlap_m2,3)
            finally:e.close();store.close()
