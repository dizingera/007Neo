# AgriPilot – Gestaltung

Stand1.5.42,16.10.2026. Bestehende Tablet-Webanwendung, Modus Operate.

## Leitbild
Ein präzises Arbeitsgerät für die Traktorkabine: Karte/Spur und echte Messwerte
führen, Bedienung bleibt am bekannten Platz. Nachtansicht mit dunklem Waldgrün,
Tagansicht mit hellem warmem Neutral. Keine dekorativen Fotos, neuen Claims,
Cloud-Abhängigkeiten oder erfundenen Betriebszustände.

## System
Lokale IBM Plex Sans für Texte, Rajdhani für Messzahlen; tabellarische Ziffern.
Menütext16px, Eingaben16px, Hauptüberschrift24px, Abschnitt18–20px.
Abstände8/12/16/20/24/28px, Kontrollen8px Radius, Gruppen12–14px. Große
Bedientasten mit mindestens52px im Menü. Fokus3px mit3px Abstand.
Primäre Aktionen blau; echte Spur/aktive Lenkung grün, Hinweise bernstein,
Fehler rot. Aktive Wende/Stopp blau mit weißer Schrift. Geänderte Eingaben
behalten blaue Kontur und getönten Hintergrund. Farbe ergänzt vorhandene Texte.

## Flächen und Struktur
Instrumentflächen weitgehend opak, wenig Leuchten, keine dekorative Unschärfe.
Einzelne Gruppen erhalten eine Kontur; verschachtelte Formgruppen werden durch
Abstände/Trennlinien gegliedert. Vorhandenes lokales App-Symbol im Menükopf.
Kein eigenes Symbolpaket nötig. Menüseiten, Texte, Aktionen und Reihenfolge
bleiben erhalten. Code infrontend/design.css ergänzt gezieltstyle.css.
Neue Menüregeln immer mit.sheet präfixen.

## Geräte
Querformat: Bedientasten rechts, Messwerte links, Blick auf Traktor frei.
Hochformat: Bedientasten unten. Unter600px stehen Feld/Spur und GPS/Netzstatus
als zusammenhängende Gruppe. Die Abstandsmessung berücksichtigt beide
Statusgruppen und beobachtet deren Größe; Werkzeugleiste bleibt darunter.
Schmales Menü nutzt die vorhandene horizontale Reiterleiste. Keine verdeckten
Primäraktionen und kein horizontaler Inhaltsüberlauf. Reduzierte Bewegung
unterdrückt die kurze Menü-Einblendung. Tag/Nacht sind gleichwertige Ansichten.

## Nachweisgrenze
Gestaltungsprüfung in eigener Simulator-Datenbank auf Port8092 mit als Demo
benannten Feldern; keine praktischen Maschinen-/Sonnenlicht-/Handschuhtests.
Der installierte Büro-Master auf8080 ist vom Designprüflauf getrennt.

## Markierung ab1.5.43
Bearbeitete Fläche gleichmäßig grün, Überlappung bernsteinfarben; kleine Legende
bei vorhandener Fläche/laufender Arbeit. Das Live-Endstück wird mit den
gezeichneten Kacheln einmal pro Farbebene transparent verrechnet. Maßgebend
bleiben tatsächliche Gerätebreite, bestätigte Sektionen und getrennte Aufträge.
Lange Arbeitsnamen in der Markieren-Taste höchstens zweizeilig; vollständiger
Text bleibt im DOM. Server-Hektarberechnung unverändert.

## 1.5.44 – Kartenfarben und Gesamtprüfung

Menü → Einstellungen → Kartenfarben: Feldgrenze und Fahrgassen frei einfärben,
mit schnellen Farbmustern oder dem Farbwähler. Tag- und Nachtansicht haben eigene
Farben. „Kartenfarben übernehmen“ speichert sie nur in diesem Browser;
„Standardfarben wiederherstellen“ setzt nur die gewählte Ansicht zurück.
Offene Farbauswahlen bleiben beim Wechsel der Ansicht erhalten.

Die Gesamtprüfung korrigiert zusätzlich den getrennten Sendezeitpunkt beim
Tablet-Abgleich, die sofortige Lenkabschaltung bei ungültigem GPS-Fix, eine erneute
Stillstandsprüfung nach dem Online-Download und den Zugriffsschutz der externen
Live-Verbindung. Gerätenamen werden ausschließlich als Text angezeigt.
Prüfungen am echten Traktor und an noch nicht gewählter Teilbreitenhardware stehen aus.

## Menü ab 1.5.67

Leiste 272 px: Gruppenüberschriften 13 px fett, Versalien mit Haarlinie bis zum Rand;
Einträge mindestens 47 px mit eigenem Linien-Symbol (24er Raster, Strich 1,8) in einer
9-px-Kachel, Name 15,5 px, Inhaltszeile 12,5 px. Aktiv: blau gefüllt, Kachel weiß getönt.
Fuß mit Version/Rolle nur ab 860 px Höhe – 1280×800 bleibt ohne Rollen. Seitenkopf:
Symbol in blau getönter 44-px-Kachel, Titel, Inhaltszeile. Saatplan: Vorschaubild 250 px,
14 px Radius, Norden oben, Startkante blau mit Pfeil, Legende darunter. Feldkarte: 18 px
Radius, Segment-Umschalter, schwebende Knöpfe mit Symbol, Feldnamen als Schildchen,
Luftbild außerhalb der Felder gedämpft. Unter 760 px: Reiterzeile ohne Symbole.
