"""Die Anzeige in einem eigenen Fenster - ohne Browser, ohne Adresszeile.

Das Fenster ist das Programm, so wie man es von Windows kennt: das Symbol startet
AgriPilot, Schließen beendet es. Dahinter läuft der eigentliche Teil (Empfänger,
Lenkung, Abgleich) als Aufgabe "AgriPilot" - mit höchsten Rechten und Neustart nach
einem Absturz, aber ohne Start beim Anmelden. Läuft gerade die Lenkung oder eine
Arbeit, fragt das Fenster vor dem Beenden nach.

Gebaut mit WebView2 - dem Edge-Kern, der in Windows 10/11 steckt. Darin läuft
dieselbe Oberfläche wie auf dem Android-Tablet; es gibt nur eine. Ein zweiter
Klick auf das Symbol holt das offene Fenster nach vorn. Fehlt WebView2 doch
einmal, öffnet sich die Anzeige als Edge-App-Fenster und erst zuletzt im Browser.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import urllib.request
from pathlib import Path
from typing import Optional

TITEL = "AgriPilot"
MUTEX = "Local\\AgriPilotFenster"
AUFGABE = "AgriPilot"
OHNE_KONSOLE = 0x08000000      # CREATE_NO_WINDOW: schtasks ohne aufblitzendes Fenster


def adresse(port: int) -> str:
    return f"http://localhost:{port}"


def dienst_bereit(url: str, timeout: float = 2.0) -> bool:
    try:
        urllib.request.urlopen(f"{url}/api/system", timeout=timeout).read()
        return True
    except Exception:  # noqa: BLE001 - noch nicht oben, oder gar nicht
        return False


def _aufgabe(aktion: str) -> bool:
    """Die Aufgabe "AgriPilot" starten (/Run) oder hart anhalten (/End)."""
    if os.name != "nt":
        return False
    try:
        return subprocess.run(["schtasks", aktion, "/TN", AUFGABE], capture_output=True,
                              creationflags=OHNE_KONSOLE, timeout=20).returncode == 0
    except Exception:  # noqa: BLE001 - etwa aus dem Quellbaum ohne Installation
        return False


def dienst_starten() -> bool:
    return _aufgabe("/Run")


def _anfrage(url: str, methode: str = "GET", timeout: float = 3.0) -> Optional[dict]:
    try:
        anfrage = urllib.request.Request(url, method=methode,
                                         data=b"{}" if methode == "POST" else None,
                                         headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(anfrage, timeout=timeout) as antwort:
            return json.loads(antwort.read() or b"{}")
    except Exception:  # noqa: BLE001
        return None


def beenden_erlaubt(url: str, fragen=None) -> bool:
    """Vor dem Schließen: läuft die Lenkung oder eine Arbeit, erst nachfragen."""
    stand = (_anfrage(f"{url}/api/beenden") or {}).get("data") or {}
    gruende = []
    if stand.get("lenkung"):
        gruende.append("Die Lenkung ist eingeschaltet und wird ausgeschaltet.")
    if stand.get("arbeit"):
        gruende.append("Eine Arbeit läuft. Sie wird gesichert und kann später fortgesetzt werden.")
    if not gruende:
        return True
    fragen = fragen or _frage_ja_nein
    return fragen("AgriPilot beenden?", "\n".join(gruende) + "\n\nAgriPilot jetzt beenden?")


def _frage_ja_nein(titel: str, text: str) -> bool:
    import ctypes
    # MB_YESNO | MB_ICONWARNING | MB_DEFBUTTON2 | MB_TOPMOST; 6 = Ja
    return ctypes.windll.user32.MessageBoxW(  # type: ignore[attr-defined]
        None, text, titel, 0x4 | 0x30 | 0x100 | 0x40000) == 6


def dienst_beenden(url: str, warten_s: float = 15.0) -> str:
    """Sauber beenden lassen; hilft das nicht, die Aufgabe anhalten."""
    if not dienst_bereit(url, timeout=1.0):
        return "lief nicht"
    _anfrage(f"{url}/api/beenden", "POST")
    ende = time.monotonic() + warten_s
    while time.monotonic() < ende:
        if not dienst_bereit(url, timeout=1.0):
            return "beendet"
        time.sleep(0.5)
    _aufgabe("/End")
    return "angehalten"


def warteseite(url: str) -> str:
    """Steht der Dienst noch nicht (gleich nach dem Anmelden), wartet das Fenster mit.

    Die Seite fragt jede Sekunde nach und springt um, sobald er antwortet. Nach
    einer Minute sagt sie, wo man nachsehen muss, statt ewig zu kreisen.
    """
    return f"""<!doctype html><html lang="de"><head><meta charset="utf-8">
