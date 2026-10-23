#!/usr/bin/env python3
"""Den Master auf dem Raspberry Pi vom PC aus aktualisieren.

    python scripts/pi_ausrollen.py                         # Programm auf den Pi
    python scripts/pi_ausrollen.py --setup AgriPilot-Setup.exe   # dazu das Setup für die Arbeiter

Packt ``backend/agripilot``, ``frontend`` und die Pi-Skripte, kopiert sie per ssh
auf den Pi und ruft dort ``scripts/install_pi.sh`` auf. Das sichert vorher
Programm, Konfiguration und Datenbank nach ``/var/backups/agripilot/`` und
startet den Dienst neu. Konfiguration und Daten bleiben, wie sie sind.

Mit ``--setup`` landet die Windows-exe als ``AgriPilot-Setup-<Version>.exe`` in
``/var/lib/agripilot/setup/``. Von dort holen sich die Arbeiter (Tablets, Büro-PC)
das Update über ``/api/sync/setup``. Die Version kommt aus
``backend/agripilot/__init__.py``; die exe muss aus genau diesem Stand gebaut sein.

Braucht ssh/scp mit Schlüssel und ``sudo -n`` für den Benutzer auf dem Pi.
"""

import argparse
import hashlib
import io
import subprocess
import sys
import tarfile
import time
from pathlib import Path

GPS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(GPS / "backend"))

STANDARD_ZIEL = "pi@Dreambox.local"
STANDARD_PORT = 8090            # 8080 belegt auf dem Pi der Certis-Assistent
BEHALTEN_SETUPS = 2

DATEIEN = [
    ("backend/agripilot", True),
    ("backend/requirements.txt", False),
    ("frontend", True),
    ("scripts/install_pi.sh", False),
    ("scripts/agripilot.service", False),
    ("README.md", False),
]
WEGLASSEN = ("__pycache__", "node_modules", "tests", ".pytest_cache")


def _weglassen(info: tarfile.TarInfo):
    teile = Path(info.name).parts
    if any(t in WEGLASSEN for t in teile) or info.name.endswith((".pyc", ".pyo")):
        return None
    info.uid = info.gid = 0
    info.uname = info.gname = "root"
    # Unter Windows kommt 0o666 heraus - auf dem Pi soll niemand außer root schreiben.
    info.mode = 0o755 if info.isdir() or info.name.endswith(".sh") else 0o644
    return info


NUR_LF = (".sh", ".service")     # git (autocrlf) checkt sie unter Windows mit CRLF aus; bash scheitert daran


def paket() -> bytes:
    puffer = io.BytesIO()
    with tarfile.open(fileobj=puffer, mode="w:gz") as tar:
        for name, ordner in DATEIEN:
            pfad = GPS / name
            if not pfad.exists():
                if ordner:
                    raise SystemExit(f"{pfad} fehlt")
                continue
            if name.endswith(NUR_LF):
                inhalt = pfad.read_bytes().replace(b"\r\n", b"\n")
                info = _weglassen(tar.gettarinfo(str(pfad), arcname=name))
                info.size = len(inhalt)
                tar.addfile(info, io.BytesIO(inhalt))
                continue
            tar.add(pfad, arcname=name, filter=_weglassen)
    return puffer.getvalue()


SSH_OPTIONEN: list[str] = ["-oServerAliveInterval=10", "-oServerAliveCountMax=6"]


def _ausfuehren(befehl: list[str], eingabe: bytes | None = None, leise: bool = False,
                timeout: float | None = None) -> None:
    befehl = [befehl[0], *SSH_OPTIONEN, *befehl[1:]]
    if not leise:
        print("  >", " ".join(befehl))
    subprocess.run(befehl, input=eingabe, check=True, timeout=timeout)


STUECK = 256 * 1024
VERSUCHE = 12


