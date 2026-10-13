"""Beenden und Deinstallieren: nur vom Rechner selbst, Daten bleiben, wenn nicht anders gewählt."""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from agripilot import exe


def _app(ordner: str):
    from agripilot import config as config_module
    from agripilot.server import create_app
    config = config_module.load("/kein-solcher-pfad.yaml")
    config.server.data_dir = ordner
    config.gnss.source = "simulator"
    return create_app(config)


class BeendenEndpunktTests(unittest.TestCase):
    def test_only_this_computer_may_end_the_program(self):
        from fastapi.testclient import TestClient
        with tempfile.TemporaryDirectory() as ordner:
            app = _app(ordner)
            app.state.beenden = mock.Mock()
            with TestClient(app, client=("192.168.0.77", 50000)) as tablet:
                self.assertEqual(tablet.post("/api/beenden").status_code, 403)
                self.assertEqual(tablet.get("/api/beenden").status_code, 403)
            app.state.beenden.assert_not_called()
        with tempfile.TemporaryDirectory() as ordner:
            app = _app(ordner)
            app.state.beenden = mock.Mock()
            with TestClient(app, client=("127.0.0.1", 50000)) as hier:
                stand = hier.get("/api/beenden").json()["data"]
                self.assertEqual(stand, {"lenkung": False, "arbeit": False})
                self.assertEqual(hier.post("/api/beenden").status_code, 200)
                import time
                time.sleep(0.6)
            app.state.beenden.assert_called_once()

    def test_through_the_tunnel_ending_is_refused(self):
        from fastapi.testclient import TestClient
        with tempfile.TemporaryDirectory() as ordner:
            app = _app(ordner)
            app.state.beenden = mock.Mock()
            with TestClient(app, client=("127.0.0.1", 50000)) as client:
                antwort = client.post("/api/beenden", headers={"cf-connecting-ip": "1.2.3.4"})
                self.assertEqual(antwort.status_code, 403)
            app.state.beenden.assert_not_called()


class EintragTests(unittest.TestCase):
    def test_apps_and_features_entry_points_at_the_uninstaller(self):
        eintrag = exe.deinstallations_eintrag(Path(r"C:\AgriPilot\AgriPilot.exe"),
                                              Path(r"C:\AgriPilot"), "1.5.4", 22000)
        self.assertEqual(eintrag["DisplayName"], "AgriPilot")
        self.assertEqual(eintrag["DisplayVersion"], "1.5.4")
        self.assertEqual(eintrag["UninstallString"],
                         r'"C:\AgriPilot\AgriPilot.exe" --deinstallieren')
        self.assertIn("--still", eintrag["QuietUninstallString"])
        self.assertEqual((eintrag["NoModify"], eintrag["NoRepair"]), (1, 1))

    def test_install_plan_registers_the_uninstaller(self):
        texte = [s.text for s in exe.plan(r"C:\AgriPilot")]
        self.assertTrue(any("Apps & Features" in t for t in texte))
        self.assertTrue(any("kein Autostart" in t for t in texte))


