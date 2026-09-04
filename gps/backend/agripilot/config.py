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

def _default_paths() -> tuple[Path, Path]:
    """Konfigurations- und Datenpfad je Betriebssystem.

    Auf einem Windows-Tablet gibt es kein /etc und kein /var; dort gehört beides
    unter ProgramData, damit es Benutzerwechsel und Updates übersteht.
    """
    if os.name == "nt":
        base = Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData")) / "AgriPilot"
        return base / "config.yaml", base
    return Path("/etc/agripilot/config.yaml"), Path("/var/lib/agripilot")


DEFAULT_PATH = Path(os.environ.get("AGRIPILOT_CONFIG", _default_paths()[0]))
DEFAULT_DATA_DIR = str(_default_paths()[1])


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
class CorrectionsConfig:
    """RTK-Korrekturdaten. Ohne sie führt das System, aber nicht auf Zentimeter.

    Die Daten kommen je nach Anlage auf verschiedenen Wegen herein, und der Weg
    entscheidet, was hier einzustellen ist:

    * ``ntrip``  - ein Caster im Netz. Der übliche Fall bei einem Dienst, und
                   auch bei einer eigenen Basis, die einen Caster mitbringt
                   (etwa RTKBase). Es gibt einen Anmeldevorgang und einen
                   Mountpoint.
    * ``tcp``    - ein roher RTCM3-Strom auf einem Netzwerkport, ohne
                   Anmeldung. So gibt eine selbst gebaute Basis ihre Daten aus,
                   wenn sie nur einen Server öffnet statt eines Casters.
    * ``serial`` - RTCM3 kommt über ein Funkmodem oder direkt von der Basis an
                   einem seriellen Anschluss dieses Rechners.
    * ``aus``    - keine Korrekturen von hier. Richtig, wenn ein Funkmodem
                   unmittelbar am Empfänger hängt: dann sieht die Software den
                   Strom gar nicht, der Empfänger bekommt ihn direkt.
    """

    source: str = "aus"         # ntrip | tcp | serial | aus
    host: str = ""              # ntrip und tcp
    port: int = 2101            # ntrip: meist 2101, tcp: was die Basis öffnet
    mountpoint: str = ""        # nur ntrip
    username: str = ""
    password: str = ""
    serial_port: str = ""       # nur serial, z.B. COM4 oder /dev/ttyUSB0
    baudrate: int = 115200
    send_gga: bool = True       # Netz-RTK braucht die eigene Position
    gga_interval_s: int = 10

    @property
    def enabled(self) -> bool:
        return (self.source or "aus").lower() not in ("", "aus", "off", "none")


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
class ImuConfig:
    """Neigungssensor - Hangausgleich und Drehrate.

    Ohne IHN wandert die Spur am Hang um Dezimeter, ohne dass der Empfänger
    etwas davon merkt: siehe imu.py.
    """

    source: str = "aus"          # "aus" | "tinkerforge" | "simulator"
    host: str = "localhost"      # Brick Daemon
    port: int = 4223
    uid: str = ""                # leer = erstes gefundenes IMU-Gerät
    axis_map: str = "standard"   # standard | swapped | inverted | swapped_inverted
    roll_sign: float = 1.0       # -1, wenn der Ausgleich in die falsche Richtung geht
    terrain_compensation: bool = True
    use_for_heading: bool = True  # Kurs vom IMU, wenn GPS zu langsam ist


