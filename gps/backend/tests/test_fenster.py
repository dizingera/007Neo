"""AgriPilot im eigenen Fenster: Startdatei, Verknüpfungen, Start und Beenden, Ersatzwege."""

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
    def test_a_real_shortcut_with_spaces_in_every_path(self):
        with tempfile.TemporaryDirectory() as ordner:
            ort = Path(ordner) / "Ordner mit Leerzeichen"
            ort.mkdir()
            lnk = ort / "AgriPilot.lnk"
            vbs = ort / "fenster.vbs"
            exe.verknuepfung_anlegen(lnk, exe.wscript(), f'"{vbs}"', Path(sys.executable), ort)
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
    def _symbol_schritt(self, ordner: Path, rolle: str = "master"):
        oeffentlich = ordner / "Public"
        (oeffentlich / "Desktop").mkdir(parents=True)
        programme = ordner / "PD" / "Microsoft" / "Windows" / "Start Menu" / "Programs"
        (programme / "StartUp").mkdir(parents=True)
        # Was frühere Versionen hinterlassen haben:
        (oeffentlich / "Desktop" / "AgriPilot.url").write_text("[InternetShortcut]", encoding="ascii")
        (programme / "StartUp" / "AgriPilot.lnk").write_bytes(b"alt")
        with mock.patch.dict(os.environ, {"PUBLIC": str(oeffentlich),
                                          "PROGRAMDATA": str(ordner / "PD")}):
            schritt = [s for s in exe.plan(str(ordner / "ziel"), daten=str(ordner / "d"),
                                           rolle=rolle)
                       if s.text.startswith("Symbol")][0]
            angelegt = []
            with mock.patch.object(exe, "verknuepfung_anlegen",
                                   side_effect=lambda lnk, ziel, argumente, *rest:
                                   angelegt.append((lnk, Path(ziel).name, argumente))):
                schritt.aktion()
        return angelegt, oeffentlich, programme

    def test_desktop_and_start_menu_open_the_window_and_uninstall_is_offered(self):
        with tempfile.TemporaryDirectory() as tmp:
            angelegt, oeffentlich, programme = self._symbol_schritt(Path(tmp))
            ziele = {lnk: (ziel, argumente) for lnk, ziel, argumente in angelegt}
            self.assertEqual(ziele[oeffentlich / "Desktop" / "AgriPilot.lnk"][0], "wscript.exe")
            self.assertIn("fenster.vbs", ziele[programme / "AgriPilot.lnk"][1])
            self.assertEqual(ziele[programme / "AgriPilot deinstallieren.lnk"],
                             ("AgriPilot.exe", "--deinstallieren"))

    def test_nothing_starts_with_windows_and_old_autostarts_go(self):
        for rolle in ("master", "client"):
            with tempfile.TemporaryDirectory() as tmp:
                angelegt, oeffentlich, programme = self._symbol_schritt(Path(tmp), rolle)
                self.assertFalse(any("StartUp" in str(lnk) for lnk, *_ in angelegt))
                self.assertFalse((programme / "StartUp" / "AgriPilot.lnk").exists())
                self.assertFalse((oeffentlich / "Desktop" / "AgriPilot.url").exists())


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


def _webview(start=None):
    fenster_obj = types.SimpleNamespace(events=types.SimpleNamespace(closing=_Ereignis()))
    return types.SimpleNamespace(create_window=mock.Mock(return_value=fenster_obj),
                                 start=start or mock.Mock()), fenster_obj


class _Ereignis(list):
    def __iadd__(self, funktion):
        self.append(funktion)
        return self


