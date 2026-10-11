"""Die .exe-Version: Installationsplan, Startdatei, Konfiguration, Aktualisierungssperre."""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from agripilot import config as config_module
from agripilot import exe, update


class StandardKonfigurationTests(unittest.TestCase):
    def test_the_first_configuration_loads_and_starts_on_the_simulator(self):
        with tempfile.TemporaryDirectory() as ordner:
            pfad = Path(ordner) / "config.yaml"
            pfad.write_text(exe.standard_konfiguration("TABLET-1", 8123), encoding="utf-8")
            config = config_module.load(pfad)
        self.assertEqual(config.gnss.source, "simulator")      # läuft ohne Hardware
        self.assertEqual(config.server.port, 8123)
        self.assertEqual(config.network.device_name, "TABLET-1")
        self.assertFalse(config.steering.enabled)             # die Lenkung bleibt ab Werk aus


class RolleTests(unittest.TestCase):
    def _laden(self, text):
        with tempfile.TemporaryDirectory() as ordner:
            pfad = Path(ordner) / "config.yaml"
            pfad.write_text(text, encoding="utf-8")
            return config_module.load(pfad)

    def test_a_worker_configuration_names_the_master(self):
        config = self._laden(exe.standard_konfiguration(
            "TABLET-2", 8080, "client", "http://192.168.1.20:8080"))
        self.assertEqual(config.network.role, "client")
        self.assertEqual(config.network.master_url, "http://192.168.1.20:8080")
        self.assertFalse(config.is_master)

    def test_a_worker_without_an_address_keeps_the_default_to_be_set_later(self):
        config = self._laden(exe.standard_konfiguration("TABLET-2", 8080, "client"))
        self.assertEqual(config.network.role, "client")

    def test_the_master_is_the_default(self):
        self.assertTrue(self._laden(exe.standard_konfiguration("PC")).is_master)

    def _frage(self, antworten, vorhanden=False):
        with tempfile.TemporaryDirectory() as ordner:
            if vorhanden:
                (Path(ordner) / "config.yaml").write_text("x: 1\n", encoding="utf-8")
            stdin = mock.Mock()
            stdin.isatty.return_value = True
            with mock.patch.object(sys, "stdin", stdin), \
                    mock.patch("builtins.input", side_effect=antworten), \
                    mock.patch("builtins.print"):
                return exe._rolle_erfragen(Path(ordner))

    def test_enter_means_master(self):
        self.assertEqual(self._frage([""]), ("master", ""))

    def test_a_worker_gives_the_master_address(self):
        self.assertEqual(self._frage(["a", "192.168.1.20:8080"]),
                         ("client", "http://192.168.1.20:8080"))

    def test_a_worker_may_leave_the_address_for_later(self):
        self.assertEqual(self._frage(["A", ""]), ("client", ""))

    def test_an_existing_configuration_is_not_asked_about(self):
        self.assertEqual(self._frage([], vorhanden=True), ("master", ""))

    def test_without_a_keyboard_nothing_is_asked(self):
        stdin = mock.Mock()
        stdin.isatty.return_value = False
        with tempfile.TemporaryDirectory() as ordner, mock.patch.object(sys, "stdin", stdin):
            self.assertEqual(exe._rolle_erfragen(Path(ordner)), ("master", ""))


class StartdateiTests(unittest.TestCase):
    def test_vbs_runs_hidden_waits_and_passes_the_exit_code_on(self):
        text = exe.start_vbs(r"C:\AgriPilot\AgriPilot.exe")
        self.assertIn(r'""C:\AgriPilot\AgriPilot.exe"" --dienst', text)
        self.assertIn(", 0, True)", text)        # Fenster aus, auf das Ende warten
        self.assertIn("WScript.Quit code", text)  # sonst gäbe es nie einen Neustart nach Absturz

    @unittest.skipUnless(os.name == "nt", "PowerShell-Syntax nur unter Windows")
    def test_the_task_script_is_valid_powershell_with_restart_on_failure(self):
        skript = exe.aufgaben_skript(r"C:\AgriPilot", r"C:\AgriPilot\start_leise.vbs")
        self.assertIn("-RestartCount 999", skript)
        self.assertIn("-AtLogOn", skript)
        pruefung = ("$e=$null; [void][System.Management.Automation.Language.Parser]::"
                    "ParseInput($env:SKRIPT,[ref]$null,[ref]$e); $e.Count")
        ergebnis = subprocess.run(["powershell", "-NoProfile", "-Command", pruefung],
                                  capture_output=True, text=True,
                                  env=dict(os.environ, SKRIPT=skript))
        self.assertEqual(ergebnis.stdout.strip(), "0", ergebnis.stderr)


