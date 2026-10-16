# AgriPilot – Hinweise für Coding-Agenten

GPS-Lenksystem für den Traktor: FastAPI-Backend (`backend/agripilot`), Vanilla-JS-Oberfläche
(`frontend`), ausgeliefert als eine Windows-exe (PyInstaller). Sprache in Code, Kommentaren,
Oberfläche und Commits: **Deutsch**.

## Prüfen

```bash
cd backend && PYTHONIOENCODING=utf-8 ./.venv/Scripts/python.exe -m unittest discover -s tests
cd frontend && node --test tests/*.test.js
```

Nur mit `backend/.venv` – das System-Python gibt dutzende Fehler.
Simulator mit Oberfläche: `backend/.venv/Scripts/python.exe scripts/run_sim.py --port 8088`.

## Ausliefern

- Version nur in `backend/agripilot/__init__.py`; je Änderung eine Version, ein Commit
  (`feat: 1.5.x …`), `docs/BEDIENUNG.md` mitziehen.
- Setup: `.buildvenv/Scripts/python.exe scripts/build_exe.py` → `AgriPilot-Setup.exe`.
- Master installieren: Setup mit `--installieren --still` (Adminrechte), danach
  `schtasks /End /TN AgriPilot` und `schtasks /Run /TN AgriPilot`. Arbeiter-Tablets holen das
  Update danach selbst vom Master (`/api/sync/setup`).

## Regeln

- Python-Dateien: vorhandene Zeilenenden (CRLF) behalten, beim Schreiben `newline=""`.
- Menü-CSS mit `.sheet` präfixen; neue Frontend-Dateien in `sw.js` (`HUELLE`) eintragen und
  `CACHE` hochzählen.
- An pywebview-js_api-Objekten nur Methoden öffentlich.
- **Keine Zugangsdaten, Arbeitercodes oder Tokens ins Repo** – es ist öffentlich.
  `Tablet-Paket/Tablet-einrichten.bat` enthält den Arbeitercode und wird nicht weitergegeben.
- Arbeitsbreiten der Geräte trägt der Betreiber selbst ein – nicht ändern.
- Nichts ist am echten Traktor geprüft, was nicht ausdrücklich so dokumentiert ist.

Ausführliche Übergabe und offene Punkte: Obsidian-Vault, `Wiki/Projekte/AgriPilot-Uebergabe-2026-10-16.md`.
