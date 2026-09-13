#!/usr/bin/env python3
"""Ein Installationspaket bauen: eine Zip-Datei für ein frisches Tablet.

    python3 scripts/make_install.py                 -> gps/agripilot-installation-<version>-<commit>.zip
    python3 scripts/make_install.py -o D:\\stick      -> auf den USB-Stick
    python3 scripts/make_install.py --pakete         -> mit Python-Paketen, für den Einbau ohne Internet

Unterschied zum Update-Paket
----------------------------

``make_update.py`` baut, was ein **laufendes** System einspielt: Programmdateien
mit Prüfwerten, eingelesen über Menü → System. Es setzt voraus, dass auf dem
Tablet schon alles steht.

Dieses Paket ist für ein Tablet, auf dem noch **nichts** ist. Es enthält
dieselben Programmdateien und zusätzlich:

* ``INSTALLIEREN.bat`` ganz oben - doppelklicken genügt, die Datei holt sich
  selbst die Administratorrechte,
* ``LIESMICH.txt`` mit den drei Schritten, die es wirklich braucht,
* auf Wunsch ``pakete/`` mit den Python-Bibliotheken, damit der Einbau ohne
  Internet durchläuft. In einer Maschinenhalle ist das der Normalfall.

Warum kein fertiges .exe
------------------------

Ein gebündeltes Programm müsste den Python-Unterbau, die Treiber für
Phidget und Tinkerforge und die Weboberfläche in eine Datei pressen - und
jedes Mal neu, wenn sich eine Kleinigkeit ändert. Auf dem Tablet liegt
stattdessen ein gewöhnlicher Python-Ordner: nachvollziehbar, einzeln
austauschbar, und im Fehlerfall kann man hineinsehen. Der Preis ist ein
einmaliger Python-Installer, und den holt die Anleitung ab.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

HIER = Path(__file__).resolve().parent
sys.path.insert(0, str(HIER.parent / "backend"))

# Die Python-Pakete, die auf dem Tablet gebraucht werden und nicht in
# requirements.txt stehen: die Treiberbibliotheken für Lenkmotor und
# Neigungssensor. Dieselben Namen wie in install_windows.ps1.
TREIBER = ("phidget22", "tinkerforge")

# Wofür die Pakete geladen werden, wenn sie beiliegen sollen. Das Tablet ist
# ein 64-Bit-Windows; die Python-Fassung muss zu der passen, die dort
# installiert wird.
ZIEL_PLATTFORM = "win_amd64"
ZIEL_PYTHON = "3.11"


LIESMICH = """AgriPilot – Einrichtung auf dem Windows-Tablet
=============================================

Version {version} ({commit})

Dieses Paket gehört auf das Tablet, das in der Kabine rechnet. Das
Android-Tablet bekommt nichts installiert – es zeigt nur an, über den Browser.


So geht es
----------

1. Diesen Ordner vollständig auf das Tablet kopieren (Stick, Netzlaufwerk).
   Vollständig heißt: die Zip-Datei entpacken, nicht nur hineinschauen.

2. Ist Python noch nicht installiert: von python.org holen und einrichten.
   Beim Installieren den Haken bei "Add python.exe to PATH" setzen.
   {python_hinweis}

3. INSTALLIEREN.bat doppelklicken. Windows fragt nach Administratorrechten –
   die braucht es für die Firewall und den automatischen Start.

Danach läuft AgriPilot bei jeder Anmeldung von selbst. Am Ende der Einrichtung
stehen die Adressen im Fenster: eine für dieses Tablet, eine für das
Android-Tablet im selben WLAN.


Das Android-Tablet
------------------

Es braucht nur den Browser. Damit aber die Kachel auf dem Startbildschirm
entsteht und – wichtiger – der Bildschirm während der Arbeit anbleibt, muss die
Verbindung verschlüsselt sein. Dafür einmal auf dem Windows-Tablet:

    powershell -ExecutionPolicy Bypass -File C:\\AgriPilot\\scripts\\make_cert.ps1

Das Skript sagt danach Schritt für Schritt, was auf dem Android-Tablet zu tun
ist. Die ausführliche Fassung steht in docs\\TABLETS.md.


Wenn etwas klemmt
-----------------

* "Python fehlt"  – Schritt 2 nachholen, dann erneut doppelklicken.
* Pakete laden nicht – das Tablet ins WLAN, oder das Paket am Hofrechner mit
  "python scripts\\make_install.py --pakete" neu bauen; dann liegen sie bei.
* Kein Empfänger – C:\\AgriPilot\\venv\\Scripts\\python.exe
  C:\\AgriPilot\\scripts\\scan_devices.py zeigt, was angeschlossen ist.