@dataclass
class PhidgetConfig:
    """Lenkmotor über eine Phidget-Motorsteuerung.

    Die Rückmeldung entscheidet, wie gut das wird:

    * ``was``       - Radwinkelsensor (Poti an der Achsschenkellenkung) an einem
                      Spannungsverhältnis-Eingang. Die saubere Lösung.
    * ``yaw_rate``  - keine Rückmeldung am Rad, dafür die Drehrate aus dem IMU.
                      Geregelt wird dann nicht der Radwinkel, sondern wie schnell
                      sich der Traktor dreht. Funktioniert erstaunlich gut und
                      braucht keinen zusätzlichen Sensor.
    * ``encoder``   - Drehgeber am Motor, Mitte wird beim Scharfschalten gelernt.
                      Nur eine Notlösung: die Mitte verliert sich über den Tag.
    """

    serial_number: int = -1      # -1 = erstes gefundenes Gerät
    motor_channel: int = 0
    invert_motor: bool = False

    # Regelungsart:
    #   "position" - Positionsregler der Phidget-Steuerung. Der Sollwinkel geht
    #                in Grad direkt an die Platine, der PID läuft dort in der
    #                Firmware. Braucht einen Drehgeber und counts_per_deg.
    #   "velocity" - eigener Regelkreis auf Drehzahl, mit der unten
    #                eingestellten Rückmeldung.
    control: str = "position"
    feedback: str = "yaw_rate"   # nur bei control=velocity: was | yaw_rate | encoder

    # -- Positionsregler ------------------------------------------------
    # Zählwerte des Drehgebers je Grad Einschlag der Räder AM BODEN. Aus diesem
    # Wert wird der RescaleFactor der Phidget-Steuerung gebildet, damit
    # Sollwinkel und Istwinkel dort direkt in Grad geführt werden.
    counts_per_deg: float = 40.0
    max_wheel_angle_deg: float = 35.0   # mechanischer Anschlag, harte Grenze
    dead_band_deg: float = 0.3          # darunter wird nicht nachgeregelt
    velocity_limit: float = 0.55        # 0..1, Anteil der vollen Leistung
    stall_velocity: float = 0.0         # 0 = Blockiererkennung der Platine aus
    position_kp: float = 12000.0
    position_ki: float = 40.0
    position_kd: float = 300000.0
    normalize_pid: bool = True          # PID in vergleichbaren Einheiten
    override_deg: float = 4.0           # ab dieser Dauerabweichung: Fahrer/Blockade

    # Grenzen für den Motor
    current_limit_a: float = 2.0     # niedrig halten: das Rad muss von Hand zu übersteuern sein
    max_duty: float = 0.55           # 0..1, Anteil der vollen Leistung
    acceleration: float = 2.0        # Anteil pro Sekunde
    failsafe_ms: int = 500           # Phidget stoppt selbst, wenn wir verstummen

    # Radwinkelsensor
    was_channel: int = 0
    was_centre_ratio: float = 0.5    # Spannungsverhältnis bei Geradeausstellung
    was_deg_per_ratio: float = 200.0 # Grad je Einheit Spannungsverhältnis
    was_invert: bool = False

    # Drehgeber
    encoder_channel: int = 0
    encoder_counts_per_deg: float = 40.0

    # Regler auf die Rückmeldung
    gain_p: float = 0.09
    gain_i: float = 0.02
    gain_d: float = 0.01
    integral_limit: float = 0.25


@dataclass
class SteeringConfig:
    """Autosteer output.

    Off by default.  Turning this on makes the machine steer itself, which is
    only safe with a properly installed steering motor or valve, a working
    emergency stop and an operator in the seat.
    """

    enabled: bool = False
    output: str = "udp"          # "phidget" | "udp" | "none" (nur mitschreiben)
    host: str = "192.168.5.9"    # nur für "udp"
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
    data_dir: str = DEFAULT_DATA_DIR
    update_hz: float = 10.0


@dataclass
class Config:
    gnss: GnssConfig = field(default_factory=GnssConfig)
    imu: ImuConfig = field(default_factory=ImuConfig)
    phidget: PhidgetConfig = field(default_factory=PhidgetConfig)
    corrections: CorrectionsConfig = field(default_factory=CorrectionsConfig)
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
        data["corrections"] = {**data["corrections"],
                               "password": "***" if self.corrections.password else ""}
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
        imu=ImuConfig(**_subset(ImuConfig, data.get("imu"))),
        phidget=PhidgetConfig(**_subset(PhidgetConfig, data.get("phidget"))),
        corrections=CorrectionsConfig(**_corrections_section(data)),
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


def _corrections_section(data: dict) -> dict:
    """Den Abschnitt für die Korrekturdaten einlesen.

    Frühere Konfigurationen hatten hier ``ntrip:`` mit einem Schalter
    ``enabled``. Solche Dateien sollen weiter laufen, ohne dass jemand von Hand
    umschreiben muss - deshalb wird der alte Abschnitt übernommen und der
    Schalter in die neue Quellenangabe übersetzt.
    """
    if isinstance(data.get("corrections"), dict):
        return _subset(CorrectionsConfig, data["corrections"])
    alt = data.get("ntrip")
    if not isinstance(alt, dict):
        return {}
    werte = _subset(CorrectionsConfig, alt)
    werte["source"] = "ntrip" if alt.get("enabled") else "aus"
    return werte


def _subset(cls: type, values: Any) -> dict:
    """Ignore unknown keys so an older binary still starts on a newer config."""
    if not isinstance(values, dict):
        return {}
    allowed = {f for f in cls.__dataclass_fields__}
    return {k: v for k, v in values.items() if k in allowed}
