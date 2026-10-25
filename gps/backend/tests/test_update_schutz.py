"""Update-Gegenproben: nur temporäre Daten, keine Tasks und keine Hardware."""
import base64
import hashlib
import io
import json
import tempfile
import time
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient
from agripilot.config import Config
from agripilot.nmea import Fix
from agripilot.server import create_app
from agripilot import update


_STANDARD = object()

def paket(manifest=_STANDARD):
    daten = b"fixture"
    if manifest is _STANDARD:
        manifest = {"version":"test", "dateien":{"backend/agripilot/test.py":hashlib.sha256(daten).hexdigest()}}
    b = io.BytesIO()
    with zipfile.ZipFile(b,"w") as z:
        z.writestr("update.json",json.dumps(manifest))
        z.writestr("backend/agripilot/test.py",daten)
    return b.getvalue()


class UpdateSchutzTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        c = Config(); c.path = Path(self.tmp.name)/"config.yaml"
        c.server.data_dir = self.tmp.name
        c.gnss.source = "aus"; c.imu.source = "aus"
        c.corrections.source = "aus"; c.steering.output = "none"
        self.api = create_app(c); self.a = self.api.state.app; self.e = self.a.engine
        self.c = TestClient(self.api,raise_server_exceptions=False)
        self.addCleanup(self.a.store.close); self.addCleanup(self.c.close)
        self.e.fix = Fix(lat=48.,lon=11.,fix_quality=4,speed_ms=0.,received_at=time.time())
        self.wege = [("paket","einspielen",{"version":"test","commit":"fixture"}),
                     ("git","aus_repo_holen",{"geaendert":False}),
                     ("zurueck","zurueckholen",{}),("neustart","neustarten",None)]

    def aufruf(self,weg):
        return self.c.post("/api/update/"+weg,json={"zip":base64.b64encode(paket()).decode()} if weg=="paket" else {})

    def test_alle_wege_bei_fahrt_ohne_nebenwirkung_abgelehnt(self):
        for tempo in (3.,-3.):
            self.e.fix.speed_ms = tempo
            for weg,fn,res in self.wege:
                with self.subTest(tempo=tempo,weg=weg), patch.object(update,fn,return_value=res) as m, patch.object(self.a.steering,"disarm") as disarm, patch.object(self.e,"suspend_job") as suspend:
                    self.assertEqual(self.aufruf(weg).status_code,409)
                    m.assert_not_called(); disarm.assert_not_called(); suspend.assert_not_called()

    def test_scharfe_lenkung_ablehnen_statt_ausschalten(self):
        self.a.steering.armed = True
        for weg,fn,res in self.wege:
            with self.subTest(weg=weg),patch.object(update,fn,return_value=res) as m,patch.object(self.a.steering,"disarm") as disarm:
                self.assertEqual(self.aufruf(weg).status_code,409)
                m.assert_not_called();disarm.assert_not_called()

    def test_frischer_stillstand_erlaubt(self):
        for weg,fn,res in self.wege:
            with self.subTest(weg=weg),patch.object(update,fn,return_value=res) as m:
                r=self.aufruf(weg);self.assertEqual(r.status_code,200,r.text);m.assert_called_once()

    def test_unbekannter_stillstand_bei_fahrkonfiguration_abgelehnt(self):
        self.a.config.gnss.source = "serial"
        for art in ("fehlt","alt","ungueltig","zukunft","nan"):
            self.e.fix=Fix(lat=48.,lon=11.,fix_quality=4,speed_ms=0.,received_at=time.time())
            if art=="fehlt":self.e.fix=None
            if art=="alt":self.e.fix.received_at-=3
            if art=="ungueltig":self.e.fix.fix_quality=0
            if art=="zukunft":self.e.fix.received_at+=10
            if art=="nan":self.e.fix.speed_ms=float("nan")
            for weg,fn,res in self.wege:
                with self.subTest(art=art,weg=weg),patch.object(update,fn,return_value=res) as m:
                    self.assertEqual(self.aufruf(weg).status_code,409);m.assert_not_called()

    def test_master_ohne_gps_und_ohne_arbeit_oder_ausgaenge_erlaubt(self):
        self.e.fix=None
        for weg,fn,res in self.wege:
            with self.subTest(weg=weg),patch.object(update,fn,return_value=res) as m:
                self.assertEqual(self.aufruf(weg).status_code,200);m.assert_called_once()

    def test_ohne_gps_mit_arbeit_oder_motorkonfiguration_verweigert(self):
        self.e.fix=None
        for art in ("job","motor","teilbreiten"):
            self.e.job={} if art=="job" else None
            self.a.config.steering.output="phidget" if art=="motor" else "none"
            self.a.config.teilbreiten.output="udp" if art=="teilbreiten" else "aus"
            with self.subTest(art=art),patch.object(update,"neustarten") as m:
                self.assertEqual(self.aufruf("neustart").status_code,409);m.assert_not_called()

    def test_kaputtes_paket_hat_keine_nebenwirkung(self):
        for daten in (b"kaputt",paket([]),paket({"dateien":{"backend/agripilot/test.py":None}})):
            with self.subTest(daten=daten[:12]),patch.object(self.a.steering,"disarm") as disarm,patch.object(self.e,"suspend_job") as suspend,patch.object(update,"einspielen") as m:
                r=self.c.post("/api/update/paket",json={"zip":base64.b64encode(daten).decode()})
                self.assertEqual(r.status_code,400,r.text)
                disarm.assert_not_called();suspend.assert_not_called();m.assert_not_called()

    def online(self,download=None):
        from contextlib import ExitStack
        with ExitStack() as stack:
            stack.enter_context(patch.object(update,"als_programm",return_value=True))
            stack.enter_context(patch.object(self.a,"online_pruefen",return_value={"fehler":"","verfuegbar":True,"neu":"test"}))
            self.a.online_info={}
            stack.enter_context(patch("agripilot.onlineupdate.herunterladen",side_effect=download,return_value=Path(self.tmp.name)/"fake.exe"))
            installer=stack.enter_context(patch("agripilot.exe.setup_starten"))
            r=self.c.post("/api/update/online")
            return r,installer.called

    def test_online_rueckwaerts_sowie_unbekannt_verweigert(self):
        self.a.config.gnss.source="serial"
        for art in ("rueckwaerts","alt","fehlt"):
            self.e.fix=Fix(lat=48.,lon=11.,fix_quality=4,speed_ms=-3 if art=="rueckwaerts" else 0,received_at=time.time()-(3 if art=="alt" else 0))
            if art=="fehlt":self.e.fix=None
            r,gerufen=self.online();self.assertEqual(r.status_code,409);self.assertFalse(gerufen)

    def test_online_frischer_stillstand_und_verwaltung_erlaubt(self):
        self.a.config.gnss.source="serial"
        r,gerufen=self.online();self.assertEqual(r.status_code,200,r.text);self.assertTrue(gerufen)
        self.a.config.gnss.source="aus";self.e.fix=None
        r,gerufen=self.online();self.assertEqual(r.status_code,200,r.text);self.assertTrue(gerufen)

    def test_paket_nach_pruefung_erneut_stillstand_verlangen(self):
        echt=update.paket_pruefen
        def pruefen(daten):
            ergebnis=echt(daten);self.e.fix.speed_ms=-3
            return ergebnis
        with patch.object(update,"paket_pruefen",side_effect=pruefen),patch.object(update,"einspielen") as schreiben,patch.object(self.e,"suspend_job") as sichern:
            self.assertEqual(self.aufruf("paket").status_code,409)
            schreiben.assert_not_called();sichern.assert_not_called()

    def test_online_nach_download_erneut_pruefen(self):
        self.a.config.gnss.source="serial"
        for art in ("rueckwaerts","alt","scharf"):
            self.a.steering.armed=False
            self.e.fix=Fix(lat=48.,lon=11.,fix_quality=4,speed_ms=0.,received_at=time.time())
            def download(*args):
                if art=="rueckwaerts":self.e.fix.speed_ms=-3
                if art=="alt":self.e.fix.received_at-=3
                if art=="scharf":self.a.steering.armed=True
                return Path(self.tmp.name)/"fake.exe"
            r,gerufen=self.online(download);self.assertEqual(r.status_code,409);self.assertFalse(gerufen)


