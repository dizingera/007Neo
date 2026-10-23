#!/usr/bin/env python3
"""Eine neue Version bauen und für die Online-Aktualisierung bereitstellen.

    python scripts/release.py                       # bauen, Prüfsumme zeigen, Anleitung
    python scripts/release.py --veroeffentlichen --notizen "Was neu ist"

Ohne ``--veroeffentlichen`` passiert nichts im Netz: gebaut wird
``gps/AgriPilot-Setup.exe`` samt ``.sha256``, und die Anleitung zum Hochladen von
Hand steht am Ende (GitHub → Releases → Draft a new release).

Mit ``--veroeffentlichen`` legt das Skript das Release selbst an. Das ist
**öffentlich** (das Repo ist es) und greift in das ein, was jedes Tablet beim
nächsten Suchen findet - deshalb nur auf ausdrücklichen Wunsch, mit einem Token
in der Umgebungsvariable ``GITHUB_TOKEN`` (Rechte: Contents → Read and write), und
nur von einem gepushten, sauberen Stand.

Das Etikett ist ``agripilot-v<Version>``; die Version steht in
``backend/agripilot/__init__.py``. Tablets nehmen nur Releases mit höherer
Version, keine Entwürfe und keine Vorabversionen.
"""

import argparse
import hashlib
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

GPS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(GPS / "backend"))

REPO = "dizingera/007Neo"


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=GPS, capture_output=True, text=True,
                          check=True).stdout.strip()


def _api(methode: str, url: str, token: str, daten: bytes = b"", typ: str = "application/json") -> dict:
    anfrage = urllib.request.Request(url, data=daten or None, method=methode, headers={
        "Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
        "Content-Type": typ, "User-Agent": "AgriPilot-Release"})
    try:
        with urllib.request.urlopen(anfrage, timeout=300) as antwort:
            return json.loads(antwort.read() or b"{}")
    except urllib.error.HTTPError as exc:
        raise SystemExit(f"GitHub antwortet {exc.code}: {exc.read().decode('utf-8', 'replace')[:400]}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--veroeffentlichen", action="store_true",
                        help="Release auf GitHub anlegen (öffentlich!)")
    parser.add_argument("--notizen", default="", help="Text zum Release - das sieht der Fahrer")
    parser.add_argument("--ohne-bauen", action="store_true", help="vorhandene AgriPilot-Setup.exe nehmen")
    args = parser.parse_args()

    from agripilot import __version__
    etikett = f"agripilot-v{__version__}"
    exe = GPS / "AgriPilot-Setup.exe"

    if not args.ohne_bauen:
        # Die Build-Umgebung (siehe build_exe.py) hat PyInstaller; die Entwicklungsumgebung nicht.
        python = GPS / ".buildvenv" / "Scripts" / "python.exe"
        if subprocess.run([str(python if python.exists() else sys.executable),
                           str(GPS / "scripts" / "build_exe.py")]).returncode != 0:
            return 1
    if not exe.exists():
        print("AgriPilot-Setup.exe fehlt.")
        return 1

    summe = hashlib.sha256(exe.read_bytes()).hexdigest()
    (GPS / "AgriPilot-Setup.exe.sha256").write_text(f"{summe}  AgriPilot-Setup.exe\n", encoding="ascii")
    print(f"\nVersion {__version__}  Etikett {etikett}\nSHA-256 {summe}\n{exe.stat().st_size / 1e6:.1f} MB")

    if not args.veroeffentlichen:
        print(f"""
Von Hand bereitstellen:
  1. https://github.com/{REPO}/releases/new
  2. Etikett: {etikett}   (neu anlegen, Ziel: der gepushte Stand)
  3. Titel: AgriPilot {__version__}; in die Beschreibung, was neu ist
  4. Anhängen: {exe}
     und {exe}.sha256
  5. 'Publish release' - Entwurf und Vorabversion werden von den Tablets ignoriert.
Tablets finden es beim nächsten Suchen (alle zwölf Stunden, oder Menü → System →
Aktualisierung → Online nach neuer Version suchen).""")
        return 0

    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        print("GITHUB_TOKEN fehlt.")
        return 1
    if _git("status", "--porcelain", "--", "."):
        print("Es gibt nicht eingecheckte Änderungen - erst committen und pushen.")
        return 1
    kopf = _git("rev-parse", "HEAD")
    if not _git("branch", "-r", "--contains", kopf):
        print("Der Stand ist nicht gepusht - erst pushen.")
        return 1
    release = _api("POST", f"https://api.github.com/repos/{REPO}/releases", token, json.dumps({
        "tag_name": etikett, "target_commitish": kopf, "name": f"AgriPilot {__version__}",
        "body": args.notizen or f"AgriPilot {__version__}", "draft": False, "prerelease": False,
    }).encode())
    hoch = release["upload_url"].split("{")[0]
    for datei, typ in ((exe, "application/octet-stream"),
                       (GPS / "AgriPilot-Setup.exe.sha256", "text/plain")):
        _api("POST", f"{hoch}?name={datei.name}", token, datei.read_bytes(), typ)
        print(f"hochgeladen: {datei.name}")
    print(f"\nVeröffentlicht: {release['html_url']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
