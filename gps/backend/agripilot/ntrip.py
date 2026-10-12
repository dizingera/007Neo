"""RTK-Korrekturen: Quellen, und ein Verteiler, damit eine Verbindung den Hof versorgt.

Zentimeter gibt es nur mit einem Korrekturstrom von einer Basisstation. Wie er
hereinkommt, hängt an der Anlage - ein Dienst spricht NTRIP, eine selbst
gebaute Basis oft nur einen rohen RTCM3-Strom auf einem Port, und ein Funkmodem
liefert ihn seriell. Alle drei landen hier bei derselben Senke.

The relay matters for a fleet.  Casters normally allow one connection per
account, and mobile data in a field is unreliable and metered.  So the master Pi
holds the single caster connection and re-serves the same bytes to the other
tractors over the farm's own radio link - they need no SIM card and no second
account, and if the master loses signal every machine degrades together rather
than silently drifting apart.
"""

from __future__ import annotations

import asyncio
import time
from typing import Callable, Optional

from .nmea import build_gga
from .ntrip_protokoll import (
    CasterFehler, ChunkedDecoder, RtcmPruefer, anfrage, antwort_pruefen, kopf_zerlegen,
    rtcm_urteil, sourcetable_lesen,
)

KOPF_MAX = 16384          # Antwortkopf des Casters - länger ist keine Antwort eines Casters

RtcmSink = Callable[[bytes], None]


async def _schliessen(writer: asyncio.StreamWriter) -> None:
    """Verbindung zumachen und das Ende abwarten.

    Ohne das Abwarten meldet Python beim Aufräumen einen offenen Datenstrom -
    harmlos, aber es steht im Protokoll neben den echten Fehlern.
    """
    writer.close()
    try:
        await writer.wait_closed()
    except Exception:  # noqa: BLE001 - beim Zumachen ist alles schon vorbei
        pass


async def antwort_lesen(reader: asyncio.StreamReader, timeout: float = 15.0):
    """Antwortkopf des Casters lesen: Deutung und die schon mitgelesenen Nutzbytes.

    NTRIP 1 ("ICY 200 OK") hat oft keine Leerzeile vor den Daten; dann beginnen
    die Korrekturen direkt nach der Statuszeile. NTRIP 2 ist sauberes HTTP.
    """
    puffer = b""
    while b"\r\n" not in puffer:
        stueck = await asyncio.wait_for(reader.read(4096), timeout)
        if not stueck:
            raise CasterFehler("Caster hat ohne Antwort aufgelegt - Adresse oder Port falsch?")
        puffer += stueck
        if len(puffer) > KOPF_MAX:
            raise CasterFehler("Das ist kein NTRIP-Caster (keine lesbare Antwort).")
    statuszeile = puffer.split(b"\r\n", 1)[0]
    if statuszeile.upper().startswith(b"ICY"):
        rest = puffer[len(statuszeile) + 2:]
        if rest.startswith(b"\r\n"):
            rest = rest[2:]
        return antwort_pruefen(statuszeile.decode("latin-1"), {}), rest
    while b"\r\n\r\n" not in puffer:
        stueck = await asyncio.wait_for(reader.read(4096), timeout)
        if not stueck:
            break
        puffer += stueck
        if len(puffer) > KOPF_MAX:
            raise CasterFehler("Antwortkopf des Casters zu lang.")
    kopf, _, rest = puffer.partition(b"\r\n\r\n")
    status, felder = kopf_zerlegen(kopf)
    return antwort_pruefen(status, felder), rest


async def _verbinden(host: str, port: int, timeout: float = 15.0):
    try:
        return await asyncio.wait_for(asyncio.open_connection(host, port), timeout)
    except (OSError, asyncio.TimeoutError) as exc:
        grund = str(exc) or "Zeitüberschreitung"
        raise CasterFehler(f"{host}:{port} nicht erreichbar ({grund}).") from exc


