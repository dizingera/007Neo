"""Statusfehler dürfen weder Programmstand noch Update-Sicherungen beschädigen."""
import hashlib
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch
from agripilot import update

class UpdateStatusTransaktionTests(unittest.TestCase):
    def test_statusfehler_rollen_dateien_und_status_bytegleich_zurueck(self):
        for modus in ("einspielen","zurueckholen"):
            for fehlerart in ("schreiben","tauschen"):
                for status_vorhanden in (True,False):
                    with self.subTest(modus=modus,fehler=fehlerart,status=status_vorhanden),tempfile.TemporaryDirectory() as d:
                        root=Path(d);(root/"backend").mkdir();(root/"backend/current.py").write_bytes(b"current")
                        status=root/"update.json";alt=b'{ "version" : "alt", "extra": true }\n'
                        if status_vorhanden:status.write_bytes(alt)
                        quelle=root/".update/sicherung-20000101-000000"
                        (quelle/"backend").mkdir(parents=True);(quelle/"backend/previous.py").write_bytes(b"previous")
                        b=io.BytesIO();inhalt=b"new"
                        with zipfile.ZipFile(b,"w") as z:
                            z.writestr("update.json",json.dumps({"version":"new","dateien":{"backend/new.py":hashlib.sha256(inhalt).hexdigest()}}))
                            z.writestr("backend/new.py",inhalt)
                        original_write=Path.write_text;original_replace=Path.replace
                        def schreiben(path,*args,**kwargs):
                            if fehlerart=="schreiben" and path.name=="update.json":raise OSError("fixture status write")
                            return original_write(path,*args,**kwargs)
                        failed=[]
                        def tauschen(path,target):
                            if fehlerart=="tauschen" and Path(target)==status and not failed:
                                failed.append(True);raise OSError("fixture status swap")
                            return original_replace(path,target)
                        with patch.object(Path,"write_text",schreiben),patch.object(Path,"replace",tauschen),self.assertRaises(update.UpdateFehler):
                            if modus=="einspielen":update.einspielen(b.getvalue(),root,pip=False)
                            else:update.zurueckholen(quelle.name,root)
                        self.assertEqual((root/"backend/current.py").read_bytes(),b"current")
                        self.assertEqual(sorted(p.name for p in (root/"backend").iterdir()),["current.py"])
                        self.assertEqual(status.read_bytes() if status.exists() else None,alt if status_vorhanden else None)
                        self.assertEqual((quelle/"backend/previous.py").read_bytes(),b"previous")
                        if fehlerart=="tauschen":self.assertTrue(failed)
