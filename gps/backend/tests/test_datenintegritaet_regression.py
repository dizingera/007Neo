"""Regressionen ausschließlich mit isolierten Daten und gemockter Engine."""
import tempfile
import time
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace as N
from unittest.mock import Mock, patch
from agripilot import arbeitstag, backup, zusatzsync as z
from agripilot.storage import Storage
from agripilot.sync import apply_changes

class DatenRegression(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.s=Storage(self.root/'db');self.addCleanup(self.s.close)
        self.f=self.s.save_field(dict(id='f',name='Lokal',datum_lat=48,datum_lon=9))

    def test_geloeschter_auftrag_bleibt_inaktiv(self):
        j=self.s.start_job('f','test');self.s.delete_job(j['id'])
        e=N(store=self.s,job=None,steering=None,fix=N(valid=True,received_at=time.time(),speed_ms=0),config=N(network=N(device_id='test')),profile=N(name='',spacing_m=3),load_field=Mock(),_remember_session=Mock(),note=Mock(),genauigkeit=Mock())
        with self.assertRaises(RuntimeError):arbeitstag.fortsetzen(e,j['id'])
        self.assertIsNone(e.job);e.load_field.assert_not_called()

    def konflikt(self,delta=-1):
        row=self.s.changes_since('fields',0)[0]
        incoming={**row,'name':'Fremd','updated_at':row['updated_at']+delta}
        apply_changes(self.s,{'fields':[incoming]})
        return row,incoming,self.s.get_setting('sync_konflikte',[])

    def test_aelterer_konflikt_archiviert_wiederholung_dedupliziert(self):
        row,inc,arch=self.konflikt();self.assertEqual(len(arch),1)
        self.assertEqual(self.s.get_field('f')['name'],'Lokal')
        self.assertEqual(arch[0]['erwarteter_stand'],row['updated_at'])
        apply_changes(self.s,{'fields':[inc]});self.assertEqual(len(self.s.get_setting('sync_konflikte')),1)
        z.loesen(self.s,arch[0]['id'],'eingehend');self.assertEqual(self.s.get_field('f')['name'],'Fremd')

    def test_aelterer_konflikt_zwischenbearbeitung_sperrt(self):
        row,inc,arch=self.konflikt();self.assertEqual(len(arch),1)
        self.s.save_field({**self.s.get_field('f'),'name':'Spaeter'})
        with self.assertRaises(ValueError):z.loesen(self.s,arch[0]['id'],'lokal')

    def test_gleiche_zeit_abweichend_archiviert(self):
        row,inc,arch=self.konflikt(0);self.assertEqual(len(arch),1)
        z.loesen(self.s,arch[0]['id'],'eingehend');self.assertEqual(self.s.get_field('f')['name'],'Fremd')

    def test_altes_archiv_ohne_erwarteten_stand_bleibt_loesbar(self):
        row,inc,arch=self.konflikt(1);arch[0].pop('erwarteter_stand',None)
        self.s.set_setting('sync_konflikte',arch);z.loesen(self.s,arch[0]['id'],'lokal')
        self.assertEqual(self.s.get_field('f')['name'],'Lokal')

    def restore_fixture(self,invalid=False):
        self.s.close();db=self.root/'ziel.db';cfg=self.root/'ziel.yaml'
        db.write_bytes(b'original-db');cfg.write_bytes(b'original-config')
        wal=Path(str(db)+'-wal');wal.write_bytes(b'original-wal')
        shm=Path(str(db)+'-shm');shm.write_bytes(b'original-shm')
        previous=Path(str(db)+'.vorher');previous.write_bytes(b'older-backup')
        paths=[db,cfg,wal,shm,previous];before={p:p.read_bytes() for p in paths}
        archive=self.root/'restore.zip'
        with zipfile.ZipFile(archive,'w') as a:
            a.writestr('agripilot.db',b'not sqlite' if invalid else (self.root/'db').read_bytes())
            a.writestr('config.yaml',b'new-config')
        return archive,db,cfg,before

    def test_restore_korrupte_db_keine_originalaenderung(self):
        a,db,cfg,before=self.restore_fixture(True)
        with self.assertRaises(ValueError):backup.zurueckspielen(a,db,cfg)
        self.assertEqual({p:p.read_bytes() for p in before},before)

    def test_restore_config_lesefehler_keine_originalaenderung(self):
        a,db,cfg,before=self.restore_fixture();original=zipfile.ZipFile.read
        def lesen(obj,name,*args,**kwargs):
            if name=='config.yaml':raise zipfile.BadZipFile('fixture')
            return original(obj,name,*args,**kwargs)
        with patch.object(zipfile.ZipFile,'read',lesen):
            with self.assertRaises((ValueError,zipfile.BadZipFile)):backup.zurueckspielen(a,db,cfg)
        self.assertEqual({p:p.read_bytes() for p in before},before)

    def test_restore_staging_schreibfehler_keine_originalaenderung(self):
        a,db,cfg,before=self.restore_fixture();original=Path.write_bytes
        def schreiben(path,data):
            if path.name==cfg.name and path != cfg:
                raise OSError('fixture staging failure')
            return original(path,data)
        with patch.object(Path,'write_bytes',schreiben):
            with self.assertRaises(OSError):backup.zurueckspielen(a,db,cfg)
        self.assertEqual({p:p.read_bytes() for p in before},before)

    def test_restore_doppelte_config_keine_originalaenderung(self):
        import warnings
        a,db,cfg,before=self.restore_fixture()
        with warnings.catch_warnings():
            warnings.simplefilter('ignore')
            with zipfile.ZipFile(a,'a') as archive:archive.writestr('config.yaml',b'duplicate')
        with self.assertRaises(ValueError):backup.zurueckspielen(a,db,cfg)
        self.assertEqual({p:p.read_bytes() for p in before},before)

    def test_aelterer_konflikt_lokalauswahl_loesbar(self):
        row,inc,arch=self.konflikt();self.assertEqual(len(arch),1)
        z.loesen(self.s,arch[0]['id'],'lokal')
        self.assertEqual(self.s.get_field('f')['name'],'Lokal')
        self.assertGreater(self.s.get_field('f')['updated_at'],row['updated_at'])

    def test_restore_fehler_zwischen_db_und_config_rollt_alles_zurueck(self):
        a,db,cfg,before=self.restore_fixture()
        for target in (db,cfg,Path(str(db)+'-wal'),Path(str(db)+'-shm')):
            previous=Path(str(target)+'.vorher')
            previous.write_bytes(b'previous-'+target.name.encode())
            before[previous]=previous.read_bytes()
        original=Path.replace;failed=[]
        def tauschen(source,target):
            target=Path(target)
            if not failed and target in (cfg,Path(str(cfg)+'.vorher')):
                failed.append(True);raise OSError('fixture config swap failure')
            return original(source,target)
        with patch.object(Path,'replace',tauschen):
            with self.assertRaises(OSError):backup.zurueckspielen(a,db,cfg)
        self.assertTrue(failed)
        self.assertEqual({p:p.read_bytes() for p in before},before)

    def test_restore_jeder_tauschfehler_rollt_zurueck(self):
        # DB, Konfiguration und vier .vorher-Tausche; WAL/SHM-Löschung separat.
        for position in range(1,7):
            with self.subTest(position=position):
                a,db,cfg,before=self.restore_fixture()
                for target in (db,cfg,Path(str(db)+'-wal'),Path(str(db)+'-shm')):
                    prev=Path(str(target)+'.vorher');prev.write_bytes(b'previous-'+target.name.encode());before[prev]=prev.read_bytes()
                original=Path.replace;calls=[]
                def tauschen(source,target):
                    calls.append(Path(target))
                    if len(calls)==position:raise OSError('fixture swap failure')
                    self.assertEqual(source.parent.parent.resolve(),Path(target).parent.resolve())
                    return original(source,target)
                with patch.object(Path,'replace',tauschen):
                    with self.assertRaises(OSError):backup.zurueckspielen(a,db,cfg)
                self.assertEqual({p:p.read_bytes() for p in before},before)
                self.assertEqual(list(self.root.glob('.agripilot-restore-*')),[])

    def test_restore_wal_shm_loeschfehler_rollt_zurueck(self):
        for suffix in ('-wal','-shm'):
            with self.subTest(suffix=suffix):
                a,db,cfg,before=self.restore_fixture();original=Path.unlink;failed=[]
                def loeschen(path,*args,**kwargs):
                    if path==Path(str(db)+suffix) and not failed:
                        failed.append(True);raise OSError('fixture unlink failure')
                    return original(path,*args,**kwargs)
                with patch.object(Path,'unlink',loeschen):
                    with self.assertRaises(OSError):backup.zurueckspielen(a,db,cfg)
                self.assertEqual({p:p.read_bytes() for p in before},before)

    def test_restore_rollbackfehler_behaelt_originalkopien(self):
        a,db,cfg,before=self.restore_fixture();original=Path.replace
        def tauschen(source,target):
            if Path(target)==cfg or source.name.endswith('.original'):
                raise OSError('fixture persistent disk error')
            return original(source,target)
        with patch.object(Path,'replace',tauschen):
            with self.assertRaisesRegex(OSError,'Rückabwicklung'):backup.zurueckspielen(a,db,cfg)
        rescued=[p.read_bytes() for folder in self.root.glob('.agripilot-restore-*') for p in folder.glob('*.original')]
        self.assertIn(b'original-db',rescued)
        self.assertIn(b'original-wal',rescued)
        self.assertIn(b'original-shm',rescued)
        self.assertEqual(cfg.read_bytes(),before[cfg])

    def test_restore_config_in_anderem_ordner_staging_neben_jedem_ziel(self):
        a,db,cfg,before=self.restore_fixture();other=self.root/'anderes-ziel';other.mkdir()
        newcfg=other/'config.yaml';newcfg.write_bytes(b'old-config')
        original=Path.replace
        def tauschen(source,target):
            self.assertEqual(source.parent.parent.resolve(),Path(target).parent.resolve())
            return original(source,target)
        with patch.object(Path,'replace',tauschen):backup.zurueckspielen(a,db,newcfg)
        self.assertEqual(newcfg.read_bytes(),b'new-config')
        self.assertEqual(Path(str(newcfg)+'.vorher').read_bytes(),b'old-config')
        self.assertEqual(Path(str(db)+'.vorher').read_bytes(),before[db])
