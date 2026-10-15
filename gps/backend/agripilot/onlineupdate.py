"""Aktualisierung über das Netz: neue Version suchen, laden, prüfen.

Quelle sind die GitHub-Releases des Repos (öffentlich, ohne Anmeldung lesbar).
Weil im Repo mehrere Projekte liegen, tragen die Releases dieses Programms das
Etikett ``agripilot-v1.2.0``; andere werden ignoriert. Pro Release hängt die
Datei ``AgriPilot-Setup.exe`` daran - dieselbe, die man auch von Hand
doppelklickt.

Was hier **nicht** geschieht: nichts wird von selbst eingespielt. Gesucht wird
von allein, geladen und eingespielt nur auf Druck des Fahrers, und nur im
Stand und bei ausgeschalteter Lenkung (das prüft der Server). Eine Maschine,
die sich mitten in der Arbeit selbst umbaut, ist keine Hilfe.

Geladen wird nur, was zur veröffentlichten Prüfsumme (SHA-256) passt und wie
ein Windows-Programm aussieht. Die Prüfsumme kommt von derselben Stelle wie
die Datei - sie schützt vor einem abgebrochenen oder verstümmelten Download,
nicht vor einem manipulierten Release; das hängt am Schutz des Repos.
"""

from __future__ import annotations

import hashlib
import json
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable, Optional

STANDARD_REPO = "dizingera/007Neo"
STANDARD_API = "https://api.github.com"
ETIKETT_VORSATZ = "agripilot-v"
DATEI = "AgriPilot-Setup.exe"
KOPF = {"User-Agent": "AgriPilot-Update", "Accept": "application/vnd.github+json"}


class OnlineFehler(RuntimeError):
    """Etwas ist schiefgegangen, und der Text sagt dem Fahrer, was."""


def version_zahlen(text: str) -> tuple[int, ...]:
    """'agripilot-v1.2.0' / 'v1.2.0' / '1.2.0' -> (1, 2, 0). Unlesbares -> ()."""
    treffer = re.search(r"(\d+(?:\.\d+)*)", text or "")
    return tuple(int(t) for t in treffer.group(1).split(".")) if treffer else ()