class PlanTests(unittest.TestCase):
    def test_the_plan_names_every_step_in_order(self):
        texte = [s.text for s in exe.plan(r"C:\AgriPilot")]
        self.assertTrue(texte[0].startswith("Laufende Version anhalten"))
        self.assertTrue(texte[-2].startswith("Programm starten"))
        self.assertEqual(texte[-1], "Aufräumen")
        self.assertTrue(any("Firewall" in t for t in texte))
        self.assertTrue(any("Neustart nach Absturz" in t for t in texte))

    def test_configuration_step_keeps_an_existing_file(self):
        with tempfile.TemporaryDirectory() as ordner:
            daten = Path(ordner) / "daten"
            daten.mkdir()
            vorhanden = daten / "config.yaml"
            vorhanden.write_text("# meine Einstellungen\n", encoding="utf-8")
            schritte = exe.plan(str(Path(ordner) / "ziel"), daten=str(daten))
            nur_konfig = [s for s in schritte if s.text.startswith("Konfiguration")][0]
            nur_konfig.aktion()
            self.assertEqual(vorhanden.read_text(encoding="utf-8"), "# meine Einstellungen\n")

    def test_configuration_step_creates_one_when_there_is_none(self):
        with tempfile.TemporaryDirectory() as ordner:
            daten = Path(ordner) / "daten"
            schritte = exe.plan(str(Path(ordner) / "ziel"), daten=str(daten),
                                rechnername="TABLET-9")
            [s for s in schritte if s.text.startswith("Konfiguration")][0].aktion()
            self.assertIn("TABLET-9", (daten / "config.yaml").read_text(encoding="utf-8"))

    def test_copy_and_starter_steps_write_the_files(self):
        with tempfile.TemporaryDirectory() as ordner:
            quelle = Path(ordner) / "Setup.exe"
            quelle.write_bytes(b"MZ-programm")
            ziel = Path(ordner) / "AgriPilot"
            schritte = exe.plan(str(ziel), quelle=str(quelle), daten=str(Path(ordner) / "d"))
            for s in schritte:
                if s.text.startswith(("Programm nach", "Startdatei")):
                    s.aktion()
            self.assertEqual((ziel / "AgriPilot.exe").read_bytes(), b"MZ-programm")
            self.assertIn("--dienst", (ziel / "start_leise.vbs").read_text(encoding="ascii"))

    def test_a_new_version_that_does_not_start_is_rolled_back(self):
        with tempfile.TemporaryDirectory() as ordner:
            quelle = Path(ordner) / "neu.exe"
            quelle.write_bytes(b"MZ-neu")
            ziel = Path(ordner) / "AgriPilot"
            ziel.mkdir()
            (ziel / "AgriPilot.exe").write_bytes(b"MZ-alt")
            schritte = {s.text.split(" ")[0]: s for s in
                        exe.plan(str(ziel), quelle=str(quelle), daten=str(Path(ordner) / "d"),
                                 still=True)}
            kopieren = [s for s in exe.plan(str(ziel), quelle=str(quelle),
                                            daten=str(Path(ordner) / "d"), still=True)
                        if s.text.startswith("Programm nach")][0]
            starten = [s for s in exe.plan(str(ziel), quelle=str(quelle),
                                           daten=str(Path(ordner) / "d"), still=True)
                       if s.text.startswith("Programm starten")][0]
            kopieren.aktion()
            self.assertEqual((ziel / "AgriPilot.exe").read_bytes(), b"MZ-neu")
            self.assertEqual((ziel / "AgriPilot.exe.alt").read_bytes(), b"MZ-alt")
            with mock.patch.object(exe, "_lauf", return_value=0), \
                    mock.patch.object(exe.time, "sleep"), \
                    mock.patch.object(exe.urllib.request, "urlopen", side_effect=OSError("nichts")):
                with self.assertRaises(RuntimeError) as fehler:
                    starten.aktion()
            self.assertIn("vorige Stand wurde wieder eingesetzt", str(fehler.exception))
            self.assertEqual((ziel / "AgriPilot.exe").read_bytes(), b"MZ-alt")

    def test_dry_run_touches_nothing(self):
        with mock.patch.object(exe, "_lauf") as lauf, mock.patch("builtins.print"):
            code = exe.installieren(r"C:\AgriPilot", 8080, trocken=True)
        self.assertEqual(code, 0)
        lauf.assert_not_called()

    def test_a_failing_step_stops_the_installation_with_its_message(self):
        schritt = exe.Schritt("kaputt", mock.Mock(side_effect=RuntimeError("Platte voll")))
        folgender = exe.Schritt("danach", mock.Mock())
        with mock.patch.object(exe, "plan", return_value=[schritt, folgender]), \
                mock.patch.object(exe, "ist_admin", return_value=True), \
                mock.patch.object(exe.os, "name", "nt"), \
                mock.patch("builtins.print") as ausgabe:
            code = exe.installieren(r"C:\AgriPilot", 8080, trocken=False)
        self.assertEqual(code, 1)
        folgender.aktion.assert_not_called()
        self.assertTrue(any("Platte voll" in str(a) for a in ausgabe.call_args_list))


class AlsProgrammTests(unittest.TestCase):
    def test_update_by_zip_is_refused_with_a_pointer_to_the_setup(self):
        with mock.patch.object(sys, "frozen", True, create=True):
            for aufruf in (lambda: update.einspielen(b"x"), update.aus_repo_holen,
                           update.zurueckholen):
                with self.assertRaises(update.UpdateFehler) as fehler:
                    aufruf()
                self.assertIn("AgriPilot-Setup.exe", str(fehler.exception))

    def test_restart_command_is_the_exe_itself(self):
        with mock.patch.object(sys, "frozen", True, create=True), \
                mock.patch.object(sys, "argv", ["AgriPilot.exe", "--dienst"]):
            self.assertEqual(update.kommandozeile(), [sys.executable, "--dienst"])


if __name__ == "__main__":
    unittest.main()
