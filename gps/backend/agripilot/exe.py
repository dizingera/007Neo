"""Das Programm als einzelne .exe - ohne Python auf dem Tablet.

``AgriPilot-Setup.exe`` ist beides in einer Datei:

* **Doppelklick** richtet AgriPilot auf diesem Rechner ein (Installation,
  Erst- und Wiederholungslauf; Konfiguration und Daten bleiben erhalten),
* ``--fenster`` ist AgriPilot im eigenen Fenster (Symbol auf dem Desktop): startet
  das Programm und beendet es beim Schließen,
* ``--dienst`` ist das laufende Programm dahinter (Aufgabe "AgriPilot", kein Autostart),
* ``--deinstallieren`` entfernt alles wieder (Apps & Features; Daten bleiben auf Wunsch).

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

def neuer_code() -> str:
    """Ein Arbeitercode zum Abtippen auf dem Tablet: acht Zeichen, ohne 0/O/1/I."""
    import secrets
    zeichen = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    roh = "".join(secrets.choice(zeichen) for _ in range(8))
    return f"{roh[:4]}-{roh[4:]}"


def standard_konfiguration(rechnername: str, port: int = 8080, rolle: str = "master",
                           master_url: str = "", code: str = "") -> str:
    """Erste Konfiguration. Wer sie schon hat, behält seine (siehe plan()).

    ``rolle``: "master" (der Rechner im Büro: erfasst Felder, Maschinen, Umrisse und
    schickt sie an die Arbeiter) oder "client" (ein Arbeiter: das Tablet im Traktor,
    das vom Master bekommt, was der erfasst hat).
    """
    master_zeile = f"  master_url: {master_url}\n" if master_url else ""
    code_zeile = f"  sync_token: {code}\n" if code else ""
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
{master_zeile}{code_zeile}  device_id: {rechnername}
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


def fenster_vbs(exe: str) -> str:
    """Öffnet die Anzeige ohne das schwarze Konsolenfenster daneben.

    Die exe ist ein Konsolenprogramm (sie richtet auch ein und fragt dabei nach);
    versteckt wird nur die Konsole, das Anzeigefenster erscheint normal.
    """
    return (
        'Set shell = CreateObject("WScript.Shell")\r\n'
        f'shell.Run """{exe}"" --fenster", 0, False\r\n'
    )


def verknuepfung_skript(lnk: str, ziel: str, argumente: str, symbol: str, ordner: str,
                        beschreibung: str) -> tuple[str, dict]:
    """PowerShell für eine Verknüpfung mit dem Programmsymbol.

    Die Werte gehen als Umgebungsvariablen hinein, nicht in den Befehlstext: so
    stören weder Leerzeichen noch Anführungszeichen in einem Pfad.
    """
    return (
        "$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:LNK); "
        "$s.TargetPath = $env:ZIEL; "
        "$s.Arguments = $env:ARGUMENTE; "
        "$s.IconLocation = $env:SYMBOL + ',0'; "
        "$s.WorkingDirectory = $env:ORDNER; "
        "$s.Description = $env:BESCHREIBUNG; "
        "$s.Save()"
    ), {"LNK": lnk, "ZIEL": ziel, "ARGUMENTE": argumente, "SYMBOL": symbol,
        "ORDNER": ordner, "BESCHREIBUNG": beschreibung}


def verknuepfung_anlegen(lnk: Path, ziel: Path, argumente: str, symbol: Path, ordner: Path,
                         beschreibung: str = "AgriPilot - Spurführung") -> None:
    skript, umgebung = verknuepfung_skript(str(lnk), str(ziel), argumente, str(symbol),
                                           str(ordner), beschreibung)
    ergebnis = subprocess.run(["powershell", "-NoProfile", "-Command", skript],
                              capture_output=True, text=True, errors="replace",
                              env={**os.environ, **umgebung})
    if ergebnis.returncode != 0:
        raise RuntimeError(f"Verknüpfung {lnk.name}: "
                           f"{(ergebnis.stderr or ergebnis.stdout).strip()[:300]}")


