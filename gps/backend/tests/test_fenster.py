"""Die Anzeige im eigenen Fenster: Startdatei, Verknüpfung, Einrichtung, Ersatzwege."""

import os
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from agripilot import exe, fenster


class StartdateiTests(unittest.TestCase):
    def test_the_window_starts_without_console_and_without_waiting(self):
        text = exe.fenster_vbs(r"C:\AgriPilot\AgriPilot.exe")
        self.assertIn(r'""C:\AgriPilot\AgriPilot.exe"" --fenster', text)
        self.assertIn(", 0, False\r\n", text)   # Konsole versteckt, nicht auf das Ende warten

    def test_starter_step_writes_both_scripts(self):
        with tempfile.TemporaryDirectory() as ordner:
            ziel = Path(ordner) / "AgriPilot"
            ziel.mkdir()
            starter = [s for s in exe.plan(str(ziel), daten=str(Path(ordner) / "d"))
                       if s.text.startswith("Startdatei")][0]
            starter.aktion()
            self.assertIn("--dienst", (ziel / "start_leise.vbs").read_text(encoding="ascii"))
            self.assertIn("--fenster", (ziel / "fenster.vbs").read_text(encoding="ascii"))

    @unittest.skipUnless(os.name == "nt", "Verknüpfungen nur unter Windows")
    def test_the_shortcut_opens_the_window_with_the_program_icon(self):
        with tempfile.TemporaryDirectory() as ordner:
            ort = Path(ordner) / "Ordner mit Leerzeichen"
            ort.mkdir()
            lnk = ort / "AgriPilot.lnk"
            vbs = ort / "fenster.vbs"
            exe.verknuepfung_anlegen(lnk, vbs, Path(sys.executable), ort)
            self.assertTrue(lnk.exists())
            lesen = ("$s=(New-Object -ComObject WScript.Shell).CreateShortcut($env:LNK); "
                     "$s.TargetPath; $s.Arguments; $s.IconLocation")
            ausgabe = subprocess.run(["powershell", "-NoProfile", "-Command", lesen],
                                     capture_output=True, text=True,
                                     env=dict(os.environ, LNK=str(lnk))).stdout.splitlines()
            self.assertTrue(ausgabe[0].lower().endswith("wscript.exe"), ausgabe)
            self.assertEqual(ausgabe[1], f'"{vbs}"')
            self.assertTrue(ausgabe[2].startswith(sys.executable), ausgabe)


class SymbolTests(unittest.TestCase):
    def _symbol_schritt(self, ordner: Path, rolle: str = "master", konfig: str = ""):
        oeffentlich = ordner / "Public"
        (oeffentlich / "Desktop").mkdir(parents=True)
        programme = ordner / "PD" / "Microsoft" / "Windows" / "Start Menu" / "Programs"
        (programme / "StartUp").mkdir(parents=True)
        daten = ordner / "daten"
        if konfig:
            daten.mkdir()
            (daten / "config.yaml").write_text(konfig, encoding="utf-8")
        (oeffentlich / "Desktop" / "AgriPilot.url").write_text("[InternetShortcut]", encoding="ascii")
        with mock.patch.dict(os.environ, {"PUBLIC": str(oeffentlich),
                                          "PROGRAMDATA": str(ordner / "PD")}):
            schritt = [s for s in exe.plan(str(ordner / "ziel"), daten=str(daten), rolle=rolle)
                       if s.text.startswith("Symbol")][0]
            angelegt = []
            with mock.patch.object(exe, "verknuepfung_anlegen",
                                   side_effect=lambda lnk, *rest: angelegt.append(lnk)):
                schritt.aktion()
        return angelegt, oeffentlich, programme

    def test_master_gets_desktop_and_start_menu_and_the_browser_link_goes(self):
        with tempfile.TemporaryDirectory() as tmp:
            angelegt, oeffentlich, programme = self._symbol_schritt(Path(tmp))
            self.assertEqual(angelegt, [oeffentlich / "Desktop" / "AgriPilot.lnk",
                                        programme / "AgriPilot.lnk"])
            self.assertFalse((oeffentlich / "Desktop" / "AgriPilot.url").exists())

    def test_a_worker_opens_the_window_at_logon(self):
        with tempfile.TemporaryDirectory() as tmp:
            angelegt, _, programme = self._symbol_schritt(Path(tmp), rolle="client")
            self.assertIn(programme / "StartUp" / "AgriPilot.lnk", angelegt)

    def test_an_updated_worker_is_recognised_by_its_configuration(self):
        with tempfile.TemporaryDirectory() as tmp:
            angelegt, _, programme = self._symbol_schritt(
                Path(tmp), konfig="network:\n  role: client\n  master_url: http://x\n")
            self.assertIn(programme / "StartUp" / "AgriPilot.lnk", angelegt)

    def test_is_worker_reads_only_the_role(self):
        with tempfile.TemporaryDirectory() as tmp:
            konfig = Path(tmp) / "config.yaml"
            self.assertFalse(exe.ist_arbeiter(konfig))
            konfig.write_text("network:\n  role: master\n# role: client\n", encoding="utf-8")
            self.assertFalse(exe.ist_arbeiter(konfig))
            konfig.write_text("network:\n  role: 'client'\n", encoding="utf-8")
            self.assertTrue(exe.ist_arbeiter(konfig))


