"""Ein kurzer Dauerlauf als Regressionstest: Störungen im Zeitraffer, keine Regel verletzt.

Der lange Lauf (scripts/dauerlauf.py --stunden 3) gehört vor jede Auslieferung;
hier läuft eine knappe halbe Stunde virtueller Arbeit mit Störungen, in Sekunden.
"""

import argparse
import contextlib
import importlib.util
import io
import sys
import time
import unittest
from pathlib import Path

SKRIPT = Path(__file__).resolve().parents[2] / "scripts" / "dauerlauf.py"


def _laden():
    spec = importlib.util.spec_from_file_location("dauerlauf", SKRIPT)
    modul = importlib.util.module_from_spec(spec)
    sys.modules["dauerlauf"] = modul
    spec.loader.exec_module(modul)
    return modul


class DauerlaufTest(unittest.TestCase):
    def _lauf(self, **werte):
        modul = _laden()
        args = argparse.Namespace(stunden=0.3, seed=1, ohne_stoerungen=False,
                                  fortschritt=False, ausfuehrlich=False)
        for schluessel, wert in werte.items():
            setattr(args, schluessel, wert)
        echte_zeit = time.time
        ausgabe = io.StringIO()
        try:
            with contextlib.redirect_stdout(ausgabe):
                ergebnis = modul.Lauf(args).laufen()
        finally:
            time.time = echte_zeit
        return ergebnis, ausgabe.getvalue()

    def test_without_disturbances_the_machine_works_a_field_cleanly(self):
        ergebnis, text = self._lauf(ohne_stoerungen=True, stunden=0.4)
        self.assertEqual(ergebnis, 0, text)
        self.assertIn("Alle Regeln eingehalten", text)

    def test_with_disturbances_no_rule_is_broken(self):
        for seed in (1, 2):
            ergebnis, text = self._lauf(seed=seed)
            self.assertEqual(ergebnis, 0, f"Seed {seed}:\n{text}")


if __name__ == "__main__":
    unittest.main()