def wscript() -> Path:
    return Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "wscript.exe"


def desktop_ordner() -> Path:
    return Path(os.environ.get("PUBLIC", r"C:\Users\Public")) / "Desktop"


def startmenue_ordner() -> Path:
    return (Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData"))
            / "Microsoft" / "Windows" / "Start Menu" / "Programs")


def alte_autostarts() -> list[Path]:
    """Was AgriPilot früher beim Anmelden gestartet hat - kommt bei jeder Einrichtung weg."""
    return [startmenue_ordner() / "StartUp" / "AgriPilot.lnk", desktop_ordner() / "AgriPilot.url"]


def aufgaben_skript(ziel: str, vbs: str) -> str:
    """PowerShell, das die Aufgabe "AgriPilot" einträgt: Neustart nach Absturz,
    keine Zeitbegrenzung, auch im Akkubetrieb - aber **kein** Start beim Anmelden.

    Gestartet wird sie vom Fenster (Symbol "AgriPilot"), beendet beim Schließen.
    Eine Aufgabe statt eines einfachen Prozesses, weil sie mit höchsten Rechten
    läuft, ohne dass Windows bei jedem Start nachfragt, und nach einem Absturz
    von selbst wieder anläuft.
    """
    return (
        f'$aktion = New-ScheduledTaskAction -Execute "wscript.exe" -Argument \'"{vbs}"\' '
        f'-WorkingDirectory "{ziel}"; '
        "$option = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries "
        "-DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero) "
        "-RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1); "
        f'Register-ScheduledTask -TaskName "{AUFGABE}" -Action $aktion '
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
         still: bool = False, rolle: str = "master", master_url: str = "",
         code: str = "") -> list[Schritt]:
    """Die Schritte der Installation, in der Reihenfolge, in der sie laufen."""
    quelle = quelle or sys.executable
    rechnername = rechnername or os.environ.get("COMPUTERNAME", "TABLET")
    daten_ordner = Path(daten or Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData")) / "AgriPilot")
    zielpfad = Path(ziel)
    exe = zielpfad / EXE_NAME
    vbs = zielpfad / "start_leise.vbs"
    vbs_fenster = zielpfad / "fenster.vbs"
    konfig = daten_ordner / "config.yaml"

    fenster_war_offen = []

    def stoppen() -> None:
        # Auch das Fenster muss zu: es hält die exe-Datei offen. Nach der
        # Aktualisierung geht es wieder auf, wenn es vorher offen war.
        if _lauf(["powershell", "-NoProfile", "-Command",
                  "if (Get-CimInstance Win32_Process -Filter \"Name='AgriPilot.exe'\" | "
                  "Where-Object { $_.CommandLine -like '*--fenster*' }) { exit 0 } else { exit 1 }"],
                 ok_codes=None) == 0:
            fenster_war_offen.append(True)
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
            # Der Master bekommt gleich einen Arbeitercode, ohne den von außen nichts geht.
            konfig.write_text(standard_konfiguration(
                rechnername, port, rolle, master_url,
                code or (neuer_code() if rolle == "master" else "")), encoding="utf-8")

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
        vbs_fenster.write_text(fenster_vbs(str(exe)), encoding="ascii")

    def aufgabe() -> None:
        _lauf(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command",
               aufgaben_skript(str(zielpfad), str(vbs))])

    def symbol() -> None:
        # Für alle Benutzer: Desktop und Startmenü. Nichts im Autostart - AgriPilot
        # läuft nur, wenn man es öffnet.
        for ort in (desktop_ordner(), startmenue_ordner()):
            if ort.is_dir():
                verknuepfung_anlegen(ort / "AgriPilot.lnk", wscript(), f'"{vbs_fenster}"',
                                     exe, zielpfad)
        if startmenue_ordner().is_dir():
            verknuepfung_anlegen(startmenue_ordner() / "AgriPilot deinstallieren.lnk", exe,
                                 "--deinstallieren", exe, zielpfad, "AgriPilot entfernen")
        for alt_eintrag in alte_autostarts():
            if alt_eintrag.exists():
                alt_eintrag.unlink()

    def eintragen() -> None:
        deinstallation_eintragen(exe, zielpfad)

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
        if not still or fenster_war_offen:
            # Über den Explorer, damit das Fenster als normaler Benutzer läuft und
            # nicht mit den Administratorrechten der Installation.
            subprocess.Popen(["explorer.exe", str(vbs_fenster)])

    def aufraeumen() -> None:
        _lauf(["schtasks", "/Delete", "/TN", UPDATE_AUFGABE, "/F"], ok_codes=None)

    return [
        Schritt("Laufende Version anhalten", stoppen),
        Schritt(f"Programm nach {ziel} kopieren", kopieren),
        Schritt(f"Konfiguration in {daten_ordner} (vorhandene bleibt)", konfiguration),
        Schritt(f"Firewall: Port {port} für das Android-Tablet freigeben", firewall),
        Schritt("Startdatei ohne Fenster anlegen", starter),
        Schritt("Aufgabe 'AgriPilot' eintragen (Neustart nach Absturz, kein Autostart)", aufgabe),
        Schritt("Symbol 'AgriPilot' auf Desktop und Startmenü legen", symbol),
        Schritt("Unter Apps & Features zum Deinstallieren eintragen", eintragen),
        Schritt("Programm starten und Anzeige im eigenen Fenster öffnen", starten),
        Schritt("Aufräumen", aufraeumen),
    ]