<title>{TITEL}</title><style>
body {{ margin:0; height:100vh; display:grid; place-items:center; background:#0b0f0c;
       color:#e8efe9; font:18px/1.5 system-ui, sans-serif; text-align:center; }}
.punkt {{ width:14px; height:14px; border-radius:50%; background:#5dff8e; margin:0 auto 18px;
         animation: p 1.2s ease-in-out infinite; }}
@keyframes p {{ 50% {{ opacity:.25; }} }}
small {{ color:#8fa396; display:block; margin-top:10px; max-width:34em; }}
</style></head><body><div><div class="punkt"></div><div id="t">AgriPilot startet …</div>
<small id="h"></small></div><script>
const ziel = {url!r};
const beginn = Date.now();
async function pruefen() {{
  try {{
    await fetch(ziel + '/api/system', {{ mode: 'no-cors', cache: 'no-store' }});
    location.replace(ziel);
    return;
  }} catch (e) {{}}
  if (Date.now() - beginn > 60000) {{
    document.getElementById('t').textContent = 'AgriPilot antwortet nicht.';
    document.getElementById('h').textContent = 'Fenster schließen und AgriPilot neu öffnen. '
      + 'Hilft das nicht, die Setup-Datei erneut ausführen. Protokoll: '
      + 'C:\\\\ProgramData\\\\AgriPilot\\\\agripilot.log';
  }}
  setTimeout(pruefen, 1000);
}}
pruefen();
</script></body></html>"""


def speicherort() -> str:
    """Wo WebView2 Einstellungen der Oberfläche ablegt (Ansicht, Helligkeit ...).

    Ohne festen Ort öffnet pywebview ein "privates" Fenster und alles ist beim
    nächsten Start vergessen.
    """
    basis = os.environ.get("LOCALAPPDATA") or str(Path.home())
    ordner = Path(basis) / "AgriPilot" / "Fenster"
    ordner.mkdir(parents=True, exist_ok=True)
    return str(ordner)


def schon_offen() -> bool:
    """True, wenn schon ein Fenster läuft - das wird dann nach vorn geholt."""
    if os.name != "nt":
        return False
    import ctypes
    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    # Der Griff bleibt bis zum Prozessende offen; genau so lange gilt die Sperre.
    schon_offen.griff = kernel32.CreateMutexW(None, False, MUTEX)  # type: ignore[attr-defined]
    if kernel32.GetLastError() != 183:          # ERROR_ALREADY_EXISTS
        return False
    user32 = ctypes.windll.user32  # type: ignore[attr-defined]
    fenster = user32.FindWindowW(None, TITEL)
    if fenster:
        user32.ShowWindow(fenster, 9)            # SW_RESTORE: aus der Taskleiste holen
        user32.SetForegroundWindow(fenster)
    return True


class _Bruecke:
    """Was die Oberfläche im Fenster vom Fenster selbst braucht.

    Nur Methoden ohne Unterstrich sind für die Oberfläche. pywebview durchsucht
    alle anderen öffentlichen Attribute rekursiv nach Funktionen - ein öffentlicher
    Verweis auf das Fenster ließ es endlos durch den .NET-Objektbaum laufen
    (25 GB Speicher nach 20 Minuten, ein Kern dauerbelegt). Deshalb `_fenster`.
    """

    def __init__(self) -> None:
        self._fenster = None

    def vollbild(self) -> bool:
        if self._fenster is not None:
            self._fenster.toggle_fullscreen()
        return True


def edge_pfad() -> Optional[str]:
    for kandidat in (shutil.which("msedge"),
                     r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
                     r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"):
        if kandidat and Path(kandidat).exists():
            return kandidat
    return None


def ersatz_oeffnen(url: str) -> str:
    """Ohne WebView2: Edge als App-Fenster, und erst wenn auch das fehlt, der Browser."""
    edge = edge_pfad()
    if edge:
        subprocess.Popen([edge, f"--app={url}", "--start-maximized"])
        return "edge"
    import webbrowser
    webbrowser.open(url)
    return "browser"


def oeffnen(port: int) -> int:
    url = adresse(port)
    if schon_offen():
        return 0
    bereit = dienst_bereit(url)
    if not bereit:
        dienst_starten()
    try:
        import webview
    except Exception:  # noqa: BLE001 - fehlt das Paket, geht es trotzdem
        ersatz_oeffnen(url)
        return 0
    bruecke = _Bruecke()
    if bereit:
        fenster = webview.create_window(TITEL, url, maximized=True, min_size=(800, 500),
                                        text_select=False, js_api=bruecke)
    else:
        fenster = webview.create_window(TITEL, html=warteseite(url), maximized=True,
                                        min_size=(800, 500), text_select=False, js_api=bruecke)
    bruecke._fenster = fenster
    # False hält das Fenster offen: wer bei der Rückfrage "Nein" sagt, arbeitet weiter.
    fenster.events.closing += lambda: beenden_erlaubt(url)
    try:
        webview.start(private_mode=False, storage_path=speicherort())
    except Exception:  # noqa: BLE001 - etwa WebView2 fehlt: dann wenigstens die Anzeige
        ersatz_oeffnen(url)
        return 0
    # Fenster zu = Programm zu. Im Ersatzweg (Edge, Browser) gibt es kein Schließen
    # zum Abfangen; dort bleibt das Programm an, bis der Rechner herunterfährt.
    dienst_beenden(url)
    return 0