def daten_senden(ziel: str, daten: bytes, fern: str, name: str = "") -> None:
    """Daten in Stücken auf den Pi schicken und dort per SHA-256 prüfen.

    Am 22.10.2026 riss jede größere Übertragung vom Büro-PC zum Pi ab
    ("Connection reset by peer"), über WLAN wie über Kabel - der PC verlor beim
    Senden ein Viertel seiner Pakete. Kleine Stücke kommen durch; ein
    abgebrochenes wird noch einmal geschickt, ein fertiges nie zweimal.
    """
    stuecke = [daten[i:i + STUECK] for i in range(0, len(daten), STUECK)] or [b""]
    print(f"  {name or fern}: {len(daten) / 1e6:.1f} MB in {len(stuecke)} Stücken")
    beginn = time.monotonic()
    for nr, stueck in enumerate(stuecke):
        teil = f"{fern}.{nr:05d}"
        for versuch in range(1, VERSUCHE + 1):
            try:
                # Erst unter Nebennamen, dann umbenennen: ein halbes Stück zählt nie.
                _ausfuehren(["ssh", ziel, f"cat > {teil}.halb && mv {teil}.halb {teil}"], stueck,
                            leise=True, timeout=240)
                break
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
                if versuch == VERSUCHE:
                    raise
                print(f"  Stück {nr + 1}/{len(stuecke)} abgebrochen, Versuch {versuch + 1}", flush=True)
                time.sleep(3)
        if nr % 8 == 7 or nr == len(stuecke) - 1:
            dauer = time.monotonic() - beginn
            print(f"  {nr + 1}/{len(stuecke)} Stücke, {dauer:.0f} s", flush=True)
    soll = hashlib.sha256(daten).hexdigest()
    zusammen = (f"cat {fern}.[0-9][0-9][0-9][0-9][0-9] > {fern} && rm -f {fern}.[0-9][0-9][0-9][0-9][0-9] && "
                f"test \"$(sha256sum {fern} | cut -d' ' -f1)\" = {soll}")
    _ausfuehren(["ssh", ziel, zusammen], timeout=120)
    print(f"  angekommen, SHA-256 {soll[:16]}... stimmt")


def main() -> None:
    from agripilot import __version__ as version

    teile = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    teile.add_argument("--ziel", default=STANDARD_ZIEL, help="ssh-Ziel (Benutzer@Rechner)")
    teile.add_argument("--port", type=int, default=STANDARD_PORT)
    teile.add_argument("--rolle", default="master", choices=("master", "client"))
    teile.add_argument("--setup", type=Path, help="AgriPilot-Setup.exe zum Verteilen an die Arbeiter")
    teile.add_argument("--adresse", help="IP statt der aus ~/.ssh/config, z. B. das LAN-Kabel "
                       "192.168.0.126 - über WLAN reißen große Übertragungen ab")
    args = teile.parse_args()
    if args.adresse:
        SSH_OPTIONEN.append(f"-oHostName={args.adresse}")

    if args.setup is not None and not args.setup.is_file():
        raise SystemExit(f"{args.setup} gibt es nicht")
    if args.setup is not None and args.setup.read_bytes()[:2] != b"MZ":
        raise SystemExit(f"{args.setup} ist kein Windows-Programm")

    stempel = time.strftime("%Y%m%d-%H%M%S")
    fern = f"/tmp/agripilot-ausrollen-{stempel}"
    print(f"AgriPilot {version} -> {args.ziel} (Port {args.port}, {args.rolle})")
    daten = paket()
    print(f"  Paket {len(daten) / 1e6:.1f} MB")

    _ausfuehren(["ssh", args.ziel, f"mkdir -p {fern}"])
    daten_senden(args.ziel, daten, f"{fern}/paket.tar.gz", "Programm")
    if args.setup is not None:
        daten_senden(args.ziel, args.setup.read_bytes(), f"{fern}/setup.exe", args.setup.name)

    schritte = [
        "set -e",
        f"cd {fern}",
        "tar xzf paket.tar.gz",
        f"sudo -n env AGRIPILOT_PORT={args.port} bash scripts/install_pi.sh {args.rolle}",
    ]
    if args.setup is not None:
        name = f"AgriPilot-Setup-{version}.exe"
        schritte += [
            f"sudo -n install -o agripilot -g agripilot -m 0644 setup.exe /var/lib/agripilot/setup/{name}",
            # Nur die neuesten behalten: ein Setup ist 50 MB, die SD-Karte ist endlich.
            # Der Datenordner ist nur für den Dienst lesbar (UMask 0027): mit sudo.
            "sudo -n sh -c 'cd /var/lib/agripilot/setup && ls -1 AgriPilot-Setup-*.exe | sort -V | "
            f"head -n -{BEHALTEN_SETUPS} | xargs -r rm -f --'",
        ]
    schritte.append(f"rm -rf {fern}")
    _ausfuehren(["ssh", args.ziel, "\n".join(schritte)])

    pruefen = (f"curl -fsS http://127.0.0.1:{args.port}/openapi.json | "
               "python3 -c \"import json,sys; print('Version auf dem Pi:', json.load(sys.stdin)['info']['version'])\"")
    _ausfuehren(["ssh", args.ziel, pruefen])
    if args.setup is not None:
        _ausfuehren(["ssh", args.ziel, "sudo -n ls -l /var/lib/agripilot/setup/"])


if __name__ == "__main__":
    main()
