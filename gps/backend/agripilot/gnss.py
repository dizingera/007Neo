"""GNSS input.

One interface, four sources: a receiver on a serial port, a receiver reachable
over TCP or UDP (common when the antenna box sits on the roof with its own
network module), and a simulator.

The simulator is not a toy.  It drives a bicycle model that responds to steering
commands, which means the whole chain - guidance, coverage, autosteer output,
the cab display - can be exercised on a desk, and a new install can be checked
before the tractor is ever moved.
"""

from __future__ import annotations

import asyncio
import math
import random
import time
from typing import Awaitable, Callable, Optional

from .geo import LocalPlane
from .nmea import Fix, NmeaParser, build_gga

FixCallback = Callable[[Fix], Awaitable[None] | None]


class GnssSource:
    """Base class. Subclasses push raw NMEA lines into `handle_line`."""

    def __init__(self, on_fix: FixCallback) -> None:
        self.on_fix = on_fix
        self.parser = NmeaParser()
        self.running = False
        self.last_line_at = 0.0
        self.lines_received = 0
        self.status = "gestoppt"

    async def run(self) -> None:  # pragma: no cover - overridden
        raise NotImplementedError

    async def stop(self) -> None:
        self.running = False

    def write_rtcm(self, data: bytes) -> None:
        """Feed correction data back to the receiver. No-op where impossible."""

    async def handle_line(self, line: str) -> None:
        self.lines_received += 1
        self.last_line_at = time.time()
        fix = self.parser.feed(line)
        if fix is not None:
            fix.received_at = self.last_line_at
            result = self.on_fix(fix)
            if asyncio.iscoroutine(result):
                await result

    @property
    def healthy(self) -> bool:
        return self.running and (time.time() - self.last_line_at) < 3.0


class SerialSource(GnssSource):
    """Receiver on /dev/ttyACM0 or similar.

    pyserial is blocking, so the read loop lives in a worker thread and hands
    lines back through the event loop.  Reconnects on unplug, because a USB
    receiver in a cab will get knocked loose eventually.
    """

    def __init__(self, on_fix: FixCallback, port: str, baudrate: int = 115200) -> None:
        super().__init__(on_fix)
        self.port = port
        self.baudrate = baudrate
        self._serial = None

    async def run(self) -> None:
        self.running = True
        backoff = 1.0
        while self.running:
            try:
                import serial  # imported late: not needed for TCP or simulator
            except ImportError:
                self.status = "pyserial fehlt (pip install pyserial)"
                await asyncio.sleep(5)
                continue
            try:
                self._serial = serial.Serial(self.port, self.baudrate, timeout=1)
                self.status = f"verbunden {self.port} @ {self.baudrate}"
                backoff = 1.0
                await self._read_loop()
            except Exception as exc:  # noqa: BLE001 - keep the cab display alive
                self.status = f"Fehler: {exc}"
                await asyncio.sleep(backoff)
                backoff = min(15.0, backoff * 2)
            finally:
                if self._serial is not None:
                    try:
                        self._serial.close()
                    except Exception:  # noqa: BLE001
                        pass
                    self._serial = None

    async def _read_loop(self) -> None:
        loop = asyncio.get_running_loop()
        while self.running and self._serial is not None:
            raw = await loop.run_in_executor(None, self._serial.readline)
            if not raw:
                continue
            await self.handle_line(raw.decode("ascii", errors="ignore"))

    def write_rtcm(self, data: bytes) -> None:
        if self._serial is not None:
            try:
                self._serial.write(data)
            except Exception:  # noqa: BLE001 - a failed correction is not fatal
                pass


class TcpSource(GnssSource):
    """Receiver that serves NMEA over TCP."""

    def __init__(self, on_fix: FixCallback, host: str, port: int) -> None:
        super().__init__(on_fix)
        self.host = host
        self.port = port
        self._writer: Optional[asyncio.StreamWriter] = None

    async def run(self) -> None:
        self.running = True
        backoff = 1.0
        while self.running:
            try:
                reader, writer = await asyncio.open_connection(self.host, self.port)
                self._writer = writer
                self.status = f"verbunden {self.host}:{self.port}"
                backoff = 1.0
                while self.running:
                    line = await reader.readline()
                    if not line:
                        raise ConnectionError("Verbindung beendet")
                    await self.handle_line(line.decode("ascii", errors="ignore"))
            except Exception as exc:  # noqa: BLE001
                self.status = f"Fehler: {exc}"
                self._writer = None
                await asyncio.sleep(backoff)
                backoff = min(15.0, backoff * 2)

    def write_rtcm(self, data: bytes) -> None:
        if self._writer is not None:
            try:
                self._writer.write(data)
            except Exception:  # noqa: BLE001
                pass


