#!/usr/bin/env python3
"""AgriPilot-Setup.exe bauen - eine Datei, ohne Python auf dem Zielrechner.

    python scripts/build_exe.py

Braucht eine Build-Umgebung mit PyInstaller und allen Paketen (siehe unten);
die Entwicklungsumgebung backend/.venv hat sie nicht. Einmalig:

    python -m venv .buildvenv
    .buildvenv/Scripts/python -m pip install pyinstaller -r backend/requirements.txt phidget22 tinkerforge

Ergebnis: gps/AgriPilot-Setup.exe. Doppelklick = Installation, ``--dienst`` = Programm.
Die Datei ist nicht signiert: Windows SmartScreen fragt beim ersten Start nach
("Weitere Informationen" -> "Trotzdem ausführen").
"""

import shutil
import subprocess
import sys
from pathlib import Path

GPS = Path(__file__).resolve().parent.parent


def main() -> int:
    # --test: ohne Administrator-Manifest, damit sich die Datei aus einer normalen
    # Shell starten lässt (zum Prüfen). Die ausgelieferte Datei verlangt Admin.
    test = "--test" in sys.argv
    arbeit = GPS / ("build-exe-test" if test else "build-exe")
    if arbeit.exists():
        shutil.rmtree(arbeit)
    befehl = [
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onefile",
        "--name", "AgriPilot-Setup",
        *([] if test else ["--uac-admin"]),
        "--distpath", str(arbeit / "dist"), "--workpath", str(arbeit / "work"),
        "--specpath", str(arbeit),
        "--paths", str(GPS / "backend"),
        "--add-data", f"{GPS / 'frontend'};frontend",
        "--collect-submodules", "agripilot",
        "--collect-submodules", "uvicorn",
        "--collect-submodules", "serial",
        "--collect-submodules", "websockets",
        "--collect-submodules", "Phidget22",
        "--collect-submodules", "tinkerforge",
        "--hidden-import", "yaml",
        "--hidden-import", "httpx",
        str(GPS / "scripts" / "exe_start.py"),
    ]
    print(" ".join(befehl[:6]), "...")
    if subprocess.run(befehl, cwd=GPS).returncode != 0:
        return 1
    ergebnis = arbeit / "dist" / "AgriPilot-Setup.exe"
    ziel = GPS / ("AgriPilot-Test.exe" if test else "AgriPilot-Setup.exe")
    shutil.copy2(ergebnis, ziel)
    print(f"\nFertig: {ziel}  ({ziel.stat().st_size / 1_000_000:.1f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
