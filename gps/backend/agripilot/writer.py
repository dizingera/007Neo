"""Schreiben im Hintergrund: Fahrspur und Zwischenstand einer Arbeit.

Die Lenkschleife darf nicht auf die Festplatte warten. Im Dauerlauf kostete das
Zwischenspeichern der bearbeiteten Fläche 50 bis 150 ms, im Ausreißer über eine
halbe Sekunde - auf demselben Faden wie Position und Lenkung, also fehlten in
dieser Zeit zwei bis fünf Positionen. Auf einem Pi mit SD-Karte ist es mehr.

Ein eigener Faden schreibt, mit einer eigenen, kurzlebigen Datenbankverbindung
(SQLite im WAL-Modus erlaubt das; ein Schreiber wartet höchstens auf den
anderen, und beide schreiben nur Millisekunden). Aufträge laufen in der
Reihenfolge, in der sie kamen. Die Verbindung wird nach jedem Stapel wieder
geschlossen, damit nichts die Datei festhält, wenn das Programm endet.
"""

from __future__ import annotations

import queue
import threading
from pathlib import Path
from typing import Callable, Optional

from .storage import Storage


class HintergrundSchreiber:
    def __init__(self, pfad: str | Path) -> None:
        self.pfad = Path(pfad)
        self._aufgaben: queue.Queue = queue.Queue()
        self._faden: Optional[threading.Thread] = None
        self._sperre = threading.Lock()
        self._fehler: list[str] = []

    # -- Aufträge ---------------------------------------------------------

    def auftrag(self, arbeit: Callable[[Storage], None]) -> None:
        """Eine Schreibarbeit einreihen. ``arbeit`` bekommt die Verbindung des Fadens."""
        self._starten()
        self._aufgaben.put(arbeit)

    @property
    def beschaeftigt(self) -> bool:
        return self._aufgaben.unfinished_tasks > 0

    def warten(self) -> None:
        """Bis alles Eingereihte geschrieben ist."""
        self._aufgaben.join()

    def beenden(self) -> None:
        self.warten()
        faden = self._faden
        if faden is not None:
            self._aufgaben.put(None)
            faden.join(timeout=5.0)
            self._faden = None

    def fehler_holen(self) -> list[str]:
        """Fehler seit dem letzten Aufruf - der Faden selbst kann niemandem etwas melden."""
        with self._sperre:
            fehler, self._fehler = self._fehler, []
        return fehler

    # -- Faden ------------------------------------------------------------

    def _starten(self) -> None:
        with self._sperre:
            if self._faden is not None and self._faden.is_alive():
                return
            self._faden = threading.Thread(target=self._lauf, name="agripilot-schreiber",
                                           daemon=True)
            self._faden.start()

    def _lauf(self) -> None:
        while True:
            erste = self._aufgaben.get()
            if erste is None:
                self._aufgaben.task_done()
                return
            stapel = [erste]
            ende = False
            while True:                       # was schon wartet, in einem Zug
                try:
                    weitere = self._aufgaben.get_nowait()
                except queue.Empty:
                    break
                if weitere is None:
                    ende = True
                    stapel.append(weitere)
                    break
                stapel.append(weitere)
            store = None
            try:
                store = Storage(self.pfad, schema=False)
            except Exception as exc:  # noqa: BLE001
                self._melden(f"Hintergrundschreiber: Datenbank nicht zu öffnen: {exc}")
            for arbeit in stapel:
                if arbeit is not None and store is not None:
                    try:
                        arbeit(store)
                    except Exception as exc:  # noqa: BLE001 - weiterarbeiten, melden
                        self._melden(f"Sichern fehlgeschlagen: {exc}")
                self._aufgaben.task_done()
            if store is not None:
                try:
                    store.close()
                except Exception:  # noqa: BLE001
                    pass
            if ende:
                return

    def _melden(self, text: str) -> None:
        with self._sperre:
            self._fehler.append(text)
            del self._fehler[:-10]