class UdpSource(GnssSource):
    """Receiver broadcasting NMEA over UDP."""

    def __init__(self, on_fix: FixCallback, port: int) -> None:
        super().__init__(on_fix)
        self.port = port

    async def run(self) -> None:
        self.running = True
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[bytes] = asyncio.Queue()

        class _Protocol(asyncio.DatagramProtocol):
            def datagram_received(self, data: bytes, addr) -> None:  # noqa: ANN001
                queue.put_nowait(data)

        transport, _ = await loop.create_datagram_endpoint(
            _Protocol, local_addr=("0.0.0.0", self.port)
        )
        self.status = f"lauscht auf UDP {self.port}"
        try:
            while self.running:
                data = await queue.get()
                for line in data.decode("ascii", errors="ignore").splitlines():
                    await self.handle_line(line)
        finally:
            transport.close()


class SimulatorSource(GnssSource):
    """A virtual tractor.

    Bicycle model: the heading changes with speed and steering angle, the
    position follows the heading.  Steering can be driven from outside, so the
    autosteer controller can be watched closing in on a line without a machine.
    """

    def __init__(self, on_fix: FixCallback, lat: float = 48.1372, lon: float = 11.5756,
                 rate_hz: float = 10.0, noise_m: float = 0.02,
                 fix_quality: int = 4) -> None:
        super().__init__(on_fix)
        self.plane = LocalPlane(lat, lon)
        self.rate_hz = rate_hz
        self.noise_m = noise_m
        self.fix_quality = fix_quality
        self.east = 0.0
        self.north = 0.0
        self.heading = 0.0
        self.speed_ms = 2.5
        self.steer_deg = 0.0
        self.wheelbase = 2.6
        self.auto_steer = False

    def set_steer(self, degrees: float) -> None:
        self.steer_deg = max(-40.0, min(40.0, degrees))

    def set_speed_kmh(self, kmh: float) -> None:
        self.speed_ms = max(0.0, kmh / 3.6)

    def teleport(self, east: float, north: float, heading: float) -> None:
        self.east, self.north, self.heading = east, north, heading

    async def run(self) -> None:
        self.running = True
        self.status = "Simulator"
        dt = 1.0 / self.rate_hz
        while self.running:
            self._step(dt)
            lat, lon = self.plane.to_wgs(
                self.east + random.gauss(0, self.noise_m),
                self.north + random.gauss(0, self.noise_m),
            )
            sentence = build_gga(lat, lon, 520.0, self.fix_quality, 22, 0.6)
            await self.handle_line(sentence)
            # RMC carries speed and course, which GGA does not.
            await self.handle_line(self._rmc(lat, lon))
            await asyncio.sleep(dt)

    def _step(self, dt: float) -> None:
        steer = math.radians(self.steer_deg)
        if abs(steer) > 1e-6 and self.speed_ms > 0.01:
            turn_rate = self.speed_ms / self.wheelbase * math.tan(steer)
            self.heading = (self.heading + math.degrees(turn_rate) * dt) % 360.0
        h = math.radians(self.heading)
        self.east += math.sin(h) * self.speed_ms * dt
        self.north += math.cos(h) * self.speed_ms * dt

    def _rmc(self, lat: float, lon: float) -> str:
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        lat_dir = "N" if lat >= 0 else "S"
        lon_dir = "E" if lon >= 0 else "W"
        lat_abs, lon_abs = abs(lat), abs(lon)
        body = (
            f"GPRMC,{now.strftime('%H%M%S.%f')[:-3]},A,"
            f"{int(lat_abs):02d}{(lat_abs % 1) * 60:09.6f},{lat_dir},"
            f"{int(lon_abs):03d}{(lon_abs % 1) * 60:09.6f},{lon_dir},"
            f"{self.speed_ms / 0.514444:.2f},{self.heading:.1f},"
            f"{now.strftime('%d%m%y')},,,A"
        )
        crc = 0
        for ch in body:
            crc ^= ord(ch)
        return f"${body}*{crc:02X}\r\n"


def build_source(config, on_fix: FixCallback) -> GnssSource:
    """Pick a source from the config."""
    kind = (config.gnss.source or "simulator").lower()
    if kind == "serial":
        return SerialSource(on_fix, config.gnss.port, config.gnss.baudrate)
    if kind == "tcp":
        return TcpSource(on_fix, config.gnss.host, config.gnss.tcp_port)
    if kind == "udp":
        return UdpSource(on_fix, config.gnss.tcp_port)
    return SimulatorSource(on_fix)
