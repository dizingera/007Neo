"""Pause/Papierkorb gegen echte Engine, SQLite und Mehrgeräteabgleich."""
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from agripilot import arbeitstag as a, zusatzsync as z
from agripilot.config import Config
from agripilot.coverage import CoverageMap
from agripilot.engine import Engine, RESUME_MAX_AGE_S
from agripilot.nmea import Fix
from agripilot.storage import Storage
from agripilot.sync import collect_changes, apply_changes

class Arbeitsablauf16Tests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.st=self.store(); self.cfg=Config(); self.cfg.network.device_id='a'
        self.e=self.engine()
        self.f=self.st.save_field(dict(name='Test',datum_lat=48.,datum_lon=11.))
        self.e.load_field(self.f['id']); self.fix()
    def store(self):
        st=Storage(Path(self.tmp.name)/(str(time.time_ns())+'.db'))
        self.addCleanup(st.close); return st
    def engine(self):
        e=Engine(self.cfg,self.st); self.addCleanup(e.close); return e
    def fix(self,speed=0):
        self.e.fix=Fix(lat=48.,lon=11.,fix_quality=4,speed_ms=speed,received_at=time.time())
    def job(self):
        j=self.e.start_job('Grubbern'); self.e.coverage.cells={(1,1),(1,2),(2,2)}
        self.e.coverage.applied_area_m2=1.25
        self.e.distance_m=100; self.e.working_time_s=30; return j['id']
    def exchange(self,source,target):
        apply_changes(target,collect_changes(source,0))
        z.uebernehmen(target,z.exportieren(source,device_id='a'),device_id='b')
    def test_pause_moving_saves_open_job_and_does_not_touch_steering(self):
        jid=self.job(); steering=Mock(); self.e.steering=steering; self.fix(3)
        r=a.pausieren(self.e)
        self.assertEqual(r['id'],jid); self.assertTrue(r['pausiert'])
        self.assertIsNone(r['ended_at']); self.assertIsNone(self.e.job)
        self.assertEqual((r['distance_m'],r['working_time_s']),(100,30))
        self.assertEqual(CoverageMap.unpack(self.st.get_job_coverage(jid)).cells,{(1,1),(1,2),(2,2)})
        steering.arm.assert_not_called(); steering.disarm.assert_not_called()
    def test_pause_survives_restart_even_old_and_not_closed_as_orphan(self):
        jid=self.job(); a.pausieren(self.e)
        self.st.db.execute('UPDATE jobs SET updated_at=? WHERE id=?',(time.time()-RESUME_MAX_AGE_S-10,jid)); self.st.db.commit()
        self.e=self.engine(); r=self.e.resume_session()
        self.assertIsNone(self.e.job); self.assertFalse(r['arbeit'])
        self.assertTrue(self.e.paused_job['pausiert'])
        self.assertIsNone(self.st.get_job(jid)['ended_at'])
        self.assertEqual(self.st.close_orphan_jobs('a'),0)
    def test_resume_same_job_cells_overlap_and_no_gap_bridge(self):
        jid=self.job(); a.pausieren(self.e); r=a.fortsetzen(self.e,jid)
        self.assertEqual(r['id'],jid); self.assertFalse(r['pausiert']); self.assertIsNone(self.e.paused_job)
        self.assertEqual(self.e.coverage.cells,{(1,1),(1,2),(2,2)})
        self.assertAlmostEqual(self.e.coverage.overlap_m2,.5)
        self.assertEqual(self.e.working_time_s,30)
        self.assertIsNone(self.e.tool_position); self.assertIsNone(self.e.implement_position)
        self.assertIsNone(self.e.steering)
    def test_resume_rejects_moving_stale_nan_and_wrong_owner(self):
        jid=self.job(); a.pausieren(self.e)
        for speed in (2,float('nan')):
            self.fix(speed)
            with self.assertRaises(RuntimeError): a.fortsetzen(self.e,jid)
        self.fix(); self.e.fix.received_at-=5
        with self.assertRaises(RuntimeError): a.fortsetzen(self.e,jid)
        self.fix(); self.st.update_job(jid,device_id='fremd')
        with self.assertRaises(RuntimeError): a.fortsetzen(self.e,jid)
        self.assertTrue(self.st.get_setting('arbeitsablauf:'+jid)['pausiert']); self.assertIsNone(self.e.job)
    def test_new_job_never_inherits_field_coverage(self):
        first=self.job(); self.e.stop_job(); blob=self.st.get_job_coverage(first)
        second=self.e.start_job('Saeen')
        self.assertNotEqual(first,second['id']); self.assertEqual(self.e.coverage.cells,set())
        self.assertEqual(self.e.coverage.applied_area_m2,0)
        self.assertEqual((self.e.distance_m,self.e.working_time_s),(0,0))
        self.assertEqual(self.st.get_job_coverage(first),blob)
    def test_trash_retains_coverage_tracks_and_restore_gets_new_version(self):
        jid=self.job(); self.e.stop_job()
        self.st.add_track_points(jid,[(time.time(),48.,11.,500.,2.,0.,4.,0.)])
        blob=self.st.get_job_coverage(jid); self.st.delete_job(jid); deleted=self.st.get_job(jid)
        self.assertEqual(self.st.list_jobs(),[])
        self.assertEqual(a.papierkorb(self.st,'a')['jobs'][0]['id'],jid)
        r=a.wiederherstellen(self.e,jid)
        self.assertGreater(r['updated_at'],deleted['updated_at']); self.assertFalse(r['deleted'])
        self.assertEqual(r['ended_at'],deleted['ended_at'])
        self.assertEqual(self.st.get_job_coverage(jid),blob); self.assertEqual(len(self.st.track_points(jid)),1)
        self.assertEqual(a.papierkorb(self.st)['jobs'],[]); self.assertIsNone(self.e.job)
    def test_restore_open_job_stays_paused_after_restart(self):
        jid=self.job(); a.pausieren(self.e); self.st.delete_job(jid)
        r=a.wiederherstellen(self.e,jid); self.assertTrue(r['pausiert']); self.assertIsNone(r['ended_at'])
        self.assertIsNone(self.e.job); self.e=self.engine(); self.e.resume_session(); self.assertIsNone(self.e.job)
    def test_historical_deletions_are_not_promised_recoverable(self):
        jid=self.job(); self.e.stop_job(); self.st._soft_delete('jobs',jid)
        self.assertEqual(a.papierkorb(self.st)['jobs'],[])
        with self.assertRaises(RuntimeError): a.wiederherstellen(self.e,jid)
    def test_restore_guards_no_mutation_on_failure(self):
        jid=self.job(); self.e.stop_job(); self.st.delete_job(jid); self.fix(1)
        with self.assertRaises(RuntimeError): a.wiederherstellen(self.e,jid)
        self.fix(); self.st.update_job(jid,device_id='fremd')
        with self.assertRaises(RuntimeError): a.wiederherstellen(self.e,jid)
        self.st.update_job(jid,device_id='a'); self.st._soft_delete('fields',self.f['id'])
        with self.assertRaises(RuntimeError): a.wiederherstellen(self.e,jid)
        self.assertTrue(self.st.get_job(jid)['deleted']); self.assertFalse(a.papierkorb(self.st)['jobs'][0]['wiederherstellbar'])
    def test_sync_restore_beats_old_tombstone_through_third_device(self):
        jid=self.job(); self.e.stop_job(); self.st.delete_job(jid)
        tomb=collect_changes(self.st,0); oldmeta=z.exportieren(self.st,device_id='a')
        other=self.store(); self.exchange(self.st,other)
        self.assertEqual(other.list_deleted_jobs()[0]['id'],jid)
        r=a.wiederherstellen(self.e,jid); self.exchange(self.st,other)
        apply_changes(other,tomb); z.uebernehmen(other,oldmeta,device_id='b')
        self.assertFalse(other.get_job(jid)['deleted']); self.assertEqual(other.get_job(jid)['updated_at'],r['updated_at'])
        third=self.store(); self.exchange(other,third)
        self.assertFalse(third.get_job(jid)['deleted']); self.assertEqual(third.get_job_coverage(jid),self.st.get_job_coverage(jid))
    def test_sync_pause_resume_preserves_original_version_across_relay(self):
        jid=self.job(); a.pausieren(self.e); oldmeta=z.exportieren(self.st,device_id='a')
        other=self.store(); self.exchange(self.st,other)
        self.assertTrue(other.get_job(jid)['pausiert']); self.assertIsNone(other.find_resumable_job('a',RESUME_MAX_AGE_S))
        a.fortsetzen(self.e,jid); self.exchange(self.st,other); z.uebernehmen(other,oldmeta,device_id='b')
        self.assertFalse(other.get_job(jid)['pausiert'])
        third=self.store(); self.exchange(other,third); self.assertFalse(third.get_job(jid)['pausiert'])
    def test_invalid_pause_metadata_rejected(self):
        item=dict(key='arbeitsablauf:abc',value=dict(zeit=1,pausiert='ja',papierkorb=False,device_id='a'),updated_at=1,device_id='a')
        self.assertEqual(z.uebernehmen(self.st,[item],device_id='b')['abgelehnt'],1)

    def test_atomic_delete_and_restore_roll_back_if_metadata_write_fails(self):
        jid=self.job(); self.e.stop_job()
        self.st.db.execute("CREATE TEMP TRIGGER test_fail BEFORE INSERT ON settings WHEN NEW.key LIKE 'arbeitsablauf:%' BEGIN SELECT RAISE(ABORT,'test'); END")
        with self.assertRaises(Exception): self.st.delete_job(jid)
        self.assertFalse(self.st.get_job(jid)['deleted'])
        self.st.db.execute('DROP TRIGGER test_fail'); self.st.delete_job(jid)
        self.st.db.execute("CREATE TEMP TRIGGER test_fail BEFORE INSERT ON settings WHEN NEW.key LIKE 'arbeitsablauf:%' BEGIN SELECT RAISE(ABORT,'test'); END")
        with self.assertRaises(Exception): self.st.restore_job(jid)
        self.assertTrue(self.st.get_job(jid)['deleted'])
    def test_restore_clock_rollback_and_idempotent_delete(self):
        jid=self.job(); self.e.stop_job(); self.st.delete_job(jid)
        deleted=self.st.get_job(jid)['updated_at']; self.st.delete_job(jid)
        self.assertEqual(self.st.get_job(jid)['updated_at'],deleted)
        with patch('agripilot.storage.time.time',return_value=1): r=self.st.restore_job(jid)
        self.assertGreater(r['updated_at'],deleted)
        with self.assertRaises(RuntimeError): self.st.restore_job(jid)
    def test_direct_remote_tombstone_without_raster_cannot_destroy_it(self):
        jid=self.job(); self.e.stop_job(); old=self.st.get_job_coverage(jid)
        row=collect_changes(self.st,0)['jobs'][0]
        row.update(deleted=1,coverage=None,updated_at=row['updated_at']+1)
        self.st.apply_remote('jobs',[row])
        self.assertEqual(self.st.get_job_coverage(jid),old)
    def test_pause_does_not_record_extra_swath_or_tracks_on_following_fix(self):
        jid=self.job(); a.pausieren(self.e)
        blob=self.st.get_job_coverage(jid); before=len(self.st.track_points(jid))
        self.e.on_fix(Fix(lat=48.00001,lon=11.,fix_quality=4,speed_ms=3,course_deg=0,received_at=time.time()))
        self.e.on_fix(Fix(lat=48.00002,lon=11.,fix_quality=4,speed_ms=3,course_deg=0,received_at=time.time()+.3))
        self.e.warte_auf_sicherung()
        self.assertEqual(self.st.get_job_coverage(jid),blob)
        self.assertEqual(len(self.st.track_points(jid)),before)
        self.assertEqual(self.e.coverage.cells,{(1,1),(1,2),(2,2)})
    def test_status_from_wrong_owner_rejected(self):
        jid=self.job()
        item=dict(key='arbeitsablauf:'+jid,value=dict(zeit=time.time(),pausiert=True,papierkorb=False,device_id='wrong'),updated_at=time.time(),device_id='b')
        self.assertEqual(z.uebernehmen(self.st,[item],device_id='a')['abgelehnt'],1)
        self.assertFalse(self.st.get_job(jid)['pausiert'])

    def test_pause_failed_snapshot_does_not_claim_success(self):
        jid=self.job()
        with patch.object(self.st,'update_job',side_effect=OSError('test')):
            with self.assertRaises(OSError): a.pausieren(self.e)
        self.assertEqual(self.e.job['id'],jid)
        self.assertFalse(self.st.get_job(jid)['pausiert'])

    def test_costs_roundtrip_real_writer_and_third_device(self):
        from agripilot.arbeitsbericht import kosten_speichern, kosten_lesen
        jid=self.job(); self.e.stop_job()
        kosten_speichern(self.st,jid,dict(diesel_liter=23.5,diesel_preis_eur_l=1.6,betriebsmittel_eur=0,stundensatz_eur=None))
        other=self.store(); self.exchange(self.st,other)
        read=kosten_lesen(other,jid)
        self.assertEqual(read['diesel_liter'],23.5); self.assertEqual(read['betriebsmittel_eur'],0)
        self.assertIsNone(read['stundensatz_eur']); self.assertFalse(read['vollstaendig'])
        third=self.store(); self.exchange(other,third)
        self.assertEqual(kosten_lesen(third,jid)['aktualisiert_am'],read['aktualisiert_am'])
        self.assertEqual(kosten_lesen(third,jid)['diesel_eur'],37.6)
    def test_costs_version_uses_edit_time_not_receive_or_envelope_time(self):
        other=self.store(); key='arbeitskosten:test'
        def item(stamp,envelope,liters):
            return dict(key=key,value=dict(aktualisiert_am=stamp,diesel_liter=liters),updated_at=envelope,device_id='a')
        z.uebernehmen(self.st,[item(20,20,2)],device_id='b')
        z.uebernehmen(self.st,[item(10,9999999999,1)],device_id='b')
        self.assertEqual(self.st.get_setting(key)['diesel_liter'],2)
        z.uebernehmen(self.st,[item(30,1,3)],device_id='b')
        sent=z.exportieren(self.st,device_id='b')
        cost=next(x for x in sent if x['key']==key)
        self.assertEqual(cost['updated_at'],30)
        z.uebernehmen(other,sent,device_id='c')
        z.uebernehmen(other,[item(20,9999999999,2)],device_id='c')
        self.assertEqual(other.get_setting(key)['diesel_liter'],3)
    def test_costs_boundaries_nonfinite_bool_and_unknown_keys(self):
        from agripilot.arbeitsbericht import KOSTEN_GRENZEN
        for field,maximum in KOSTEN_GRENZEN.items():
            for bad in (-1,maximum+1,float('nan'),float('inf'),True,'4'):
                item=dict(key='arbeitskosten:test',value=dict(aktualisiert_am=10,**{field:bad}),updated_at=10,device_id='a')
                self.assertEqual(z.uebernehmen(self.st,[item],device_id='b')['abgelehnt'],1,(field,bad))
        item=dict(key='arbeitskosten:valid',value=dict(aktualisiert_am=11,**KOSTEN_GRENZEN,secret='must vanish'),updated_at=11,device_id='a')
        self.assertEqual(z.uebernehmen(self.st,[item],device_id='b')['angewendet'],1)
        self.assertNotIn('secret',self.st.get_setting('arbeitskosten:valid'))
        self.st.set_setting('schnellzugriffe',['a']); self.st.set_setting('arbeitsvorlagen',[{'name':'x'}])
        self.assertTrue(all(x['key'].startswith(('arbeitskosten:','arbeitsablauf:','feld_einfahrt:','grenze_verlauf:','probefahrt_')) for x in z.exportieren(self.st,device_id='a')))

    def test_new_job_preserves_fresh_navigation_geometry_and_time_anchor(self):
        self.e.tool_position=(3.,4.); self.e.implement_position=(2.,4.)
        self.e._last_update_at=self.e.fix.received_at
        anchor=self.e._last_update_at
        self.e.coverage.cells={(99,99)}
        self.e.start_job('Neue Arbeit')
        self.assertEqual(self.e.tool_position,(3.,4.))
        self.assertEqual(self.e.implement_position,(2.,4.))
        self.assertEqual(self.e._last_update_at,anchor)
        self.assertEqual(self.e.coverage.cells,set())
    def test_new_job_drops_stale_recording_anchor_without_erasing_navigation(self):
        self.e.tool_position=(3.,4.); self.e.implement_position=(2.,4.)
        self.e.fix.received_at=time.time()-10
        self.e._last_update_at=self.e.fix.received_at
        self.e.start_job('Neue Arbeit')
        self.assertIsNone(self.e._last_update_at)
        self.assertEqual(self.e.tool_position,(3.,4.))
        self.assertEqual(self.e.implement_position,(2.,4.))
