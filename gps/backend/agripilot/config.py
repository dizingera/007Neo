"""Configuration.

Split in two on purpose:

* the YAML file holds what belongs to *this box* - which serial port the
  receiver is on, whether this Pi is the master, where the master lives.  Those
  are set once when the machine is built and are wrong to sync between tractors.
* everything the driver changes during work - working width, offsets, sections -
  lives in the database as a vehicle profile and does sync.
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

DEFAULT_PATH = Path(
    os.environ.get("AGRIPILOT_CONFIG", "/etc/agripilot/config.yaml")
)


@dataclass
class GnssConfig:
    # "serial" for a receiver on USB/UART, "tcp"/"udp" for one on the network,
    # "simulator" to run the whole system on a desk with no hardware at all.
    source: str = "simulator"
    port: str = "/dev/ttyACM0"
    baudrate: int = 115200
    host: str = "127.0.0.1"
    tcp_port: int = 9000
    # Where to write RTCM corrections back to.  For a USB receiver this is the
    # same serial port; leave empty to not feed corrections at all.
    rtcm_out: str = "auto"


@dataclass
class NtripConfig:
    """RTK corrections. Without these the system guides, but not to centimetres."""

    enabled: bool = False
    host: str = ""
    port: int = 2101
    mountpoint: str = ""
    username: str = ""
    password: str = ""
    send_gga: bool = True       # required by VRS/NearestBase casters
    gga_interval_s: int = 10


@dataclass
class NetworkConfig:
    role: str = "master"        # "master" or "client"
    device_id: str = ""         # filled from the hostname when empty
    device_name: str = ""
    master_url: str = "http://agripilot-master.local:8080"
    sync_interval_s: int = 30
    # The master re-serves its NTRIP stream on this port so the other tractors
    # need neither a mobile connection nor their own caster account.
    rtcm_relay_port: int = 2102
    use_master_rtcm: bool = True


@dataclass
class SteeringConfig:
    """Autosteer output.

    Off by default.  Turning this on makes the machine steer itself, which is
    only safe with a properly installed steering motor or valve, a working
    emergency stop and an operator in the seat.
    """

    enabled: bool = False
    output: str = "udp"          # "udp" to a steering board, "none" to log only
    host: str = "192.168.5.9"
    port: int = 8888
    min_speed_ms: float = 0.3    # below this the machine must not steer itself
    max_speed_ms: float = 8.0
    max_cross_track_m: float = 1.5   # too far off the line: hand back to the driver
    require_rtk: bool = True
    watchdog_ms: int = 500       # no fresh command in this window -> board centres


@dataclass
class ServerConfig:
    host: str = "0.0.0.0"
    port: int = 8080
    data_dir: str = "/var/lib/agripilot"
    update_hz: float = 10.0


@dataclass
class Config:
    gnss: GnssConfig = field(default_factory=GnssConfig)
    ntrip: NtripConfig = field(default_factory=NtripConfig)
    network: NetworkConfig = field(default_factory=NetworkConfig)
    steering: SteeringConfig = field(default_factory=SteeringConfig)
    server: ServerConfig = field(default_factory=ServerConfig)
    path: Optional[Path] = None

    @property
    def is_master(self) -> bool:
        return self.network.role == "master"

    @property
    def db_path(self) -> Path:
        return Path(self.server.data_dir) / "agripilot.db"

    def to_dict(self) -> dict:
        data = {k: v for k, v in asdict(self).items() if k != "path"}
        # The caster password is not something to hand out over the API.
        data["ntrip"] = {**data["ntrip"], "password": "***" if self.ntrip.password else ""}
        return data

    def save(self, path: Optional[Path] = None) -> Path:
        target = Path(path or self.path or DEFAULT_PATH)
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = {k: v for k, v in asdict(self).items() if k != "path"}
        target.write_text(_dump(payload), encoding="utf-8")
        self.path = target
        return target


def _dump(payload: dict) -> str:
    try:
        import yaml
        return yaml.safe_dump(payload, sort_keys=False, allow_unicode=True)
    except ImportError:
        import json
        return json.dumps(payload, indent=2, ensure_ascii=False)


def _load_text(text: str) -> dict:
    try:
        import yaml
        return yaml.safe_load(text) or {}
    except ImportError:
        import json
        return json.loads(text or "{}")


def load(path: str | Path | None = None) -> Config:
    """Read the config, filling in defaults for anything missing.

    A missing file is not an error: a fresh Pi should boot into the simulator
    and show a working screen, so the installer can check the display before the
    receiver is even wired up.
    """
    target = Path(path or DEFAULT_PATH)
    data: dict[str, Any] = {}
    if target.exists():
        data = _load_text(target.read_text(encoding="utf-8"))

    config = Config(
        gnss=GnssConfig(**_subset(GnssConfig, data.get("gnss"))),
        ntrip=NtripConfig(**_subset(NtripConfig, data.get("ntrip"))),
        network=NetworkConfig(**_subset(NetworkConfig, data.get("network"))),
        steering=SteeringConfig(**_subset(SteeringConfig, data.get("steering"))),
        server=ServerConfig(**_subset(ServerConfig, data.get("server"))),
        path=target,
    )
    if not config.network.device_id:
        import socket
        config.network.device_id = socket.gethostname()
    if not config.network.device_name:
        config.network.device_name = config.network.device_id
    return config


def _subset(cls: type, values: Any) -> dict:
    """Ignore unknown keys so an older binary still starts on a newer config."""
    if not isinstance(values, dict):
        return {}
    allowed = {f for f in cls.__dataclass_fields__}
    return {k: v for k, v in values.items() if k in allowed}