class FensterTests(unittest.TestCase):
    def _oeffnen(self, bereit=True, start=None):
        webview, fenster_obj = _webview(start)
        with mock.patch.object(fenster, "schon_offen", return_value=False), \
                mock.patch.object(fenster, "dienst_bereit", return_value=bereit), \
                mock.patch.object(fenster, "speicherort", return_value="x"), \
                mock.patch.object(fenster, "dienst_starten") as starten, \
                mock.patch.object(fenster, "dienst_beenden") as beenden, \
                mock.patch.dict(sys.modules, {"webview": webview}):
            fenster.oeffnen(8080)
        return webview, fenster_obj, starten, beenden

    def test_opening_starts_the_program_closing_ends_it(self):
        webview, fenster_obj, starten, beenden = self._oeffnen(bereit=False)
        starten.assert_called_once()
        self.assertIn("http://localhost:8080", webview.create_window.call_args.kwargs["html"])
        beenden.assert_called_once_with("http://localhost:8080")
        self.assertEqual(len(fenster_obj.events.closing), 1)   # fragt vor dem Schließen nach

    def test_a_running_program_is_shown_directly_and_not_started_twice(self):
        webview, _, starten, _ = self._oeffnen(bereit=True)
        starten.assert_not_called()
        args, kwargs = webview.create_window.call_args
        self.assertEqual(args[:2], ("AgriPilot", "http://localhost:8080"))
        self.assertTrue(kwargs["maximized"])
        # Einstellungen der Oberfläche bleiben über Neustarts erhalten.
        self.assertEqual(webview.start.call_args.kwargs,
                         {"private_mode": False, "storage_path": "x"})

    def test_without_webview_the_display_still_opens(self):
        with mock.patch.object(fenster, "schon_offen", return_value=False), \
                mock.patch.object(fenster, "dienst_bereit", return_value=True), \
                mock.patch.dict(sys.modules, {"webview": None}), \
                mock.patch.object(fenster, "ersatz_oeffnen") as ersatz:
            self.assertEqual(fenster.oeffnen(8080), 0)
        ersatz.assert_called_once_with("http://localhost:8080")

    def test_a_second_click_does_not_open_a_second_window(self):
        webview = mock.Mock()
        with mock.patch.object(fenster, "schon_offen", return_value=True), \
                mock.patch.dict(sys.modules, {"webview": webview}):
            self.assertEqual(fenster.oeffnen(8080), 0)
        webview.create_window.assert_not_called()

    def test_if_webview2_fails_edge_steps_in_and_the_program_keeps_running(self):
        with mock.patch.object(fenster, "ersatz_oeffnen") as ersatz:
            _, _, _, beenden = self._oeffnen(start=mock.Mock(side_effect=RuntimeError("kein WebView2")))
        ersatz.assert_called_once()
        beenden.assert_not_called()

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


class BeendenTests(unittest.TestCase):
    def test_closing_asks_only_when_steering_or_a_job_would_be_cut(self):
        fragen = mock.Mock(return_value=False)
        with mock.patch.object(fenster, "_anfrage",
                               return_value={"data": {"lenkung": False, "arbeit": False}}):
            self.assertTrue(fenster.beenden_erlaubt("u", fragen))
        fragen.assert_not_called()
        with mock.patch.object(fenster, "_anfrage",
                               return_value={"data": {"lenkung": True, "arbeit": True}}):
            self.assertFalse(fenster.beenden_erlaubt("u", fragen))   # "Nein" = bleibt offen
        text = fragen.call_args[0][1]
        self.assertIn("Lenkung", text)
        self.assertIn("gesichert", text)

    def test_a_program_that_does_not_answer_closes_without_question(self):
        with mock.patch.object(fenster, "_anfrage", return_value=None):
            self.assertTrue(fenster.beenden_erlaubt("u", mock.Mock()))

    def test_clean_end_first_hard_stop_only_if_needed(self):
        with mock.patch.object(fenster, "dienst_bereit", side_effect=[True, False]), \
                mock.patch.object(fenster, "_anfrage") as anfrage, \
                mock.patch.object(fenster, "_aufgabe") as aufgabe:
            self.assertEqual(fenster.dienst_beenden("http://localhost:8080"), "beendet")
        anfrage.assert_called_once_with("http://localhost:8080/api/beenden", "POST")
        aufgabe.assert_not_called()
        with mock.patch.object(fenster, "dienst_bereit", return_value=True), \
                mock.patch.object(fenster, "_anfrage"), \
                mock.patch.object(fenster, "_aufgabe") as aufgabe, \
                mock.patch.object(fenster.time, "sleep"):
            self.assertEqual(fenster.dienst_beenden("http://localhost:8080", warten_s=0.01),
                             "angehalten")
        aufgabe.assert_called_once_with("/End")


if __name__ == "__main__":
    unittest.main()