# ---------------------------------------------------------- Deinstallation

UNINSTALL_SCHLUESSEL = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\AgriPilot"


def deinstallations_eintrag(exe: Path, ziel: Path, version: str, groesse_kb: int) -> dict:
    """Was Windows unter "Apps & Features" anzeigt - und womit es deinstalliert."""
    return {
        "DisplayName": "AgriPilot",
        "DisplayVersion": version,
        "Publisher": "AgriPilot",
        "DisplayIcon": f"{exe},0",
        "InstallLocation": str(ziel),
        "UninstallString": f'"{exe}" --deinstallieren',
        "QuietUninstallString": f'"{exe}" --deinstallieren --still',
        "NoModify": 1,
        "NoRepair": 1,
        "EstimatedSize": groesse_kb,
    }


def deinstallation_eintragen(exe: Path, ziel: Path) -> None:
    import winreg
    from . import __version__
    groesse = int(exe.stat().st_size / 1024) if exe.exists() else 0
    with winreg.CreateKeyEx(winreg.HKEY_LOCAL_MACHINE, UNINSTALL_SCHLUESSEL, 0,
                            winreg.KEY_WRITE | winreg.KEY_WOW64_64KEY) as schluessel:
        for name, wert in deinstallations_eintrag(exe, ziel, __version__, groesse).items():
            art = winreg.REG_DWORD if isinstance(wert, int) else winreg.REG_SZ
            winreg.SetValueEx(schluessel, name, 0, art, wert)


def deinstallation_austragen() -> None:
    import winreg
    try:
        winreg.DeleteKeyEx(winreg.HKEY_LOCAL_MACHINE, UNINSTALL_SCHLUESSEL,
                           winreg.KEY_WOW64_64KEY)
    except FileNotFoundError:
        pass


def ordner_sicher_loeschbar(ordner: Path) -> bool:
    """Nur einen Programmordner von AgriPilot löschen - nie ein Laufwerk oder Windows."""
    ordner = Path(ordner)
    return (len(ordner.resolve().parts) >= 2 and "agripilot" in ordner.name.lower()
            and ordner.resolve() != Path(os.environ.get("SystemRoot", r"C:\Windows")).resolve())


