"""Das Programm als einzelne .exe - ohne Python auf dem Tablet.

``AgriPilot-Setup.exe`` ist beides in einer Datei:

* **Doppelklick** richtet AgriPilot auf diesem Rechner ein (Installation,
  Erst- und Wiederholungslauf; Konfiguration und Daten bleiben erhalten),
* ``--dienst`` ist das laufende Programm - so startet es Windows beim Anmelden.

Gebaut wird sie mit ``scripts/build_exe.py`` (PyInstaller). Was die Installation
tut, steht als Liste von Schritten in ``plan()``; ``--trocken`` zeigt sie nur an.
Das ist auch, wie getestet wird: die Schritte selbst fassen Windows an (Aufgabe,
Firewall), die Liste nicht.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

AUFGABE = "AgriPilot"
STANDARD_ZIEL = r"C:\AgriPilot"
EXE_NAME = "AgriPilot.exe"


# ------------------------------------------------------------- Bausteine

def standard_konfiguration(rechnername: str, port: int = 8080, rolle: str = "master",
                           master_url: str = "") -> str:
    """Erste Konfiguration. Wer sie schon hat, behält seine (siehe plan()).

    ``rolle``: "master" (der Rechner im Büro: erfasst Felder, Maschinen, Umrisse und
    schickt sie an die Arbeiter) oder "client" (ein Arbeiter: das Tablet im Traktor,
    das vom Master bekommt, was der erfasst hat).
    """
    master_zeile = f"  master_url: {master_url}\n" if master_url else ""
    return f"""# AgriPilot - {"Master (Büro)" if rolle == "master" else "Arbeiter (Traktor)"}.
# Anschlüsse und Kanäle findet das Programm selbst: Menü -> System -> Geräte suchen.

gnss:
  source: simulator        # nach "Geräte suchen" auf serial umstellen (Menü -> System)
  port: COM3
  baudrate: 115200
  rtcm_out: auto

imu:
  source: aus              # auf tinkerforge setzen, sobald brickd läuft
  uid: ''
  axis_map: standard
  roll_sign: 1.0
  terrain_compensation: true

corrections:
  source: aus
  host: ''
  port: 2101
  mountpoint: ''
  username: ''
  password: ''
  serial_port: ''
  baudrate: 115200
  send_gga: true

network:
  role: {rolle}
{master_zeile}  device_id: {rechnername}
  device_name: {rechnername}
  rtcm_relay_port: 2102

server:
  host: 0.0.0.0
  port: {port}
"""


def start_vbs(exe: str) -> str:
    """Startet das Programm ohne Konsolenfenster und gibt dessen Ausstiegscode weiter.

    Der Code ist wichtig: stürzt das Programm ab, soll Windows die Aufgabe neu
    starten (RestartCount); ein Skript, das immer 0 meldet, würde das verhindern.
    """
    return (
        'Set shell = CreateObject("WScript.Shell")\r\n'
        f'code = shell.Run("""{exe}"" --dienst", 0, True)\r\n'
        "WScript.Quit code\r\n"
    )


def aufgaben_skript(ziel: str, vbs: str) -> str:
    """PowerShell, das die Aufgabe "AgriPilot" einträgt: Start beim Anmelden,
    Neustart nach Absturz, keine Zeitbegrenzung, auch im Akkubetrieb."""
    return (
        f'$aktion = New-ScheduledTaskAction -Execute "wscript.exe" -Argument \'"{vbs}"\' '
        f'-WorkingDirectory "{ziel}"; '
        "$ausloeser = New-ScheduledTaskTrigger -AtLogOn; "
        "$option = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries "
        "-DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero) "
        "-RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1); "
        f'Register-ScheduledTask -TaskName "{AUFGABE}" -Action $aktion -Trigger $ausloeser '
        "-Settings $option -Force -RunLevel Highest | Out-Null"
    )


@dataclass
class Schritt:
    text: str
    aktion: Callable[[], None]


UPDATE_AUFGABE = "AgriPilot-Update"


