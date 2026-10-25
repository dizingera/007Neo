"""Ein fehlerhaftes Diagnoseprotokoll darf den Überwachungstakt nicht beenden."""
import asyncio
import sqlite3
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock, patch
from agripilot.config import Config
from agripilot.server import Application

class BetriebsbuchUeberwachungTests(unittest.IsolatedAsyncioTestCase):
    async def test_speicherfehler_im_betriebsbuch_laesst_watchdog_weiterlaufen(self):
        with tempfile.TemporaryDirectory() as d:
            cfg=Config();cfg.server.data_dir=d;cfg.gnss.source='aus';cfg.imu.source='aus';cfg.steering.output='none'
            app=Application(cfg)
            try:
                app.engine.tick=Mock()
                app.engine.betriebsbuch.observation=Mock(side_effect=sqlite3.OperationalError('disk full'))
                app.checklist.beobachten=Mock()
                with patch('agripilot.server.asyncio.sleep',new=AsyncMock(side_effect=[None,None,asyncio.CancelledError()])):
                    with self.assertRaises(asyncio.CancelledError):
                        await app._broadcast_loop()
                self.assertEqual(app.engine.tick.call_count,2)
                self.assertEqual(app.checklist.beobachten.call_count,2)
                self.assertTrue(any('Betriebsbuch' in m for m in app.engine.messages))
            finally: app.store.close()

    async def test_echter_sqlite_schreibfehler_bleibt_vom_watchdog_getrennt(self):
        with tempfile.TemporaryDirectory() as d:
            cfg=Config();cfg.server.data_dir=d;cfg.gnss.source='aus';cfg.imu.source='aus';cfg.steering.output='none'
            app=Application(cfg)
            try:
                app.engine.tick=Mock();app.checklist.beobachten=Mock();app.steering.armed=True
                app.store.db.execute("CREATE TRIGGER test_speicherfehler BEFORE INSERT ON settings WHEN NEW.key='betriebsbuch' BEGIN SELECT RAISE(FAIL,'disk fault'); END")
                app.store.db.commit()
                with patch('agripilot.server.asyncio.sleep',new=AsyncMock(side_effect=[None,None,asyncio.CancelledError()])):
                    with self.assertRaises(asyncio.CancelledError):await app._broadcast_loop()
                self.assertEqual(app.engine.tick.call_count,2)
                self.assertEqual(app.checklist.beobachten.call_count,2)
                self.assertEqual(sum('Betriebsbuch' in x for x in app.engine.messages),1)
            finally:app.store.close()
