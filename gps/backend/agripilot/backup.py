"""Sicherung von Datenbank und Konfiguration.

Felder, Spuren, Fahrten und die eingemessene Anlage (Antennenmaße, Lenkmotor)
sind Wochen Arbeit. Ein defekter Datenträger im Tablet darf sie nicht mitnehmen.

Eine Sicherung ist ein einziges Zip: ``agripilot.db`` und ``config.yaml``. Die
Datenbank wird über die SQLite-Sicherungsschnittstelle kopiert und nicht als
Datei - sie läuft im WAL-Modus, und eine mitten im Schreiben kopierte Datei ist
eine kaputte Datenbank.

Zurückspielen geht ohne das Programm: Zip öffnen, beide Dateien an ihre Stelle
legen (Pfade stehen in Menü → System).
"""

from __future__ import annotations

import sqlite3
import tempfile
import time
import zipfile
from pathlib import Path
from typing import Optional

PREFIX = "agripilot-"
SUFFIX = ".zip"


def sichern(db_path: Path, config_path: Optional[Path], ziele: list[Path],
            behalten: int = 14, jetzt: Optional[float] = None) -> dict:
    """Eine Sicherung erzeugen und in jedes Ziel legen.

    Ein Ziel, das nicht erreichbar ist (USB-Stick gezogen), bricht die anderen
    nicht ab - es landet mit seinem Fehler im Ergebnis, damit die Oberfläche es
    zeigen kann.
    """
    zeit = jetzt if jetzt is not None else time.time()
    name = PREFIX + time.strftime("%Y%m%d-%H%M%S", time.localtime(zeit)) + SUFFIX
    ergebnis: dict = {"name": name, "ziele": [], "zeit": zeit}

    with tempfile.TemporaryDirectory() as arbeit:
        zip_pfad = Path(arbeit) / name
        kopie = Path(arbeit) / "agripilot.db"
        _datenbank_kopieren(Path(db_path), kopie)
        with zipfile.ZipFile(zip_pfad, "w", zipfile.ZIP_DEFLATED) as archiv:
            archiv.write(kopie, "agripilot.db")
            if config_path is not None and Path(config_path).is_file():
                archiv.write(config_path, "config.yaml")
        groesse = zip_pfad.stat().st_size
        ergebnis["bytes"] = groesse
        for ziel in ziele:
            eintrag = {"ordner": str(ziel), "ok": False, "fehler": ""}
            try:
                ziel = Path(ziel)
                # Ohne parents: ein nicht eingehängter USB-Stick (/media/usb) soll
                # nicht still als Ordner auf dem Systemlaufwerk landen.
                ziel.mkdir(exist_ok=True)
                # Erst unter Nebennamen, dann umbenennen: ein gezogener Stick
                # hinterlässt sonst ein halbes Zip, das als neueste Sicherung
                # zählt und die guten älteren verdrängt.
                halb = ziel / (name + ".teil")
                halb.write_bytes(zip_pfad.read_bytes())
                halb.replace(ziel / name)
                aufraeumen(ziel, behalten)
                eintrag["ok"] = True
            except OSError as exc:
                eintrag["fehler"] = str(exc)
            ergebnis["ziele"].append(eintrag)
    ergebnis["ok"] = any(z["ok"] for z in ergebnis["ziele"])
    return ergebnis


def _datenbank_kopieren(quelle: Path, ziel: Path) -> None:
    verbindung = sqlite3.connect(quelle)
    try:
        sicherung = sqlite3.connect(ziel)
        try:
            verbindung.backup(sicherung)
        finally:
            sicherung.close()
    finally:
        verbindung.close()


def liste(ordner: Path) -> list[Path]:
    """Sicherungen in einem Ordner, neueste zuerst."""
    ordner = Path(ordner)
    if not ordner.is_dir():
        return []
    return sorted(ordner.glob(f"{PREFIX}*{SUFFIX}"), reverse=True)


def aufraeumen(ordner: Path, behalten: int) -> int:
    """Die ältesten löschen, ``behalten`` Stück bleiben. Gibt die Zahl gelöschter zurück."""
    weg = liste(ordner)[max(1, behalten):]
    for datei in weg:
        try:
            datei.unlink()
        except OSError:
            pass
    return len(weg)


def letzte(ordner: Path) -> Optional[dict]:
    dateien = liste(ordner)
    if not dateien:
        return None
    neueste = dateien[0]
    stat = neueste.stat()
    return {"name": neueste.name, "zeit": stat.st_mtime, "bytes": stat.st_size}


def faellig(ordner: Path, abstand_s: float = 24 * 3600.0,
            jetzt: Optional[float] = None) -> bool:
    """Ist die letzte Sicherung älter als ``abstand_s`` (oder gibt es keine)?"""
    zuletzt = letzte(ordner)
    if zuletzt is None:
        return True
    return (jetzt if jetzt is not None else time.time()) - zuletzt["zeit"] >= abstand_s


def zurueckspielen(zip_pfad: Path, db_ziel: Path, config_ziel: Optional[Path]) -> None:
    """Eine Sicherung einspielen. Nur bei gestopptem Programm aufrufen.

    Der Stand davor bleibt als ``.vorher`` neben den Dateien liegen.
    """
    with zipfile.ZipFile(zip_pfad) as archiv:
        namen = archiv.namelist()
        if "agripilot.db" not in namen:
            raise ValueError("Keine AgriPilot-Sicherung: agripilot.db fehlt")
        for ziel in (db_ziel, Path(str(db_ziel) + "-wal"), Path(str(db_ziel) + "-shm")):
            if ziel.exists():
                ziel.replace(Path(str(ziel) + ".vorher"))
        db_ziel.write_bytes(archiv.read("agripilot.db"))
        if config_ziel is not None and "config.yaml" in namen:
            if Path(config_ziel).exists():
                Path(config_ziel).replace(Path(str(config_ziel) + ".vorher"))
            Path(config_ziel).write_bytes(archiv.read("config.yaml"))