class DeinstallationsPlanTests(unittest.TestCase):
    def test_data_stays_unless_asked(self):
        texte = [s.text for s in exe.plan_deinstallation(r"C:\AgriPilot")]
        self.assertTrue(texte[0].startswith("AgriPilot beenden"))
        self.assertTrue(texte[-1].startswith("Programmordner"))
        self.assertTrue(any("bleiben" in t for t in texte))
        self.assertFalse(any(t.startswith("Felder, Spuren") for t in texte))
        mit = [s.text for s in exe.plan_deinstallation(r"C:\AgriPilot", daten_loeschen=True)]
        self.assertTrue(any(t.startswith("Felder, Spuren") for t in mit))

    def test_steps_remove_shortcuts_and_data_only_when_chosen(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            oeffentlich, pd, lokal = tmp / "Public", tmp / "PD", tmp / "Lokal"
            programme = pd / "Microsoft" / "Windows" / "Start Menu" / "Programs"
            for ordner in (oeffentlich / "Desktop", programme / "StartUp"):
                ordner.mkdir(parents=True)
            for lnk in (oeffentlich / "Desktop" / "AgriPilot.lnk", programme / "AgriPilot.lnk",
                        programme / "AgriPilot deinstallieren.lnk",
                        programme / "StartUp" / "AgriPilot.lnk"):
                lnk.write_bytes(b"x")
            daten = tmp / "AgriPilot-Daten"
            daten.mkdir()
            (daten / "agripilot.db").write_bytes(b"felder")
            (lokal / "AgriPilot" / "Fenster").mkdir(parents=True)
            umgebung = {"PUBLIC": str(oeffentlich), "PROGRAMDATA": str(pd),
                        "LOCALAPPDATA": str(lokal)}
            with mock.patch.dict(os.environ, umgebung):
                for schritt in exe.plan_deinstallation(str(tmp / "AgriPilot"), daten=str(daten)):
                    if schritt.text.startswith(("Symbole", "Fensterdaten", "Felder")):
                        schritt.aktion()
                self.assertEqual(list(oeffentlich.rglob("*.lnk")) + list(programme.rglob("*.lnk")), [])
                self.assertTrue((daten / "agripilot.db").exists())        # bleibt
                self.assertFalse((lokal / "AgriPilot").exists())
                for schritt in exe.plan_deinstallation(str(tmp / "AgriPilot"), daten=str(daten),
                                                       daten_loeschen=True):
                    if schritt.text.startswith("Felder"):
                        schritt.aktion()
                self.assertFalse(daten.exists())

    def test_never_deletes_a_folder_that_is_not_agripilots(self):
        self.assertTrue(exe.ordner_sicher_loeschbar(Path(r"C:\AgriPilot")))
        self.assertFalse(exe.ordner_sicher_loeschbar(Path("C:\\")))
        self.assertFalse(exe.ordner_sicher_loeschbar(Path(r"C:\Programme")))
        schritt = [s for s in exe.plan_deinstallation(r"C:\Users")
                   if s.text.startswith("Programmordner")][0]
        with mock.patch.object(exe.subprocess, "Popen") as popen:
            with self.assertRaises(RuntimeError):
                schritt.aktion()
        popen.assert_not_called()

    def test_the_program_folder_is_removed_after_the_uninstaller_exits(self):
        with tempfile.TemporaryDirectory() as tmp:
            ziel = Path(tmp) / "AgriPilot"
            ziel.mkdir()
            schritt = [s for s in exe.plan_deinstallation(str(ziel))
                       if s.text.startswith("Programmordner")][0]
            with mock.patch.object(exe.subprocess, "Popen") as popen:
                schritt.aktion()
            befehl = popen.call_args[0][0]
            self.assertIn("ping -n", befehl)                 # erst warten ...
            self.assertIn(f'rmdir /s /q "{ziel}"', befehl)    # ... dann löschen


class DeinstallationsAufrufTests(unittest.TestCase):
    def test_asks_first_and_cancels_on_no(self):
        if os.name != "nt":
            self.skipTest("nur Windows")
        with mock.patch.object(exe, "ist_admin", return_value=True), \
                mock.patch("builtins.input", return_value="n"), \
                mock.patch("builtins.print"), \
                mock.patch.object(exe, "plan_deinstallation") as plan:
            self.assertEqual(exe.deinstallieren(r"C:\AgriPilot"), 0)
        plan.assert_not_called()

    def test_yes_then_keep_data(self):
        if os.name != "nt":
            self.skipTest("nur Windows")
        schritt = exe.Schritt("x", mock.Mock())
        with mock.patch.object(exe, "ist_admin", return_value=True), \
                mock.patch("builtins.input", side_effect=["j", ""]), \
                mock.patch("builtins.print"), \
                mock.patch.object(exe, "plan_deinstallation", return_value=[schritt]) as plan:
            self.assertEqual(exe.deinstallieren(r"C:\AgriPilot"), 0)
        self.assertFalse(plan.call_args.kwargs["daten_loeschen"])
        schritt.aktion.assert_called_once()

    def test_the_installed_exe_uninstalls_its_own_folder(self):
        with mock.patch.object(sys, "frozen", True, create=True), \
                mock.patch.object(sys, "executable", r"D:\Programme\AgriPilot\AgriPilot.exe"), \
                mock.patch.object(exe, "deinstallieren", return_value=0) as deinst, \
                mock.patch("builtins.input"):
            exe.main(["--deinstallieren", "--still"])
        self.assertEqual(deinst.call_args[0][0], r"D:\Programme\AgriPilot")


if __name__ == "__main__":
    unittest.main()