async def mountpoints_holen(host: str, port: int, benutzer: str = "", passwort: str = "",
                            position: Optional[tuple[float, float]] = None,
                            timeout: float = 15.0) -> list[dict]:
    """Die Mountpoint-Liste (Sourcetable) eines Casters holen."""
    if not host:
        raise CasterFehler("Erst die Adresse des Casters eintragen.")
    reader, writer = await _verbinden(host, port, timeout)
    loop = asyncio.get_running_loop()
    ende = loop.time() + timeout
    try:
        writer.write(anfrage(host, port, "", benutzer, passwort))
        await writer.drain()
        roh = b""
        while b"\r\n\r\n" not in roh:
            stueck = await asyncio.wait_for(reader.read(8192), max(0.1, ende - loop.time()))
            if not stueck:
                raise CasterFehler("Der Caster hat keine Mountpoint-Liste geschickt.")
            roh += stueck
        kopf, _, rest = roh.partition(b"\r\n\r\n")
        status, felder = kopf_zerlegen(kopf)
        teile = status.split()
        if not (status.upper().startswith("SOURCETABLE") or teile[1:2] == ["200"]):
            raise CasterFehler(antwort_pruefen(status, felder).fehler)
        decoder = (ChunkedDecoder()
                   if "chunked" in felder.get("transfer-encoding", "").lower() else None)
        daten = decoder.feed(rest) if decoder else rest
        while b"ENDSOURCETABLE" not in daten and not (decoder and decoder.ende):
            try:
                stueck = await asyncio.wait_for(reader.read(8192), max(0.1, ende - loop.time()))
            except asyncio.TimeoutError:
                break
            if not stueck:
                break
            daten += decoder.feed(stueck) if decoder else stueck
        return sourcetable_lesen(daten.decode("latin-1"), position)
    except asyncio.TimeoutError as exc:
        raise CasterFehler("Der Caster antwortet nicht.") from exc
    finally:
        await _schliessen(writer)


async def verbindung_testen(cfg, position: Optional[tuple[float, float]] = None,
                            dauer_s: float = 8.0) -> dict:
    """Einmal zum Caster verbinden, ein paar Sekunden mitlesen, ehrlich berichten.

    Schreibt nichts an den Empfänger - das ist ein Blick, kein Betrieb.
    """
    ergebnis = {"ok": False, "verbunden": False, "fehler": "", "version": "",
                "chunked": False, "bytes": 0, "rtcm": None, "urteil": ""}
    if not cfg.host:
        ergebnis["fehler"] = "Erst die Adresse des Casters eintragen."
        return ergebnis
    if not cfg.mountpoint:
        ergebnis["fehler"] = "Kein Mountpoint eingetragen - über „Mountpoints abrufen“ auswählen."
        return ergebnis
    try:
        reader, writer = await _verbinden(cfg.host, cfg.port)
    except CasterFehler as exc:
        ergebnis["fehler"] = str(exc)
        return ergebnis
    pruefer = RtcmPruefer()
    loop = asyncio.get_running_loop()
    try:
        writer.write(anfrage(cfg.host, cfg.port, cfg.mountpoint, cfg.username, cfg.password))
        await writer.drain()
        antwort, chunk = await antwort_lesen(reader)
        if not antwort.ok:
            ergebnis["fehler"] = antwort.fehler
            return ergebnis
        ergebnis.update(verbunden=True, version=antwort.version, chunked=antwort.chunked)
        if cfg.send_gga and position:
            writer.write(build_gga(position[0], position[1], 0.0, 1).encode())
            await writer.drain()
        decoder = ChunkedDecoder() if antwort.chunked else None
        ende = loop.time() + dauer_s
        while True:
            if chunk:
                daten = decoder.feed(chunk) if decoder else chunk
                ergebnis["bytes"] += len(daten)
                pruefer.feed(daten, time.time())
            if loop.time() >= ende:
                break
            try:
                chunk = await asyncio.wait_for(reader.read(4096), ende - loop.time())
            except asyncio.TimeoutError:
                break
            if not chunk:
                break
    except asyncio.TimeoutError:
        ergebnis["fehler"] = "Der Caster antwortet nicht."
    except CasterFehler as exc:
        ergebnis["fehler"] = str(exc)
    finally:
        await _schliessen(writer)
    ergebnis["rtcm"] = pruefer.zusammenfassung(position)
    if ergebnis["verbunden"] and not ergebnis["fehler"]:
        ergebnis["urteil"] = rtcm_urteil(ergebnis["rtcm"], ergebnis["bytes"])
        if not ergebnis["bytes"] and cfg.send_gga and not position:
            ergebnis["urteil"] = ("Verbunden, aber keine Daten. Netz-RTK (VRS) schickt erst, "
                                  "wenn der Empfänger eine Position hat.")
    ergebnis["ok"] = bool(ergebnis["verbunden"] and not ergebnis["fehler"]
                          and not ergebnis["urteil"])
    return ergebnis


