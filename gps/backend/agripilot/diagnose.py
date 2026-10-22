"""Diagnosepaket: alles, was man braucht, um einen Fehler vom Feld nachzuvollziehen.

Ein Knopf (Menü → System) packt eine Zip-Datei: Zustand und Einstellungen,
das Protokoll, die letzten Spurpunkte, die Kurven von „Lenkung live“ und die
jüngste Rohdaten-Aufzeichnung. Die Datei geht an den, der helfen soll.

Was **nicht** hineinkommt: Passwörter, Arbeitercode, Zugangsnamen - alles, dessen
Schlüssel danach klingt, wird geschwärzt (``schwaerzen``). Das Paket soll man
weitergeben können, ohne nachzusehen, was darin steht.
"""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path
from typing import Any, Optional

GEHEIM = ("password", "passwort", "token", "secret", "geheim", "username", "benutzer", "tls_key", "arbeitercode")
GESCHWAERZT = "*** geschwärzt ***"
PROTOKOLL_ZEILEN = 4000
ROHDATEN_MAX_B = 40 * 1024 * 1024
ROHDATEN_MAX_ALTER_S = 24 * 3600


def _geheim(schluessel: str) -> bool:
    s = str(schluessel).lower()
    return any(g in s for g in GEHEIM)


def schwaerzen(wert: Any, schluessel: str = "") -> Any:
    """Eine Kopie, in der alles Geheime geschwärzt ist - in jeder Tiefe."""
    if isinstance(wert, dict):
        return {k: schwaerzen(v, k) for k, v in wert.items()}
    if isinstance(wert, list):
        return [schwaerzen(v, schluessel) for v in wert]
    if schluessel and _geheim(schluessel) and wert not in (None, "", False):
        return GESCHWAERZT
    return wert


def protokoll_ende(pfad: Path, zeilen: int = PROTOKOLL_ZEILEN) -> str:
    """Die letzten Zeilen einer Protokolldatei, ohne die ganze Datei zu laden."""
    try:
        with open(pfad, "rb") as f:
            f.seek(0, 2)
            groesse = f.tell()
            f.seek(max(0, groesse - zeilen * 200))
            text = f.read().decode("utf-8", "replace")
    except OSError:
        return ""
    return "\n".join(text.splitlines()[-zeilen:])


def packen(teile: dict[str, Any], dateien: Optional[dict[str, Path]] = None) -> bytes:
    """``teile``: Name -> Text oder JSON-fähiges; ``dateien``: Name im Paket -> Datei auf der Platte."""
    puffer = io.BytesIO()
    with zipfile.ZipFile(puffer, "w", zipfile.ZIP_DEFLATED) as z:
        for name, inhalt in teile.items():
            if inhalt is None:
                continue
            if not isinstance(inhalt, (str, bytes)):
                inhalt = json.dumps(schwaerzen(inhalt), ensure_ascii=False, indent=1, default=str)
            z.writestr(name, inhalt)
        for name, pfad in (dateien or {}).items():
            try:
                z.write(pfad, name)
            except OSError:
                continue
    return puffer.getvalue()
