"""Onlineupdate-Dateifehler und parallele Downloads, nur temporaere Dateien."""
import builtins
import hashlib
import io
import tempfile
import threading
import unittest
import urllib.error
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from agripilot import onlineupdate as ou


EXE = b'MZ' + b'audit-update' * 4000


class Antwort(io.BytesIO):
    def __init__(self, data):
        super().__init__(data)
        self.headers = {'Content-Length': str(len(data))}


class OnlineupdateAudit105(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.target = self.root / 'AgriPilot-Setup-9.8.7.exe'
        self.info = dict(version='9.8.7', url='https://update.example/fixture.exe',
                         sha256=hashlib.sha256(EXE).hexdigest(), groesse=len(EXE))

    def download(self, data=EXE, **kwargs):
        with patch.object(ou.urllib.request, 'urlopen', side_effect=lambda *a, **k: Antwort(data)):
            return ou.herunterladen(self.info, self.root, **kwargs)

    def test_gesperrtes_ziel_liefert_onlinefehler_statt_rohem_permissionerror(self):
        self.target.write_bytes(b'alte-datei')
        with patch.object(Path, 'replace', side_effect=PermissionError('fixture installer lock')):
            with self.assertRaises(ou.OnlineFehler) as error:
                self.download()
        self.assertIsInstance(error.exception.__cause__, PermissionError)
        self.assertEqual(self.target.read_bytes(), b'alte-datei')
        self.assertEqual(list(self.root.glob('*.teil')), [])

    def test_vollstaendig_gueltiges_gesperrtes_setup_wird_wiederverwendet(self):
        self.target.write_bytes(EXE)
        progress = []
        with patch.object(Path, 'replace', side_effect=PermissionError('fixture installer lock')) as replace, \
                patch.object(ou.urllib.request, 'urlopen', side_effect=AssertionError('Kein erneuter Download')) as network:
            result = ou.herunterladen(self.info, self.root, fortschritt=lambda a,b: progress.append((a,b)))
        self.assertEqual(result, self.target)
        self.assertEqual(result.read_bytes(), EXE)
        self.assertEqual(progress[-1], (len(EXE), len(EXE)))
        replace.assert_not_called()
        network.assert_not_called()

    def test_aufraeumfehler_verdeckt_verbindungsfehler_nicht(self):
        root_error = urllib.error.URLError('fixture interrupted')
        with patch.object(ou.urllib.request, 'urlopen', side_effect=root_error), \
                patch.object(Path, 'unlink', side_effect=PermissionError('fixture cleanup lock')):
            with self.assertRaises(ou.OnlineFehler) as error:
                ou.herunterladen(self.info, self.root)
        self.assertIs(error.exception.__cause__, root_error)
        self.assertFalse(self.target.exists())

    def test_aufraeumfehler_verdeckt_pruefsummenfehler_nicht(self):
        with patch.object(Path, 'unlink', side_effect=PermissionError('fixture cleanup lock')):
            with self.assertRaises(ou.OnlineFehler) as error:
                self.download(b'MZverstuemmelt')
        self.assertIn('Pr\u00fcfsumme', str(error.exception))
        self.assertFalse(self.target.exists())

    def test_ordnerfehler_wird_onlinefehler(self):
        with patch.object(Path, 'mkdir', side_effect=PermissionError('fixture directory denied')):
            with self.assertRaises(ou.OnlineFehler) as error:
                self.download()
        self.assertIsInstance(error.exception.__cause__, PermissionError)

    def test_ruecklesefehler_wird_onlinefehler_und_raeumt_teildatei_auf(self):
        real_open = builtins.open
        def open_file(file, mode='r', *args, **kwargs):
            if str(file).endswith('.teil') and mode == 'rb':
                raise PermissionError('fixture readback denied')
            return real_open(file, mode, *args, **kwargs)
        with patch.object(builtins, 'open', side_effect=open_file):
            with self.assertRaises(ou.OnlineFehler) as error:
                self.download()
        self.assertIsInstance(error.exception.__cause__, PermissionError)
        self.assertFalse(self.target.exists())
        self.assertEqual(list(self.root.glob('*.teil')), [])

    def test_parallelaufrufe_benutzen_getrennte_teildateien(self):
        barrier = threading.Barrier(2)
        snapshots = []
        def progress(done, total):
            if done == total:
                barrier.wait(timeout=5)
                snapshots.append(set(self.root.glob('*.teil')))
                barrier.wait(timeout=5)
        with patch.object(ou.urllib.request, 'urlopen', side_effect=lambda *a, **k: Antwort(EXE)), \
                ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(ou.herunterladen, self.info, self.root, fortschritt=progress) for _ in range(2)]
            results = []
            errors = []
            for future in futures:
                try:
                    results.append(future.result(timeout=10))
                except Exception as exc:
                    errors.append(type(exc).__name__ + ': ' + str(exc))
        self.assertEqual([len(x) for x in snapshots], [2, 2])
        self.assertEqual(errors, [])
        self.assertEqual(results, [self.target, self.target])
        self.assertEqual(self.target.read_bytes(), EXE)
        self.assertEqual(list(self.root.glob('*.teil')), [])

    def test_gleiche_groesse_und_mz_ersetzen_keine_vollstaendige_sha_pruefung(self):
        self.target.write_bytes(EXE[:-1] + b'x')
        result = self.download()
        self.assertEqual(result.read_bytes(), EXE)

    def test_sha_passend_aber_kein_mz_darf_auch_aus_cache_nicht_starten(self):
        data = b'kein-windows-programm'
        self.info['sha256'] = hashlib.sha256(data).hexdigest()
        self.target.write_bytes(data)
        with self.assertRaises(ou.OnlineFehler) as error:
            self.download(data)
        self.assertIn('kein Windows-Programm', str(error.exception))
        self.assertEqual(self.target.read_bytes(), data)

    def test_fehlende_pruefsumme_erlaubt_auch_vorhandenes_setup_nicht(self):
        self.target.write_bytes(EXE)
        self.info['sha256'] = ''
        with self.assertRaises(ou.OnlineFehler):
            self.download()
        self.assertEqual(self.target.read_bytes(), EXE)


    def test_spaet_parallel_fertiges_setup_nach_renamekonflikt_wiederverwenden(self):
        def replace(source, target):
            Path(target).write_bytes(EXE)
            raise PermissionError('fixture anderer Installer hat gewonnen')
        with patch.object(Path, 'replace', replace):
            result = self.download()
        self.assertEqual(result, self.target)
        self.assertEqual(result.read_bytes(), EXE)
        self.assertEqual(list(self.root.glob('*.teil')), [])

    def test_nachpruefung_und_cleanup_verdecken_renameursache_nicht(self):
        root_error = PermissionError('fixture original rename denied')
        attempted = []
        real_open = builtins.open
        def replace(source, target):
            attempted.append(True)
            raise root_error
        def open_file(file, mode='r', *args, **kwargs):
            if attempted and Path(file) == self.target and mode == 'rb':
                raise OSError('fixture second read denied')
            return real_open(file, mode, *args, **kwargs)
        with patch.object(Path, 'replace', replace), \
                patch.object(builtins, 'open', side_effect=open_file), \
                patch.object(Path, 'unlink', side_effect=OSError('fixture cleanup denied')):
            with self.assertRaises(ou.OnlineFehler) as error:
                self.download()
        self.assertIs(error.exception.__cause__, root_error)
        self.assertFalse(self.target.exists())


if __name__ == '__main__':
    unittest.main()
