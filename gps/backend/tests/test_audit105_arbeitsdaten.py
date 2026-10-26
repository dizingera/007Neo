"""Arbeitsdaten-Audit: echte temporaere SQLite-Daten, keine Produktivzugriffe."""
import asyncio
import base64
import sqlite3
import struct
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from agripilot import arbeitstag, zusatzsync
from agripilot.config import Config
from agripilot.coverage import CoverageMap
from agripilot.engine import Engine
from agripilot.nmea import Fix
from agripilot.storage import Storage
from agripilot.sync import SyncClient, apply_changes, collect_changes, merge_field_coverage


class ArbeitsdatenAudit105(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.s = self.store('quelle')
        self.cfg = Config()
        self.cfg.network.device_id = 'audit-quelle'
        self.cfg.gnss.source = 'aus'
        self.cfg.imu.source = 'aus'
        self.cfg.steering.output = 'none'
        self.cfg.corrections.source = 'aus'
        self.f = self.s.save_field(dict(name='Auditfeld', datum_lat=48., datum_lon=11.))

    def store(self, name):
        store = Storage(Path(self.tmp.name) / (name + '.db'))
        self.addCleanup(store.close)
        return store

    def engine(self):
        e = Engine(self.cfg, self.s)
        self.addCleanup(e.close)
        return e

    def job(self):
        return self.s.start_job(self.f['id'], self.cfg.network.device_id, operation='Audit')

    @staticmethod
    def raster(*cells):
        cov = CoverageMap()
        cov.cells.update(cells)
        return cov.pack()

    def invalid_maps(self):
        return (b'', b'kap', self.raster((1, 2))[:-1],
                *(struct.pack('<fii', size, 1, 2)
                  for size in (0., -0.5, float('nan'), float('inf'))))

    def test_ungueltiger_erster_raster_abgleich_speichert_keine_korruption(self):
        job = self.job()
        row = collect_changes(self.s, 0)['jobs'][0]
        for i, blob in enumerate(self.invalid_maps()):
            with self.subTest(fall=i):
                target = self.store('erstempfang' + str(i))
                incoming = {**row, 'coverage': base64.b64encode(blob).decode()}
                with self.assertRaisesRegex(ValueError, 'Arbeitskarte'):
                    apply_changes(target, {'jobs': [incoming]})
                self.assertIsNone(target.get_job(job['id']))

    def test_beschaedigte_karte_nicht_fortsetzen_oder_umschreiben(self):
        e = self.engine()
        e.load_field(self.f['id'])
        e.fix = Fix(lat=48., lon=11., fix_quality=4, speed_ms=0, received_at=time.time())
        job = e.start_job('Audit')
        e.distance_m = 100
        arbeitstag.pausieren(e)
        original_status = self.s.get_setting('arbeitsablauf:' + job['id'])
        for i, blob in enumerate(self.invalid_maps()):
            with self.subTest(fall=i):
                self.s.db.execute('UPDATE jobs SET coverage=? WHERE id=?', (blob, job['id']))
                self.s.db.commit()
                with self.assertRaisesRegex(ValueError, 'Arbeitskarte'):
                    arbeitstag.fortsetzen(e, job['id'])
                self.assertIsNone(e.job)
                self.assertEqual(self.s.get_job_coverage(job['id']), blob)
                self.assertEqual(self.s.get_setting('arbeitsablauf:' + job['id']), original_status)

    def test_synchronisation_repariert_beschaedigte_lokale_karte_mit_gueltigem_stand(self):
        job = self.job()
        good = self.raster((1, 2), (3, 4))
        self.s.update_job(job['id'], coverage=struct.pack('<fii', float('nan'), 99, 99))
        row = collect_changes(self.s, 0)['jobs'][0]
        row.update(coverage=base64.b64encode(good).decode(), updated_at=row['updated_at'] + 1)
        apply_changes(self.s, {'jobs': [row]})
        self.assertEqual(CoverageMap.unpack(self.s.get_job_coverage(job['id'])).cells, {(1, 2), (3, 4)})
        self.assertEqual(self.s.get_job_coverage(job['id']), good)

    def test_defektes_raster_verfaelscht_felduebersicht_nicht(self):
        job = self.job()
        self.s.update_job(job['id'], coverage=struct.pack('<fii', float('nan'), 999, 999))
        valid = self.job()
        self.s.update_job(valid['id'], coverage=self.raster((2, 3)))
        self.assertEqual(merge_field_coverage(self.s, self.f['id']).cells, {(2, 3)})

    def test_fremder_besitzer_oder_feld_darf_auftrag_nicht_uebernehmen(self):
        job = self.job()
        self.s.update_job(job['id'], coverage=self.raster((1, 2)))
        original = collect_changes(self.s, 0)['jobs'][0]
        for changed in ({'device_id': 'anderes-geraet'}, {'field_id': 'anderes-feld'}):
            with self.subTest(aenderung=changed):
                incoming = {**original, **changed, 'updated_at': original['updated_at'] + 1,
                            'coverage': base64.b64encode(self.raster((9, 9))).decode()}
                with self.assertRaises(ValueError):
                    apply_changes(self.s, {'jobs': [incoming]})
                self.assertEqual(self.s.get_job(job['id'])['device_id'], original['device_id'])
                self.assertEqual(self.s.get_job(job['id'])['field_id'], original['field_id'])
                self.assertEqual(CoverageMap.unpack(self.s.get_job_coverage(job['id'])).cells, {(1, 2)})

    def test_fehlgeschlagener_stapel_hinterlaesst_keine_spaeter_committeten_auftraege(self):
        self.job()
        row = collect_changes(self.s, 0)['jobs'][0]
        target = self.store('stapel')
        bad = {**row, 'id': 'zweiter-auftrag', 'device_id': None}
        with self.assertRaises((ValueError, sqlite3.IntegrityError)):
            target.apply_remote('jobs', [row, bad])
        target.set_setting('audit_nachher', True)
        self.assertEqual(target.list_jobs(), [])
        self.assertEqual(target.db.execute('SELECT COUNT(*) FROM sync_received').fetchone()[0], 0)

    def test_arbeitskosten_und_pausenstatus_zaehlen_als_ausstehend(self):
        from agripilot.arbeitsbericht import kosten_speichern
        job = self.job()
        self.s.arbeitsstatus_setzen(job['id'], pausiert=True)
        kosten_speichern(self.s, job['id'], {'diesel_liter': 4})
        state = SyncClient(self.cfg, self.s).status_dict()
        self.assertEqual(state['pending']['zusatzdaten'], 2)
        self.assertEqual(state['pending_count'], sum(state['pending'].values()))


    def test_defekte_pausenmetadaten_blockieren_weder_liste_noch_neustart(self):
        for i, raw in enumerate(('[]', '{', '{"pausiert":"ja"}')):
            with self.subTest(fall=i):
                job = self.job()
                key = 'arbeitsablauf:' + job['id']
                self.s.db.execute('INSERT INTO settings(key,value,updated_at) VALUES(?,?,?)',
                                  (key, raw, time.time()))
                self.s.db.commit()
                self.assertTrue(self.s.get_job(job['id'])['pausiert'])
                self.assertTrue(next(j for j in self.s.list_jobs() if j['id'] == job['id'])['pausiert'])
                self.assertIsNone(self.s.find_resumable_job(self.cfg.network.device_id, 86400))
                self.assertEqual(self.s.close_orphan_jobs(self.cfg.network.device_id), 0)
                e = self.engine()
                self.assertIsNone(e.job)
                self.assertEqual(e.paused_job['id'], job['id'])
                self.assertIsNone(self.s.get_job(job['id'])['ended_at'])
                self.assertEqual(self.s.db.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()[0], raw)

    def test_fremde_papierkorbmetadaten_machen_altloeschung_nicht_wiederherstellbar(self):
        job = self.job()
        self.s._soft_delete('jobs', job['id'])
        self.s.set_setting('arbeitsablauf:' + job['id'],
                           dict(zeit=time.time(), pausiert=True, papierkorb=True, device_id='fremd'))
        self.assertEqual(self.s.list_deleted_jobs(), [])
        with self.assertRaises(RuntimeError):
            self.s.restore_job(job['id'])
        self.assertTrue(self.s.get_job(job['id'])['deleted'])

    def test_status_vor_job_falscher_besitzer_blockiert_echten_status_nicht(self):
        job = self.job()
        self.s.arbeitsstatus_setzen(job['id'], pausiert=True)
        proper = zusatzsync.exportieren(self.s, device_id=self.cfg.network.device_id)
        target = self.store('reihenfolge')
        wrong = dict(key='arbeitsablauf:' + job['id'], updated_at=time.time()+1000,
                     device_id='weiterleiter', value=dict(zeit=time.time()+1000,
                     pausiert=False, papierkorb=True, device_id='fremd'))
        zusatzsync.uebernehmen(target, [wrong], device_id='audit-ziel')
        apply_changes(target, collect_changes(self.s, 0))
        result = zusatzsync.uebernehmen(target, proper, device_id='audit-ziel')
        self.assertEqual(result['angewendet'], 1)
        self.assertTrue(target.get_job(job['id'])['pausiert'])
        self.assertEqual(target.get_setting(wrong['key'])['device_id'], self.cfg.network.device_id)

    def test_defekte_lokale_metadaten_durch_gueltige_gegenstelle_ersetzbar(self):
        job = self.job()
        self.s.arbeitsstatus_setzen(job['id'], pausiert=True)
        proper = zusatzsync.exportieren(self.s, device_id=self.cfg.network.device_id)
        target = self.store('metareparatur')
        apply_changes(target, collect_changes(self.s, 0))
        target.set_setting('arbeitsablauf:' + job['id'], [])
        result = zusatzsync.uebernehmen(target, proper, device_id='audit-ziel')
        self.assertEqual(result['angewendet'], 1)
        self.assertTrue(target.get_job(job['id'])['pausiert'])

    def test_fehlgeschlagener_snapshot_beendet_hintergrundabgleich_nicht(self):
        import httpx
        job = self.job()
        self.s.db.execute('UPDATE jobs SET coverage=? WHERE id=?', ('falscher-datentyp', job['id']))
        self.s.db.commit()
        client = SyncClient(self.cfg, self.s)
        before = self.s.get_setting('sync_push_cursor')
        with patch.object(httpx, 'AsyncClient', side_effect=AssertionError('Kein Netzwerk')) as network:
            result = asyncio.run(client.sync_once())
        self.assertFalse(result['ok'])
        self.assertEqual(self.s.get_setting('sync_push_cursor'), before)
        self.assertIsNone(client.last_success)
        network.assert_not_called()

    def test_direkter_abgleich_weist_leeren_blob_ohne_rasterkopf_zurueck(self):
        self.job()
        row = collect_changes(self.s, 0)['jobs'][0]
        row.update(id='leere-arbeitskarte', coverage='')
        with self.assertRaisesRegex(ValueError, 'Arbeitskarte'):
            self.s.apply_remote('jobs', [row])
        self.assertIsNone(self.s.get_job(row['id']))


    def test_fehlende_karte_mit_gemessener_flaeche_nicht_fortsetzen(self):
        e = self.engine()
        e.load_field(self.f['id'])
        e.fix = Fix(lat=48., lon=11., fix_quality=4, speed_ms=0, received_at=time.time())
        job = self.job()
        self.s.update_job(job['id'], area_ha=.25, distance_m=100, working_time_s=30)
        self.s.arbeitsstatus_setzen(job['id'], pausiert=True)
        before = self.s.get_job(job['id'])
        meta = self.s.get_setting('arbeitsablauf:' + job['id'])
        self.assertIsNone(self.s.get_job_coverage(job['id']))
        with self.assertRaisesRegex(ValueError, 'Arbeitskarte'):
            arbeitstag.fortsetzen(e, job['id'])
        self.assertIsNone(e.job)
        self.assertEqual(self.s.get_job(job['id']), before)
        self.assertEqual(self.s.get_setting('arbeitsablauf:' + job['id']), meta)
        self.assertIsNone(self.s.get_job_coverage(job['id']))

    def test_frische_arbeit_ohne_karte_und_flaeche_fortsetzbar(self):
        e = self.engine()
        e.load_field(self.f['id'])
        e.fix = Fix(lat=48., lon=11., fix_quality=4, speed_ms=0, received_at=time.time())
        for distance in (0, 100):
            with self.subTest(strecke=distance):
                job = self.job()
                self.s.update_job(job['id'], distance_m=distance)
                self.assertIsNone(self.s.get_job_coverage(job['id']))
                self.assertEqual(arbeitstag.fortsetzen(e, job['id'])['id'], job['id'])
                self.assertEqual(e.coverage.cells, set())
                self.assertEqual(e.distance_m, distance)
                self.assertEqual(e.working_time_s, 0)
                arbeitstag.pausieren(e)
                self.assertEqual(self.s.get_job(job['id'])['distance_m'], distance)
                self.assertEqual(self.s.get_job(job['id'])['area_ha'], 0)


    def test_defekte_eingangskarte_darf_vorhandene_kennzahlen_nicht_nullsetzen(self):
        job = self.job()
        blob = self.raster(*((x, y) for x in range(10) for y in range(40)))
        self.s.update_job(job['id'], coverage=blob, area_ha=.01, overlap_ha=.002,
                          distance_m=120., working_time_s=60.)
        before = self.s.get_job(job['id'])
        row = collect_changes(self.s, 0)['jobs'][0]
        for incoming in (base64.b64encode(b'kap').decode(), 'kein-base64!'):
            with self.subTest(raster=incoming):
                bad = {**row, 'coverage': incoming, 'updated_at': before['updated_at'] + 10,
                       'area_ha': 0., 'overlap_ha': 0., 'distance_m': 0., 'working_time_s': 0.,
                       'operation': 'Beschaedigter Fremdstand'}
                result = apply_changes(self.s, {'jobs': [bad]})
                self.assertEqual(self.s.get_job(job['id']), before)
                self.assertEqual(self.s.get_job_coverage(job['id']), blob)
                self.assertEqual(result['jobs'], 0)
                self.assertEqual(self.s.get_setting('sync_konflikte', []), [])
                self.assertEqual(self.s.db.execute('SELECT COUNT(*) FROM sync_received').fetchone()[0], 0)

    def test_loeschvermerk_ohne_karte_funktioniert_trotz_defektem_lokalraster(self):
        for i, blob in enumerate((b'kap', b'')):
            with self.subTest(raster=i):
                job = self.job()
                self.s.update_job(job['id'], coverage=blob, area_ha=.01, overlap_ha=.002,
                                  distance_m=120., working_time_s=60.)
                row = next(r for r in collect_changes(self.s, 0)['jobs'] if r['id'] == job['id'])
                renamed = {**row, 'coverage': None, 'operation': 'Metadaten',
                           'updated_at': row['updated_at'] + 1}
                self.assertEqual(apply_changes(self.s, {'jobs': [renamed]})['jobs'], 1)
                self.assertEqual(self.s.get_job(job['id'])['operation'], 'Metadaten')
                self.assertEqual(self.s.get_job_coverage(job['id']), blob)
                tombstone = {**renamed, 'deleted': 1, 'updated_at': row['updated_at'] + 10}
                self.assertEqual(apply_changes(self.s, {'jobs': [tombstone]})['jobs'], 1)
                self.assertTrue(self.s.get_job(job['id'])['deleted'])
                self.assertEqual(self.s.get_job_coverage(job['id']), blob)
                self.assertEqual(self.s.get_job(job['id'])['area_ha'], row['area_ha'])
                self.assertEqual(self.s.get_job(job['id'])['distance_m'], row['distance_m'])

    def test_gueltiger_hostname_mit_punkt_erlaubt_lokales_fortsetzen(self):
        owner = 'kabine.local'
        job = self.s.start_job(self.f['id'], owner, operation='Hostname')
        self.assertTrue(self.s.arbeitsstatus_setzen(job['id'], pausiert=True)['pausiert'])
        resumed = self.s.arbeitsstatus_setzen(job['id'], pausiert=False)
        meta = self.s.get_setting('arbeitsablauf:' + job['id'])
        self.assertFalse(meta['pausiert'])
        self.assertFalse(resumed['pausiert'])
        self.assertEqual(resumed['status'], 'offen')
        self.assertEqual(self.s.find_resumable_job(owner, 3600)['id'], job['id'])
        self.assertIsNone(self.s.find_resumable_job('andere.kabine', 3600))
        self.s.set_setting('arbeitsablauf:' + job['id'], {**meta, 'device_id': 'fremde.kabine', 'pausiert': True})
        self.assertFalse(self.s.get_job(job['id'])['pausiert'])
        self.assertIsNone(self.s.find_paused_job(owner))


if __name__ == '__main__':
    unittest.main()