class UpdateDateiSchutzTests(unittest.TestCase):
    def test_manifest_typen_werden_lesbar_abgelehnt(self):
        gut = {"backend/agripilot/test.py":hashlib.sha256(b"fixture").hexdigest()}
        for manifest in ([],None,1,"text",{"dateien":[]},{"dateien":{"backend/agripilot/test.py":None}},
                         {"dateien":gut,"version":[]},{"dateien":gut,"commit":2},
                         {"dateien":gut,"erstellt":{}},{"dateien":gut,"ordner":"backend"},
                         {"dateien":gut,"ordner":[[]]}):
            with self.subTest(manifest=manifest),self.assertRaises(update.UpdateFehler):update.paket_pruefen(paket(manifest))

    def test_einspielfehler_entfernt_bereits_neu_eingesetzte_dateien(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);alt=root/"backend";alt.mkdir();(alt/"current.py").write_text("current")
            daten={"backend/a.py":b"a","backend/b.py":b"b"}
            b=io.BytesIO()
            with zipfile.ZipFile(b,"w") as z:
                z.writestr("update.json",json.dumps({"dateien":{k:hashlib.sha256(v).hexdigest() for k,v in daten.items()}}))
                for k,v in daten.items():z.writestr(k,v)
            real=update.shutil.move
            def fehler(src,dst,*args,**kwargs):
                if Path(src)==root/".update/neu/backend/b.py":raise OSError("simulierte Dateisperre")
                return real(src,dst,*args,**kwargs)
            with patch.object(update.shutil,"move",side_effect=fehler),self.assertRaises(update.UpdateFehler):
                update.einspielen(b.getvalue(),wurzel_pfad=root,pip=False)
            self.assertEqual((alt/"current.py").read_text(),"current")
            self.assertEqual(sorted(p.name for p in alt.iterdir()),["current.py"])

    def test_rueckholfehler_stellt_beide_fassungen_wieder_her(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);alt=root/"backend";alt.mkdir();(alt/"current.py").write_text("current")
            quelle=root/".update/sicherung-20000101-000000/backend";quelle.mkdir(parents=True)
            (quelle/"a.py").write_text("a");(quelle/"b.py").write_text("b")
            real=update.shutil.move
            def fehler(src,dst,*args,**kwargs):
                if Path(src)==quelle/"b.py":raise OSError("simulierte Dateisperre")
                return real(src,dst,*args,**kwargs)
            with patch.object(update.shutil,"move",side_effect=fehler),self.assertRaises(update.UpdateFehler):
                update.zurueckholen(wurzel_pfad=root)
            self.assertEqual((alt/"current.py").read_text(),"current")
            self.assertEqual(sorted(p.name for p in alt.iterdir()),["current.py"])
            self.assertEqual((quelle/"a.py").read_text(),"a")
            self.assertEqual((quelle/"b.py").read_text(),"b")
