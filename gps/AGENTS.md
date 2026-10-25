# AgriPilot – Hinweise für Coding-Agenten

GPS-Lenksystem für den Traktor: FastAPI-Backend (`backend/agripilot`), Vanilla-JS-Oberfläche
(`frontend`), ausgeliefert als eine Windows-exe (PyInstaller). Sprache in Code, Kommentaren,
Oberfläche und Commits: **Deutsch**.

## Prüfen

```bash
cd backend && PYTHONIOENCODING=utf-8 ./.venv/Scripts/python.exe -m unittest discover -s tests
cd frontend && node --test tests/*.test.js
```

Nur mit `backend/.venv` – das System-Python gibt dutzende Fehler. Stand 1.5.85: 861 + 92 Tests grün.
Simulator mit Oberfläche: `backend/.venv/Scripts/python.exe scripts/run_sim.py --port 8088`.
Träger Lenkmotor und Rückwärtsfahrt lassen sich nachbilden: `POST /api/simulator` mit
`lenk_totzeit_s`, `lenk_traegheit_s`, `lenk_rate_deg_s`, `speed_kmh` (negativ = rückwärts).

## Ausliefern

- Version nur in `backend/agripilot/__init__.py`; je Änderung eine Version, ein Commit
  (`feat: 1.5.x …`), `docs/BEDIENUNG.md` mitziehen.
- Setup: `.buildvenv/Scripts/python.exe scripts/build_exe.py` → `AgriPilot-Setup.exe`.
- **Der Master ist seit 1.5.54 der Raspberry Pi** (Dienst `agripilot`, Port 8090, Quelle `aus`,
  Daten `/var/lib/agripilot` auf der SD-Karte). Ausrollen samt Setup für die Arbeiter:
  `backend/.venv/Scripts/python.exe scripts/pi_ausrollen.py --adresse 192.168.0.126 --setup AgriPilot-Setup.exe`
  (sichert vorher nach `/var/backups/agripilot/`). Arbeiter, auch der Büro-PC, holen das
  Update dann selbst vom Master (`/api/sync/setup` gibt das neueste
  `/var/lib/agripilot/setup/AgriPilot-Setup-<Version>.exe` heraus).
- Der Büro-PC ist Arbeiter (`master_url http://192.168.0.126:8090`). Ein PC-Master als exe
  (`--installieren --still`, dann `schtasks /End|/Run /TN AgriPilot`) gibt sich selbst heraus.
- Tunnel „MyTerra“ läuft auf Pi **und** PC: die Route `agripilot.dizinger.com` muss auf die
  LAN-Adresse zeigen, nie auf `localhost`. Port 8090 braucht eine ufw-Regel auf dem Pi
  (setzt der Betreiber selbst).
- Zweite Sicherung: Aufgabe `AgriPilotPiSicherung` am PC (`scripts/pi_sicherung_holen.ps1`,
  Kopie in `C:\Users\andre\AgriPilotBackup\`) holt täglich nach `J:\AgriPilot\Pi-Master-Sicherungen`.

## Regeln

- Python-Dateien: vorhandene Zeilenenden (CRLF) behalten, beim Schreiben `newline=""`.
- Menü-CSS mit `.sheet` präfixen; neue Frontend-Dateien in `sw.js` (`HUELLE`) eintragen und
  `CACHE` hochzählen.
- An pywebview-js_api-Objekten nur Methoden öffentlich.
- **Keine Zugangsdaten, Arbeitercodes oder Tokens ins Repo** – es ist öffentlich.
  `Tablet-Paket/Tablet-einrichten.bat` enthält den Arbeitercode und wird nicht weitergegeben.
- Arbeitsbreiten der Geräte trägt der Betreiber selbst ein – nicht ändern.
- Nichts ist am echten Traktor geprüft, was nicht ausdrücklich so dokumentiert ist.
- Dateien per Skript ändern: neuen Text erst vollständig im Speicher bauen, dann öffnen und
  schreiben – ein Fehler zwischen `open(..., "w")` und `write` leert die Datei.
- `guard()` in `server.py` liefert eine fertige `JSONResponse`, kein dict.
- Simulator-Störungen zum Prüfen der Lenkung über `POST /api/simulator`: `seitendrift_ms`,
  `rad_versatz_deg`, `mess_korrektur_deg`, `fix_quality`. Prüffelder in
  `.simulator/agripilot.db` danach wieder löschen.
- Externe Dienste (Luftbild LGL-BW, GitHub) können ausfallen; nie ohne Rückfall einbauen.
- Lenkmotor (`actuators.PhidgetOutput`): Der Regelkreis läuft seit 1.5.85 in einem eigenen
  Thread; ohne Lenkbefehl 0,5 s steht der Motor (die 1065 hat unter Phidget21 keinen
  Failsafe). Tests, die `_step`/`_control_loop` selbst takten, setzen
  `actuators.REGEL_FADEN = False` (setUpModule) – sonst laufen zwei Regelkreise.
- Fahrzeugwerte (`TRAKTOR_FELDER`) bestimmt das Tablet selbst; nur `REGLER_FELDER`
  (Regelart, Cerea-Werte) kommen vom Master, wenn sie dort geändert wurden.
- Pi-Fallen: `~/.ssh/config` zeigt `Dreambox.local` auf die WLAN-Adresse, für große Dateien
  `-oHostName=192.168.0.126`. Größere Übertragungen PC→Pi rissen am 22.10. ab („Connection
  reset“), während der PC beim Senden Pakete verlor – `pi_ausrollen.daten_senden` schickt
  deshalb Stücke mit Wiederholung und SHA-256-Prüfung. Git Bash schreibt `/tmp/...` in
  Argumenten für Windows-Programme zu `C:/Users/.../Temp/...` um (`MSYS_NO_PATHCONV=1`).
  `/var/lib/agripilot` ist nur für den Dienst lesbar (UMask 0027): auf dem Pi mit `sudo -n`.
- `C:\ProgramData\AgriPilot\config.yaml` und die Datenbank darf nur das Programm selbst (mit
  Adminrechten) schreiben. Einstellungen deshalb über `POST /api/settings` ändern, nicht in
  der Datei.

Ausführliche Übergabe und offene Punkte: Obsidian-Vault, `Wiki/Projekte/AgriPilot-Uebergabe-2026-10-16.md`
(fortlaufend ergänzt, aktueller Stand ganz unten). Laufender Prüfauftrag an Codex (25.10.2026):
`Codex/2026-10-25-agripilot-pruefauftrag-von-claude.md`, Ergebnis nach
`Codex/AgriPilot-Gesamtpruefung-2026-10-25/Ergebnis.md`.
