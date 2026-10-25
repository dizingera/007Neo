"""Gegenproben mit echter temporärer SQLite-Datenbank und Coverage-Pack."""
import math
import re
import struct
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

from agripilot.storage import Storage
from agripilot.coverage import CoverageMap
from agripilot import arbeitsbericht as a


class ArbeitsberichtTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.store=Storage(Path(self.tmp.name)/"test.db")
        self.field=self.store.save_field(dict(name="Müllers Feld",datum_lat=48,datum_lon=9,
            boundary=[[0,0],[10,0],[10,10],[0,10]],area_ha=999))
        self.job=self.store.start_job(self.field["id"],"test",operation="Säen")
        self.cov=CoverageMap(1)
        self.cov.cells={(x,y) for x in range(5) for y in range(10)}
        self.save()

    def tearDown(self):
        self.store.db.close()
        self.tmp.cleanup()

    def save(self,**kw):
        self.store.update_job(self.job["id"],coverage=self.cov.pack(),working_time_s=120,
                              area_ha=444,**kw)

    def test_job_raster_statt_hektarsumme(self):
        r=a.restflaeche(self.store,self.job["id"])
        self.assertTrue(r["bekannt"])
        self.assertAlmostEqual(r["gesamt_ha"],.01)
        self.assertAlmostEqual(r["bearbeitet_ha"],.005)
        self.assertAlmostEqual(r["rest_ha"],.005)
        self.assertAlmostEqual(r["prozent"],50)
        self.assertAlmostEqual(r["restzeit_min"],2)
        self.assertEqual(len(r["kartenZellen"]),50)
        self.assertEqual(r["karten_grundflaeche"],"verbleibend")
        self.assertTrue(all(c["status"]=="bearbeitet" for c in r["kartenZellen"]))
        self.assertIsNone(r["ueberlappt"])
        self.assertFalse(r["ueberlappung_raeumlich_bekannt"])

    def test_anderer_auftrag_wird_nicht_einbezogen(self):
        other=self.store.start_job(self.field["id"],"other")
        full=CoverageMap(1); full.cells={(x,y) for x in range(10) for y in range(10)}
        self.store.update_job(other["id"],coverage=full.pack())
        self.assertEqual(a.restflaeche(self.store,self.job["id"])["prozent"],50)

    def test_randzellen_anteilig_auch_zentrum_ausserhalb(self):
        f=dict(self.field,boundary=[[.9,.9],[1.1,.9],[1.1,1.1],[.9,1.1]])
        self.store.save_field(f)
        self.cov.cells={(0,0),(9,9)}; self.save()
        r=a.restflaeche(self.store,self.job["id"])
        self.assertAlmostEqual(r["prozent"],25)
        self.assertEqual(len(r["kartenZellen"]),1)
        self.assertAlmostEqual(sum(c["flaeche_m2"] for c in r["kartenZellen"]),.01)

    def test_konkave_grenze_und_negatives_raster(self):
        self.store.save_field(dict(self.field,boundary=[[-2,-2],[0,-2],[0,-1],[-1,-1],[-1,0],[-2,0]]))
        self.cov.cells={(-2,-2),(-1,-1)}; self.save()
        r=a.restflaeche(self.store,self.job["id"])
        self.assertAlmostEqual(r["prozent"],100/3)
        self.assertAlmostEqual(r["gesamt_ha"],3/10000)

    def test_concave_disconnected_clip_area(self):
        u=[[0,0],[3,0],[3,3],[2,3],[2,1],[1,1],[1,3],[0,3]]
        self.assertAlmostEqual(a._flaeche(a._clip(u,0,2,3)),2)

    def test_keine_karte_ist_unbekannt_nicht_null(self):
        self.store.update_job(self.job["id"],coverage=None)
        r=a.restflaeche(self.store,self.job["id"])
        self.assertFalse(r["bekannt"])
        self.assertIsNone(r["prozent"])
        self.assertIsNone(r["rest_ha"])

    def test_leeres_gueltiges_pack_ist_null(self):
        self.cov.cells=set(); self.save()
        r=a.restflaeche(self.store,self.job["id"])
        self.assertEqual(r["prozent"],0)
        self.assertIsNone(r["restzeit_min"])

    def test_pack_fehler_bleibt_unbekannt(self):
        for blob in (b"",b"123",struct.pack("<f",float("nan")),struct.pack("<f",0),self.cov.pack()+b"x"):
            with self.subTest(blob=blob[:4]):
                self.store.update_job(self.job["id"],coverage=blob)
                self.assertIsNone(a.restflaeche(self.store,self.job["id"])["prozent"])

    def test_zu_wenig_zeit_oder_flaeche(self):
        for seconds in (0,119.99):
            self.store.update_job(self.job["id"],working_time_s=seconds)
            self.assertIsNone(a.restflaeche(self.store,self.job["id"])["restzeit_min"])
        self.cov.cells={(0,0)}; self.save()
        self.assertIsNone(a.restflaeche(self.store,self.job["id"])["restzeit_min"])

    def test_grenzen_und_begrenzte_kosten(self):
        for poly in ([],[[0,0],[2,2],[0,2],[2,0]],[[0,0],[float('inf'),0],[0,1]]):
            self.store.save_field(dict(self.field,boundary=poly))
            r=a.restflaeche(self.store,self.job["id"])
            self.assertFalse(r["bekannt"])
            self.assertIsNone(r["rest_ha"])

    def test_kosten_persistenz_und_null_unbekannt(self):
        initial=a.kosten_lesen(self.store,self.job["id"])
        self.assertIsNone(initial["gesamt_eur"])
        result=a.kosten_speichern(self.store,self.job["id"],dict(diesel_liter=20,
            diesel_preis_eur_l=1.5,betriebsmittel_eur=12,stundensatz_eur=60))
        self.assertAlmostEqual(result["gesamt_eur"],44)
        self.assertEqual(result["herkunft"],"manuell")
        self.assertEqual(result["berechnung_herkunft"],"schaetzung")
        second=Storage(self.store.path,schema=False)
        try:
            self.assertEqual(a.kosten_lesen(second,self.job["id"])["gesamt_eur"],44)
        finally:
            second.db.close()
        self.assertEqual(self.store.get_job(self.job["id"])["area_ha"],444)

    def test_invalid_kosten_atomar_kein_write(self):
        good=dict(diesel_liter=0,diesel_preis_eur_l=0,betriebsmittel_eur=0,stundensatz_eur=0)
        a.kosten_speichern(self.store,self.job["id"],good)
        for bad in (float('nan'),float('inf'),-1,True,'2',100001):
            with self.assertRaises(ValueError):
                a.kosten_speichern(self.store,self.job["id"],dict(good,diesel_liter=bad))
            self.assertEqual(a.kosten_lesen(self.store,self.job["id"])["gesamt_eur"],0)
        with self.assertRaises(ValueError):
            a.kosten_speichern(self.store,self.job["id"],{"width_m":4})

    def test_fehlender_oder_geloeschter_job(self):
        for fn in (a.restflaeche,a.kosten_lesen):
            with self.assertRaises(ValueError): fn(self.store,"missing")
        self.store.delete_job(self.job["id"])
        with self.assertRaises(ValueError): a.kosten_speichern(self.store,self.job["id"],{})

    def test_parallel_eigene_sqlite_verbindung(self):
        with ThreadPoolExecutor(2) as pool:
            futures=[pool.submit(a.restflaeche,self.store,self.job["id"]) for _ in range(2)]
            self.assertEqual([f.result()["prozent"] for f in futures],[50,50])
        a._BERECHNUNG.acquire(); a._BERECHNUNG.acquire()
        try:
            with self.assertRaises(RuntimeError): a.restflaeche(self.store,self.job["id"])
        finally:
            a._BERECHNUNG.release(); a._BERECHNUNG.release()

    def test_pdf_echte_bytes_xref_umriss_mehrseitig(self):
        other=self.store.start_job(self.field["id"],"other",operation="Grubbern")
        pdf=a.bericht_pdf(self.store,self.field["id"])
        self.assertTrue(pdf.startswith(b"%PDF-1.4"))
        self.assertTrue(pdf.endswith(b"%%EOF\n"))
        self.assertIn(b"/Count 3",pdf)
        self.assertIn(b"M\xfcllers Feld",pdf)
        self.assertIn(b"50.0000 210.0000 m",pdf)
        offset=int(re.search(rb"startxref\n(\d+)",pdf).group(1))
        self.assertEqual(pdf[offset:offset+4],b"xref")
        for match in re.finditer(rb"(\d{10}) 00000 n",pdf):
            self.assertRegex(pdf[int(match.group(1)):],rb"^\d+ 0 obj")
        selected=a.bericht_pdf(self.store,self.field["id"],job_id=self.job["id"])
        self.assertIn(b"/Count 2",selected)
        self.assertNotIn(other["id"].encode(),selected)
        self.assertIn(b"/Count 1",a.bericht_pdf(self.store,self.field["id"],jahr=1970))

    def test_kartengrenze_kuerzt_keine_flaechen(self):
        with patch.object(a,"MAX_KARTENZELLEN",3):
            r=a.restflaeche(self.store,self.job["id"])
        self.assertTrue(r["bekannt"])
        self.assertEqual(r["prozent"],50)
        self.assertFalse(r["karten_vollstaendig"])
        self.assertEqual(r["karten_grundflaeche"],"unbekannt")
        self.assertEqual(len(r["kartenZellen"]),3)

    def test_doppelte_packzellen_kein_doppelter_fortschritt(self):
        blob=struct.pack("<f",1)+struct.pack("<ii",0,0)*50
        self.store.update_job(self.job["id"],coverage=blob)
        r=a.restflaeche(self.store,self.job["id"])
        self.assertAlmostEqual(r["prozent"],1)
        self.assertIsNone(r["ueberlappt"])

    def test_mutation_committet_keine_fremde_transaktion(self):
        self.store.db.execute("BEGIN")
        a.kosten_speichern(self.store,self.job["id"],{})
        self.assertTrue(self.store.db.in_transaction)
        self.store.db.rollback()

    def test_kosten_extreme_integer_und_teilbetrag(self):
        with self.assertRaises(ValueError):
            a.kosten_speichern(self.store,self.job["id"],{"diesel_liter":10**1000})
        r=a.kosten_speichern(self.store,self.job["id"],{"betriebsmittel_eur":12})
        self.assertEqual(r["betriebsmittel_eur"],12)
        self.assertFalse(r["vollstaendig"])
        self.assertIsNone(r["gesamt_eur"])

    def test_snapshot_bleibt_konsistent_bei_fremdem_write(self):
        with a._snapshot(self.store) as reader:
            before=reader.get_job(self.job["id"])
            self.store.update_job(self.job["id"],area_ha=123)
            self.assertEqual(reader.get_job(self.job["id"])["area_ha"],before["area_ha"])
        self.assertEqual(self.store.get_job(self.job["id"])["area_ha"],123)

    def test_bericht_geteiltes_aufwandsbudget(self):
        with a._snapshot(self.store) as reader:
            job=reader.get_job(self.job["id"])
            budget=[300]
            first=a._rest(reader,job,budget=budget,zeige_karte=False)
            second=a._rest(reader,job,budget=budget,zeige_karte=False)
            self.assertTrue(first["bekannt"])
            self.assertFalse(second["bekannt"])
            self.assertIsNone(second["prozent"])
            self.assertEqual(first["kartenZellen"],[])

    def test_langer_feldname_vollstaendig_mehrseitig(self):
        name="Langes Feld ("+"a"*400+") ENDE"
        self.store.save_field(dict(self.field,name=name))
        pdf=a.bericht_pdf(self.store,self.field["id"],jahr=1970)
        self.assertIn(b"/Count 2",pdf)
        self.assertIn(b"ENDE",pdf)
        self.assertIn(b"Fortsetzung",pdf)

    def test_20ha_mit_kleiner_coverage_kein_flaechenlimit(self):
        self.store.save_field(dict(self.field,boundary=[[0,0],[500,0],[500,400],[0,400]]))
        self.cov=CoverageMap(.5)
        self.cov.cells={(x,0) for x in range(40)}
        self.save()
        with patch.object(a,"_clip",wraps=a._clip) as clip:
            r=a.restflaeche(self.store,self.job["id"])
        self.assertTrue(r["bekannt"])
        self.assertEqual(r["gesamt_ha"],20)
        self.assertAlmostEqual(r["bearbeitet_ha"],.001)
        self.assertAlmostEqual(r["rest_ha"],19.999)
        self.assertLessEqual(clip.call_count,40)
        self.assertEqual(len(r["kartenZellen"]),40)

    def test_riesiges_feld_leere_coverage_ohne_vollraster(self):
        self.store.save_field(dict(self.field,boundary=[[0,0],[1e6,0],[1e6,1e6],[0,1e6]]))
        self.cov.cells=set(); self.save()
        with patch.object(a,"_clip",side_effect=AssertionError("Kein Vollraster")):
            r=a.restflaeche(self.store,self.job["id"])
        self.assertTrue(r["bekannt"])
        self.assertEqual(r["prozent"],0)
        self.assertEqual(r["kartenZellen"],[])

    def test_belegte_zellen_und_aufwand_weiter_begrenzt(self):
        with patch.object(a,"MAX_ZELLEN",20):
            r=a.restflaeche(self.store,self.job["id"])
        self.assertFalse(r["bekannt"])
        self.assertIsNone(r["prozent"])
        with patch.object(a,"MAX_AUFWAND",20):
            r=a.restflaeche(self.store,self.job["id"])
        self.assertFalse(r["bekannt"])

    def test_20ha_vollstaendig_800000_belegte_zellen(self):
        from array import array
        import sys
        self.store.save_field(dict(self.field,boundary=[[0,0],[500,0],[500,400],[0,400]]))
        data=array("i")
        for x in range(1000):
            for y in range(800):
                data.append(x); data.append(y)
        if sys.byteorder!="little": data.byteswap()
        blob=struct.pack("<f",.5)+data.tobytes()
        self.store.update_job(self.job["id"],coverage=blob)
        r=a.restflaeche(self.store,self.job["id"])
        self.assertTrue(r["bekannt"])
        self.assertEqual(r["bearbeitet_ha"],20)
        self.assertEqual(r["rest_ha"],0)
        self.assertEqual(r["prozent"],100)
        self.assertFalse(r["karten_vollstaendig"])
        self.assertEqual(len(r["kartenZellen"]),a.MAX_KARTENZELLEN)

    def test_pdf_lesbare_titel_zahlen_dauer_statt_db_formate(self):
        from datetime import datetime, timezone
        self.store.update_job(self.job["id"],started_at=datetime(2026,7,8,10,15,42,123456,tzinfo=timezone.utc).timestamp(),
                              working_time_s=5400,distance_m=1234.5,area_ha=0,overlap_ha=0)
        pdf=a.bericht_pdf(self.store,self.field["id"],job_id=self.job["id"])
        self.assertIn("Müllers Feld".encode("cp1252"),pdf)
        self.assertIn("Säen".encode("cp1252"),pdf)
        self.assertIn(b"1.234,50 m",pdf)
        self.assertIn(b"0,00 ha",pdf)
        self.assertIn(b"1 h 30 min",pdf)
        self.assertIn(b"08.07.2026,",pdf)
        self.assertNotIn(b"2026-07-08T",pdf)
        self.assertNotIn(b"123456",pdf)
        self.assertNotIn(b"Jobkennzahl",pdf)
        self.assertNotIn(self.field["id"].encode(),pdf)
        self.assertNotIn(self.job["id"].encode(),pdf)
        self.assertIn(self.job["id"][:8].encode(),pdf)
        self.assertIn(b"Seite 1 von",pdf)

    def test_datum_berlin_oder_klarer_utc_fallback(self):
        from datetime import datetime, timezone, timedelta
        from zoneinfo import ZoneInfoNotFoundError
        stamp=datetime(2026,7,8,10,15,42,tzinfo=timezone.utc).timestamp()
        with patch("zoneinfo.ZoneInfo",return_value=timezone(timedelta(hours=2))):
            text=a._datum(stamp)
        self.assertEqual(text,"08.07.2026, 12:15 Uhr (Europe/Berlin, UTC+02:00)")
        with patch("zoneinfo.ZoneInfo",side_effect=ZoneInfoNotFoundError("test")):
            text=a._datum(stamp)
        self.assertEqual(text,"08.07.2026, 10:15 Uhr (UTC, UTC+00:00)")
        self.assertEqual(a._dauer(0),"0 min")
        self.assertEqual(a._dauer(35),"unter 1 min")
        self.assertEqual(a._dauer(None),"unbekannt")

    def test_pdf_ungueltige_auswahl(self):
        f=self.store.save_field(dict(name="anders",datum_lat=48,datum_lon=9,boundary=self.field["boundary"]))
        with self.assertRaises(ValueError): a.bericht_pdf(self.store,f["id"],job_id=self.job["id"])
        with self.assertRaises(ValueError): a.bericht_pdf(self.store,self.field["id"],jahr=True)


if __name__ == "__main__":
    unittest.main()
