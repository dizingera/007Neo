#!/usr/bin/env python3
"""Einen Windows-Installer bauen: eine .exe zum Doppelklicken.

    python3 scripts/make_exe.py                  -> gps/AgriPilot-Setup-<version>-<commit>.exe
    python3 scripts/make_exe.py -o /pfad/stick    -> auf den USB-Stick
    python3 scripts/make_exe.py --ohne-pakete     -> ohne Python-Bibliotheken, dafür klein

Warum eine .exe und nicht mehr nur ein Zip
------------------------------------------

Ein Zip mit einer Stapeldatei darin verlangt drei Schritte, die schiefgehen
können: vollständig entpacken, die richtige Datei finden, die Rückfrage nach
Administratorrechten bestätigen. Eine .exe verlangt einen: doppelklicken.

Das Zip bleibt trotzdem (``make_install.py``) - für den Fall, dass jemand
hineinsehen oder die Einrichtung von Hand fahren will.

Was hier gebaut wird
--------------------

Die .exe ist die Oberfläche: auspacken, Fortschritt zeigen, Eintrag in "Apps
& Features", Deinstallierer. Die Einrichtung selbst macht weiterhin
``install_windows.ps1``, das mit ausgeliefert wird. So gibt es die
Arbeitsschritte nur einmal.

Gebaut wird mit NSIS, das auch auf Linux läuft:

    apt-get install nsis

Ein Windows-Rechner ist zum Bauen also nicht nötig - was nicht selbstver-
ständlich ist; die üblichen Wege (PyInstaller, Inno Setup) verlangen einen.

Python auf dem Tablet
---------------------

Der Installer bringt alle Python-Bibliotheken mit, aber nicht Python selbst -
dessen Installer ist an der Quelle nicht frei weiterzugeben, und ihn blind
mitzuliefern wäre der falsche Weg. Die .exe sucht Python, und wenn keines da
ist, sagt sie es in einem Fenster und bietet an, python.org zu öffnen. Liegt
ein Python-Installer neben der .exe, führt sie ihn still aus - damit lässt
sich auch ein Tablet ohne WLAN einrichten, indem beide Dateien auf denselben
Stick kommen.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HIER = Path(__file__).resolve().parent
sys.path.insert(0, str(HIER.parent / "backend"))

from make_install import ZIEL_PLATTFORM, ZIEL_PYTHONS, pakete_laden  # noqa: E402

NSIS = "makensis"


def payload_bauen(quelle: Path, ziel: Path) -> None:
    """Die Dateien zusammenstellen, die in die .exe wandern.

    Dieselbe Auswahl wie beim Update-Paket - damit beide Wege auf demselben
    Stand landen und nicht einer davon eine Datei vergisst.
    """
    from agripilot import update

    ziel.mkdir(parents=True, exist_ok=True)
    for ordner in update.ORDNER:
        herkunft = quelle / ordner
        if not herkunft.is_dir():
            continue
        shutil.copytree(
            herkunft, ziel / ordner,
            ignore=shutil.ignore_patterns(*update.AUSGESCHLOSSEN,
                                          *(f"*{e}" for e in update.AUSGESCHLOSSENE_ENDUNGEN)))
    for datei in ("README.md",):
        if (quelle / datei).is_file():
            shutil.copy2(quelle / datei, ziel / datei)


def nsis_vorhanden() -> bool:
    return shutil.which(NSIS) is not None


def main() -> int:
    parser = argparse.ArgumentParser(
        description="AgriPilot-Installer (.exe) für ein Windows-Tablet bauen")
    parser.add_argument("-o", "--ziel", help="Ordner oder Dateiname für die .exe")
    parser.add_argument("--ohne-pakete", action="store_true",
                        help="ohne Python-Bibliotheken - dann braucht das Tablet WLAN")
    parser.add_argument("--python", default=",".join(ZIEL_PYTHONS),
                        metavar="3.11,3.12",
                        help="für welche Python-Fassungen die Bibliotheken gelten "
                             f"(Standard: {', '.join(ZIEL_PYTHONS)})")
    args = parser.parse_args()

    if not nsis_vorhanden():
        print(f"{NSIS} fehlt - ohne NSIS lässt sich keine .exe bauen.", file=sys.stderr)
        print("  Debian/Ubuntu:  apt-get install nsis", file=sys.stderr)
        return 1

    from agripilot import __version__, update

    quelle = HIER.parent
    commit = update.commit_der_quelle(quelle) or "ohne-git"
    name = f"AgriPilot-Setup-{__version__}-{commit}.exe"

    ziel = Path(args.ziel).resolve() if args.ziel else quelle / name
    if ziel.is_dir():
        ziel = ziel / name
    ziel.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="agripilot-exe-") as arbeit:
        payload = Path(arbeit) / "quelle"
        print("Programmdateien zusammenstellen ...")
        payload_bauen(quelle, payload)

        if not args.ohne_pakete:
            fassungen = tuple(t.strip() for t in args.python.split(",") if t.strip())
            print(f"Python-Bibliotheken für Windows laden ({ZIEL_PLATTFORM}, "
                  f"Python {', '.join(fassungen)}) ...")
            geklappt, _geschafft, meldung = pakete_laden(
                payload / "pakete", quelle, fassungen)
            print(f"  {meldung}")
            if not geklappt:
                print("  Die .exe wird trotzdem gebaut - dann aber mit Internet "
                      "einzurichten.", file=sys.stderr)

        print("Installer bauen ...")
        fertig = subprocess.run(
            [NSIS, "-V2",
             f"-DQUELLE={payload}",
             f"-DVERSION={__version__}",
             f"-DAUSGABE={ziel}",
             str(HIER / "installer.nsi")],
            capture_output=True, text=True)
        if fertig.returncode != 0:
            print(fertig.stdout[-4000:], file=sys.stderr)
            print(fertig.stderr[-4000:], file=sys.stderr)
            print(f"{NSIS} ist mit Fehler {fertig.returncode} abgebrochen.",
                  file=sys.stderr)
            return fertig.returncode

    groesse = ziel.stat().st_size / 1_000_000
    print(f"Installer: {ziel}  ({groesse:.1f} MB)")
    print("Auf das Tablet kopieren und doppelklicken - mehr ist es nicht.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
