"""NTRIP-Modul: Antworten des Casters, Stücke von NTRIP 2, RTCM-Prüfung, Mountpoint-Liste.

Der Caster ist hier ein kleiner lokaler Server, der sich wie die echten verhält:
NTRIP 1 mit "ICY 200 OK" und ohne Leerzeile, NTRIP 2 mit "chunked", und die
Mountpoint-Liste, die ein Caster bei unbekanntem Mountpoint schickt.
"""

import asyncio
import math
import types
import unittest

from agripilot import ntrip
from agripilot.ntrip_protokoll import (
    CasterFehler, ChunkedDecoder, RtcmPruefer, antwort_pruefen, ecef_zu_wgs84, rtcm_rahmen,
    rtcm_urteil, sourcetable_lesen,
)

HOF = (48.4113, 9.5969)


def _bits_packen(felder: list[tuple[int, int]]) -> bytes:
    wert, laenge = 0, 0
    for zahl, breite in felder:
        wert = (wert << breite) | (zahl & ((1 << breite) - 1))
        laenge += breite
    rest = (-laenge) % 8
    return (wert << rest).to_bytes((laenge + rest) // 8, "big")


def _wgs84_zu_ecef(lat: float, lon: float, h: float) -> tuple[float, float, float]:
    a, f = 6378137.0, 1 / 298.257223563
    e2 = f * (2 - f)
    la, lo = math.radians(lat), math.radians(lon)
    n = a / math.sqrt(1 - e2 * math.sin(la) ** 2)
    return ((n + h) * math.cos(la) * math.cos(lo), (n + h) * math.cos(la) * math.sin(lo),
            (n * (1 - e2) + h) * math.sin(la))


def basis_1005(lat: float, lon: float, h: float = 520.0, station: int = 42) -> bytes:
    x, y, z = (round(v * 1e4) for v in _wgs84_zu_ecef(lat, lon, h))
    return rtcm_rahmen(_bits_packen([
        (1005, 12), (station, 12), (0, 6), (1, 1), (1, 1), (1, 1), (0, 1),
        (x, 38), (0, 1), (0, 1), (y, 38), (0, 2), (z, 38)]))


def msm(typ: int, laenge: int = 30) -> bytes:
    return rtcm_rahmen(_bits_packen([(typ, 12)]) + bytes(laenge))


STROM = basis_1005(48.43, 9.61) + msm(1074) + msm(1084) + msm(1094) + msm(1124)


class ChunkedTests(unittest.TestCase):
    def test_pieces_are_unwrapped_wherever_the_network_cuts_them(self):
        nutz = STROM * 3
        verpackt = b""
        for i in range(0, len(nutz), 77):
            stueck = nutz[i:i + 77]
            verpackt += f"{len(stueck):x}\r\n".encode() + stueck + b"\r\n"
        verpackt += b"0\r\n\r\n"
        for schnitt in (1, 2, 5, 13, 64, 1000):
            decoder = ChunkedDecoder()
            heraus = b"".join(decoder.feed(verpackt[i:i + schnitt])
                              for i in range(0, len(verpackt), schnitt))
            self.assertEqual(heraus, nutz, schnitt)
            self.assertTrue(decoder.ende)

    def test_garbage_length_is_reported(self):
        with self.assertRaises(CasterFehler):
            ChunkedDecoder().feed(b"zz\r\n")


class AntwortTests(unittest.TestCase):
    def test_the_casters_answers_are_told_apart(self):
        self.assertTrue(antwort_pruefen("ICY 200 OK", {}).ok)
        zwei = antwort_pruefen("HTTP/1.1 200 OK", {"transfer-encoding": "chunked"})
        self.assertTrue(zwei.ok)
        self.assertTrue(zwei.chunked)
        self.assertFalse(antwort_pruefen("SOURCETABLE 200 OK", {}).ok)
        liste = antwort_pruefen("HTTP/1.1 200 OK", {"content-type": "gnss/sourcetable"})
        self.assertFalse(liste.ok)
        self.assertIn("Mountpoint", liste.fehler)
        self.assertIn("Passwort", antwort_pruefen("HTTP/1.1 401 Unauthorized", {}).fehler)
        self.assertIn("404", antwort_pruefen("HTTP/1.0 404 Not Found", {}).fehler)
        self.assertIn("Unerwartete", antwort_pruefen("hallo", {}).fehler)


class RtcmTests(unittest.TestCase):
    def test_frames_types_and_base_position_are_read(self):
        pruefer = RtcmPruefer()
        # in krummen Stücken, mit Müll davor
        daten = b"\x00\x13Text\r\n" + STROM
        for i in range(0, len(daten), 7):
            pruefer.feed(daten[i:i + 7])
        z = pruefer.zusammenfassung(HOF)
        self.assertEqual(z["rahmen"], 5)
        self.assertEqual(z["typen"], [1005, 1074, 1084, 1094, 1124])
        self.assertEqual(z["systeme"], ["BeiDou", "GLONASS", "GPS", "Galileo"])
        self.assertEqual(z["station"], 42)
        self.assertAlmostEqual(z["basis"][0], 48.43, places=6)
        self.assertAlmostEqual(z["basis"][1], 9.61, places=6)
        self.assertAlmostEqual(z["basis"][2], 520.0, places=1)
        self.assertAlmostEqual(z["basis_km"], 2.3, delta=0.2)
        self.assertGreater(z["fremde_bytes"], 0)
        self.assertEqual(rtcm_urteil(z, len(daten)), "")

    def test_a_damaged_frame_is_counted_not_trusted(self):
        kaputt = bytearray(msm(1074))
        kaputt[10] ^= 0xFF
        pruefer = RtcmPruefer()
        pruefer.feed(bytes(kaputt) + msm(1084))
        self.assertEqual(pruefer.crc_fehler, 1)
        self.assertEqual(sorted(pruefer.typen), [1084])

    def test_text_instead_of_rtcm_gets_a_clear_verdict(self):
        pruefer = RtcmPruefer()
        text = b"STR;BASIS1;Hof;RTCM 3.2;;2;GPS;;DEU;48.4;9.6;0;0;x;none;N;N;0;\r\n" * 20
        pruefer.feed(text)
        self.assertIn("keine RTCM3", rtcm_urteil(pruefer.zusammenfassung(), len(text)))

    def test_a_far_base_is_named(self):
        pruefer = RtcmPruefer()
        pruefer.feed(basis_1005(49.5, 9.6) + msm(1074))
        self.assertIn("km entfernt", rtcm_urteil(pruefer.zusammenfassung(HOF), 100))

    def test_ecef_round_trip(self):
        lat, lon, h = ecef_zu_wgs84(*_wgs84_zu_ecef(48.4113, 9.5969, 512.3))
        self.assertAlmostEqual(lat, 48.4113, places=8)
        self.assertAlmostEqual(lon, 9.5969, places=8)
        self.assertAlmostEqual(h, 512.3, places=3)


SOURCETABLE = (
    "STR;FERN;Fern;RTCM 3.2;1005(10);2;GPS+GLO;;DEU;49.50;9.60;0;0;x;none;B;N;9600;\r\n"
    "STR;HOF;Hofbasis;RTCM 3.2;1005(10),1074(1);2;GPS+GLO+GAL+BDS;;DEU;48.43;9.61;0;0;x;none;B;N;9600;\r\n"
    "STR;VRS_3_4G;SAPOS VRS;RTCM 3.2;;2;GPS+GLO;SAPOS;DEU;0.00;0.00;1;1;x;none;B;Y;9600;\r\n"
    "CAS;caster;2101;x;y;0;DEU;0;0;\r\n"
    "ENDSOURCETABLE\r\n"
)


class SourcetableTests(unittest.TestCase):
    def test_nearest_base_first_and_vrs_flagged(self):
        liste = sourcetable_lesen(SOURCETABLE, HOF)
        self.assertEqual([e["mountpoint"] for e in liste], ["HOF", "FERN", "VRS_3_4G"])
        self.assertAlmostEqual(liste[0]["km"], 2.3, delta=0.2)
        vrs = liste[-1]
        self.assertTrue(vrs["netz"])
        self.assertTrue(vrs["nmea"])
        self.assertTrue(vrs["gebuehr"])
        self.assertIsNone(vrs["km"])


class FalscherCaster:
    """Ein lokaler Caster mit wählbarem Verhalten."""

    def __init__(self, art: str) -> None:
        self.art = art
        self.anfragen: list[bytes] = []
        self.server = None

    async def start(self) -> int:
        self.server = await asyncio.start_server(self._kunde, "127.0.0.1", 0)
        return self.server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        self.server.close()
        await self.server.wait_closed()

    async def _kunde(self, reader, writer) -> None:
        anfrage = await reader.readuntil(b"\r\n\r\n")
        self.anfragen.append(anfrage)
        pfad = anfrage.split(b" ")[1]
        try:
            if pfad == b"/" or self.art == "liste":
                writer.write(b"SOURCETABLE 200 OK\r\nContent-Type: text/plain\r\n\r\n"
                             + SOURCETABLE.encode())
            elif self.art == "v1":
                # Wie manche alten Caster: Daten direkt nach der Statuszeile.
                writer.write(b"ICY 200 OK\r\n" + STROM)
            elif self.art == "v2":
                writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: gnss/data\r\n"
                             b"Transfer-Encoding: chunked\r\n\r\n")
                for teil in (STROM[:50], STROM[50:]):
                    writer.write(f"{len(teil):x}\r\n".encode() + teil + b"\r\n")
            elif self.art == "401":
                writer.write(b"HTTP/1.1 401 Unauthorized\r\n\r\n")
            await writer.drain()
            await asyncio.sleep(0.3)
        finally:
            writer.close()


