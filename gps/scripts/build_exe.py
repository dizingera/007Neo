#!/usr/bin/env python3
"""AgriPilot-Setup.exe bauen - eine Datei, ohne Python auf dem Zielrechner.

    python scripts/build_exe.py

Braucht eine Build-Umgebung mit PyInstaller und allen Paketen (siehe unten);
die Entwicklungsumgebung backend/.venv hat sie nicht. Einmalig:

    python -m venv .buildvenv
    .buildvenv/Scripts/python -m pip install pyinstaller -r backend/requirements.txt phidget22 tinkerforge pywebview pillow

Ergebnis: gps/AgriPilot-Setup.exe. Doppelklick = Installation, ``--dienst`` = Programm,
``--fenster`` = Anzeige im eigenen Fenster (pywebview mit WebView2; pillow macht aus
dem App-Bild das Symbol der exe).
Die Datei ist nicht signiert: Windows SmartScreen fragt beim ersten Start nach
("Weitere Informationen" -> "Trotzdem ausführen").

Herstellerangaben (Versionsressource): Am 17.10.2026 hat Windows Defender die
installierte exe als "Trojan:Win32/Bearfoos.B!ml" entfernt - ein Fehlalarm der
Verhaltenserkennung: ein unsigniertes Programm ohne jede Herkunftsangabe, das
Aufgaben mit höchsten Rechten, Firewall-Regeln und einen Deinstallationseintrag
anlegt. Mit Hersteller, Produkt, Version und Beschreibung schlägt die Erkennung
deutlich seltener an; ganz sicher ist erst eine Signatur. UPX bleibt aus:
gepackte exe-Dateien gelten den Scannern als verdächtig.

Ohne Administrator-Manifest: sonst fragte Windows bei jedem Öffnen des Fensters
nach. Die Einrichtung fordert die Rechte selbst an (exe.installieren), Dienst und
Online-Update laufen als Aufgaben mit höchsten Rechten.
"""

import shutil
import subprocess
import sys
from pathlib import Path

GPS = Path(__file__).resolve().parent.parent

VERSIONSDATEI = """VSVersionInfo(
  ffi=FixedFileInfo(filevers={zahlen}, prodvers={zahlen}, mask=0x3f, flags=0x0,
                    OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040704B0', [
      StringStruct('CompanyName', 'Andreas Dizinger'),
      StringStruct('FileDescription', 'AgriPilot - GPS-Lenksystem für den Traktor (Einrichtung und Programm)'),
      StringStruct('FileVersion', '{version}'),
      StringStruct('InternalName', 'AgriPilot'),
      StringStruct('LegalCopyright', 'Andreas Dizinger'),
      StringStruct('OriginalFilename', 'AgriPilot-Setup.exe'),
      StringStruct('ProductName', 'AgriPilot'),
      StringStruct('ProductVersion', '{version}')])]),
    VarFileInfo([VarStruct('Translation', [0x0407, 1200])])
  ]
)
"""


def versionsdatei(ordner: Path) -> Path:
    """Die Versionsressource der exe (Hersteller, Produkt, Version) aus __init__.py."""
    sys.path.insert(0, str(GPS / "backend"))
    from agripilot import __version__
    teile = [int(x) for x in __version__.split(".")][:4]
    zahlen = tuple(teile + [0] * (4 - len(teile)))
    ordner.mkdir(parents=True, exist_ok=True)
    datei = ordner / "versionsinfo.txt"
    datei.write_text(VERSIONSDATEI.format(zahlen=zahlen, version=__version__), encoding="utf-8")
    return datei


def main() -> int:
    # --test: dieselbe Datei unter anderem Namen, zum Prüfen neben der ausgelieferten.
    test = "--test" in sys.argv
    # Nur den eigentlichen Bauordner leeren. Daneben können Simulatoren
    # und Prüfbelege liegen, die bei einem Testbau erhalten bleiben müssen.
    arbeit = GPS / "build-exe-test" / "pyinstaller" if test else GPS / "build-exe"
    if arbeit.exists():
        shutil.rmtree(arbeit)
    befehl = [
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onefile", "--noupx",
        "--name", "AgriPilot-Setup",
        "--version-file", str(versionsdatei(arbeit)),
        "--icon", str(GPS / "frontend" / "icon-512.png"),
        "--distpath", str(arbeit / "dist"), "--workpath", str(arbeit / "work"),
        "--specpath", str(arbeit),
        "--paths", str(GPS / "backend"),
        "--add-data", f"{GPS / 'frontend'};frontend",
        "--add-data", f"{GPS / 'docs' / 'DRITTLIZENZEN.md'};docs",
        "--collect-submodules", "agripilot",
        "--collect-submodules", "uvicorn",
        "--collect-submodules", "serial",
        "--collect-submodules", "websockets",
        "--collect-submodules", "Phidget22",
        "--collect-submodules", "tinkerforge",
        # Das eigene Fenster: pywebview mit WebView2 über WinForms (pythonnet).
        "--collect-all", "webview",
        "--collect-all", "clr_loader",
        "--collect-all", "pythonnet",
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