def setup_starten(pfad: Path) -> None:
    """Die geladene Setup-Datei als eigene, einmalige Windows-Aufgabe starten.

    Als Kindprozess des laufenden Programms würde sie mit ihm enden: die Aufgabe
    "AgriPilot" liegt in einem Job-Objekt, und das Anhalten der Aufgabe (erster
    Schritt der Installation) beendet alles darin. Eine eigene Aufgabe steht
    daneben und überlebt das.
    """
    befehl = f'"{pfad}" --installieren --still'
    _lauf(["schtasks", "/Create", "/TN", UPDATE_AUFGABE, "/TR", befehl, "/SC", "ONCE",
           "/ST", "23:59", "/RL", "HIGHEST", "/F"])
    _lauf(["schtasks", "/Run", "/TN", UPDATE_AUFGABE])


def plan(ziel: str, port: int = 8080, quelle: Optional[str] = None,
         daten: Optional[str] = None, rechnername: Optional[str] = None,
         still: bool = False, rolle: str = "master", master_url: str = "") -> list[Schritt]:
    """Die Schritte der Installation, in der Reihenfolge, in der sie laufen."""
    quelle = quelle or sys.executable
    rechnername = rechnername or os.environ.get("COMPUTERNAME", "TABLET")
    daten_ordner = Path(daten or Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData")) / "AgriPilot")
    zielpfad = Path(ziel)
    exe = zielpfad / EXE_NAME
    vbs = zielpfad / "start_leise.vbs"
    konfig = daten_ordner / "config.yaml"

    def stoppen() -> None:
        _lauf(["schtasks", "/End", "/TN", AUFGABE], ok_codes=None)
        eigene = os.getpid()
        _lauf(["powershell", "-NoProfile", "-Command",
               f"Get-Process AgriPilot -ErrorAction SilentlyContinue | "
               f"Where-Object {{ $_.Id -ne {eigene} }} | Stop-Process -Force"], ok_codes=None)
        time.sleep(2.0)       # Port und Datei werden erst jetzt frei

    alt = zielpfad / (EXE_NAME + ".alt")

    def kopieren() -> None:
        zielpfad.mkdir(parents=True, exist_ok=True)
        if Path(quelle).resolve() != exe.resolve():
            if exe.exists():
                shutil.copy2(exe, alt)       # der bisherige Stand, falls der neue nicht anläuft
            shutil.copy2(quelle, exe)

    def zurueckrollen() -> bool:
        if not alt.exists():
            return False
        _lauf(["schtasks", "/End", "/TN", AUFGABE], ok_codes=None)
        _lauf(["powershell", "-NoProfile", "-Command",
               f"Get-Process AgriPilot -ErrorAction SilentlyContinue | "
               f"Where-Object {{ $_.Id -ne {os.getpid()} }} | Stop-Process -Force"], ok_codes=None)
        time.sleep(2.0)
        shutil.copy2(alt, exe)
        _lauf(["schtasks", "/Run", "/TN", AUFGABE], ok_codes=None)
        return True

    def konfiguration() -> None:
        daten_ordner.mkdir(parents=True, exist_ok=True)
        if not konfig.exists():
            konfig.write_text(standard_konfiguration(rechnername, port, rolle, master_url),
                              encoding="utf-8")

    def firewall() -> None:
        regel = f"AgriPilot {port}"
        vorhanden = _lauf(["netsh", "advfirewall", "firewall", "show", "rule", f"name={regel}"],
                          ok_codes=None)
        if vorhanden != 0:
            _lauf(["netsh", "advfirewall", "firewall", "add", "rule", f"name={regel}",
                   "dir=in", "action=allow", "protocol=TCP", f"localport={port}",
                   "profile=any"])

    def starter() -> None:
        vbs.write_text(start_vbs(str(exe)), encoding="ascii")

    def aufgabe() -> None:
        _lauf(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command",
               aufgaben_skript(str(zielpfad), str(vbs))])

    def symbol() -> None:
        desktop = Path(os.environ.get("PUBLIC", r"C:\Users\Public")) / "Desktop"
        if desktop.is_dir():
            (desktop / "AgriPilot.url").write_text(
                f"[InternetShortcut]\r\nURL=http://localhost:{port}\r\n", encoding="ascii")

    def starten() -> None:
        _lauf(["schtasks", "/Run", "/TN", AUFGABE])
        for _ in range(60):
            time.sleep(1.0)
            try:
                urllib.request.urlopen(f"http://localhost:{port}/api/system", timeout=2).read()
                break
            except Exception:  # noqa: BLE001 - noch nicht oben
                continue
        else:
            zurueck = ""
            try:
                if zurueckrollen():
                    zurueck = " Der vorige Stand wurde wieder eingesetzt."
            except Exception as exc:  # noqa: BLE001
                zurueck = f" Zurückrollen scheiterte: {exc}"
            raise RuntimeError(f"Das neue Programm meldet sich nach 60 Sekunden nicht.{zurueck} "
                               f"Protokoll: {daten_ordner / 'agripilot.log'}")
        if not still:
            os.startfile(f"http://localhost:{port}")  # type: ignore[attr-defined]

    def aufraeumen() -> None:
        _lauf(["schtasks", "/Delete", "/TN", UPDATE_AUFGABE, "/F"], ok_codes=None)

    return [
        Schritt("Laufende Version anhalten", stoppen),
        Schritt(f"Programm nach {ziel} kopieren", kopieren),
        Schritt(f"Konfiguration in {daten_ordner} (vorhandene bleibt)", konfiguration),
        Schritt(f"Firewall: Port {port} für das Android-Tablet freigeben", firewall),
        Schritt("Startdatei ohne Fenster anlegen", starter),
        Schritt("Automatischen Start beim Anmelden eintragen (mit Neustart nach Absturz)", aufgabe),
        Schritt("Symbol 'AgriPilot' auf den Desktop legen", symbol),
        Schritt("Programm starten und Anzeige öffnen", starten),
        Schritt("Aufräumen", aufraeumen),
    ]


