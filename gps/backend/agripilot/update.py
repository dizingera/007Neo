"""Aktualisierung im Betrieb: Paket einspielen, aus dem Repo holen, neu starten.

Der Weg vom Schreibtisch auf das Tablet in der Kabine soll kurz sein: auf dem
PC ``scripts/make_update.py`` aufrufen, die Zip-Datei aufs Tablet bringen
(USB-Stick, WLAN, Messenger), im Menü unter System einspielen, Neustart. Kein
Installationsskript, kein Terminal, keine Kommandozeile in der Kabine.

Zwei Wege, weil der Traktor mal Internet hat und mal nicht:

* **Paket** (``.zip`` mit ``update.json``): geht immer, auch im Feld ohne Netz.
* **Repo** (``git pull``): geht, wenn das Programm aus einem Git-Klon läuft und
  eine Verbindung besteht - am Hof im WLAN.

Was beim Einspielen passiert, ist bewusst schlicht: die Ordner ``backend``,
``frontend``, ``scripts`` und ``docs`` werden ausgetauscht, die alten wandern
in eine Sicherung, alles andere bleibt unangetastet - Konfiguration, Datenbank,
Python-Umgebung. Eine Sicherung lässt sich mit einem Druck zurückholen. Jede
Datei im Paket trägt einen Prüfwert; stimmt einer nicht, wird nichts kopiert.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

# Was ein Paket enthält, und was beim Einspielen ausgetauscht wird.
ORDNER = ("backend", "frontend", "scripts", "docs")
# Was nie ins Paket kommt - Umgebungen, Daten, Zwischenstände.
AUSGESCHLOSSEN = {".venv", "venv", "__pycache__", ".simulator", ".pytest_cache",
                  ".update", "node_modules", ".git"}
AUSGESCHLOSSENE_ENDUNGEN = {".pyc", ".pyo", ".db", ".db-wal", ".db-shm", ".log"}
MANIFEST = "update.json"
MAX_PAKET_BYTES = 60_000_000
GESTARTET = time.strftime("%Y-%m-%d %H:%M:%S")


class UpdateFehler(ValueError):
    """Etwas am Paket oder am Ablauf stimmt nicht - mit Grund für den Fahrer."""


# ---------------------------------------------------------------- Wurzel


def als_programm() -> bool:
    """Läuft das als einzelne .exe (PyInstaller)? Dann gibt es keinen Ordner mit
    Dateien zum Austauschen - aktualisiert wird mit einer neuen Setup-Datei."""
    return bool(getattr(sys, "frozen", False))


_NUR_SETUP = ("Das ist die Programmversion (AgriPilot.exe). Zum Aktualisieren die neue "
              "AgriPilot-Setup.exe ausführen - Einstellungen, Felder und Aufträge bleiben.")


def wurzel() -> Path:
    """Der Installationsordner: der, in dem ``backend``, ``frontend`` und
    ``scripts`` nebeneinander liegen (``C:\\AgriPilot``, ``/opt/agripilot``,
    im Repo ``gps/``)."""
    return Path(__file__).resolve().parents[2]


def _sha256(pfad: Path) -> str:
    h = hashlib.sha256()
    with open(pfad, "rb") as f:
        for block in iter(lambda: f.read(1 << 16), b""):
            h.update(block)
    return h.hexdigest()


def _git(*args: str, cwd: Path) -> Optional[str]:
    try:
        ergebnis = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True,
                                  text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if ergebnis.returncode != 0:
        return None
    return ergebnis.stdout.strip()


def commit_der_quelle(pfad: Path) -> str:
    """Kurzer Commit des Klons, aus dem ``pfad`` stammt - leer, wenn kein Klon."""
    return _git("rev-parse", "--short", "HEAD", cwd=pfad) or ""


# ----------------------------------------------------------- Paket bauen


def paket_bauen(quelle: Path, version: str, ziel: Optional[Path] = None,
                commit: str = "") -> Path:
    """Alle Programmdateien unter ``quelle`` in eine Zip-Datei mit Manifest.

    ``quelle`` ist der ``gps``-Ordner. Ausgeschlossen sind Umgebungen und Daten
    (siehe AUSGESCHLOSSEN). Das Manifest trägt Version, Commit, Zeit und je Datei
    den SHA-256 - damit das Tablet vor dem Kopieren prüfen kann, ob das Paket
    vollständig angekommen ist.
    """
    quelle = quelle.resolve()
    commit = commit or commit_der_quelle(quelle) or "ohne-git"
    stempel = time.strftime("%Y%m%d-%H%M")
    ziel = ziel or quelle / f"agripilot-update-{version}-{commit}.zip"
    dateien: dict[str, str] = {}
    with zipfile.ZipFile(ziel, "w", zipfile.ZIP_DEFLATED) as zf:
        for ordner in ORDNER:
            basis = quelle / ordner
            if not basis.is_dir():
                continue
            for pfad in sorted(basis.rglob("*")):
                rel = pfad.relative_to(quelle)
                if any(teil in AUSGESCHLOSSEN for teil in rel.parts):
                    continue
                if not pfad.is_file() or pfad.suffix.lower() in AUSGESCHLOSSENE_ENDUNGEN:
                    continue
                if pfad.name.startswith("agripilot-update-"):
                    continue
                schluessel = rel.as_posix()
                dateien[schluessel] = _sha256(pfad)
                zf.write(pfad, schluessel)
        manifest = {"version": version, "commit": commit, "erstellt": stempel,
                    "dateien": dateien, "ordner": list(ORDNER)}
        zf.writestr(MANIFEST, json.dumps(manifest, indent=1, ensure_ascii=False))
    return ziel


# --------------------------------------------------------- Paket prüfen


@dataclass
class Paket:
    manifest: dict
    zf: zipfile.ZipFile
    warnungen: list[str] = field(default_factory=list)

    @property
    def version(self) -> str:
        return str(self.manifest.get("version", "?"))

    @property
    def commit(self) -> str:
        return str(self.manifest.get("commit", ""))


def paket_pruefen(daten: bytes) -> Paket:
    """Zip öffnen, Manifest lesen, jede Datei gegen ihren Prüfwert halten.

    Abgelehnt wird alles, was aus dem Installationsordner hinausführt
    (``..``, absolute Pfade, fremde Ordner) - ein Paket darf nur die vier
    Programmordner berühren.
    """
    if len(daten) > MAX_PAKET_BYTES:
        raise UpdateFehler(f"Das Paket ist größer als {MAX_PAKET_BYTES // 1_000_000} MB - "
                           "das ist kein AgriPilot-Update")
    try:
        zf = zipfile.ZipFile(io.BytesIO(daten))
    except zipfile.BadZipFile as exc:
        raise UpdateFehler("Das ist keine Zip-Datei") from exc
    namen = set(zf.namelist())
    if MANIFEST not in namen:
        raise UpdateFehler("Im Paket fehlt update.json - mit scripts/make_update.py erzeugen")
    try:
        manifest = json.loads(zf.read(MANIFEST).decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise UpdateFehler(f"update.json ist nicht lesbar: {exc}") from exc
    if not isinstance(manifest, dict):
        raise UpdateFehler("update.json muss ein Objekt mit Dateiliste sein")
    for key in ("version", "commit", "erstellt"):
        if key in manifest and not isinstance(manifest[key], str):
            raise UpdateFehler(f"update.json: {key} muss Text sein")
    if "ordner" in manifest and (not isinstance(manifest["ordner"], list)
            or any(not isinstance(o, str) or o not in ORDNER for o in manifest["ordner"])):
        raise UpdateFehler("update.json: ordner muss eine Liste der Programmordner sein")
    dateien = manifest.get("dateien")
    if not isinstance(dateien, dict) or not dateien:
        raise UpdateFehler("update.json nennt keine Dateien")
    for name, pruefsumme in dateien.items():
        if isinstance(name, str) and name:
            teile = Path(name).parts
            if name.startswith("/") or ".." in teile or ":" in name or not teile or teile[0] not in ORDNER:
                raise UpdateFehler(f"Unzulässiger Pfad im Paket: {name}")
        if (not isinstance(name, str) or not name or name.endswith("/")
                or not isinstance(pruefsumme, str) or len(pruefsumme) != 64
                or any(c not in "0123456789abcdef" for c in pruefsumme)):
            raise UpdateFehler("update.json enthält einen ungültigen Dateinamen oder SHA-256-Prüfwert")
    for name in namen:
        if name == MANIFEST or name.endswith("/"):
            continue
        teile = Path(name).parts
        if (name.startswith("/") or ".." in teile or ":" in name
                or teile[0] not in ORDNER):
            raise UpdateFehler(f"Unzulässiger Pfad im Paket: {name}")
        if name not in dateien:
            raise UpdateFehler(f"Datei ohne Prüfwert im Paket: {name}")
        if hashlib.sha256(zf.read(name)).hexdigest() != dateien[name]:
            raise UpdateFehler(f"Prüfwert stimmt nicht: {name} - Paket beschädigt "
                               "oder unvollständig übertragen")
    fehlend = [n for n in dateien if n not in namen]
    if fehlend:
        raise UpdateFehler(f"Im Manifest genannt, im Paket nicht enthalten: {fehlend[0]}")
    paket = Paket(manifest, zf)
    if not (zf.namelist() and any(n.startswith("backend/agripilot/") for n in namen)):
        paket.warnungen.append("Das Paket enthält kein backend/agripilot")
    return paket


# ------------------------------------------------------------ einspielen


def _requirements_installieren(wurzel_pfad: Path) -> str:
    """Neue Pakete nachziehen, wenn requirements.txt dabei ist. Ohne Netz
    schlägt das fehl - dann sagt es das, und das Programm läuft mit dem alten
    Stand der Bibliotheken weiter (die Programmdateien sind trotzdem neu)."""
    req = wurzel_pfad / "backend" / "requirements.txt"
    if not req.exists():
        return "keine requirements.txt"
    try:
        ergebnis = subprocess.run(
            [sys.executable, "-m", "pip", "install", "--quiet", "-r", str(req)],
            capture_output=True, text=True, timeout=600)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"pip nicht ausgeführt: {exc}"
    if ergebnis.returncode != 0:
        return "pip fehlgeschlagen (kein Netz?): " + (ergebnis.stderr or ergebnis.stdout)[-400:]
    return "Bibliotheken auf Stand"


def _nur_ohne_programm() -> None:
    if als_programm():
        raise UpdateFehler(_NUR_SETUP)


def einspielen(daten: bytes, wurzel_pfad: Optional[Path] = None,
               pip: bool = True) -> dict[str, Any]:
    """Ein geprüftes Paket in den Installationsordner bringen.

    Ablauf: entpacken nach ``.update/neu``, die vier Ordner nach
    ``.update/sicherung-<zeit>`` verschieben, die neuen an ihre Stelle
    verschieben. Verschieben statt Kopieren: das geht auf derselben Platte in
    Millisekunden und lässt sich mit denselben Schritten zurücknehmen. Wenn
    etwas dazwischen scheitert, wird die Sicherung sofort wieder eingesetzt.
    """
    _nur_ohne_programm()
    wurzel_pfad = (wurzel_pfad or wurzel()).resolve()
    paket = paket_pruefen(daten)
    arbeit = wurzel_pfad / ".update"
    neu = arbeit / "neu"
    if neu.exists():
        shutil.rmtree(neu)
    neu.mkdir(parents=True)
    paket.zf.extractall(neu)

    sicherung = _neue_sicherung(arbeit)
    protokoll: list[tuple[Path, Path]] = []    # (von, nach) - zum Zurückdrehen
    stand = {"version": paket.version, "commit": paket.commit,
             "erstellt": paket.manifest.get("erstellt", ""),
             "eingespielt": time.strftime("%Y-%m-%d %H:%M"),
             "dateien": len(paket.manifest["dateien"]), "sicherung": sicherung.name}
    status_neu = None
    try:
        status_neu = _status_vorbereiten(arbeit, stand)
        for ordner in ORDNER:
            quelle = neu / ordner
            if quelle.is_dir():           # ein Paket ohne docs lässt docs, wie sie sind
                _inhalt_tauschen(wurzel_pfad / ordner, quelle, sicherung / ordner, protokoll)
        _status_tauschen(wurzel_pfad / MANIFEST, status_neu, sicherung, protokoll)
    except Exception as exc:  # noqa: BLE001 - alles zurück, dann erst melden
        rueckfehler = _tausch_zurueck(protokoll)
        if rueckfehler:
            raise UpdateFehler(f"Einspielen und Rückabwicklung fehlgeschlagen: {exc}; {rueckfehler}. Sicherung: {sicherung}") from exc
        raise UpdateFehler(f"Einspielen abgebrochen, alter Stand wiederhergestellt: {exc}") from exc
    finally:
        shutil.rmtree(neu, ignore_errors=True)
        if status_neu is not None:
            shutil.rmtree(status_neu.parent, ignore_errors=True)

    _sicherungen_ausduennen(arbeit, behalten=3)
    stand["requirements"] = _requirements_installieren(wurzel_pfad) if pip else "übersprungen"
    stand["warnungen"] = paket.warnungen
    return stand


def _status_vorbereiten(arbeit: Path, stand: dict) -> Path:
    """Status auf demselben Laufwerk vollständig schreiben, bevor Dateien wechseln."""
    import tempfile
    ordner = Path(tempfile.mkdtemp(prefix="status-", dir=arbeit))
    try:
        pfad = ordner / MANIFEST
        pfad.write_text(json.dumps(stand, indent=1, ensure_ascii=False), encoding="utf-8")
        return pfad
    except Exception:
        shutil.rmtree(ordner, ignore_errors=True)
        raise


def _status_tauschen(ziel: Path, neu: Path, sicherung: Path,
                     protokoll: list[tuple[Path, Path]]) -> None:
    """Originalstatus und neuen Status in dieselbe Rückabwicklung aufnehmen."""
    if ziel.exists():
        alt = sicherung / MANIFEST
        ziel.replace(alt)
        protokoll.append((ziel, alt))
    neu.replace(ziel)
    protokoll.append((neu, ziel))


def _neue_sicherung(arbeit: Path) -> Path:
    """Ein frischer Sicherungsordner mit Zeitstempel - zweimal in derselben
    Sekunde (einspielen, gleich wieder zurück) bekommt einen Zähler."""
    stempel = time.strftime("%Y%m%d-%H%M%S")
    pfad = arbeit / f"sicherung-{stempel}"
    zaehler = 1
    while pfad.exists():
        zaehler += 1
        pfad = arbeit / f"sicherung-{stempel}-{zaehler}"
    pfad.mkdir(parents=True)
    return pfad


def _inhalt_tauschen(alt: Path, quelle: Path, ablage: Path,
                     protokoll: list[tuple[Path, Path]]) -> None:
    """Den Inhalt eines Programmordners austauschen - Eintrag für Eintrag.

    Nicht der Ordner selbst wird ersetzt, sondern sein Inhalt: ``backend`` ist
    das Arbeitsverzeichnis des laufenden Programms und enthält in der
    Entwicklung die Python-Umgebung mit dem laufenden Interpreter - beides
    lässt sich unter Windows nicht verschieben. Die Einträge darunter
    (``agripilot``, ``requirements.txt``, ``tests``) schon. Umgebungen und
    Daten (AUSGESCHLOSSEN) bleiben, wo sie sind.
    """
    ablage.mkdir(parents=True, exist_ok=True)
    alt.mkdir(parents=True, exist_ok=True)
    for kind in list(alt.iterdir()):
        if kind.name in AUSGESCHLOSSEN:
            continue
        ziel = ablage / kind.name
        shutil.move(str(kind), str(ziel))
        protokoll.append((kind, ziel))
    for kind in list(quelle.iterdir()):
        if kind.name in AUSGESCHLOSSEN:
            continue
        ziel = alt / kind.name
        shutil.move(str(kind), str(ziel))
        protokoll.append((kind, ziel))   # wie oben: tatsächliche Richtung (von, nach)


def _tausch_zurueck(protokoll: list[tuple[Path, Path]]) -> str:
    """Jeden erfolgten Verschiebeschritt rückwärts ausführen, Fehler offen melden."""
    fehler = []
    for von, nach in reversed(protokoll):
        try:
            if nach.exists():
                if von.exists():
                    raise OSError(f"Rückziel bereits vorhanden: {von}")
                von.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(nach), str(von))
        except OSError as exc:
            fehler.append(str(exc))
    return "; ".join(fehler)


def _sicherungen_ausduennen(arbeit: Path, behalten: int) -> None:
    alle = sorted(p for p in arbeit.glob("sicherung-*") if p.is_dir())
    for alt in alle[:-behalten]:
        shutil.rmtree(alt, ignore_errors=True)


def sicherungen(wurzel_pfad: Optional[Path] = None) -> list[str]:
    arbeit = (wurzel_pfad or wurzel()) / ".update"
    return sorted((p.name for p in arbeit.glob("sicherung-*") if p.is_dir()), reverse=True)


def zurueckholen(name: str = "", wurzel_pfad: Optional[Path] = None) -> dict[str, Any]:
    _nur_ohne_programm()
    """Die jüngste (oder eine benannte) Sicherung wieder einsetzen.

    Der jetzige Stand wandert dabei selbst in eine Sicherung - zurück ist auch
    nur ein Tausch, und der lässt sich wieder umkehren.
    """
    wurzel_pfad = (wurzel_pfad or wurzel()).resolve()
    arbeit = wurzel_pfad / ".update"
    vorhanden = sicherungen(wurzel_pfad)
    if not vorhanden:
        raise UpdateFehler("Keine Sicherung vorhanden")
    if name and name not in vorhanden:
        raise UpdateFehler(f"Sicherung {name} gibt es nicht")
    if ".." in name or "/" in name or "\\" in name:
        raise UpdateFehler("Unzulässiger Name")
    quelle = arbeit / (name or vorhanden[0])
    ablage = _neue_sicherung(arbeit)
    protokoll: list[tuple[Path, Path]] = []
    stand = {"version": "zurückgeholt", "commit": "", "eingespielt": time.strftime("%Y-%m-%d %H:%M"),
             "sicherung": ablage.name, "zurueck_aus": quelle.name}
    status_neu = None
    try:
        status_neu = _status_vorbereiten(arbeit, stand)
        for ordner in ORDNER:
            if (quelle / ordner).is_dir():
                _inhalt_tauschen(wurzel_pfad / ordner, quelle / ordner, ablage / ordner, protokoll)
        _status_tauschen(wurzel_pfad / MANIFEST, status_neu, ablage, protokoll)
    except Exception as exc:  # noqa: BLE001 - auch beim Zurückholen keinen halben Stand lassen
        rueckfehler = _tausch_zurueck(protokoll)
        if rueckfehler:
            raise UpdateFehler(f"Zurückholen und Rückabwicklung fehlgeschlagen: {exc}; {rueckfehler}. Sicherung: {ablage}") from exc
        raise UpdateFehler(f"Zurückholen abgebrochen, bisheriger Stand wiederhergestellt: {exc}") from exc
    finally:
        if status_neu is not None:
            shutil.rmtree(status_neu.parent, ignore_errors=True)
    # Erst nach erfolgreichem Datei- UND Statustausch ist die Quellsicherung verbraucht.
    shutil.rmtree(quelle, ignore_errors=True)
    return stand


# ------------------------------------------------------------------ Repo


def repo_verfuegbar(wurzel_pfad: Optional[Path] = None) -> bool:
    wurzel_pfad = wurzel_pfad or wurzel()
    return bool(_git("rev-parse", "--show-toplevel", cwd=wurzel_pfad))


def aus_repo_holen(wurzel_pfad: Optional[Path] = None) -> dict[str, Any]:
    _nur_ohne_programm()
    """``git pull --ff-only`` - nur vorspulen, nie zusammenführen. Eine
    örtliche Änderung, die im Weg steht, bleibt stehen und wird gemeldet."""
    wurzel_pfad = wurzel_pfad or wurzel()
    if not repo_verfuegbar(wurzel_pfad):
        raise UpdateFehler("Das Programm läuft nicht aus einem Git-Klon - Paket einspielen")
    vorher = commit_der_quelle(wurzel_pfad)
    try:
        ergebnis = subprocess.run(["git", "pull", "--ff-only"], cwd=str(wurzel_pfad),
                                  capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise UpdateFehler(f"git pull nicht ausgeführt: {exc}") from exc
    if ergebnis.returncode != 0:
        raise UpdateFehler("git pull fehlgeschlagen (kein Netz, oder örtliche Änderungen im Weg): "
                           + (ergebnis.stderr or ergebnis.stdout)[-400:].strip())
    nachher = commit_der_quelle(wurzel_pfad)
    return {"vorher": vorher, "nachher": nachher, "geaendert": vorher != nachher,
            "ausgabe": ergebnis.stdout.strip()[-600:],
            "requirements": _requirements_installieren(wurzel_pfad) if vorher != nachher else "unverändert"}


# ------------------------------------------------------------------ Stand


def stand(version: str, wurzel_pfad: Optional[Path] = None) -> dict[str, Any]:
    wurzel_pfad = wurzel_pfad or wurzel()
    datei = wurzel_pfad / MANIFEST
    eingespielt: dict = {}
    if datei.exists():
        try:
            eingespielt = json.loads(datei.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            eingespielt = {}
    git = repo_verfuegbar(wurzel_pfad)
    return {
        "version": version,
        "commit": commit_der_quelle(wurzel_pfad) if git else eingespielt.get("commit", ""),
        "eingespielt": eingespielt.get("eingespielt", ""),
        "paket_version": eingespielt.get("version", ""),
        "wurzel": str(wurzel_pfad),
        "git": git,
        "sicherungen": sicherungen(wurzel_pfad),
        "neustart": neustart_art(),
        "pid": os.getpid(),      # ändert sich beim Neustart - so sieht man, dass er war
        "gestartet": GESTARTET,
    }


# -------------------------------------------------------------- Neustart


def neustart_art() -> str:
    """Wie dieses Programm neu gestartet wird - systemd startet von selbst,
    sonst startet es sich als eigenen Nachfolger."""
    if os.environ.get("INVOCATION_ID") or os.environ.get("AGRIPILOT_SERVICE") == "systemd":
        return "systemd"
    return "nachfolger"


def kommandozeile() -> list[str]:
    """Womit dieses Programm gestartet wurde - so, dass es sich wieder starten lässt.

    ``python -m agripilot.server`` trägt in sys.argv[0] den Dateipfad des
    Moduls; als Skript gestartet fielen die relativen Importe um. Also den
    Modulnamen aus __main__.__spec__ nehmen, wenn es einen gibt.
    """
    if als_programm():
        return [sys.executable, *sys.argv[1:]]
    haupt = sys.modules.get("__main__")
    spec = getattr(haupt, "__spec__", None)
    if spec is not None and spec.name:
        name = spec.name[:-9] if spec.name.endswith(".__main__") else spec.name
        return [sys.executable, "-m", name, *sys.argv[1:]]
    return [sys.executable, *sys.argv]


def neustarten(verzoegerung_s: float = 1.0) -> None:
    """Das Programm beenden - und dafür sorgen, dass es wiederkommt.

    Unter systemd genügt das Beenden (``Restart=always``). Sonst wird vorher
    ein Nachfolger mit derselben Kommandozeile gestartet, der wartet, bis der
    Port frei ist (siehe ``auf_freien_port_warten`` in server.main). Die
    Verzögerung lässt die Antwort auf die Anfrage noch hinausgehen.
    """
    import threading

    def _gehen() -> None:
        time.sleep(verzoegerung_s)
        if neustart_art() != "systemd":
            umgebung = dict(os.environ, AGRIPILOT_WARTE_AUF_PORT="1")
            if als_programm():
                # Sonst nutzt der Nachfolger das entpackte Bündel des Vorgängers,
                # und das verschwindet, sobald der sich beendet.
                umgebung["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
            befehl = kommandozeile()
            flags = 0
            if os.name == "nt":
                flags = (getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                         | getattr(subprocess, "DETACHED_PROCESS", 0))
            subprocess.Popen(befehl, cwd=os.getcwd(), env=umgebung, close_fds=True,
                             creationflags=flags,
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL,
                             start_new_session=(os.name != "nt"))
        os._exit(0)

    threading.Thread(target=_gehen, daemon=True).start()


def auf_freien_port_warten(host: str, port: int, hoechstens_s: float = 30.0) -> None:
    """Als Nachfolger gestartet: warten, bis der Vorgänger den Port freigegeben hat."""
    import socket
    ende = time.monotonic() + hoechstens_s
    ziel = "127.0.0.1" if host in ("0.0.0.0", "", "::") else host
    while time.monotonic() < ende:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.5)
            try:
                s.connect((ziel, port))
            except OSError:
                return      # niemand nimmt ab: frei
        time.sleep(0.5)