Alles Weitere: docs\\INSTALL.md, docs\\BEDIENUNG.md, docs\\DEINE_ANLAGE.md.
"""


def _pip(*teile: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", "pip", "download", *teile],
                          capture_output=True, text=True)


def _letzte_zeile(fertig: subprocess.CompletedProcess) -> str:
    zeilen = (fertig.stderr or fertig.stdout).strip().splitlines()
    return zeilen[-1] if zeilen else "pip download ist fehlgeschlagen"


def pakete_laden(ziel: Path, quelle: Path) -> tuple[bool, str]:
    """Die Python-Bibliotheken für Windows vorab herunterladen.

    Geladen wird ausdrücklich für Windows, nicht für den Rechner, auf dem
    dieses Skript läuft - sonst landeten Linux-Dateien im Paket und der Einbau
    schlüge auf dem Tablet fehl, und zwar erst dort.

    Zwei Durchgänge, weil pip hier eine Grenze hat: für eine *fremde*
    Plattform gibt es nur fertige Pakete (``--only-binary``), und Bibliotheken
    ohne fertiges Paket fallen damit durch. ``tinkerforge`` ist so eine - sie
    besteht aus nichts als Python-Dateien und läuft auf Windows genauso, es
    gibt sie nur nicht als Rad. Die holt der zweite Durchgang als Quellpaket,
    ohne Plattformangabe.

    Was auch dann nicht kommt, wird gemeldet statt verschwiegen: ein Paket,
    das auf dem Tablet fehlt, fällt sonst erst dort auf - in der Halle, ohne
    Internet.
    """
    ziel.mkdir(parents=True, exist_ok=True)
    fuer_windows = ["--dest", str(ziel), "--only-binary", ":all:",
                    "--platform", ZIEL_PLATTFORM, "--python-version", ZIEL_PYTHON]

    fehlend: list[str] = []
    fertig = _pip(*fuer_windows, "-r", str(quelle / "backend" / "requirements.txt"))
    if fertig.returncode != 0:
        return False, _letzte_zeile(fertig)

    for name in TREIBER:
        if _pip(*fuer_windows, name).returncode == 0:
            continue
        # Kein fertiges Paket - als Quellpaket versuchen. Das ist nur dann
        # richtig, wenn nichts übersetzt werden muss; bei diesen beiden
        # Bibliotheken ist es reines Python.
        quellpaket = _pip("--dest", str(ziel), "--no-deps", "--no-binary", ":all:", name)
        if quellpaket.returncode != 0:
            fehlend.append(name)

    anzahl = len(list(ziel.glob("*")))
    if fehlend:
        return False, (f"{anzahl} Dateien geladen, aber nicht dabei: "
                       f"{', '.join(fehlend)}")
    return True, f"{anzahl} Dateien"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="AgriPilot-Installationspaket für ein Windows-Tablet bauen")
    parser.add_argument("-o", "--ziel", help="Ordner oder Dateiname für das Paket")
    parser.add_argument("--pakete", action="store_true",
                        help="Python-Bibliotheken mitnehmen (Einbau ohne Internet)")
    args = parser.parse_args()

    from agripilot import __version__, update

    quelle = HIER.parent
    commit = update.commit_der_quelle(quelle) or "ohne-git"
    name = f"agripilot-installation-{__version__}-{commit}.zip"

    ziel = Path(args.ziel) if args.ziel else quelle / name
    if ziel.is_dir():
        ziel = ziel / name

    # Erst das Programm mit Manifest - dieselbe Zusammenstellung, die auch ein
    # Update ausmacht, damit beide Wege dieselben Dateien liefern.
    update.paket_bauen(quelle, __version__, ziel, commit)

    pakete_ordner = quelle / ".pakete-fuer-windows"
    pakete_text = ""
    if args.pakete:
        print(f"Python-Pakete für Windows laden ({ZIEL_PLATTFORM}, Python {ZIEL_PYTHON}) ...")
        geklappt, meldung = pakete_laden(pakete_ordner, quelle)
        if not geklappt:
            print(f"  Fehlgeschlagen: {meldung}", file=sys.stderr)
            print("  Das Paket wird trotzdem gebaut - dann aber mit Internet "
                  "einzurichten.", file=sys.stderr)
        else:
            print(f"  {meldung}")
            pakete_text = (
                f"Die Python-Pakete liegen im Ordner pakete\\ bei, also ohne\n"
                f"   Internet einzurichten. WICHTIG: sie passen zu **Python "
                f"{ZIEL_PYTHON}** -\n"
                f"   genau diese Fassung installieren, keine neuere.")

    python_hinweis = pakete_text or (
        "Ohne Internet am Tablet: den Installer am Hofrechner laden\n"
        "   und auf demselben Stick mitbringen.")

    with zipfile.ZipFile(ziel, "a", zipfile.ZIP_DEFLATED) as zf:
        zf.write(HIER / "INSTALLIEREN.bat", "INSTALLIEREN.bat")
        zf.writestr("LIESMICH.txt", LIESMICH.format(
            version=__version__, commit=commit, python_hinweis=python_hinweis))
        if pakete_ordner.is_dir():
            for datei in sorted(pakete_ordner.iterdir()):
                if datei.is_file():
                    zf.write(datei, f"pakete/{datei.name}")

    if pakete_ordner.is_dir():
        shutil.rmtree(pakete_ordner, ignore_errors=True)

    groesse = ziel.stat().st_size / 1_000_000
    print(f"Paket: {ziel}  ({groesse:.1f} MB)")
    print("Auf das Tablet kopieren, entpacken, INSTALLIEREN.bat doppelklicken.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