class CorrectionSource:
    """Gemeinsames Verhalten aller Quellen: laufen, melden, sich erholen."""

    def __init__(self, sink: RtcmSink) -> None:
        self.sink = sink
        self.running = False
        self.status = "aus"
        self.bytes_received = 0
        self.last_data_at = 0.0
        # Liest mit, ob im Strom wirklich RTCM3 steckt - Bytes allein sagen nichts.
        self.pruefer = RtcmPruefer()

    @property
    def healthy(self) -> bool:
        return self.running and (time.time() - self.last_data_at) < 10.0

    def _received(self, chunk: bytes) -> None:
        self.bytes_received += len(chunk)
        self.last_data_at = time.time()
        self.pruefer.feed(chunk, self.last_data_at)
        self.sink(chunk)

    @property
    def adresse(self) -> str:
        """Woher die Korrekturen kommen sollen - eine Zeile für die Anzeige."""
        return ""

    def _stoerung(self, exc: BaseException) -> None:
        """Fehler melden, ohne zu verschweigen, welche Quelle gemeint ist.

        Ein nacktes "Fehler: Gegenstelle hat beendet" hilft in der Kabine
        niemandem: bei Basis, Funkmodem und Caster steht dieselbe Zeile, und wer
        zwei Wege eingerichtet hat, weiß nicht, welcher davon schweigt.
        """
        self.status = f"{self.adresse} - Fehler: {exc}" if self.adresse else f"Fehler: {exc}"

    async def run(self) -> None:  # pragma: no cover - in Unterklassen
        raise NotImplementedError

    async def stop(self) -> None:
        self.running = False


