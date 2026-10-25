import base64
import tempfile
import time
import unittest
from agripilot.coverage import CoverageMap
from agripilot.storage import Storage
from agripilot.sync import apply_changes, collect_changes


def karte(*zellen):
    c = CoverageMap(0.5)
    c.cells.update(zellen)
    return c.pack()


class ArbeitsflaechenAbgleich(unittest.TestCase):
    def test_union_keeps_newer_metadata_and_reaches_third_device(self):
        for versatz in (-100.0, 0.0, 100.0):
            with self.subTest(versatz=versatz), tempfile.TemporaryDirectory() as d:
                stores = [Storage(d + '/' + n + '.db') for n in ('a', 'b', 'c')]
                a, b, c = stores
                try:
                    feld = a.save_field({'name': 'Test', 'datum_lat': 48.0, 'datum_lon': 11.0})
                    job = a.start_job(feld['id'], 'a', 'Traktor', 'Lokal')
                    a.update_job(job['id'], coverage=karte((0, 0)))
                    basis = collect_changes(a, 0)
                    apply_changes(b, basis)
                    apply_changes(c, basis)
                    record = dict(basis['jobs'][0])
                    record.update(coverage=base64.b64encode(karte((10, 10))).decode(),
                                  operation='Entfernt', updated_at=record['updated_at'] + versatz)
                    seit = time.time() - 0.001
                    self.assertEqual(apply_changes(a, {'jobs': [record]})['jobs'], 1)
                    self.assertEqual(CoverageMap.unpack(a.get_job_coverage(job['id'])).cells,
                                     {(0, 0), (10, 10)})
                    lokal = a.get_job(job['id'])
                    self.assertEqual(lokal['operation'], 'Entfernt' if versatz > 0 else 'Lokal')
                    self.assertEqual(lokal['updated_at'], max(basis['jobs'][0]['updated_at'], record['updated_at']))
                    delta = collect_changes(a, seit)
                    self.assertEqual(len(delta['jobs']), 1)
                    apply_changes(c, delta)
                    self.assertEqual(CoverageMap.unpack(c.get_job_coverage(job['id'])).cells,
                                     {(0, 0), (10, 10)})
                    # Gleiche Zellen in anderer Binärreihenfolge dürfen keine Endlosschleife erzeugen.
                    self.assertEqual(apply_changes(a, {'jobs': [record]})['jobs'], 0)
                finally:
                    for s in stores: s.close()

    def test_newer_metadata_without_a_map_does_not_erase_existing_work(self):
        with tempfile.TemporaryDirectory() as d:
            s = Storage(d + '/x.db')
            try:
                field = s.save_field({'name': 'Test', 'datum_lat': 48.0, 'datum_lon': 11.0})
                job = s.start_job(field['id'], 'a', 'Traktor', 'Lokal')
                s.update_job(job['id'], coverage=karte((1, 1)))
                record = collect_changes(s, 0)['jobs'][0]
                record.update(coverage=None, updated_at=record['updated_at'] + 1, operation='Neu')
                apply_changes(s, {'jobs': [record]})
                self.assertIsNotNone(s.get_job_coverage(job['id']))
                self.assertEqual(CoverageMap.unpack(s.get_job_coverage(job['id'])).cells, {(1, 1)})
                self.assertEqual(s.get_job(job['id'])['operation'], 'Neu')
            finally:
                s.close()

    def test_newer_invalid_or_incompatible_map_keeps_existing_cells(self):
        incompatible = CoverageMap(1.0)
        incompatible.cells.add((7, 7))
        for incoming in ('kein-base64!', base64.b64encode(incompatible.pack()).decode()):
            with self.subTest(incoming=incoming), tempfile.TemporaryDirectory() as d:
                s = Storage(d + '/x.db')
                try:
                    field = s.save_field({'name': 'Test', 'datum_lat': 48.0, 'datum_lon': 11.0})
                    job = s.start_job(field['id'], 'a', 'Traktor', 'Lokal')
                    s.update_job(job['id'], coverage=karte((1, 1)))
                    record = collect_changes(s, 0)['jobs'][0]
                    record.update(coverage=incoming, updated_at=record['updated_at'] + 1, operation='Neu')
                    apply_changes(s, {'jobs': [record]})
                    self.assertEqual(CoverageMap.unpack(s.get_job_coverage(job['id'])).cells, {(1, 1)})
                    self.assertEqual(s.get_job(job['id'])['operation'], 'Neu')
                finally:
                    s.close()

    def test_older_map_does_not_revive_a_deleted_job(self):
        with tempfile.TemporaryDirectory() as d:
            s = Storage(d + '/x.db')
            try:
                field = s.save_field({'name': 'Test', 'datum_lat': 48.0, 'datum_lon': 11.0})
                job = s.start_job(field['id'], 'a')
                s.update_job(job['id'], coverage=karte((1, 1)))
                record = collect_changes(s, 0)['jobs'][0]
                s.delete_job(job['id'])
                record.update(coverage=base64.b64encode(karte((2, 2))).decode(),
                              updated_at=record['updated_at'] - 100)
                self.assertEqual(apply_changes(s, {'jobs': [record]})['jobs'], 0)
                self.assertTrue(s.get_job(job['id'])['deleted'])
                self.assertEqual(CoverageMap.unpack(s.get_job_coverage(job['id'])).cells, {(1, 1)})
            finally:
                s.close()
