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
import base64
import time
from typing import Callable, Optional

from .nmea import build_gga

RtcmSink = Callable[[bytes], None]


class CorrectionSource:
    """Gemeinsames Verhalten aller Quellen: laufen, melden, sich erholen."""

    def __init__(self, sink: RtcmSink) -> None:
        self.sink = sink
        self.running = False
        self.status = "aus"
        self.bytes_received = 0
        self.last_data_at = 0.0

    @property
    def healthy(self) -> bool:
        return self.running and (time.time() - self.last_data_at) < 10.0

    def _received(self, chunk: bytes) -> None:
        self.bytes_received += len(chunk)
        self.last_data_at = time.time()
        self.sink(chunk)

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
                self.status = f"Fehler: {exc}"
                await asyncio.sleep(backoff)
                backoff = min(30.0, backoff * 2)

    async def _session(self) -> None:
        cfg = self.config
        reader, writer = await asyncio.open_connection(cfg.host, cfg.port)
        self._writer = writer
        credentials = base64.b64encode(
            f"{cfg.username}:{cfg.password}".encode()
        ).decode()
        request = (
            f"GET /{cfg.mountpoint} HTTP/1.1\r\n"
            f"Host: {cfg.host}:{cfg.port}\r\n"
            "Ntrip-Version: Ntrip/2.0\r\n"
            "User-Agent: NTRIP AgriPilot/1.0\r\n"
            f"Authorization: Basic {credentials}\r\n"
            "Connection: close\r\n\r\n"
        )
        writer.write(request.encode())
        await writer.drain()

        header = await reader.readuntil(b"\r\n")
        if b"200" not in header and b"ICY" not in header:
            raise ConnectionError(header.decode(errors="ignore").strip())
        # Skip the rest of the header block.
        while True:
            line = await reader.readline()
            if line in (b"\r\n", b"\n", b""):
                break

        self.status = f"verbunden {cfg.host}/{cfg.mountpoint}"
        gga_task = asyncio.create_task(self._send_gga_loop(writer))
        try:
            while self.running:
                chunk = await reader.read(4096)
                if not chunk:
                    raise ConnectionError("Caster hat die Verbindung beendet")
                self._received(chunk)
        finally:
            gga_task.cancel()
            writer.close()
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
            await asyncio.sleep(self.config.gga_interval_s)

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

    async def run(self) -> None:
        if not self.host:
            self.status = "keine Adresse eingetragen"
            return
        self.running = True
        backoff = 2.0
        while self.running:
            try:
                reader, writer = await asyncio.open_connection(self.host, self.port)
                self.status = f"{self.beschreibung} {self.host}:{self.port}"
                backoff = 2.0
                try:
                    while self.running:
                        chunk = await reader.read(4096)
                        if not chunk:
                            raise ConnectionError("Gegenstelle hat beendet")
                        self._received(chunk)
                finally:
                    writer.close()
            except Exception as exc:  # noqa: BLE001
                self.status = f"Fehler: {exc}"
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
                self.status = f"Funkmodem {self.port} @ {self.baudrate}"
                backoff = 1.0
                while self.running:
                    chunk = await loop.run_in_executor(None, link.read, 512)
                    if chunk:
                        self._received(chunk)
            except ImportError:
                self.status = "pyserial fehlt (pip install pyserial)"
                await asyncio.sleep(10)
            except Exception as exc:  # noqa: BLE001
                self.status = f"Fehler: {exc}"
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