class NtripClient(CorrectionSource):
    """Holt RTCM3 von einem Caster."""

    def __init__(self, config, sink: RtcmSink,
                 position: Optional[Callable[[], tuple[float, float]]] = None) -> None:
        super().__init__(sink)
        self.config = config
        self.position = position
        self._writer: Optional[asyncio.StreamWriter] = None

    @property
    def adresse(self) -> str:
        cfg = self.config
        return f"{cfg.host}/{cfg.mountpoint}" if cfg.mountpoint else str(cfg.host)

    async def run(self) -> None:
        if not self.config.host:
            self.status = "kein Caster eingetragen"
            return
        self.running = True
        backoff = 2.0
        while self.running:
            try:
                await self._session()
                backoff = 2.0
            except Exception as exc:  # noqa: BLE001 - never take the cab display down
                self._stoerung(exc)
                await asyncio.sleep(backoff)
                backoff = min(30.0, backoff * 2)

    async def _session(self) -> None:
        cfg = self.config
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(cfg.host, cfg.port), timeout=15)
        self._writer = writer
        try:
            writer.write(anfrage(cfg.host, cfg.port, cfg.mountpoint, cfg.username, cfg.password))
            await writer.drain()
            antwort, chunk = await antwort_lesen(reader)
            if not antwort.ok:
                raise CasterFehler(antwort.fehler)
            decoder = ChunkedDecoder() if antwort.chunked else None
            self.status = f"verbunden {self.adresse}" + (" (NTRIP 2)" if antwort.version == "2" else "")
            gga_task = asyncio.create_task(self._send_gga_loop(writer))
            try:
                while self.running:
                    if chunk:
                        # Bei NTRIP 2 erst die Stücklängen herausnehmen, sonst
                        # stehen sie mitten im RTCM-Strom des Empfängers.
                        daten = decoder.feed(chunk) if decoder else chunk
                        if daten:
                            self._received(daten)
                        if decoder and decoder.ende:
                            raise ConnectionError("Caster hat den Strom beendet")
                    chunk = await reader.read(4096)
                    if not chunk:
                        raise ConnectionError("Caster hat die Verbindung beendet")
            finally:
                gga_task.cancel()
        finally:
            await _schliessen(writer)
            self._writer = None

    async def _send_gga_loop(self, writer: asyncio.StreamWriter) -> None:
        """Tell the caster where we are.

        Network RTK (VRS) computes a virtual base station at the rover's
        position, so without this the caster sends nothing at all.
        """
        if not self.config.send_gga or self.position is None:
            return
        while True:
            try:
                position = self.position()
                if position:
                    lat, lon = position
                    writer.write(build_gga(lat, lon, 0.0, 1).encode())
                    await writer.drain()
            except Exception:  # noqa: BLE001
                return
            # Ohne Position nicht lange warten: ein VRS-Caster schweigt, bis er sie kennt.
            await asyncio.sleep(self.config.gga_interval_s if position else 2)

    async def stop(self) -> None:
        self.running = False
        if self._writer is not None:
            self._writer.close()


class TcpRtcmSource(CorrectionSource):
    """Roher RTCM3-Strom von einem Netzwerkport, ohne Anmeldung.

    So gibt eine selbst gebaute Basis ihre Daten aus, wenn sie nur einen Server
    öffnet statt eines Casters - etwa str2str aus RTKLIB im Server-Betrieb. Es
    gibt keinen Anmeldevorgang und keinen Mountpoint: verbinden und mitlesen.
    """

    def __init__(self, host: str, port: int, sink: RtcmSink,
                 beschreibung: str = "Basisstation") -> None:
        super().__init__(sink)
        self.host = host
        self.port = port
        self.beschreibung = beschreibung

    @property
    def adresse(self) -> str:
        return f"{self.beschreibung} {self.host}:{self.port}"

    async def run(self) -> None:
        if not self.host:
            self.status = "keine Adresse eingetragen"
            return
        self.running = True
        backoff = 2.0
        while self.running:
            try:
                reader, writer = await asyncio.open_connection(self.host, self.port)
                self.status = self.adresse
                backoff = 2.0
                try:
                    while self.running:
                        chunk = await reader.read(4096)
                        if not chunk:
                            raise ConnectionError("Gegenstelle hat beendet")
                        self._received(chunk)
                finally:
                    await _schliessen(writer)
            except Exception as exc:  # noqa: BLE001
                self._stoerung(exc)
                await asyncio.sleep(backoff)
                backoff = min(30.0, backoff * 2)


