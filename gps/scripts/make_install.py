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
# ein 64-Bit-Windows.
ZIEL_PLATTFORM = "win_amd64"

# Mehrere Python-Fassungen, nicht eine. Die meisten Pakete sind ohnehin
# fassungsunabhängig; nur drei bringen übersetzten Code mit und liegen deshalb
# je Fassung einmal bei - zusammen gut zwei Megabyte pro Fassung. Das ist der
# Preis dafür, dass niemand auf dem Tablet eine bestimmte Python-Fassung
# nachinstallieren muss, nur weil das Paket vor einem halben Jahr gebaut wurde.
#
# pip sucht sich aus dem Ordner von selbst die passende Datei; doppelte
# Fassungen stören einander nicht.
ZIEL_PYTHONS = ("3.11", "3.12", "3.13", "3.14")


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


def _aufzaehlung(teile: list[str]) -> str:
    """Aus 3.11/3.12/3.13 wird "3.11, 3.12 und 3.13" - fürs Lesen gedacht."""
    if len(teile) < 2:
        return "".join(teile)
    return f"{', '.join(teile[:-1])} und {teile[-1]}"


def _pip(*teile: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", "pip", "download", *teile],
                          capture_output=True, text=True)


def _letzte_zeile(fertig: subprocess.CompletedProcess) -> str:
    zeilen = (fertig.stderr or fertig.stdout).strip().splitlines()
    return zeilen[-1] if zeilen else "pip download ist fehlgeschlagen"


def pakete_laden(ziel: Path, quelle: Path,
                 fassungen: tuple[str, ...]) -> tuple[bool, list[str], str]:
    """Die Python-Bibliotheken für Windows vorab herunterladen.

    Geladen wird ausdrücklich für Windows, nicht für den Rechner, auf dem
    dieses Skript läuft - sonst landeten Linux-Dateien im Paket und der Einbau
    schlüge auf dem Tablet fehl, und zwar erst dort.

    Geladen wird für mehrere Python-Fassungen, damit das Paket nicht an einer
    einzigen hängt - welche auf dem Tablet landet, weiß beim Bauen niemand.

    Zwei Durchgänge je Fassung, weil pip hier eine Grenze hat: für eine *fremde*
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
    anforderungen = str(quelle / "backend" / "requirements.txt")
    fehlend: list[str] = []
    geschafft: list[str] = []

    for fassung in fassungen:
        fuer_windows = ["--dest", str(ziel), "--only-binary", ":all:",
                        "--platform", ZIEL_PLATTFORM, "--python-version", fassung]
        fertig = _pip(*fuer_windows, "-r", anforderungen)
        if fertig.returncode != 0:
            # Eine Fassung, für die es (noch) keine Pakete gibt, ist kein
            # Grund aufzuhören - die anderen taugen trotzdem. Gemeldet wird
            # sie am Ende.
            fehlend.append(f"Python {fassung}: {_letzte_zeile(fertig)}")
            continue
        geschafft.append(fassung)
        for name in TREIBER:
            if _pip(*fuer_windows, name).returncode == 0:
                continue
            # Kein fertiges Paket - als Quellpaket versuchen. Das ist nur dann
            # richtig, wenn nichts übersetzt werden muss; bei diesen beiden
            # Bibliotheken ist es reines Python.
            if _pip("--dest", str(ziel), "--no-deps", "--no-binary", ":all:",
                    name).returncode != 0:
                fehlend.append(f"{name} gar nicht")

    # setuptools und wheel liegen mit bei, obwohl AgriPilot sie nicht braucht:
    # tinkerforge kommt als Quellpaket, und pip baut daraus auf dem Tablet ein
    # Rad - dafür braucht es setuptools. Ohne Internet holt pip sie nirgends
    # her, und ab Python 3.12 bringt eine neue Umgebung sie auch nicht mehr
    # mit. Der Einbau bräche dann mitten im letzten Schritt ab.
    if geschafft and _pip("--dest", str(ziel), "--no-deps",
                          "setuptools", "wheel").returncode != 0:
        fehlend.append("setuptools/wheel (tinkerforge lässt sich ohne sie "
                       "nicht bauen)")

    anzahl = len(list(ziel.glob("*")))
    if not geschafft:
        return False, geschafft, (fehlend[-1] if fehlend else "nichts geladen")
    meldung = f"{anzahl} Dateien für Python {', '.join(geschafft)}"
    if fehlend:
        meldung += f" (nicht dabei: {'; '.join(sorted(set(fehlend)))})"
    return True, geschafft, meldung


def main() -> int:
    parser = argparse.ArgumentParser(
        description="AgriPilot-Installationspaket für ein Windows-Tablet bauen")
    parser.add_argument("-o", "--ziel", help="Ordner oder Dateiname für das Paket")
    parser.add_argument("--pakete", action="store_true",
                        help="Python-Bibliotheken mitnehmen (Einbau ohne Internet)")
    parser.add_argument("--python", default=",".join(ZIEL_PYTHONS),
                        metavar="3.11,3.12",
                        help="für welche Python-Fassungen die Pakete gelten "
                             f"(Standard: {', '.join(ZIEL_PYTHONS)})")
    args = parser.parse_args()

    fassungen = tuple(t.strip() for t in args.python.split(",") if t.strip())
    if args.pakete and not fassungen:
        print("--python: keine Fassung angegeben", file=sys.stderr)
        return 2

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
        print(f"Python-Pakete für Windows laden ({ZIEL_PLATTFORM}, "
              f"Python {', '.join(fassungen)}) ...")
        geklappt, geschafft, meldung = pakete_laden(pakete_ordner, quelle, fassungen)
        if not geklappt:
            print(f"  Fehlgeschlagen: {meldung}", file=sys.stderr)
            print("  Das Paket wird trotzdem gebaut - dann aber mit Internet "
                  "einzurichten.", file=sys.stderr)
        else:
            print(f"  {meldung}")
            pakete_text = (
                f"Die Python-Pakete liegen im Ordner pakete\\ bei, also ohne "
                f"Internet\n"
                f"   einzurichten. Sie gelten für Python "
                f"{_aufzaehlung(geschafft)} - eine\n"
                f"   davon genügt. Mit einer anderen Fassung geht es auch, "
                f"dann aber\n"
                f"   mit WLAN am Tablet.")

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
