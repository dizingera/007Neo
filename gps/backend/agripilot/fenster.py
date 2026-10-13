"""Die Anzeige in einem eigenen Fenster - ohne Browser, ohne Adresszeile.

Das Programm selbst läuft weiter als Dienst im Hintergrund (Aufgabe "AgriPilot"):
schließt man das Fenster, lenkt und zeichnet es weiter. Das Fenster ist nur die
Anzeige dafür, gebaut mit WebView2 - dem Edge-Kern, der in Windows 10/11 steckt.
Darin läuft dieselbe Oberfläche wie auf dem Android-Tablet; es gibt nur eine.

Ein Fenster genügt: ein zweiter Klick auf das Symbol holt das offene nach vorn,
statt ein zweites zu öffnen. Fehlt WebView2 doch einmal, öffnet sich die Anzeige
als Edge-App-Fenster (auch ohne Adresszeile) und erst zuletzt im Browser.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import urllib.request
from pathlib import Path
from typing import Optional

TITEL = "AgriPilot"
MUTEX = "Local\\AgriPilotFenster"


def adresse(port: int) -> str:
    return f"http://localhost:{port}"


def dienst_bereit(url: str, timeout: float = 2.0) -> bool:
    try:
        urllib.request.urlopen(f"{url}/api/system", timeout=timeout).read()
        return True
    except Exception:  # noqa: BLE001 - noch nicht oben, oder gar nicht
        return False


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
    document.getElementById('h').textContent = 'Das Programm läuft im Hintergrund als Aufgabe '
      + '„AgriPilot“. Rechner neu starten oder die Setup-Datei erneut ausführen. Protokoll: '
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
    """Was die Oberfläche im Fenster vom Fenster selbst braucht."""

    def __init__(self) -> None:
        self.fenster = None

    def vollbild(self) -> bool:
        if self.fenster is not None:
            self.fenster.toggle_fullscreen()
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
    try:
        import webview
    except Exception:  # noqa: BLE001 - fehlt das Paket, geht es trotzdem
        ersatz_oeffnen(url)
        return 0
    bruecke = _Bruecke()
    if dienst_bereit(url):
        fenster = webview.create_window(TITEL, url, maximized=True, min_size=(800, 500),
                                        text_select=False, js_api=bruecke)
    else:
        fenster = webview.create_window(TITEL, html=warteseite(url), maximized=True,
                                        min_size=(800, 500), text_select=False, js_api=bruecke)
    bruecke.fenster = fenster
    try:
        webview.start(private_mode=False, storage_path=speicherort())
    except Exception:  # noqa: BLE001 - etwa WebView2 fehlt: dann wenigstens die Anzeige
        ersatz_oeffnen(url)
    return 0