def _lauf(befehl: list[str], ok_codes: Optional[tuple] = (0,)) -> int:
    """Ein Windows-Werkzeug aufrufen. ``ok_codes=None``: Ausgang egal."""
    ergebnis = subprocess.run(befehl, capture_output=True, text=True, errors="replace")
    if ok_codes is not None and ergebnis.returncode not in ok_codes:
        raise RuntimeError(f"{' '.join(befehl[:3])} ... lieferte {ergebnis.returncode}: "
                           f"{(ergebnis.stderr or ergebnis.stdout).strip()[:300]}")
    return ergebnis.returncode


def ist_admin() -> bool:
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        return False


# --------------------------------------------------------------- Aufrufe

def _rolle_erfragen(daten_ordner: Path) -> tuple[str, str]:
    """Master oder Arbeiter? Nur beim ersten Einrichten und nur mit Tastatur davor."""
    if (daten_ordner / "config.yaml").exists() or not sys.stdin or not sys.stdin.isatty():
        return "master", ""
    print("Ist dieses Gerät der MASTER oder ein ARBEITER?")
    print("  M = Master: der Rechner im Büro. Hier werden Felder, Maschinen und Umrisse erfasst.")
    print("  A = Arbeiter: das Tablet im Traktor. Es bekommt vom Master, was der erfasst hat.")
    wahl = input("[M/A] (Enter = M): ").strip().lower()
    if not wahl.startswith("a"):
        return "master", ""
    url = input("Adresse des Masters, z. B. http://192.168.1.20:8080\n"
                "(steht beim Master unter Menü → System; Enter = später eintragen): ").strip()
    if url and not url.startswith("http"):
        url = "http://" + url
    return "client", url


