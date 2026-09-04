"""Autosteer output.

This is the one module that can move a machine, so it is built to refuse rather
than to try.  Steering only ever engages when every condition holds at once:

* it is enabled in the config file (a deliberate act during installation),
* the driver has armed it from the cab screen for this session,
* a guidance line is active and the machine is close enough to it,
* the fix is good enough - a float RTK solution can jump half a metre,
* speed is inside the configured window, and the position data is fresh.

The moment any of those stops being true the controller sends a disengage and
says why.  The steering board is expected to centre itself if commands stop
arriving (see `watchdog_ms`), so a crashed Pi or a pulled cable also ends in
hands-back-to-the-driver rather than a machine holding a turn.
"""

from __future__ import annotations

import asyncio
import struct
import time
from dataclasses import dataclass
from typing import Optional

from .guidance import GuidanceState
from .nmea import Fix

# Wire format to the steering board.  Small, fixed, and easy to reimplement on
# an Arduino or ESP32: magic, version, flags, angle in centidegrees, speed in
# cm/s, cross track in mm, XOR checksum.
_FRAME = struct.Struct("<2sBBhHhB")
_MAGIC = b"AP"
_VERSION = 1


@dataclass
class SteerCommand:
    engaged: bool = False
    angle_deg: float = 0.0
    reason: str = "nicht scharf"
    speed_ms: float = 0.0
    cross_track_m: float = 0.0

    def to_dict(self) -> dict:
        return {
            "engaged": self.engaged,
            "angle_deg": self.angle_deg,
            "reason": self.reason,
        }

    def encode(self) -> bytes:
        flags = 1 if self.engaged else 0
        angle = int(max(-90.0, min(90.0, self.angle_deg)) * 100)
        speed = int(max(0.0, min(300.0, self.speed_ms)) * 100)
        xte = int(max(-30.0, min(30.0, self.cross_track_m)) * 1000)
        body = _FRAME.pack(_MAGIC, _VERSION, flags, angle, speed, xte, 0)
        checksum = 0
        for byte in body[:-1]:
            checksum ^= byte
        return body[:-1] + bytes([checksum])


@dataclass
class BoardFeedback:
    """What the steering board reports back, when it reports at all."""

    wheel_angle_deg: Optional[float] = None
    driver_override: bool = False
    remote_switch: bool = False
    received_at: float = 0.0

    @property
    def fresh(self) -> bool:
        return self.received_at > 0 and (time.time() - self.received_at) < 2.0