class SerialRtcmSource(CorrectionSource):
    """RTCM3 von einem Funkmodem oder direkt von der Basis am seriellen Anschluss.

    Gemeint ist der Fall, dass das Modem am *Rechner* hängt und die Korrekturen
    von hier an den Empfänger weitergereicht werden. Steckt das Modem
    unmittelbar am Empfänger, ist hier nichts einzustellen - dann sieht die
    Software den Strom gar nicht, und das ist auch gut so.
    """

    def __init__(self, port: str, baudrate: int, sink: RtcmSink) -> None:
        super().__init__(sink)
        self.port = port
        self.baudrate = baudrate

    @property
    def adresse(self) -> str:
        return f"Funkmodem {self.port} @ {self.baudrate}"

    async def run(self) -> None:
        if not self.port:
            self.status = "kein Anschluss eingetragen"
            return
        self.running = True
        backoff = 1.0
        loop = asyncio.get_running_loop()
        while self.running:
            link = None
            try:
                import serial
                link = serial.Serial(self.port, self.baudrate, timeout=1)
                self.status = self.adresse
                backoff = 1.0
                while self.running:
                    chunk = await loop.run_in_executor(None, link.read, 512)
                    if chunk:
                        self._received(chunk)
            except ImportError:
                self.status = "pyserial fehlt (pip install pyserial)"
                await asyncio.sleep(10)
            except Exception as exc:  # noqa: BLE001
                self._stoerung(exc)
                await asyncio.sleep(backoff)
                backoff = min(15.0, backoff * 2)
            finally:
                if link is not None:
                    try:
                        link.close()
                    except Exception:  # noqa: BLE001
                        pass


def build_corrections(config, sink: RtcmSink,
                      position: Optional[Callable[[], tuple[float, float]]] = None
                      ) -> Optional[CorrectionSource]:
    """Quelle für die Korrekturdaten aus der Konfiguration wählen."""
    cfg = config.corrections
    art = (cfg.source or "aus").lower()
    if art in ("", "aus", "off", "none"):
        return None
    if art == "tcp":
        return TcpRtcmSource(cfg.host, cfg.port, sink)
    if art == "serial":
        return SerialRtcmSource(cfg.serial_port, cfg.baudrate, sink)
    return NtripClient(cfg, sink, position)


class RtcmRelay:
    """Gibt einen Korrekturstrom an alle Maschinen im Hofnetz weiter."""

    def __init__(self, port: int) -> None:
        self.port = port
        self.clients: set[asyncio.StreamWriter] = set()
        self.server: Optional[asyncio.AbstractServer] = None
        self.bytes_sent = 0
        self.status = "aus"

    async def start(self) -> bool:
        """Start the relay. A blocked port must not take the system down.

        The tractor still needs to guide, record and steer if the port is
        occupied - by a second instance, or by another program on the Pi - so
        the failure is reported on the system screen and everything else runs.
        """
        try:
            self.server = await asyncio.start_server(
                self._handle_client, "0.0.0.0", self.port
            )
        except OSError as exc:
            self.server = None
            self.status = f"Port {self.port} nicht verfügbar: {exc.strerror or exc}"
            return False
        self.status = f"aktiv auf Port {self.port}"
        return True

    async def stop(self) -> None:
        for writer in list(self.clients):
            writer.close()
        self.clients.clear()
        if self.server is not None:
            self.server.close()
            await self.server.wait_closed()

    async def _handle_client(self, reader: asyncio.StreamReader,
                             writer: asyncio.StreamWriter) -> None:
        self.clients.add(writer)
        try:
            # Clients only listen; a read of nothing means they hung up.
            while await reader.read(256):
                pass
        except Exception:  # noqa: BLE001
            pass
        finally:
            self.clients.discard(writer)
            writer.close()

    def broadcast(self, data: bytes) -> None:
        dead = []
        for writer in self.clients:
            try:
                writer.write(data)
                self.bytes_sent += len(data)
            except Exception:  # noqa: BLE001
                dead.append(writer)
        for writer in dead:
            self.clients.discard(writer)

    @property
    def client_count(self) -> int:
        return len(self.clients)


class RtcmRelayClient(TcpRtcmSource):
    """Die andere Seite des Verteilers: ein Traktor holt sich die Korrekturen
    vom Master. Technisch derselbe rohe Strom wie von einer Basis, nur mit einer
    anderen Beschriftung auf der Systemseite."""

    def __init__(self, host: str, port: int, sink: RtcmSink) -> None:
        super().__init__(host, port, sink, beschreibung="Korrekturen vom Master")