def _lesen(url: str, timeout: float) -> bytes:
    anfrage = urllib.request.Request(url, headers=KOPF)
    try:
        with urllib.request.urlopen(anfrage, timeout=timeout) as antwort:
            return antwort.read()
    except urllib.error.HTTPError as exc:
        if exc.code in (403, 429):
            raise OnlineFehler("GitHub nimmt gerade keine Anfragen an (Begrenzung) - "
                               "in einer Stunde noch einmal versuchen") from exc
        raise OnlineFehler(f"Die Update-Stelle antwortet mit Fehler {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise OnlineFehler("Keine Verbindung zur Update-Stelle - Netz oder WLAN prüfen") from exc


def neueste(repo: str = STANDARD_REPO, api: str = STANDARD_API,
            timeout: float = 10.0) -> Optional[dict]:
    """Das jüngste veröffentlichte Release dieses Programms, oder ``None``."""
    roh = _lesen(f"{api.rstrip('/')}/repos/{repo}/releases?per_page=30", timeout)
    try:
        releases = json.loads(roh)
    except ValueError as exc:
        raise OnlineFehler("Antwort der Update-Stelle nicht lesbar") from exc
    beste: Optional[dict] = None
    for r in releases if isinstance(releases, list) else []:
        etikett = str(r.get("tag_name") or "")
        if r.get("draft") or r.get("prerelease") or not etikett.startswith(ETIKETT_VORSATZ):
            continue
        zahlen = version_zahlen(etikett[len(ETIKETT_VORSATZ):])
        datei = next((a for a in r.get("assets", []) if a.get("name") == DATEI), None)
        if not zahlen or datei is None:
            continue
        if beste is None or zahlen > beste["zahlen"]:
            beste = {
                "zahlen": zahlen,
                "version": ".".join(str(z) for z in zahlen),
                "notizen": (r.get("body") or "").strip()[:2000],
                "url": datei.get("browser_download_url", ""),
                "groesse": int(datei.get("size") or 0),
                "sha256": str(datei.get("digest") or "").removeprefix("sha256:").lower(),
                "pruefsumme_url": next((a.get("browser_download_url") for a in r.get("assets", [])
                                       if a.get("name") == DATEI + ".sha256"), ""),
            }
    return beste


def suche(aktuell: str, repo: str = STANDARD_REPO,
          api: str = STANDARD_API) -> tuple[dict, Optional[dict]]:
    """Gibt es etwas Neueres? Liefert die Antwort für die Anzeige und das Release
    selbst (zum Laden). Fehler werden zu Text, nicht zu Ausnahmen: die
    Oberfläche zeigt ihn an, das Programm läuft weiter."""
    ergebnis = {"aktuell": aktuell, "neu": None, "verfuegbar": False, "notizen": "",
                "groesse": 0, "fehler": ""}
    try:
        info = neueste(repo, api)
    except OnlineFehler as exc:
        ergebnis["fehler"] = str(exc)
        return ergebnis, None
    if info is not None:
        ergebnis.update(neu=info["version"], notizen=info["notizen"], groesse=info["groesse"],
                        verfuegbar=info["zahlen"] > version_zahlen(aktuell))
    return ergebnis, info


MASTER_INFO = "/api/sync/setup"
MASTER_DATEI = "/api/sync/setup/datei"


def vom_master(aktuell: str, master_url: str, code: str = "",
               timeout: float = 10.0) -> tuple[dict, Optional[dict]]:
    """Beim Master nachsehen: er gibt sein eigenes Programm als Update heraus.

    So kommt ein Arbeiter an die neue Version, sobald der Master sie hat - im
    Hof-WLAN wie über den Tunnel draußen auf dem Feld, ohne GitHub. Mit dem
    Arbeitercode wie beim Abgleich.
    """
    ergebnis = {"aktuell": aktuell, "neu": None, "verfuegbar": False, "notizen": "",
                "groesse": 0, "fehler": "", "quelle": "Master"}
    basis = (master_url or "").rstrip("/")
    if not basis:
        ergebnis["fehler"] = "Keine Master-Adresse eingetragen"
        return ergebnis, None
    kopf = {**KOPF, "Accept": "application/json"}
    if code:
        kopf["X-AgriPilot-Token"] = code
    anfrage = urllib.request.Request(basis + MASTER_INFO, headers=kopf)
    try:
        with urllib.request.urlopen(anfrage, timeout=timeout) as antwort:
            daten = json.loads(antwort.read())
    except urllib.error.HTTPError as exc:
        ergebnis["fehler"] = ("Arbeitercode fehlt oder ist falsch" if exc.code in (401, 403)
                              else f"Der Master antwortet mit Fehler {exc.code}")
        return ergebnis, None
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        ergebnis["fehler"] = "Master nicht erreichbar"
        return ergebnis, None
    zahlen = version_zahlen(str(daten.get("version") or ""))
    sha = str(daten.get("sha256") or "").lower()
    if not zahlen or not sha:
        ergebnis["fehler"] = "Der Master bietet kein Update an"
        return ergebnis, None
    info = {
        "zahlen": zahlen,
        "version": ".".join(str(z) for z in zahlen),
        "notizen": "Vom Master übernommen",
        "url": basis + MASTER_DATEI,
        "groesse": int(daten.get("groesse") or 0),
        "sha256": sha,
        "pruefsumme_url": "",
        "kopf": {k: v for k, v in kopf.items() if k != "Accept"},
    }
    ergebnis.update(neu=info["version"], notizen=info["notizen"], groesse=info["groesse"],
                    verfuegbar=zahlen > version_zahlen(aktuell))
    return ergebnis, info


def pruefen(aktuell: str, repo: str = STANDARD_REPO, api: str = STANDARD_API) -> dict:
    return suche(aktuell, repo, api)[0]


def herunterladen(info: dict, ordner: Path, timeout: float = 30.0,
                  fortschritt: Optional[Callable[[int, int], None]] = None) -> Path:
    """Die Setup-Datei laden und prüfen. Gibt den Pfad der fertigen Datei zurück."""
    ordner = Path(ordner)
    ordner.mkdir(parents=True, exist_ok=True)
    sha = info.get("sha256") or ""
    if not sha and info.get("pruefsumme_url"):
        text = _lesen(info["pruefsumme_url"], timeout).decode("ascii", "replace")
        sha = text.split()[0].lower() if text.split() else ""
    if not sha:
        raise OnlineFehler("Zu diesem Release gibt es keine Prüfsumme - es wird nicht eingespielt")
    ziel = ordner / f"AgriPilot-Setup-{info['version']}.exe"
    teil = ordner / (ziel.name + ".teil")
    hash_ = hashlib.sha256()
    gelesen = 0
    anfrage = urllib.request.Request(info["url"], headers=info.get("kopf") or KOPF)
    try:
        with urllib.request.urlopen(anfrage, timeout=timeout) as antwort, open(teil, "wb") as aus:
            gesamt = int(antwort.headers.get("Content-Length") or info.get("groesse") or 0)
            while True:
                block = antwort.read(256 * 1024)
                if not block:
                    break
                aus.write(block)
                hash_.update(block)
                gelesen += len(block)
                if fortschritt:
                    fortschritt(gelesen, gesamt)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        teil.unlink(missing_ok=True)
        raise OnlineFehler("Der Download ist abgebrochen - Netz prüfen und noch einmal versuchen") from exc
    if hash_.hexdigest() != sha:
        teil.unlink(missing_ok=True)
        raise OnlineFehler("Die geladene Datei passt nicht zur Prüfsumme - sie wird verworfen")
    with open(teil, "rb") as f:
        kopf = f.read(2)
    if kopf != b"MZ":                    # erst schließen, dann löschen: Windows hält die Datei sonst fest
        teil.unlink(missing_ok=True)
        raise OnlineFehler("Die geladene Datei ist kein Windows-Programm - sie wird verworfen")
    teil.replace(ziel)
    return ziel
