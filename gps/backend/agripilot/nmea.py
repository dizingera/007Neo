"""NMEA 0183 parsing.

Every GNSS receiver worth using speaks NMEA, so this is the common denominator
between a 20 EUR USB stick and a ZED-F9P with RTK corrections.  The parser is
deliberately forgiving: field counts vary between manufacturers and a garbled
sentence in a tractor cab is normal, so anything unparseable is dropped rather
than raised.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

KNOTS_TO_MS = 0.514444

# GGA quality indicator -> short label shown to the driver.  The distinction
# between RTK fix and float matters a lot: float is decimetre level and not
# good enough for section-to-section repeatability.
FIX_LABELS = {
    0: "kein Fix",
    1: "GPS",
    2: "DGPS",
    3: "PPS",
    4: "RTK fix",
    5: "RTK float",
    6: "Koppelnavigation",
    7: "manuell",
    8: "Simulation",
}

# Accuracy classes drive the UI colour and the autosteer safety gate.
FIX_RANK = {0: 0, 1: 1, 2: 2, 6: 1, 7: 0, 8: 1, 5: 3, 3: 2, 4: 4}


@dataclass
class Fix:
    """One position solution, assembled from several sentences."""

    lat: Optional[float] = None
    lon: Optional[float] = None
    altitude: Optional[float] = None
    speed_ms: float = 0.0
    course_deg: Optional[float] = None
    heading_deg: Optional[float] = None  # from HDT (dual antenna), if present
    fix_quality: int = 0
    satellites: int = 0
    hdop: Optional[float] = None
    lat_error: Optional[float] = None  # metres, from GST
    lon_error: Optional[float] = None
    age_of_corrections: Optional[float] = None
    utc: Optional[datetime] = None
    received_at: float = 0.0
    raw: dict = field(default_factory=dict)

    @property
    def valid(self) -> bool:
        return self.lat is not None and self.lon is not None and self.fix_quality > 0

    @property
    def fix_label(self) -> str:
        return FIX_LABELS.get(self.fix_quality, f"Status {self.fix_quality}")

    @property
    def rank(self) -> int:
        return FIX_RANK.get(self.fix_quality, 0)

    @property
    def accuracy_m(self) -> Optional[float]:
        """Horizontal 1-sigma estimate, preferring the receiver's own GST figure."""
        if self.lat_error is not None and self.lon_error is not None:
            return (self.lat_error ** 2 + self.lon_error ** 2) ** 0.5
        if self.hdop is not None:
            # Rough fallback: HDOP times a typical UERE for the fix type.
            uere = {4: 0.02, 5: 0.3, 2: 1.0}.get(self.fix_quality, 3.0)
            return self.hdop * uere
        return None

    def to_dict(self) -> dict:
        return {
            "lat": self.lat,
            "lon": self.lon,
            "altitude": self.altitude,
            "speed_ms": self.speed_ms,
            "speed_kmh": self.speed_ms * 3.6,
            "course_deg": self.course_deg,
            "heading_deg": self.heading_deg,
            "fix_quality": self.fix_quality,
            "fix_label": self.fix_label,
            "rank": self.rank,
            "satellites": self.satellites,
            "hdop": self.hdop,
            "accuracy_m": self.accuracy_m,
            "age_of_corrections": self.age_of_corrections,
            "utc": self.utc.isoformat() if self.utc else None,
        }


def checksum_ok(sentence: str) -> bool:
    """Verify the *hh checksum. Cheap insurance against half-received lines."""
    if "*" not in sentence:
        return False
    body, _, given = sentence.strip().lstrip("$!").partition("*")
    if len(given) < 2:
        return False
    computed = 0
    for ch in body:
        computed ^= ord(ch)
    try:
        return computed == int(given[:2], 16)
    except ValueError:
        return False


def _to_float(value: str) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_int(value: str) -> Optional[int]:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _degrees(value: str, hemisphere: str) -> Optional[float]:
    """NMEA packs coordinates as ddmm.mmmm - split the degrees off the minutes."""
    raw = _to_float(value)
    if raw is None or not value:
        return None
    degrees = int(raw / 100)
    minutes = raw - degrees * 100
    result = degrees + minutes / 60.0
    if hemisphere in ("S", "W"):
        result = -result
    return result


def _utc_time(time_field: str, date_field: str = "") -> Optional[datetime]:
    if len(time_field) < 6:
        return None
    try:
        hour, minute = int(time_field[0:2]), int(time_field[2:4])
        second = float(time_field[4:])
        if len(date_field) == 6:
            day, month = int(date_field[0:2]), int(date_field[2:4])
            # Two-digit NMEA years: 80-99 belong to the 1900s, the rest to the 2000s.
            yy = int(date_field[4:6])
            year = 1900 + yy if yy >= 80 else 2000 + yy
        else:
            today = datetime.now(timezone.utc)
            day, month, year = today.day, today.month, today.year
        return datetime(
            year, month, day, hour, minute, int(second),
            int((second % 1) * 1_000_000), tzinfo=timezone.utc,
        )
    except (ValueError, OverflowError):
        return None