def plan_deinstallation(ziel: str, daten: Optional[str] = None, port: int = 8080,
                        daten_loeschen: bool = False) -> list[Schritt]:
    """Die Schritte der Deinstallation. Daten bleiben, wenn nicht ausdrücklich anders gewählt."""
    zielpfad = Path(ziel)
    daten_ordner = Path(daten or Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData")) / "AgriPilot")
    fenster_daten = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "AgriPilot"

    def beenden() -> None:
        try:
            anfrage = urllib.request.Request(f"http://localhost:{port}/api/beenden", data=b"{}",
                                             method="POST",
                                             headers={"Content-Type": "application/json"})
            urllib.request.urlopen(anfrage, timeout=3).read()
            time.sleep(3.0)          # Arbeit sichern lassen
        except Exception:  # noqa: BLE001 - läuft nicht, umso besser
            pass
        _lauf(["schtasks", "/End", "/TN", AUFGABE], ok_codes=None)
        # Alles von AgriPilot außer dieser Deinstallation selbst.
        _lauf(["powershell", "-NoProfile", "-Command",
               "Get-CimInstance Win32_Process -Filter \"Name='AgriPilot.exe'\" | "
               "Where-Object { $_.CommandLine -notlike '*--deinstallieren*' } | "
               "ForEach-Object { Stop-Process -Id $_.ProcessId -Force "
               "-ErrorAction SilentlyContinue }"], ok_codes=None)
        time.sleep(2.0)

    def aufgaben() -> None:
        for name in (AUFGABE, UPDATE_AUFGABE):
            _lauf(["schtasks", "/Delete", "/TN", name, "/F"], ok_codes=None)

    def firewall() -> None:
        _lauf(["powershell", "-NoProfile", "-Command",
               "Get-NetFirewallRule -DisplayName 'AgriPilot *' -ErrorAction SilentlyContinue | "
               "Remove-NetFirewallRule"], ok_codes=None)

    def symbole() -> None:
        for eintrag in [desktop_ordner() / "AgriPilot.lnk",
                        startmenue_ordner() / "AgriPilot.lnk",
                        startmenue_ordner() / "AgriPilot deinstallieren.lnk",
                        *alte_autostarts()]:
            if eintrag.exists():
                eintrag.unlink()

    def austragen() -> None:
        deinstallation_austragen()

    def daten_weg() -> None:
        for ordner in (daten_ordner, fenster_daten):
            if ordner.exists() and ordner_sicher_loeschbar(ordner):
                shutil.rmtree(ordner, ignore_errors=True)

    def fensterdaten_weg() -> None:
        if fenster_daten.exists() and ordner_sicher_loeschbar(fenster_daten):
            shutil.rmtree(fenster_daten, ignore_errors=True)

    def programm_weg() -> None:
        if not zielpfad.exists():
            return
        if not ordner_sicher_loeschbar(zielpfad):
            raise RuntimeError(f"{zielpfad} sieht nicht nach dem AgriPilot-Ordner aus - "
                               "bitte von Hand löschen.")
        # Die laufende exe kann sich nicht selbst löschen: das übernimmt eine kurze
        # Befehlszeile, die wartet, bis diese Deinstallation zu Ende ist.
        subprocess.Popen(f'cmd /c ping -n 6 127.0.0.1 >nul & rmdir /s /q "{zielpfad}"',
                         creationflags=0x00000008 | 0x08000000,   # DETACHED, kein Fenster
                         close_fds=True)

    schritte = [
        Schritt("AgriPilot beenden (laufende Arbeit wird gesichert)", beenden),
        Schritt("Aufgaben 'AgriPilot' entfernen", aufgaben),
        Schritt("Firewall-Freigabe entfernen", firewall),
        Schritt("Symbole auf Desktop und im Startmenü entfernen", symbole),
        Schritt("Eintrag unter Apps & Features entfernen", austragen),
    ]
    if daten_loeschen:
        schritte.append(Schritt(f"Felder, Spuren, Aufträge und Einstellungen löschen "
                                f"({daten_ordner})", daten_weg))
    else:
        schritte.append(Schritt(f"Fensterdaten entfernen - Felder, Spuren, Aufträge und "
                                f"Einstellungen bleiben in {daten_ordner}", fensterdaten_weg))
    schritte.append(Schritt(f"Programmordner {zielpfad} entfernen", programm_weg))
    return schritte


def _ja(frage: str) -> bool:
    return input(f"{frage} [j/N]: ").strip().lower().startswith(("j", "y"))


def deinstallieren(ziel: str, still: bool = False, daten_loeschen: bool = False,
                   trocken: bool = False) -> int:
    if trocken:
        for nummer, schritt in enumerate(plan_deinstallation(ziel, daten_loeschen=daten_loeschen), 1):
            print(f"{nummer}. {schritt.text}")
        return 0
    if os.name != "nt":
        print("Die Deinstallation gibt es nur für Windows.")
        return 1
    if not ist_admin():
        _als_admin_neu_starten()
        return 0
    port = 8080
    try:
        from . import config as config_module
        port = config_module.load().server.port
    except Exception:  # noqa: BLE001
        pass
    if not still:
        print("AgriPilot deinstallieren\n")
        if not _ja("AgriPilot wirklich von diesem Rechner entfernen?"):
            print("Abgebrochen - nichts wurde geändert.")
            return 0
        daten_loeschen = _ja("Auch Felder, Spuren, Aufträge und Einstellungen löschen? "
                             "(Nein = sie bleiben für eine spätere Neuinstallation)")
    schritte = plan_deinstallation(ziel, port=port, daten_loeschen=daten_loeschen)
    for nummer, schritt in enumerate(schritte, 1):
        print(f"[{nummer}/{len(schritte)}] {schritt.text} ...", flush=True)
        try:
            schritt.aktion()
        except Exception as exc:  # noqa: BLE001
            print(f"\nFEHLER: {exc}")
            return 1
    print("\nAgriPilot ist entfernt.")
    return 0


def _als_admin_neu_starten() -> None:
    """Sich selbst mit Administratorrechten neu starten (Windows fragt einmal nach)."""
    import ctypes
    global _WEITERGEREICHT
    print("Administratorrechte werden angefordert ...")
    ctypes.windll.shell32.ShellExecuteW(  # type: ignore[attr-defined]
        None, "runas", sys.executable, " ".join(f'"{a}"' for a in sys.argv[1:]), None, 1)
    _WEITERGEREICHT = True


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

def _rolle_erfragen(daten_ordner: Path) -> tuple[str, str, str]:
    """Master oder Arbeiter? Nur beim ersten Einrichten und nur mit Tastatur davor."""
    if (daten_ordner / "config.yaml").exists() or not sys.stdin or not sys.stdin.isatty():
        return "master", "", ""
    print("Ist dieses Gerät der MASTER oder ein ARBEITER?")
    print("  M = Master: der Rechner im Büro. Hier werden Felder, Maschinen und Umrisse erfasst.")
    print("  A = Arbeiter: das Tablet im Traktor. Es bekommt vom Master, was der erfasst hat.")
    wahl = input("[M/A] (Enter = M): ").strip().lower()
    if not wahl.startswith("a"):
        return "master", "", ""
    url = input("Adresse des Masters, z. B. http://192.168.1.20:8080\n"
                "(steht beim Master unter Menü → System; Enter = später eintragen): ").strip()
    if url and not url.startswith("http"):
        url = "http://" + url
    code = input("Arbeitercode vom Master (Menü → System; Enter = später eintragen): ").strip()
    return "client", url, code


def installieren(ziel: str, port: int, trocken: bool, still: bool = False,
                 rolle: Optional[str] = None, master_url: str = "", code: str = "") -> int:
    schritte = plan(ziel, port, still=still, rolle=rolle or "master", master_url=master_url,
                    code=code)
    if trocken:
        for nummer, schritt in enumerate(schritte, 1):
            print(f"{nummer}. {schritt.text}")
        return 0
    if os.name != "nt":
        print("Die Installation gibt es nur für Windows.")
        return 1
    if not ist_admin():
        # Die exe verlangt Adminrechte nicht schon beim Start (sonst fragte Windows
        # auch bei jedem Öffnen des Anzeigefensters) - nur die Einrichtung holt sie sich.
        _als_admin_neu_starten()
        return 0
    if rolle is None and not still:
        daten_ordner = Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData")) / "AgriPilot"
        rolle, master_url, code = _rolle_erfragen(daten_ordner)
        schritte = plan(ziel, port, still=still, rolle=rolle, master_url=master_url, code=code)
    print("AgriPilot wird eingerichtet.\n")
    for nummer, schritt in enumerate(schritte, 1):
        print(f"[{nummer}/{len(schritte)}] {schritt.text} ...", flush=True)
        try:
            schritt.aktion()
        except Exception as exc:  # noqa: BLE001
            print(f"\nFEHLER: {exc}")
            return 1
    print("\nFertig. AgriPilot läuft. Fenster schließen beendet es, das Symbol startet es wieder.")
    print("Kein Start mit Windows. Entfernen: Apps & Features oder Startmenü → "
          "'AgriPilot deinstallieren'.")
    print("Anzeige: Symbol 'AgriPilot' auf dem Desktop oder im Startmenü "
          f"(im Netz auch http://<dieser Rechner>:{port})")
    return 0


_WEITERGEREICHT = False   # die Einrichtung läuft in einem neuen Fenster mit Adminrechten


def fenster(port: int) -> int:
    """Die Anzeige im eigenen Fenster; der Port kommt aus der Konfiguration."""
    from . import config as config_module
    from . import fenster as fenster_module
    try:
        port = config_module.load().server.port
    except Exception:  # noqa: BLE001 - ohne lesbare Konfiguration der übliche Port
        pass
    return fenster_module.oeffnen(port)


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
    parser.add_argument("--code", default="", help="Arbeitercode vom Master (für --arbeiter)")
    parser.add_argument("--still", action="store_true",
                        help="ohne Rückfragen und ohne Browser (Aktualisierung über das Netz)")
    parser.add_argument("--fenster", action="store_true",
                        help="AgriPilot im eigenen Fenster öffnen (so startet es das Desktop-Symbol)")
    parser.add_argument("--deinstallieren", action="store_true",
                        help="AgriPilot von diesem Rechner entfernen (fragt, ob die Daten bleiben)")
    parser.add_argument("--daten-loeschen", action="store_true",
                        help="beim Deinstallieren mit --still auch Felder, Spuren und Einstellungen löschen")
    parser.add_argument("--version", action="store_true")
    args = parser.parse_args(argv)
    if args.version:
        from . import __version__
        print(__version__)
        return 0
    if args.dienst:
        return dienst()
    if args.fenster:
        return fenster(args.port)
    if args.deinstallieren:
        # Aus dem installierten Programm aufgerufen (Apps & Features): dessen Ordner.
        ziel = args.ziel
        if getattr(sys, "frozen", False) and Path(sys.executable).name.lower() == EXE_NAME.lower():
            ziel = str(Path(sys.executable).parent)
        code = deinstallieren(ziel, args.still, args.daten_loeschen, args.trocken)
        if (getattr(sys, "frozen", False) and not args.trocken and not args.still
                and not _WEITERGEREICHT):
            input("\nEnter zum Schließen ... ")
        return code
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
    code = installieren(args.ziel, args.port, args.trocken, args.still, rolle, master_url, args.code)
    if (getattr(sys, "frozen", False) and not args.trocken and not args.still
            and not _WEITERGEREICHT):
        input("\nEnter zum Schließen ... ")
    return code
