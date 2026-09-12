#!/usr/bin/env python3
"""Ein Update-Paket bauen: eine Zip-Datei mit allem, was das Tablet braucht.

    python3 scripts/make_update.py              -> gps/agripilot-update-<version>-<commit>.zip
    python3 scripts/make_update.py -o D:\\stick  -> auf den USB-Stick

Das Paket enthält backend, frontend, scripts und docs mit einem Manifest
(update.json: Version, Commit, Prüfwert je Datei). Eingespielt wird es im
Programm unter Menü → System → Aktualisierung; danach Neustart mit einem Druck.
Konfiguration, Datenbank und Python-Umgebung auf dem Tablet bleiben unberührt.
"""

import argparse
import sys
from pathlib import Path

HIER = Path(__file__).resolve().parent
sys.path.insert(0, str(HIER.parent / "backend"))


def main() -> int:
    parser = argparse.ArgumentParser(description="AgriPilot-Update-Paket bauen")
    parser.add_argument("-o", "--ziel", help="Ordner oder Dateiname für das Paket")
    args = parser.parse_args()

    from agripilot import __version__, update

    quelle = HIER.parent
    ziel = None
    if args.ziel:
        ziel = Path(args.ziel)
        if ziel.is_dir():
            commit = update.commit_der_quelle(quelle) or "ohne-git"
            ziel = ziel / f"agripilot-update-{__version__}-{commit}.zip"
    pfad = update.paket_bauen(quelle, __version__, ziel)
    groesse = pfad.stat().st_size / 1_000_000
    print(f"Paket: {pfad}  ({groesse:.1f} MB)")
    print("Einspielen: auf dem Tablet Menue > System > Aktualisierung > Paket einspielen, dann Neustart.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