def installieren(ziel: str, port: int, trocken: bool, still: bool = False,
                 rolle: Optional[str] = None, master_url: str = "") -> int:
    schritte = plan(ziel, port, still=still, rolle=rolle or "master", master_url=master_url)
    if trocken:
        for nummer, schritt in enumerate(schritte, 1):
            print(f"{nummer}. {schritt.text}")
        return 0
    if os.name != "nt":
        print("Die Installation gibt es nur für Windows.")
        return 1
    if not ist_admin():
        import ctypes
        print("Administratorrechte werden angefordert ...")
        ctypes.windll.shell32.ShellExecuteW(  # type: ignore[attr-defined]
            None, "runas", sys.executable, " ".join(f'"{a}"' for a in sys.argv[1:]), None, 1)
        return 0
    if rolle is None and not still:
        daten_ordner = Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData")) / "AgriPilot"
        rolle, master_url = _rolle_erfragen(daten_ordner)
        schritte = plan(ziel, port, still=still, rolle=rolle, master_url=master_url)
    print("AgriPilot wird eingerichtet.\n")
    for nummer, schritt in enumerate(schritte, 1):
        print(f"[{nummer}/{len(schritte)}] {schritt.text} ...", flush=True)
        try:
            schritt.aktion()
        except Exception as exc:  # noqa: BLE001
            print(f"\nFEHLER: {exc}")
            return 1
    print("\nFertig. AgriPilot läuft und startet künftig beim Anmelden von selbst.")
    print(f"Anzeige: http://localhost:{port}  (Symbol 'AgriPilot' auf dem Desktop)")
    return 0


def dienst() -> int:
    """Das eigentliche Programm. Ausgaben gehen in eine Datei: es läuft ohne Fenster."""
    from . import config as config_module
    daten = Path(config_module.load().server.data_dir)
    daten.mkdir(parents=True, exist_ok=True)
    protokoll = daten / "agripilot.log"
    if protokoll.exists() and protokoll.stat().st_size > 5_000_000:
        protokoll.replace(daten / "agripilot.log.alt")
    datei = open(protokoll, "a", encoding="utf-8", buffering=1)
    sys.stdout = sys.stderr = datei
    print(f"\n=== Start {time.strftime('%Y-%m-%d %H:%M:%S')} ===", flush=True)
    from .server import main as server_main
    server_main()
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="AgriPilot")
    parser.add_argument("--dienst", action="store_true", help="Programm starten (so startet es Windows)")
    parser.add_argument("--installieren", action="store_true", help="einrichten (Standard bei Doppelklick)")
    parser.add_argument("--ziel", default=STANDARD_ZIEL)
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--trocken", action="store_true", help="nur anzeigen, was die Installation täte")
    parser.add_argument("--arbeiter", action="store_true",
                        help="als Arbeiter einrichten (Tablet im Traktor); Standard ist die Frage beim Einrichten")
    parser.add_argument("--master", default="", metavar="URL",
                        help="Adresse des Masters für den Arbeiter, z. B. http://192.168.1.20:8080")
    parser.add_argument("--still", action="store_true",
                        help="ohne Rückfragen und ohne Browser (Aktualisierung über das Netz)")
    parser.add_argument("--version", action="store_true")
    args = parser.parse_args(argv)
    if args.version:
        from . import __version__
        print(__version__)
        return 0
    if args.dienst:
        return dienst()
    if args.still:
        # Gestartet vom laufenden Programm, ohne Fenster: Ausgabe in eine Datei.
        from . import config as config_module
        daten = Path(config_module.load().server.data_dir)
        daten.mkdir(parents=True, exist_ok=True)
        sys.stdout = sys.stderr = open(daten / "update.log", "a", encoding="utf-8", buffering=1)
        print(f"\n=== Aktualisierung {time.strftime('%Y-%m-%d %H:%M:%S')} ===")
    rolle = "client" if (args.arbeiter or args.master) else None
    master_url = args.master
    if master_url and not master_url.startswith("http"):
        master_url = "http://" + master_url
    code = installieren(args.ziel, args.port, args.trocken, args.still, rolle, master_url)
    if getattr(sys, "frozen", False) and not args.trocken and not args.still:
        input("\nEnter zum Schließen ... ")
    return code
