; AgriPilot - Windows-Installer.
;
; Gebaut wird die .exe mit scripts/make_exe.py, nicht von Hand. Der Aufruf
; braucht drei Angaben:
;
;     makensis -DQUELLE=<ordner> -DVERSION=<fassung> -DAUSGABE=<datei.exe> installer.nsi
;
; Was diese Datei macht und was nicht
; -----------------------------------
;
; Sie ist die Oberflaeche: auspacken, Fortschritt zeigen, am Ende ein Haken.
; Die Einrichtung selbst - Python-Umgebung, Konfiguration, Firewall,
; automatischer Start, Verknuepfung - macht weiterhin install_windows.ps1.
; So gibt es die Arbeitsschritte nur einmal, und wer kein .exe mag, ruft
; dieselbe Datei von Hand auf.
;
; QUELLE ist ein Linux-Pfad (gebaut wird auf dem Hofrechner), deshalb stehen
; in den File-Zeilen Schraegstriche. Pfade, die auf dem Tablet gelten
; ($INSTDIR und dergleichen), behalten die Rueckstriche.

Unicode true

!include "MUI2.nsh"
!include "LogicLib.nsh"

!ifndef QUELLE
  !error "QUELLE fehlt - bitte scripts/make_exe.py benutzen"
!endif
!ifndef VERSION
  !define VERSION "0.0.0"
!endif
!ifndef AUSGABE
  !define AUSGABE "AgriPilot-Setup.exe"
!endif

!define DEINSTALL "Software\Microsoft\Windows\CurrentVersion\Uninstall\AgriPilot"

Name "AgriPilot ${VERSION}"
OutFile "${AUSGABE}"
InstallDir "C:\AgriPilot"
InstallDirRegKey HKLM "Software\AgriPilot" "Pfad"
RequestExecutionLevel admin
ShowInstDetails show
ShowUninstDetails show
SetCompressor /SOLID lzma

VIProductVersion "${VERSION}.0"
VIAddVersionKey "ProductName"     "AgriPilot"
VIAddVersionKey "FileDescription" "AgriPilot einrichten"
VIAddVersionKey "FileVersion"     "${VERSION}"
VIAddVersionKey "ProductVersion"  "${VERSION}"
VIAddVersionKey "LegalCopyright"  ""

!define MUI_ICON   "${QUELLE}/frontend/icon.ico"
!define MUI_UNICON "${QUELLE}/frontend/icon.ico"
!define MUI_ABORTWARNING

!define MUI_WELCOMEPAGE_TITLE "AgriPilot auf diesem Tablet einrichten"
!define MUI_WELCOMEPAGE_TEXT "Dieses Programm richtet AgriPilot ${VERSION} auf dem Tablet ein, das in der Kabine rechnet.$\r$\n$\r$\nEs legt das Programm ab, richtet die Python-Umgebung ein, oeffnet den Port fuer das Android-Tablet in der Firewall, traegt den Start beim Anmelden ein und legt ein Symbol auf den Desktop.$\r$\n$\r$\nKonfiguration, Felder und aufgezeichnete Arbeiten bleiben unangetastet - auch beim zweiten Mal.$\r$\n$\r$\nLaeuft AgriPilot gerade, wird es kurz angehalten und am Ende wieder gestartet."

!define MUI_DIRECTORYPAGE_TEXT_TOP "AgriPilot wird in den folgenden Ordner gelegt. Der Vorschlag passt fuer fast jeden Fall; die Daten (Konfiguration, Felder, Aufzeichnungen) liegen ohnehin getrennt davon unter ProgramData und bleiben bei einer Deinstallation erhalten."

!define MUI_FINISHPAGE_TITLE "AgriPilot ist eingerichtet"
!define MUI_FINISHPAGE_TEXT "AgriPilot startet ab jetzt bei jeder Anmeldung von selbst.$\r$\n$\r$\nAuf dem Desktop liegt das Symbol AgriPilot - antippen oeffnet die Anzeige.$\r$\n$\r$\nFuer das Android-Tablet: im selben WLAN den Browser auf die Adresse des Tablets richten. Wie es dort zur Kachel wird, steht in docs\TABLETS.md."
; Der Umweg ueber den Explorer ist Absicht: der Installer laeuft mit
; Administratorrechten, und ein Browser, der von hier aus startet, erbte sie.
; Der Explorer laeuft als der angemeldete Benutzer und gibt sie nicht weiter.
!define MUI_FINISHPAGE_RUN
!define MUI_FINISHPAGE_RUN_FUNCTION AnzeigeOeffnen
!define MUI_FINISHPAGE_RUN_TEXT "AgriPilot jetzt oeffnen"

