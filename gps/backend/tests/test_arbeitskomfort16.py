import unittest,tempfile
from pathlib import Path
from types import SimpleNamespace
from agripilot.storage import Storage
from agripilot import arbeitskomfort as k

class KomfortTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.store=Storage(Path(self.tmp.name)/'test.db')
 def tearDown(self):
  self.store.close();self.tmp.cleanup()
 def test_favoriten_keine_fremden_kennungen(self):
  f=self.store.save_field({'name':'Nord','datum_lat':48,'datum_lon':9})
  r=k.speichern(SimpleNamespace(store=self.store,engine=None),{'favoriten':[f['id'],'falsch',f['id']]})
  self.assertEqual(r['favoriten'],[f['id']])
 def test_zuletzt_ist_begrenzt(self):
  for i in range(20):k.besucht(self.store,str(i))
  k.besucht(self.store,'19');self.assertEqual(len(k.vorlieben(self.store)['zuletzt']),12)
  self.assertEqual(k.vorlieben(self.store)['zuletzt'][0],'19')
 def test_standardvorlagen_keine_breiten(self):
  for v in k.vorlieben(self.store)['vorlagen']:self.assertNotIn('width_m',v)