class NmeaParser:
    """Accumulates sentences into a single Fix.

    A receiver spreads one epoch over several sentences (GGA has the height and
    fix type, RMC the date, VTG the speed, GST the accuracy), so the parser keeps
    a running Fix and reports it complete when the position-bearing sentence of
    the epoch arrives.
    """

    def __init__(self) -> None:
        self.fix = Fix()
        self.last_error: Optional[str] = None

    def feed(self, sentence: str) -> Optional[Fix]:
        """Consume one line. Returns the Fix when an epoch is complete."""
        sentence = sentence.strip()
        if not sentence.startswith(("$", "!")):
            return None
        if not checksum_ok(sentence):
            self.last_error = "Prüfsumme falsch"
            return None
        body = sentence[1:].split("*")[0]
        parts = body.split(",")
        talker_type = parts[0]
        if len(talker_type) < 5:
            return None
        kind = talker_type[2:]
        handler = getattr(self, f"_handle_{kind.lower()}", None)
        if handler is None:
            return None
        return handler(parts)

    # -- sentence handlers -------------------------------------------------

    def _handle_gga(self, p: list[str]) -> Optional[Fix]:
        if len(p) < 10:
            return None
        fix = self.fix
        fix.utc = _utc_time(p[1]) or fix.utc
        fix.lat = _degrees(p[2], p[3])
        fix.lon = _degrees(p[4], p[5])
        fix.fix_quality = _to_int(p[6]) or 0
        fix.satellites = _to_int(p[7]) or 0
        fix.hdop = _to_float(p[8])
        fix.altitude = _to_float(p[9])
        if len(p) > 13:
            fix.age_of_corrections = _to_float(p[13])
        return fix if fix.valid else None

    def _handle_rmc(self, p: list[str]) -> Optional[Fix]:
        if len(p) < 10:
            return None
        fix = self.fix
        active = p[2] == "A"
        fix.utc = _utc_time(p[1], p[9]) or fix.utc
        if active:
            lat, lon = _degrees(p[3], p[4]), _degrees(p[5], p[6])
            if lat is not None and lon is not None:
                fix.lat, fix.lon = lat, lon
            speed = _to_float(p[7])
            if speed is not None:
                fix.speed_ms = speed * KNOTS_TO_MS
            course = _to_float(p[8])
            if course is not None:
                fix.course_deg = course
            if fix.fix_quality == 0:
                fix.fix_quality = 1  # receiver only sends RMC: assume plain GPS
        return fix if (active and fix.valid) else None

    def _handle_vtg(self, p: list[str]) -> None:
        if len(p) < 8:
            return None
        course = _to_float(p[1])
        if course is not None:
            self.fix.course_deg = course
        kmh = _to_float(p[7])
        if kmh is not None:
            self.fix.speed_ms = kmh / 3.6
        return None

    def _handle_gst(self, p: list[str]) -> None:
        if len(p) < 8:
            return None
        self.fix.lat_error = _to_float(p[6])
        self.fix.lon_error = _to_float(p[7])
        return None

    def _handle_hdt(self, p: list[str]) -> None:
        # True heading from a dual-antenna receiver.  Far better than course over
        # ground, which is meaningless when the tractor is nearly stopped.
        if len(p) < 2:
            return None
        heading = _to_float(p[1])
        if heading is not None:
            self.fix.heading_deg = heading
        return None

    def _handle_gsa(self, p: list[str]) -> None:
        if len(p) >= 17:
            hdop = _to_float(p[16])
            if hdop is not None:
                self.fix.hdop = hdop
        return None


def build_gga(lat: float, lon: float, altitude: float = 0.0, quality: int = 1,
              satellites: int = 12, hdop: float = 0.9,
              when: Optional[datetime] = None) -> str:
    """Compose a GGA sentence.

    Needed in two places: the simulator, and the NTRIP client - a VRS caster
    only starts sending corrections once it knows roughly where you are.
    """
    when = when or datetime.now(timezone.utc)
    lat_dir, lon_dir = ("N" if lat >= 0 else "S"), ("E" if lon >= 0 else "W")
    lat_abs, lon_abs = abs(lat), abs(lon)
    lat_nmea = f"{int(lat_abs):02d}{(lat_abs % 1) * 60:09.6f}"
    lon_nmea = f"{int(lon_abs):03d}{(lon_abs % 1) * 60:09.6f}"
    time_str = when.strftime("%H%M%S.%f")[:-3]
    body = (
        f"GPGGA,{time_str},{lat_nmea},{lat_dir},{lon_nmea},{lon_dir},"
        f"{quality},{satellites:02d},{hdop:.1f},{altitude:.1f},M,45.0,M,,"
    )
    crc = 0
    for ch in body:
        crc ^= ord(ch)
    return f"${body}*{crc:02X}\r\n"