!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH

!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES

!insertmacro MUI_LANGUAGE "German"


; --------------------------------------------------------------- Einrichten

Section "AgriPilot" Haupt
  SetDetailsPrint both

  DetailPrint "Dateien werden ausgepackt ..."
  InitPluginsDir
  SetOutPath "$PLUGINSDIR\quelle"
  File /r "${QUELLE}/*"

  Call PythonSuchen
  Pop $R0
  ${If} $R0 == ""
    Abort "Ohne Python geht es nicht."
  ${EndIf}

  ; Die eigentliche Einrichtung. Die Ausgabe landet im Fenster darunter -
  ; damit im Fehlerfall dasteht, woran es lag, statt nur einer Nummer.
  DetailPrint "AgriPilot wird eingerichtet - das dauert eine Minute ..."
  StrCpy $R1 ""
  IfFileExists "$PLUGINSDIR\quelle\pakete\*.*" 0 +2
    StrCpy $R1 ' -Pakete "$PLUGINSDIR\quelle\pakete"'
  nsExec::ExecToLog 'powershell -NoProfile -ExecutionPolicy Bypass -File "$PLUGINSDIR\quelle\scripts\install_windows.ps1" -Ziel "$INSTDIR"$R1'
  Pop $0
  ${If} $0 != 0
    MessageBox MB_ICONSTOP "Die Einrichtung ist fehlgeschlagen.$\r$\n$\r$\nIm Fenster darunter steht, woran es lag - die letzten Zeilen sind die wichtigen. Mit Rechtsklick laesst sich der Text kopieren."
    Abort "Die Einrichtung ist fehlgeschlagen."
  ${EndIf}

  ; Damit AgriPilot in "Apps & Features" auftaucht und sich dort auch wieder
  ; entfernen laesst - wie jedes andere Programm auch.
  WriteUninstaller "$INSTDIR\AgriPilot-deinstallieren.exe"
  WriteRegStr HKLM "Software\AgriPilot" "Pfad" "$INSTDIR"
  WriteRegStr HKLM "${DEINSTALL}" "DisplayName"     "AgriPilot"
  WriteRegStr HKLM "${DEINSTALL}" "DisplayVersion"  "${VERSION}"
  WriteRegStr HKLM "${DEINSTALL}" "DisplayIcon"     "$INSTDIR\frontend\icon.ico"
  WriteRegStr HKLM "${DEINSTALL}" "InstallLocation" "$INSTDIR"
  WriteRegStr HKLM "${DEINSTALL}" "UninstallString" '"$INSTDIR\AgriPilot-deinstallieren.exe"'
  WriteRegDWORD HKLM "${DEINSTALL}" "NoModify" 1
  WriteRegDWORD HKLM "${DEINSTALL}" "NoRepair" 1
SectionEnd


; Der Umweg ueber den Explorer ist Absicht: der Installer laeuft mit
; Administratorrechten, und ein Browser, der direkt von hier aus startet,
; erbte sie. Der Explorer laeuft als der angemeldete Benutzer und gibt sie
; nicht weiter - die Anzeige oeffnet also als gewoehnliches Fenster.
Function AnzeigeOeffnen
  Exec '"$WINDIR\explorer.exe" "$INSTDIR\AgriPilot.bat"'
FunctionEnd


