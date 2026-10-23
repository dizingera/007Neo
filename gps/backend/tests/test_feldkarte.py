"""Feldkarte: alle Felder in WGS84 für die Schlagauswahl auf der Karte."""

import tempfile
import unittest

from test_tablet_lenkung import geraet


class Feldkarte(unittest.TestCase):
    def test_all_fields_with_boundary_come_in_wgs84_and_the_loaded_one_is_marked(self):
        with tempfile.TemporaryDirectory() as ordner, geraet(ordner, "client", "tablet") as c:
            store = c.app.state.app.store
            a = store.save_field(dict(name="Acker", datum_lat=48.4, datum_lon=9.6,
                                      boundary=[[0, 0], [200, 0], [200, 100], [0, 100]]))
            store.save_field(dict(name="Ohne Grenze", datum_lat=48.4, datum_lon=9.6, boundary=[]))
            daten = c.get("/api/feldkarte").json()
            self.assertEqual([f["name"] for f in daten["felder"]], ["Acker"])
            feld = daten["felder"][0]
            lat, lon = feld["grenze"][0]
            self.assertAlmostEqual(lat, 48.4, places=5)
            self.assertAlmostEqual(lon, 9.6, places=5)
            # 200 m nach Osten sind bei 48,4° rund 0,0027° Länge.
            self.assertAlmostEqual(feld["grenze"][1][1] - lon, 200 / (6371000 * 0.6639 * 3.14159265 / 180), places=4)
            self.assertIsNone(feld["luftbild"])          # nicht von selbst geholt
            self.assertIsNone(daten["geladen"])
            c.post(f"/api/fields/{a['id']}/load")
            self.assertEqual(c.get("/api/feldkarte").json()["geladen"], a["id"])


if __name__ == "__main__":
    unittest.main()