class AufrufTests(unittest.TestCase):
    def test_window_argument_opens_the_window(self):
        with mock.patch.object(fenster, "oeffnen", return_value=0) as oeffnen:
            self.assertEqual(exe.main(["--fenster"]), 0)
        oeffnen.assert_called_once()

    def test_install_without_admin_hands_over_and_does_not_wait_for_enter(self):
        if os.name != "nt":
            self.skipTest("nur Windows")
        shell32 = mock.Mock()
        with mock.patch.object(exe, "ist_admin", return_value=False), \
                mock.patch("ctypes.windll.shell32", shell32, create=True), \
                mock.patch.object(sys, "frozen", True, create=True), \
                mock.patch("builtins.input") as eingabe, mock.patch("builtins.print"):
            exe._WEITERGEREICHT = False
            self.assertEqual(exe.main([]), 0)
        shell32.ShellExecuteW.assert_called_once()
        self.assertEqual(shell32.ShellExecuteW.call_args[0][1], "runas")
        eingabe.assert_not_called()
        exe._WEITERGEREICHT = False


class FensterTests(unittest.TestCase):
    def test_without_webview_the_display_still_opens(self):
        with mock.patch.object(fenster, "schon_offen", return_value=False), \
                mock.patch.dict(sys.modules, {"webview": None}), \
                mock.patch.object(fenster, "ersatz_oeffnen") as ersatz:
            self.assertEqual(fenster.oeffnen(8080), 0)
        ersatz.assert_called_once_with("http://localhost:8080")

    def test_a_running_service_is_shown_directly_otherwise_the_waiting_page(self):
        for bereit, erwartet in ((True, "url"), (False, "html")):
            webview = types.SimpleNamespace(create_window=mock.Mock(return_value=mock.Mock()),
                                            start=mock.Mock())
            with mock.patch.object(fenster, "schon_offen", return_value=False), \
                    mock.patch.object(fenster, "dienst_bereit", return_value=bereit), \
                    mock.patch.object(fenster, "speicherort", return_value="x"), \
                    mock.patch.dict(sys.modules, {"webview": webview}):
                fenster.oeffnen(8080)
            args, kwargs = webview.create_window.call_args
            self.assertEqual(args[0], "AgriPilot")
            self.assertTrue(kwargs["maximized"])
            if erwartet == "url":
                self.assertEqual(args[1], "http://localhost:8080")
            else:
                self.assertIn("http://localhost:8080", kwargs["html"])
            # Einstellungen der Oberfläche bleiben über Neustarts erhalten.
            self.assertEqual(webview.start.call_args.kwargs,
                             {"private_mode": False, "storage_path": "x"})

    def test_a_second_click_does_not_open_a_second_window(self):
        webview = mock.Mock()
        with mock.patch.object(fenster, "schon_offen", return_value=True), \
                mock.patch.dict(sys.modules, {"webview": webview}):
            self.assertEqual(fenster.oeffnen(8080), 0)
        webview.create_window.assert_not_called()

    def test_if_webview2_fails_edge_or_the_browser_steps_in(self):
        webview = types.SimpleNamespace(create_window=mock.Mock(),
                                        start=mock.Mock(side_effect=RuntimeError("kein WebView2")))
        with mock.patch.object(fenster, "schon_offen", return_value=False), \
                mock.patch.object(fenster, "dienst_bereit", return_value=True), \
                mock.patch.object(fenster, "speicherort", return_value="x"), \
                mock.patch.dict(sys.modules, {"webview": webview}), \
                mock.patch.object(fenster, "ersatz_oeffnen") as ersatz:
            fenster.oeffnen(8080)
        ersatz.assert_called_once()

    def test_edge_app_window_before_the_browser(self):
        with mock.patch.object(fenster, "edge_pfad", return_value=r"C:\Edge\msedge.exe"), \
                mock.patch.object(fenster.subprocess, "Popen") as popen:
            self.assertEqual(fenster.ersatz_oeffnen("http://localhost:8080"), "edge")
        self.assertIn("--app=http://localhost:8080", popen.call_args[0][0])
        with mock.patch.object(fenster, "edge_pfad", return_value=None), \
                mock.patch("webbrowser.open") as browser:
            self.assertEqual(fenster.ersatz_oeffnen("http://localhost:8080"), "browser")
        browser.assert_called_once()

    def test_the_waiting_page_polls_and_explains_after_a_minute(self):
        seite = fenster.warteseite("http://localhost:8080")
        self.assertIn("'http://localhost:8080'", seite)
        self.assertIn("location.replace(ziel)", seite)
        self.assertIn("60000", seite)
        self.assertIn("agripilot.log", seite)


if __name__ == "__main__":
    unittest.main()