def _cfg(port: int, mountpoint: str = "HOF", **extra):
    werte = dict(host="127.0.0.1", port=port, mountpoint=mountpoint, username="andy",
                 password="geheim", send_gga=False, gga_interval_s=10)
    werte.update(extra)
    return types.SimpleNamespace(**werte)


class CasterTests(unittest.IsolatedAsyncioTestCase):
    async def _empfangen(self, art: str) -> tuple[bytes, ntrip.NtripClient]:
        caster = FalscherCaster(art)
        port = await caster.start()
        self.addAsyncCleanup(caster.stop)
        empfangen = bytearray()
        client = ntrip.NtripClient(_cfg(port), empfangen.extend)
        client.running = True
        with self.assertRaises((ConnectionError, CasterFehler)) as fehler:
            await asyncio.wait_for(client._session(), 5)
        self.assertIn(b"Authorization: Basic", caster.anfragen[0])
        client.letzter_fehler = str(fehler.exception)
        return bytes(empfangen), client

    async def test_ntrip1_without_blank_line_delivers_every_byte(self):
        daten, client = await self._empfangen("v1")
        self.assertEqual(daten, STROM)
        self.assertEqual(client.pruefer.rahmen, 5)

    async def test_ntrip2_chunks_never_reach_the_receiver(self):
        daten, client = await self._empfangen("v2")
        self.assertEqual(daten, STROM)
        self.assertEqual(client.pruefer.crc_fehler, 0)

    async def test_a_sourcetable_is_not_a_connection(self):
        daten, client = await self._empfangen("liste")
        self.assertEqual(daten, b"")
        self.assertIn("Mountpoint unbekannt", client.letzter_fehler)

    async def test_test_reports_what_came_through(self):
        caster = FalscherCaster("v2")
        port = await caster.start()
        self.addAsyncCleanup(caster.stop)
        ergebnis = await ntrip.verbindung_testen(_cfg(port), HOF, dauer_s=1.0)
        self.assertTrue(ergebnis["ok"], ergebnis)
        self.assertTrue(ergebnis["chunked"])
        self.assertEqual(ergebnis["bytes"], len(STROM))
        self.assertEqual(ergebnis["rtcm"]["station"], 42)

    async def test_test_names_wrong_password_and_unknown_mountpoint(self):
        for art, erwartet in (("401", "Passwort"), ("liste", "Mountpoint")):
            caster = FalscherCaster(art)
            port = await caster.start()
            ergebnis = await ntrip.verbindung_testen(_cfg(port), HOF, dauer_s=1.0)
            await caster.stop()
            self.assertFalse(ergebnis["ok"])
            self.assertIn(erwartet, ergebnis["fehler"])

    async def test_test_without_mountpoint_or_host_says_what_is_missing(self):
        self.assertIn("Mountpoint", (await ntrip.verbindung_testen(_cfg(1, "")))["fehler"])
        self.assertIn("Adresse", (await ntrip.verbindung_testen(_cfg(1, host="")))["fehler"])

    async def test_unreachable_caster(self):
        ergebnis = await ntrip.verbindung_testen(_cfg(1), dauer_s=0.5)
        self.assertIn("nicht erreichbar", ergebnis["fehler"])

    async def test_mountpoints_are_fetched_nearest_first(self):
        caster = FalscherCaster("v2")
        port = await caster.start()
        self.addAsyncCleanup(caster.stop)
        liste = await ntrip.mountpoints_holen("127.0.0.1", port, position=HOF, timeout=3)
        self.assertEqual(liste[0]["mountpoint"], "HOF")
        self.assertEqual(len(liste), 3)


if __name__ == "__main__":
    unittest.main()