; Python suchen - und wenn keines da ist, das nicht als Fehlernummer melden,
; sondern als Satz. Liegt ein Python-Installer neben dieser Datei, wird er
; still ausgefuehrt: so laesst sich ein Tablet ohne WLAN einrichten, indem
; man beide Dateien auf denselben Stick legt.
;
; Zurueck kommt "1" (gefunden) oder "" (nicht gefunden). Welche Fassung es
; genau ist, schreibt gleich darauf install_windows.ps1 ins Fenster - hier
; auf der Ausgabe herumzuschneiden waere nur eine Quelle fuer Fehler.
;
; $2 und $3 werden benutzt und nicht gesichert; im Abschnitt darueber stehen
; die Werte in $R0 und $R1.
Function PythonSuchen
  DetailPrint "Python suchen ..."
  Call PythonFragen
  Pop $2
  ${If} $2 != ""
    DetailPrint "Python ist vorhanden."
    Push "1"
    Return
  ${EndIf}

  FindFirst $3 $2 "$EXEDIR\python-*.exe"
  FindClose $3
  ${If} $2 != ""
    DetailPrint "Python wird aus $2 installiert - das dauert einen Moment ..."
    nsExec::ExecToLog '"$EXEDIR\$2" /quiet InstallAllUsers=1 PrependPath=1 Include_pip=1 Include_launcher=1'
    Pop $3
    Call PythonFragen
    Pop $2
    ${If} $2 != ""
      DetailPrint "Python ist eingerichtet."
      Push "1"
      Return
    ${EndIf}
  ${EndIf}

  MessageBox MB_ICONEXCLAMATION|MB_OKCANCEL "Auf diesem Tablet ist kein Python installiert - AgriPilot braucht es als Unterbau.$\r$\n$\r$\nSo geht es weiter:$\r$\n  1. Auf python.org unter Downloads die Fassung fuer Windows holen.$\r$\n  2. Beim Installieren den Haken bei 'Add python.exe to PATH' setzen.$\r$\n  3. Diese Datei erneut starten.$\r$\n$\r$\nOhne WLAN am Tablet: den Python-Installer am Hofrechner laden und neben diese Datei legen - dann wird er beim naechsten Start von hier aus still mit installiert.$\r$\n$\r$\nOK oeffnet python.org im Browser." IDCANCEL keinbrowser
  ExecShell "open" "https://www.python.org/downloads/windows/"
keinbrowser:
  Push ""
FunctionEnd


; Antwortet ein Python auf die Frage nach seiner Fassung? Zuerst der Starter
; py.exe - den gibt es auch dann, wenn beim Einrichten der Haken bei "Add to
; PATH" vergessen wurde, was oft genug vorkommt.
;
; Zurueck kommt "1" oder "". $2 und $3 werden dabei ueberschrieben.
Function PythonFragen
  nsExec::ExecToStack 'cmd /c py -3 -V'
  Pop $2          ; Rueckgabewert
  Pop $3          ; Ausgabe - hier nicht gebraucht
  ${If} $2 == 0
    Push "1"
    Return
  ${EndIf}

  nsExec::ExecToStack 'cmd /c python -V'
  Pop $2
  Pop $3
  ${If} $2 == 0
    Push "1"
    Return
  ${EndIf}

  Push ""
FunctionEnd


; ----------------------------------------------------------- Deinstallieren

Section "Uninstall"
  SetDetailsPrint both
  SetOutPath "$TEMP"
  ; "all" macht aus $APPDATA das ProgramData aller Benutzer - dort liegen die
  ; Daten, und genau die bleiben liegen.
  SetShellVarContext all

  DetailPrint "AgriPilot wird angehalten und ausgetragen ..."
  IfFileExists "$INSTDIR\scripts\uninstall_windows.ps1" 0 ohneskript
    nsExec::ExecToLog 'powershell -NoProfile -ExecutionPolicy Bypass -File "$INSTDIR\scripts\uninstall_windows.ps1" -Ziel "$INSTDIR"'
    Pop $0
ohneskript:

  DeleteRegKey HKLM "${DEINSTALL}"
  DeleteRegKey HKLM "Software\AgriPilot"

  ; Die Daten unter ProgramData bleiben mit Absicht liegen: Felder, Grenzen
  ; und aufgezeichnete Arbeiten sind die Arbeit von Jahren, und ein
  ; Deinstallieren ist oft nur der erste Schritt einer Neuinstallation.
  Delete /REBOOTOK "$INSTDIR\AgriPilot-deinstallieren.exe"
  RMDir /r /REBOOTOK "$INSTDIR"

  MessageBox MB_ICONINFORMATION|MB_OK "AgriPilot ist entfernt.$\r$\n$\r$\nFelder, Grenzen und aufgezeichnete Arbeiten liegen weiterhin unter$\r$\n$APPDATA\AgriPilot$\r$\n$\r$\nWer auch die loswerden will, loescht diesen Ordner von Hand."
SectionEnd
