"""Der Master auf dem Pi hat keinen Empfänger: Quelle "aus"."""

import asyncio
import unittest

from agripilot import config as config_module
from agripilot.gnss import KeineQuelle, SimulatorSource, build_source
from agripilot.settings import GRUPPEN


class KeineQuelleTests(unittest.TestCase):
    def _config(self, quelle):
        config = config_module.load("/kein-solcher-pfad.yaml")
        config.gnss.source = quelle
        return config

    def test_aus_builds_no_simulator(self):
        self.assertIsInstance(build_source(self._config("aus"), lambda fix: None), KeineQuelle)
        self.assertIsInstance(build_source(self._config("AUS"), lambda fix: None), KeineQuelle)
        self.assertIsInstance(build_source(self._config("simulator"), lambda fix: None),
                              SimulatorSource)

    def test_it_runs_quietly_and_stops(self):
        fixe = []
        quelle = KeineQuelle(fixe.append)

        async def ablauf():
            lauf = asyncio.create_task(quelle.run())
            await asyncio.sleep(0.05)
            self.assertTrue(quelle.running)
            self.assertEqual(quelle.status, "kein Empfänger")
            self.assertFalse(quelle.healthy)
            await quelle.stop()
            await asyncio.wait_for(lauf, timeout=3)

        asyncio.run(ablauf())
        self.assertEqual(fixe, [])

    def test_it_can_be_chosen_in_the_settings(self):
        feld = next(f for g in GRUPPEN for f in g.felder if f.schluessel == "gnss.source")
        self.assertIn("aus", [wert for wert, _ in feld.auswahl])


if __name__ == "__main__":
    unittest.main()