class SteeringController:
    def __init__(self, config) -> None:
        self.config = config
        self.armed = False           # driver-controlled, resets on every stop
        self.command = SteerCommand()
        self.feedback = BoardFeedback()
        self.last_sent_at = 0.0
        self.max_rate_deg_s = 25.0
        self._last_angle = 0.0
        self._last_angle_at = 0.0
        self._transport: Optional[asyncio.DatagramTransport] = None
        self.disengage_count = 0

    # -- driver controls --------------------------------------------------

    def arm(self) -> str:
        if not self.config.enabled:
            self.armed = False
            return "Lenkautomatik ist in der Konfiguration deaktiviert"
        self.armed = True
        return "scharf"

    def disarm(self, reason: str = "vom Fahrer ausgeschaltet") -> None:
        if self.armed:
            self.disengage_count += 1
        self.armed = False
        self.command = SteerCommand(reason=reason)

    # -- main loop --------------------------------------------------------

    def update(self, guidance: GuidanceState, fix: Fix,
               now: Optional[float] = None) -> SteerCommand:
        """Decide whether to steer, and how much."""
        now = now or time.time()
        blocked = self._blocking_reason(guidance, fix, now)
        if blocked:
            if self.command.engaged:
                self.disengage_count += 1
            self.command = SteerCommand(
                engaged=False, angle_deg=0.0, reason=blocked,
                speed_ms=fix.speed_ms, cross_track_m=guidance.cross_track_m,
            )
            self._last_angle, self._last_angle_at = 0.0, now
        else:
            self.command = SteerCommand(
                engaged=True,
                angle_deg=self._rate_limit(guidance.steer_angle_deg, now),
                reason="lenkt",
                speed_ms=fix.speed_ms,
                cross_track_m=guidance.cross_track_m,
            )
        self._send(self.command)
        return self.command

    def _rate_limit(self, target: float, now: float) -> float:
        """Do not ask for more movement than the actuator can deliver.

        A steering motor needs about a second to go lock to lock, so a
        controller that jumps straight to a large angle is asking for something
        that will not happen and then over-corrects when it arrives late.
        Limiting the rate here matches the command to the machine and takes the
        weave out of the first metres after engaging.
        """
        dt = min(0.5, now - self._last_angle_at) if self._last_angle_at else 0.1
        limit = self.max_rate_deg_s * max(0.02, dt)
        delta = max(-limit, min(limit, target - self._last_angle))
        self._last_angle = self._last_angle + delta
        self._last_angle_at = now
        return self._last_angle

    def _blocking_reason(self, guidance: GuidanceState, fix: Fix,
                         now: float) -> Optional[str]:
        cfg = self.config
        if not cfg.enabled:
            return "in der Konfiguration deaktiviert"
        if not self.armed:
            return "nicht scharf"
        if self.feedback.fresh and self.feedback.driver_override:
            # The driver turned the wheel. Hand over immediately and stay off
            # until they arm again - silently re-engaging would be a nasty
            # surprise halfway through a manual correction.
            self.armed = False
            return "Fahrer hat eingegriffen"
        if not guidance.active:
            return "keine Spur aktiv"
        if not fix.valid:
            return "kein GPS-Fix"
        age = now - fix.received_at if fix.received_at else 99.0
        if age > 1.0:
            return f"GPS-Daten veraltet ({age:.1f} s)"
        if cfg.require_rtk and fix.rank < 4:
            return f"RTK nötig, aktuell: {fix.fix_label}"
        if fix.speed_ms < cfg.min_speed_ms:
            return "zu langsam"
        if fix.speed_ms > cfg.max_speed_ms:
            return "zu schnell"
        if abs(guidance.cross_track_m) > cfg.max_cross_track_m:
            return f"zu weit von der Spur ({guidance.cross_track_m:.2f} m)"
        return None

    # -- transport --------------------------------------------------------

    async def start(self) -> None:
        if self.config.output != "udp":
            return
        loop = asyncio.get_running_loop()

        controller = self

        class _Protocol(asyncio.DatagramProtocol):
            def datagram_received(self, data: bytes, addr) -> None:  # noqa: ANN001
                controller._on_feedback(data)

        self._transport, _ = await loop.create_datagram_endpoint(
            _Protocol, remote_addr=(self.config.host, self.config.port)
        )

    async def stop(self) -> None:
        self.disarm("System wird beendet")
        if self._transport is not None:
            # One last disengage so the board does not sit on the watchdog.
            try:
                self._transport.sendto(SteerCommand().encode())
            except Exception:  # noqa: BLE001
                pass
            self._transport.close()
            self._transport = None

    def _send(self, command: SteerCommand) -> None:
        if self._transport is None:
            return
        try:
            self._transport.sendto(command.encode())
            self.last_sent_at = time.time()
        except Exception:  # noqa: BLE001
            pass

    def _on_feedback(self, data: bytes) -> None:
        """Parse a status frame from the board: angle, override, switch."""
        if len(data) < 6 or data[:2] != _MAGIC:
            return
        try:
            angle, flags = struct.unpack_from("<hB", data, 3)
        except struct.error:
            return
        self.feedback = BoardFeedback(
            wheel_angle_deg=angle / 100.0,
            driver_override=bool(flags & 0x01),
            remote_switch=bool(flags & 0x02),
            received_at=time.time(),
        )

    def status(self) -> dict:
        return {
            "configured": self.config.enabled,
            "armed": self.armed,
            "command": self.command.to_dict(),
            "wheel_angle_deg": self.feedback.wheel_angle_deg,
            "driver_override": self.feedback.driver_override and self.feedback.fresh,
            "board_seen": self.feedback.fresh,
            "disengagements": self.disengage_count,
        }
